# Override HLE patterns

When the fix isn't in `functions.csv`, it's an **override**: a host C++ hook that replaces
or wraps a guest function. This is where you High-Level-Emulate things the runtime doesn't
provide (an absent IOP, SIF/DMA completion, a data loader). All game-specific behavior lives
here — **never** patch the generated runner or `tools/PS2Recomp/`.

## The mechanism

In the game repo's `src/register_overrides.cpp`:

```cpp
// Registered once; matched against basename(elfPath), case-insensitive.
PS2_REGISTER_GAME_OVERRIDE("My Game", "SLXS_123.45", 0, 0, &applyMyOverrides)

static void applyMyOverrides(PS2Runtime& runtime) {
    runtime.registerFunction(0xGUEST_ADDR, [](uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) {
        // rdram = guest RAM; mask guest addresses with 0x01FFFFFF before indexing.
        // ctx    = EE context; GPR_U32(ctx,n) / SET_GPR_S32(ctx,n,v); a0..=4.., v0=2, sp=29, ra=31, gp=28.
        // ... do work ...
        ctx->pc = GPR_U32(ctx, 31);   // jr ra  (or fall through to the real fn — see below)
    });
}
```

Dispatch is **by address** — the hook fires whenever the guest calls `0xGUEST_ADDR`,
regardless of the CSV `name`. To **call the original** from inside a wrapper, call its
generated symbol (`sub_00XXXXXX_0xXXXXXX` / `FUN_00XXXXXX_0xXXXXXX`) — match the *current*
name in `<game_dir>/tmp/generated/ps2_recompiled_functions.h` (ps2_recomp picks `FUN_` vs
`sub_` and casing per regen; verify it after any regen).

The override gets the real `PS2Runtime*` — its public surface is callable, e.g.
`runtime->iop().handleRPC(...)`, `runtime->lookupFunction(pc)`. Use that to reuse runtime
HLE instead of reimplementing it.

### `BUILD_TAG` discipline (do this every time)

Keep a `static const char* BUILD_TAG = "...";` and print it from the apply function:

```cpp
std::fprintf(stderr, "[build-id] tag=%s compiled=%s %s\n", BUILD_TAG, __DATE__, __TIME__);
```

**Bump it on every edit** and confirm `tag=<new>` in `run.txt` after rebuilding. This
catches the most demoralizing failure mode — debugging a stale binary.

---

## Pattern: wait-free replacement (uncompletable busy-wait)

A guest function spins on a flag that a never-invoked completion handler would clear (an
absent IOP, an unfired DMA-completion ISR). **Poking the flag and running the real function
re-enters the spin** (it re-reads a re-set flag). The reliable fix is to **replace the whole
function** with its wait-free body — compute the outputs the completion path would have
produced, write them, and return. Read the real function's success path in the disassembly
and replicate just those stores.

## Pattern: clean skip (caller ignores the return)

A function blocks (or does work that's moot without the IOP) and its **caller ignores the
return value** — verify in the disassembly that the call site is `jal X; nop` with no use of
`v0`, and that `X` has no other callers that depend on its side effects. Then override it to
return immediately:

```cpp
runtime.registerFunction(0xX, [](uint8_t*, R5900Context* ctx, PS2Runtime*) {
    SET_GPR_S32(ctx, 2, 0); ctx->pc = GPR_U32(ctx, 31);   // v0 = 0; jr ra
});
```

If a later step turns out to need a side effect the skipped function set up, that surfaces as
the next frontier — re-add it narrowly.

## Pattern: SIF/IOP handshake fakes

With no emulated IOP, EE-side SIF init polls hardware registers / module-handle tables that
never get written. Fake the minimum the poll checks:

- **Reg poll** — wrap the register read; for the specific reg the handshake waits on, return
  a non-zero "ready" value and pass every other register through to the real read.
- **Module-handle table** — after the entry function clears BSS, pre-populate the handle
  array (each slot → 1) so "is module N loaded?" polls exit immediately.

Identify *which* reg/offset by reading the failing `bne`/`beq` in the disassembly around the
spin.

## Pattern: answering raw-transport SIF RPCs via the runtime stub

The runtime stubs many IOP services (`runtime->iop().handleRPC(...)` — version checks,
generic acks, sound, etc.), **but only on the `sceSifCallRpc` syscall path**. A library that
hand-rolls its RPC over the *raw* SIF transport (its own send+wait function, not
`sceSifCallRpc`) bypasses that stub, so the transfer's `WaitSema` hangs.

Bridge it: hook the raw transport function(s), recognize the target service from the call
args (e.g. a server-id in `a1`), and call the runtime stub yourself — then write its result
where the caller polls for completion and return success without the DMA+wait:

```cpp
runtime.registerFunction(0xRAW_XFER, [](uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) {
    const uint32_t a0  = GPR_U32(ctx, 4);     // request/descriptor struct
    const uint32_t cmd = GPR_U32(ctx, 5);     // command (server-id | rpc-num), here
    if ((cmd & 0xFFFFFF00u) == 0xSERVER_SID) {
        uint32_t resultPtr = 0; bool sig = false;
        runtime->iop().handleRPC(runtime, cmd & 0xFFFFFF00u, cmd,
                                 /*send*/sendBuf, sendSz, /*recv*/recvBuf, recvSz, resultPtr, sig);
        const uint32_t done = resultPtr ? resultPtr : 1u;
        std::memcpy(rdram + ((a0 + COMPLETION_OFF) & 0x01FFFFFFu), &done, 4);  // clear the poll
        SET_GPR_S32(ctx, 2, 0); ctx->pc = GPR_U32(ctx, 31);                    // success; skip wait
        return;
    }
    sub_00RAW_XFER_0xRAW_XFER(rdram, ctx, runtime);   // non-target: real transport
});
```

Work out the arg layout (which register holds the command, the send/recv buffers, and where
the caller reads the reply) from the **caller's** setup in the disassembly — a call-with-recv
variant carries the recv buffer the game actually reads, so pass the *real* recv buffer, not
a placeholder. There may be more than one transport entry (a bind/simple one and a
call-with-recv one); hook each.

### Identifying the service behind an unhandled sid

When `handleRPC` returns **false**, the sid isn't one the runtime stubs — and that "missing
routine" lives in an **IOP module the game loads**, sitting right in
`<game_dir>/gamefiles/MODULES/*.IRX`. The module identifies the service and lets you confirm
it (this is a *different* "missing routine" class from a CSV gap — it's a whole IOP service,
not an EE function):

- **Name it:** `find gamefiles -iname '*.irx'`. Names map to services — `MCMAN`/`MCSERV` =
  memory card, `DBCMAN` = data loader, `LIBSD`/`AUDIOPF` = sound, `SIO2MAN` = pad/MC
  transport. The EE ELF corroborates: `strings <ELF> | grep -i 'irx\|MODULES'` shows the
  `MODULES\X.IRX` load paths and tell-tale errors like `libmc: too old release of mcserv.irx`.
- **Confirm it (definitive):** disassemble the `.IRX` (it's an IOP ELF — `funcs.py load_elf`
  reads it; R3000 ≈ base MIPS) and find where it *builds* the sid — scan for `lui rX,0x8000`
  then `ori rX,rX,0xNNNN` (the value handed to `sceSifRegisterRpc`). E.g. `MCSERV.IRX` builds
  `0x80000400` — confirming sid `0x80000400` is the memory card.

Then choose: **HLE** it (disasm the IRX's RPC dispatcher — typically `lw vX,table(at); jr vX`,
a jump table by rpc-num — and serve each rpc host-side), or **fake** it (when boot is only
*probing* the service). For a fake, override the EE-side init function to return success and
skip the RPC. Watch for **version checks** (`sltiu version, MIN` against a minimum, e.g. libmc
requires mcserv ≥ `0x20E`) — overriding the init wholesale *skips* the check entirely (it
never runs), which is usually simpler than synthesizing a version reply.

## Pattern: resumable mid-function entry

The generated code is `switch(ctx->pc) { ... goto ...; }` with **resumable entry labels at
call-return addresses**. So an override can run partway, then set `ctx->pc` to a *label
address inside* the same function (typically the return address after a `jal`) and let the
real function continue from there — useful to skip a blocking call while preserving the rest
of a function's work. Only addresses that are actual resume points work; confirm against the
generated `.cpp`.

---

## Discipline

- **Comment every hook** with the address, what it replaces, *why* it's needed, and how you
  verified the data offsets — the next reader (often you, months later) needs the reasoning,
  not just the code.
- **Validate offsets against the disassembly**, not against doc claims. Treat prior notes as
  hypotheses to re-check (`funcs.py disasm`), especially after a regen.
- **Prefer the narrowest hook.** A skip or a single faked reply beats a broad transport
  rewrite; broad hooks (e.g. on a transport with many callers) must pass non-target traffic
  through to the real function.
- **Record each landed override** in the game repo's `docs/progress.md` with its frontier.
