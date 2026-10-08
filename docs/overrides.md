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

## Where new code goes: control plane + domain modules

An override file grows without bound (LOTR's hit **7,500 lines / 190 hooks** before we split it).
The shape that scales, and the one the build now supports:

- **`src/register_overrides.cpp` = the CONTROL PLANE.** It holds the one
  `PS2_REGISTER_GAME_OVERRIDE` descriptor and **every** `registerFunction` call, in one ordered
  list. That list *is* the semantics of the override set (see below), so it must be readable
  top-to-bottom in one file.
- **`src/<domain>/<domain>.{cpp,h}` = the hook BODIES**, as named functions:

```cpp
// src/register_overrides.cpp — the ordered list; the guard stays here, with the registration
if (std::getenv("LOTR_PROBE")) runtime.registerFunction(0x1F6C00u, lotr::pad_input::hook_1f6c00);

// src/pad_input/pad_input.cpp — the body
void hook_1f6c00(uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) { ... }
```

### ★ Never relocate a registration line

`registerFunction` → `replaceFunction` does `table[slot] = fn`: one slot per address, **last
registration wins**. Addresses are often registered several times, and many registrations sit
inside a conditional. So a registration's **position and guard are semantics, not formatting**:

- Moving one — or hoisting a group into a per-module `installX(runtime)` helper — can put it under
  a *different* guard, or change who wins the slot. The hook then silently stops running. (This
  cost us a build: a pad-hook group landed inside another domain's `if`, and the only symptom was
  one log line disappearing.) **Move the body; leave the registration where it is.**
- A `#define` must stay in the same TU as its `#if`. If it drifts into a module, the `#if` left
  behind quietly becomes `0`, its registration vanishes, and a *different* hook wins the address —
  invisible to any check that ignores the preprocessor. **Prefer env vars; they cannot drift.**
- A registration is **dead** if a later one for the same address is unconditional — it can never
  win. Dead registrations accumulate and hide broken debug switches (a probe that "does nothing"
  when you enable it is usually this).
- The **one exception** is promoting a hook into the `mods/` block at the end of the list, which is
  a relocation *by design* — and only with the equivalence proof spelled out under
  "`src/` is the base game" below.

After **any** control-plane change:
`.claude/skills/ps2recomp-fix-next-crash/check_registrations.py --game <game_dir>` — asserts same
address / same order / **same guard depth** / same body vs a git ref, and audits every `#if`.
`--dead` lists the registrations that can never win.

### Which module? (and when a new one is warranted)

Default: **the existing domain that owns that guest subsystem** — a loader hook goes in `loader/`,
a glyph fix in `font_text/`. Create a **new domain** only when it clears all three:

1. it is a **distinct guest subsystem** (not another hook on an existing one);
2. it **owns state** — globals its hooks share and nobody else touches;
3. it has, or will plainly have, **more than a hook or two**.

A one-off probe does not earn a folder — it goes in the domain it probes, env-gated, default OFF,
and gets deleted when the frontier moves on. Two failure modes to watch:

- **Sprawl** — when a domain stops having one story (LOTR's `menu_flow`: 69 hooks), split it along
  a real seam. Safe: moving a *body* between modules changes nothing.
- **Accretion into a mega-hook** — a per-frame/vsync hook becomes the dumping ground for every
  domain's periodic work (LOTR's `0x144B70`: **1,172 lines**). When new work starts landing there,
  give it its own module and split it by what the pieces actually do.

### `src/` is the base game; `mods/` is what changes it

A second tree sits beside `src/`: **`mods/<mod>/`**, one subfolder per mod. The line between them is
not technical — the build collects both the same way — it is the line that keeps *"is this what the
disc did?"* answerable:

- **`src/`** = the **faithful base game**: hooks that make the original run as a PS2 ran it (CSV
  fixes' companions, HLE for hardware we don't emulate, fidelity gaps like rumble).
- **`mods/`** = anything that **changes** the original: widescreen, HUD layout, framerate, HD
  texture packs, network play.

The ordered list does not become two lists — it gets **one tail**. `applyLOTROverrides` ends with a
single unconditional statement:

```cpp
// src/register_overrides.cpp — the LAST line of applyLOTROverrides, no guard, nothing after it
lotr::mods::registerMods(runtime);

// mods/register_mods.cpp — the tail: every mod registers here, in install order
void registerMods(PS2Runtime& runtime) {
    runtime.registerFunction(0x145960u, lotr::mods::widescreen::hook_145960);
}
```

- Dispatch is last-wins, so everything in the tail lands **after** every base registration and takes
  its slot **without a line changing inside `src/`**. Adding a mod touches `mods/register_mods.cpp`
  and the new mod's folder — nothing else, so contributors adding mods never collide on the control
  plane.
- **Append, never insert** in the tail, or mods start silently overriding each other.
- **Nothing may follow that call, and it must never acquire a guard** — a conditional there would
  gate every mod at once, invisibly.
- `check_registrations.py` **splices the tail in at the call site** and audits one merged list
  (`--list` marks each row `base` or `mods`). If the call is present but the tail file is not
  readable it says so loudly rather than quietly auditing less: a registration the tool cannot see is
  the exact failure this checker exists to catch.
- **Env-gated, default OFF = original behaviour**, with the gate in the hook **body** so the
  registration stays unconditional (a registration behind `if (getenv(...))` is how a "default ON"
  flag twice became OFF — see the `BUILD_TAG`/flag discipline in [working-rules](working-rules.md)).
- A mod still has to leave the game running: **call the original** unless replacing it outright.

**Moving an existing hook into `mods/` is the one sanctioned relocation** of a registration line —
and it is sanctioned only with proof. Before the move: the address must be registered **nowhere
else** (so a later position cannot change who wins), and the body must move verbatim. After it:
`check_registrations.py --list` before/after must differ by **exactly** that relocation, at the same
guard depth, with the **same body hash**, and `--dead` must be unchanged. Anything less is the
ordinary never-relocate rule.

Note what that buys once the tail exists: moving the registration from the end of
`applyLOTROverrides` into `registerMods()` is a **no-op for the merged list**, so the checker's own
diff passes unchanged (`✓ same order, same guards, same bodies`) — the refactor is verified by the
tool rather than argued for.

### Mechanics

- Sources are installed into the runner **flattened by basename** (its CMake glob is non-recursive,
  headers are included by bare filename), so **basenames must be globally unique** across `src/`
  *and* `mods/` — `scripts/03_build_game.sh` hard-errors on a collision, including with a generated
  file. `mods/` is optional: a game repo without one builds unchanged.
- Modules are excluded from the runner's unity build (engine patch 08), so each is its own TU:
  adding one recompiles one file instead of reshuffling every unity batch.
- Namespace each domain (`namespace lotr::<domain>`); the control plane `using namespace`s them.

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

## Pitfalls (each one cost a bad build or worse)

- **A hook only sees callers that go through dispatch.** `jal`/`jalr`, returns and thread/interrupt
  entries look the address up (`dispatchGuestBranch` → the function table), so a hook fires. A
  **tail jump** (`j <fn>` ending another function) and a **thunk** (a lone `j <fn>` at its own
  address) are emitted as a *direct C++ call* to the target's generated body — the hook never sees
  them (LOTR: ~400 tail jumps + 66 thunks; the thunk 0x1B5BE0 → 0x1B5810 bypassed a mod and froze a
  level). A hook calling a generated body directly is equally invisible to other hooks.
  Census every function you hook:
  `grep -l "<generated symbol>(rdram, ctx, runtime)" <game_dir>/tmp/generated/*.cpp` — every file but
  its own is a path the hook misses; hook that caller too (the thunk's address) or note the gap.
- **Chain to an existing hook, not to the generated body.** Last registration wins; if the address
  already has a hook, a later one that calls `FUN_…` directly silently drops the earlier fix. Call
  that hook's `hook_…` function instead (`check_registrations.py --list` shows who registers it).
- **Read the args before calling the original.** The generated body clobbers a0..a3 (and v0/v1,
  ra); anything the wrapper needs afterwards must be saved first.
- **The caller resumes at `jal` + 8**, not + 4 (the delay slot already ran). A hook that returns by
  hand sets `ctx->pc = ra`; a guard that matches a caller's "return address" must use + 8.
- **Calling another guest function from a hook must pump it to completion.** The generated code can
  return to the host mid-chain at a scheduler checkpoint, so one call runs only part of it. Save the
  registers, set `ra = 0`, call, then `while (ctx->pc != 0) runtime->lookupFunction(ctx->pc)(…)` inside
  a host-pump scope (`ps2xBeginHostPump()`/`ps2xEndHostPump()`), and restore. LOTR's
  `mods/fourplayer` `callGuest()` is a complete implementation.
- **⚠ …but a host pump cannot drive guest code that WAITS.** A blocking syscall/stub (`sceGsSyncV` →
  `EeScheduler::waitVSync`, any `[[noreturn]]` `blockCurrent`) unwinds the host stack to the scheduler and later
  resumes the thread at its return address — the pump's C++ frame and its sentinel `ra = 0` are gone, and the
  thread runs off into pc 0 or hangs (LOTR: a render replay hung inside the flip). Drive such a
  sequence as GUEST control flow instead: enter each step with `ra = K`, where K is an unused address inside the
  dense function table (e.g. the padding nop after a function's `jr ra`) whose registered hook starts the next
  step; the last step restores the caller's `ra`. Waits then behave exactly as in the original. LOTR's
  `mods/interp` is a complete implementation.
- **A hook that REPLACES a function and makes its call (`ra = <jal + 8>`, `lookupFunction(callee)`) must
  check that the call finished: `ctx->pc == <jal + 8>`.** Even a trivial callee can be abandoned at a checkpoint
  if it makes any guest call (LOTR's vsync getter calls DI/EI, which handlers can interrupt; an emulated IOP
  raises EE interrupts inside the cycle accounting, so under IOP LLE this happens during loads). The abandoned
  callee's frame is still on the stack. A hook that carries on and sets `ctx->pc = ra` returns to its caller
  with `sp` too low, the caller's epilogue loads its saved registers from the callee's frame, and the
  `jr ra` lands on a saved s-register (LOTR: `missing branch target 0x3`, `s1` = the callee's saved `ra`). The fix
  is to build the original's own frame (`addiu sp` / `sd ra`), return with `ctx->pc` untouched if it is not
  `<jal + 8>`, register the same hook at `<jal + 8>` (registering the entry does not cover the function's resume
  slots), and do the rest plus the original epilogue there. Prove it with a test lever that leaves the call in
  flight on demand. The real abandonment is rare. LOTR's `mods/framerate` `hook_154110_framerate` is the example.
- **An env var derived with `setenv()` during registration is invisible to a namespace-scope
  `static` reader** — it initialised before. Read flags in function-local statics, and let the
  settings layer (which applies the file and re-executes before start-up) carry player options.
- **A missing branch target ends the run** (fork row 241: `[runtime:fatal]`, exit 3;
  `PS2X_MISSING_TARGET=continue` = the old log-and-continue, debugging only). It is either a
  `functions.csv` gap (the target is real code: add the row) or corrupt guest state (it is not: find
  the writer). Before row 241 the default resumed the caller, i.e. silently skipped the call.

---

- **An IOP module may not speak SIF RPC at all.** rotk USA's DirtySock (DRTYSCKF.IRX) talks to the EE through
  SIF *command* 7 + DMA rings + a FourCC member table, behind ONE EE gateway function; our runtime's
  `sceSifSendCmd` answers nothing, so the module looks dead with no "unhandled sid" line anywhere. Before writing
  an RPC handler, check the IRX's imports (`sifcmd` vs `sifrpc`) and grep the EE client for BindRpc/CallRpc.
  Replacing such a module at its EE gateway (a hook per member, run synchronously) was far less work than
  emulating the mailbox (rotk progress.md usa-7).
- **The SDK patches the kernel at boot, and some patches are code we cannot run.** ps2sdk's LoadExecPS2 support
  copies kernel code to 0x80075000 and points syscalls 0x5A/0x5B/0x54-0x59 at it. A syscall override whose
  handler is not recompiled code must fall back to the runtime's built-in syscall (fork row 251), or every one of
  those calls fails silently for the whole session (rotk: GetEntryAddress = -1, the event-flag calls broken).
- **PS2 file-open access mode is a 2-bit VALUE** (1 read, 2 write, 3 both), not two flag bits: `flags & RDWR`
  turns every read-only open into a write and fails on read-only disc copies (fork row 250; PCSX2 `IopBios.cpp`).

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
