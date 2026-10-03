# Upstream PS2Recomp wiki — distilled reference

A faithful synthesis of the **ran-j/PS2Recomp** project wiki
(<https://github.com/ran-j/PS2Recomp/wiki>), captured, covering all six content
pages: *Game Override Hooks*, *PS2 ELF Files*, *Stripped Game Walkthrough For LLMs*,
*Ps2xAnalyzer*, *Ps2xRecomp*, *Ps2xRuntime*.

> **Status caveat (read first).** This is the **upstream project's own documentation** of
> how the toolchain is *intended* to work. Per the [operating-manual](operating-manual.md)
> verification rule, treat it as **secondary** to our own ground truth (disassembly +
> generated C++ + gdb): the wiki is terse, some pages are explicitly marked outdated/WIP,
> and the wiki's CLI/path conventions are the **upstream/Windows** ones, not our wrapper
> scripts. Where the wiki and a live run disagree, the run wins. The toolchain is split into
> three binaries — **analyzer → recompiler → runtime** — plus the **game-override** layer.
> Our engine wraps these (`scripts/01_setup.sh`, `03_build_game.sh`, `04_run_game.sh`); the
> wiki documents the binaries underneath.

---

## 1. The pipeline at a glance

```
ELF ──▶ ps2_analyzer ──▶ game.toml ──▶ ps2_recomp ──▶ generated .cpp/.h ──▶ ps2EntryRunner
        (discover fns)    (config)      (MIPS→C++)      (into ps2xRuntime)    (run)
```

Upstream CLI (ours is wrapped by `scripts/`):
- **Analyze:** `ps2_analyzer <input_elf> <output_toml>` (e.g. `ps2_analyzer SLUS_203.12 game_config.toml`)
- **Recompile:** `ps2_recomp config.toml`
- **Run:** `ps2EntryRunner game.elf`

The three-binary names on the wiki are `ps2_analyzer` / `ps2_recomp` / `ps2EntryRunner`.

---

## 2. Ps2xAnalyzer — ELF → TOML config

Converts a PS2 ELF into the TOML the recompiler consumes. *"Generates a TOML configuration
file that will later be consumed by the recompiler to produce C/C++ recompiled code."*

**Three function-discovery methods** (in order of fidelity):
1. **DWARF debug symbols** (best) — libdwarf extracts exact names + addresses from
   debug-enabled builds.
2. **Native heuristic "JAL Scanner"** (for retail/stripped) — detects function boundaries by
   scanning for common MIPS calling patterns. *"May produce incomplete or imperfect
   boundaries depending on compiler settings and anti-reversing techniques."* ← **this is the
   root of our dominant truncated/missing-function bug class.**
3. **Ghidra integration** (for complex/obfuscated) — consumes a CSV function map exported
   from Ghidra.

**Generated TOML structure (per the analyzer page):**
- `[general]` — input ELF path, optional Ghidra CSV map path.
- `stubs` — library functions needing C/C++ stub replacements.
- `skip` — **functions to ignore ("startup/initialization code")**.
- `[patches]` — individual CPU-instruction patches (syscalls, privileged COP0 ops).

**Ghidra workflow:**
1. Install the Ghidra PS2 addon **`ghidra-emotionengine-reloaded`**.
2. Run the export script `ps2xRecomp/tools/ghidra/ExportPS2Functions.py`.
3. Reference the CSV in TOML under `[general]` key `ghidra_output`.

> **Relevant to us:** our `recomp/functions.csv` *is* this Ghidra export. The analyzer/JAL
> scanner's "imperfect boundaries" caveat is exactly why we fix truncated / over-bound /
> missing-gap CSV rows (see [functions-csv.md](functions-csv.md)). Note also that **the
> analyzer can place "startup/initialization code" in `skip`** — worth auditing a game's
> config when boot-time construction looks dormant.

---

## 3. Ps2xRecomp — MIPS → C++

Transforms the TOML config into C++ source, converting R5900 MIPS instructions to operations
on an emulated CPU context.

**Full TOML schema (recompiler page):**

`[general]`
- `input` — path to source ELF.
- `output` — destination folder for generated C++.
- `ghidra_output` — optional Ghidra CSV function map.
- `single_file_output` — bool; one combined `.cpp` (true) or **one file per function**
  (false). *(Our setup uses per-function files: `tmp/generated/FUN_<addr>_0x<addr>.cpp`.)*
- `patch_syscalls` — apply syscall patches (**false recommended**).
- `patch_cop0` — apply COP0 instruction patches.
- `patch_cache` — apply CACHE instruction patches.
- `stubs` — additional function **names** forced as stubs.
- `skip` — additional **names** forced as skip wrappers.

`[patches]`
- `instructions` — **raw instruction replacements indexed by address.** ← a surgical
  per-address override of a single emitted instruction, without touching the runner source.

**Function categorization:**
- **Stubs** (`handler@addr`) — *not* recompiled; route to a named runtime handler.
- **Skip** — *not* recompiled; emit `ps2_stubs::TODO_NAMED("FunctionName")` placeholders
  (intentionally unsupported).
- **Regular** — fully recompiled to C++.

**Code emission** — each MIPS op becomes a context update, e.g. `addiu $r4,$r4,0x20`
→ `ctx->r4 = ADD32(ctx->r4, 0x20);`. *(Our generated code uses the macro forms
`SET_GPR_S32(ctx, 4, ADD32(GPR_U32(ctx,4), 0x20))` and a `switch(ctx->pc){…goto label_<addr>}`
resumable-dispatch prologue — see [working-rules.md](working-rules.md) and
[reference_cross_reference_generated_code].)*

**Post-recompile verification** — grep the generated code for unhandled-instruction comments
before trusting it:
- `// Unhandled opcode: <raw>`
- `// Unhandled FPU.S instruction: <raw>`
- `// Unhandled PMTHL instruction: <raw>`

> **Relevant to us:** `[patches].instructions` is a tool we have **not** been using and is the
> cleanest fix for a *single mis-emitted instruction* (vs. a whole-function CSV bound fix or a
> runtime override). The "unhandled opcode" grep is a cheap correctness check after any regen.

---

## 4. Ps2xRuntime — executing the recompiled code

The runtime library that runs the generated C++; emulates a minimal PS2 environment (memory,
CPU state, I/O routing, syscall dispatch). **Explicitly "the most underdeveloped part of the
project" (WIP).**

**Startup sequence:**
1. `PS2Runtime runtime; runtime.initialize("ps2xRuntime (Raylib host)")`
2. `registerAllFunctions(runtime)` — binds recompiled function addresses → C++ fn pointers
   (**must run before `loadELF`**).
3. `runtime.loadELF(elfPath)` — loads the PS2 executable.
4. `runtime.run()` — begins execution.

> **Key consequence (verified by us, consistent with the wiki):** `run()` **jumps to the
> ELF's own entry point and executes the game's recompiled crt0** — there is **no separate
> runtime step that iterates `.init_array`/`.ctors`**. So whatever C++ static init the game
> performs is whatever its own crt0 performs; the runtime adds none. A "make the runtime run
> static initializers" feature does not exist.

**Memory model:**
- **32 MB main RAM (RDRAM)** with fast-path masked access (mask = `0x01FFFFFF`; matches the
  `& 0x01FFFFFFu` we use in overrides). Host address of a guest addr = `rdram + (guest & 0x01FFFFFF)`.
- **16 KB scratchpad**.
- Special regions: EE I/O, BIOS window, GS registers, VU memory windows.
- **Hybrid access:** fast path = direct masked RDRAM read/write; slow path = routes special
  addresses (MMIO, scratchpad, BIOS) through runtime handlers.

**CPU context (`R5900Context`):**
- 128-bit GPRs — `__m128i r[32]` (the 128-bit width is why MMI/quadword ops work).
- `pc`, `hi`/`lo`, `hi1`/`lo1`, `sa` (shift amount).
- COP0 (control) registers, COP1 (FPU) registers.
- VU0 macro-mode state (VF/VI/Q/ACC).

**Function dispatch:** a function table maps **guest address → C++ function pointer** (acts
like dynamic linking for translated code); `PS2Runtime::lookupFunction(pc)` queries it. The
top-level `dispatchLoop` is flat: `pc = ctx->pc; fn = lookupFunction(pc); fn(rdram, ctx, runtime);`
— so **every cross-unit return-to-loop dispatch passes through `lookupFunction`** (the hook
point for dispatch-tracing; see [debugging.md](debugging.md) §4).

**Syscalls / stubs:** central `handleSyscall` for numeric syscalls; stub hooks for skipped
functions via generated wrappers. Per-game customization files (upstream paths):
`ps2xRuntime/include/ps2_call_list.h`, `ps2xRuntime/src/lib/ps2_syscalls.cpp`. Both `stubs`
and `skip` generate code calling runtime stub handlers — needing safe default returns or
minimal emulation.

**Platform / SIMD:** uses SSE intrinsics (`__m128i`) — MSVC `<intrin.h>`; Clang/GCC x86
`<immintrin.h>`/`<smmintrin.h>`; ARM needs `USE_SSE2NEON` + `sse2neon.h`.

**Current limitations (wiki):** many syscalls/I-O behaviors incomplete; complex DMA/VIF/GS
behaviors missing; per-title fixes (stubs, patches, MMIO) frequently required. Debugging
relies on breakpoints in `PS2Runtime::lookupFunction`, `ps2_stubs::TODO_NAMED` handlers, and
`ps2_syscalls::TODO` paths.

> **Note on what the wiki does *not* say:** it doesn't document the `recover-pc` /
> `first-bad-pc` machinery our runtime prints on a data-as-code jump, nor the cooperative
> yield/resumable-entry dispatch — those are facts we've established from runs, not the wiki.

---

## 5. Game Override Hooks — per-game patches without regen

The mechanism for per-game behavior that stays **scoped and buildable without regenerating
the runner files**. Header: `ps2xRuntime/include/game_overrides.h`.

**Registration macro:**
```cpp
PS2_REGISTER_GAME_OVERRIDE(name, elfName, entry, crc32, applyFn)
```
- `name` — descriptive label.
- `elfName` — ELF **basename**, matched **case-insensitively**.
- `entry` — optional entry point (`0` disables this match criterion).
- `crc32` — optional ELF CRC32 (`0` disables this criterion).
- `applyFn` — the apply callback.

All three match criteria are optional; matching happens **during `loadELF`, before normal
function registration**.

**Apply-callback signature:**
```cpp
void applyFunctionName(PS2Runtime &runtime) { ... }
```

**Per-address hook lambda** (registered inside the apply fn):
```cpp
runtime.registerFunction(0xADDRESSu,
    [](uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) { ... });
```

**Two binding approaches:**
1. **Direct handler binding** — `ps2_game_overrides::bindAddressHandler(runtime, address, "handler")`
   routes a stripped address to an existing named runtime handler.
2. **Custom registration** — `runtime.registerFunction(0xADDRESSu, lambda)` for fully custom
   logic.

**Context access:** the lambda gets `R5900Context *ctx` (registers + `ctx->pc`) and helpers
like `setReturnS32(ctx, 0)` / `getRegU32(ctx, n)`. *(Our overrides use the macro family
`GPR_U32(ctx,n)` / `SET_GPR_S32(ctx,n,v)` / `ctx->pc`; see [overrides.md](overrides.md).)*

**Critical pitfall — infinite re-dispatch:** many stubs/handlers do **not** advance `ctx->pc`.
If a handler doesn't move PC, the dispatch loop re-enters it forever. Add the fallback:
```cpp
if (ctx->pc == entryPc) {
    ctx->pc = getRegU32(ctx, 31);   // return to $ra
}
```

> **How this maps to our setup:** we register overrides in the **game repo's
> `src/register_overrides.cpp`** (`runtime.registerFunction(...)`), which is the same
> mechanism. **`RecompiledFunction` is a plain function pointer, so our override lambdas must
> be non-capturing** (a fact from our work, not the wiki). The "advance PC or re-dispatch
> forever" rule is the same hazard we manage with `ctx->pc = GPR_U32(ctx,31)` / `ctx->pc = 0`.

---

## 6. PS2 ELF Files — format notes

*"ELF = Executable and Linkable Format."* PS2 game executables are ELF even without a `.elf`
extension. Magic bytes: `0x7F 45 4C 46` (`\x7FELF`).

**Region naming** (ISO filesystem root): `SLUS_XXXXX` (NA), `SLES_XXXXX` (EU), `SLPS_XXXXX` (JP).
*(Our example game: `SLES_520.17`, PAL/EU.)*

**Common sections** (a retail build is *"often stripped"* of many):

| Section | Contents |
|---|---|
| `.text` | executable code/functions |
| `.data` | initialized globals/statics |
| `.bss` | uninitialized globals/statics |
| `.rodata` | read-only constants + strings |
| `.sdata` / `.sbss` | small-data variants (MIPS-specific, gp-relative) |
| `.ctors` / `.dtors` | C++ constructor/destructor metadata |
| `.eh_frame` | exception/unwind info (DWARF2) |
| `.jcr` | Java-related |
| `.mdebug` | extended debug/symbol info |

> **Relevant to us:** the `.sdata`/`.sbss` note explains **gp-relative** addressing of small
> globals (we found the frontend class table at `gp+0x2f0`, missed by an absolute-constant
> search). `.ctors`/`.dtors` is the standard C++ static-init metadata — but our game's
> stripped ELF exposes no section names (only `PT_LOAD` segments), and the frontend
> construction we care about is **runtime engine logic, not crt `.ctors`** (verified).

---

## 7. Stripped-game walkthrough — the LLM playbook

Upstream's own guidance for working a stripped retail game (closely mirrors our
[operating-manual](operating-manual.md)):

**Core principle:** runtime dispatch is **address-based** — the identity that matters is
`0xADDR → function pointer`, *not* the symbolic `sub_xxx` name. **Renaming generated functions
or CSV rows does nothing.** *(We rely on this constantly: the CSV name column is ignored.)*

**Build/deploy cycle:** analyze → (improve boundaries with Ghidra CSV) → recompile → move
generated `.cpp` into `ps2xRuntime/src/runner/` and headers into `ps2xRuntime/include/` →
**replace the `register_functions.cpp` placeholder** → build → run.

> Our `scripts/03_build_game.sh` automates the move/install + `register_functions.cpp`
> population. The wiki's manual-copy steps are what the script does for us.

**Address binding in TOML** (`stubs` list):
```toml
stubs = [
  "sceCdRead@0x00123456",
  "ret0@0x001D9410",
  "ret1@0x001D5BC8",
  "reta0@0x0024B7C0"
]
```
**Triage handlers** to probe a caller's contract before implementing real behavior:
`ret0` (return 0), `ret1` (return 1), `reta0` (return `$a0`).

**Common blockers (table):**

| Blocker | Log signature | Action |
|---|---|---|
| Function not found | `Warning: Function at address 0x… not found` | verify `registerAllFunctions` compiled + called before `loadELF`; add the address mapping |
| Unimplemented stub | `Warning: Unimplemented PS2 stub called. name=…` | bind a handler or use a triage return |
| Unknown syscall | `[Syscall TODO]…` | implement in `ps2_syscalls.cpp` |
| PC hang | `CPU … PC not updating` | check loop exits / handler bindings; breakpoint the dispatch |

**Diagnostic command:** find unresolved raw dispatches —
`rg -n "lookupFunction\(0x" ps2xRuntime/src/runner`.

**Static analysis of unnamed `sub_xxx`** — infer behavior from callsites + `$a0..$a3` setup,
return-value checks (`==0`, `<0`, pointer use), immediate constants + nearby strings, and side
effects (buffer writes, DMA, RPC).

**Safety pitfalls:** forgetting to replace the `register_functions.cpp` placeholder; copying
an address map from a **different ELF build** (addresses are build-specific); raw handler
binding without the PC fallback (→ infinite re-dispatch); leaving too many `ret0`/`ret1`
triage stubs (→ hard-to-debug late failures).

**Three-phase progress (Boot → Menu):**
1. **Boot stability** — registration works, entry dispatch succeeds, critical syscalls resolved.
2. **I/O & modules** — CD/file/SIF paths functional (`sceCd*`, `fio*`, `Sif*`).
3. **Menu reach** — frame loop reaches menu logic without temporary stubs.

---

## 8. Items most actionable for our current work

Distilled "what to actually do with this," cross-referenced to our methodology:

1. **`[patches].instructions` (recompiler) is an unused surgical tool.** For a single
   mis-emitted instruction (a candidate cause of the kind of `ra`-corruption / data-as-code
   fault we chase), a per-address instruction patch in the TOML is cleaner than a CSV bound
   fix or a runtime override. Worth keeping in the toolbox alongside truncated/over-bound
   fixes ([functions-csv.md](functions-csv.md)).
2. **Audit `skip` in a game's config when boot-time construction looks dormant.** The
   analyzer deliberately puts "startup/initialization code" into `skip` → those become
   `ps2_stubs::TODO_NAMED` no-ops. If a construction/init function we expect to run is in
   `skip`, that's a one-line config fix + regen.
3. **No runtime static-init step exists** — `run()` executes the game's own crt0 only.
   Confirms a "runtime iterates `.init_array`" fix is not available, and that absent
   construction is either a missing/skip'd function or runtime-gated game logic, not a
   runtime gap.
4. **`gp`-relative / small-data addressing is real** (`.sdata`/`.sbss`) — searches for a
   global must consider `gp+offset`, not just the absolute constant. (This is what corrected
   our "class table is never referenced" miss.)
5. **The "unhandled opcode/FPU/PMTHL" grep** of generated code is a cheap correctness gate
   after every regen.
6. **Override discipline** (matches ours): non-capturing lambdas, always advance `ctx->pc`
   (`= GPR_U32(ctx,31)` to return, or `= 0` to stop), addresses are build-specific.

---

## 9. Source pages

- [Game Override Hooks](https://github.com/ran-j/PS2Recomp/wiki/Game-Override-Hooks)
- [PS2 ELF Files](https://github.com/ran-j/PS2Recomp/wiki/PS2-ELF-Files)
- [PS2Recomp Stripped Game Walkthrough For LLMs](https://github.com/ran-j/PS2Recomp/wiki/PS2Recomp-Stripped-Game-Walkthrough-For-LLMs)
- [Ps2xAnalyzer](https://github.com/ran-j/PS2Recomp/wiki/Ps2xAnalyzer)
- [Ps2xRecomp](https://github.com/ran-j/PS2Recomp/wiki/Ps2xRecomp)
- [Ps2xRuntime](https://github.com/ran-j/PS2Recomp/wiki/Ps2xRuntime)
- [Emotion Engine (EE)](https://github.com/ran-j/PS2Recomp/wiki/Emotion-Engine-(EE)-(outdated)) — *marked outdated; not ingested.*
