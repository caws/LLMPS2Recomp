---
name: ps2recomp-fix-next-crash
description: Diagnose and fix the next crash/stall in a PS2Recomp game decompilation. Use when a game spins, exits early, or logs "Function at address ... not found" / data-as-code jumps. Covers the build-run-fix loop, the truncated-function root cause, and override-based diagnostics. Works on any game repo via the engine.
---

# Fix the next PS2Recomp decomp crash/stall

This engine recompiles a PS2 game (EE MIPS R5900 ELF → C++ on `ps2xRuntime`). Each
game is a separate **game repo**; you push its execution forward (ultimately to the
first disc/file read) by fixing one frontier issue per cycle. **Fix incrementally** —
do not mass-rewrite `functions.csv`.

Everything below operates on a `<game_dir>` (e.g. the LOTR game at
`~/Documents/projects/decompilations/lotr_decomp`). The engine scripts take the game
dir; the game repo also has `scripts/build.sh` / `scripts/run.sh` wrappers.

## Hard rules (do not violate)

- **Never modify PS2Recomp's tracked source** under `tools/PS2Recomp/` (separate git
  repo). The `tools/PS2Recomp/ps2xRuntime/src/runner/` dir is wiped and regenerated
  every build — never hand-edit it. All game-specific behavior goes in the game repo's
  `src/register_overrides.cpp` via `PS2_REGISTER_GAME_OVERRIDE` +
  `runtime.registerFunction(addr, lambda)`.
- **Build in the background** with a 600000 ms tool timeout (it can take many minutes);
  poll the build log for `Build complete`.
- **Always run with a `timeout`** — a spinning game emits hundreds of MB/s. The runner
  writes to `<game_dir>/tmp/run.txt`; never pipe a raw run to `head`/`tail`.
- **Write temp files to a `tmp/`** (gitignored) and clean them up afterward.
- Don't `git commit` or do destructive git ops unless explicitly asked.
- Record each frontier/override you resolve in the game repo's `docs/progress.md`.

## Build / run

From the engine (pass the game dir):

```
scripts/03_build_game.sh <game_dir>                              # full: regen → install → build
scripts/03_build_game.sh <game_dir> --skip-regen --changed-recomp # fast: override-only changes
timeout 20 scripts/04_run_game.sh <game_dir>                      # run → <game_dir>/tmp/run.txt
```

Flags: no flags = full pipeline (ps2_recomp regen → install → cmake build).
`--skip-regen` reuses the game's `tmp/generated/` — use ONLY for override changes; **any
change to `recomp/functions.csv` requires a FULL regen**. `--changed-recomp` = smart
install (fewer unity recompiles); pair with `--skip-regen` for fast override cycles.
`--skip-build` = regen+install only. The build guards against ps2_recomp's silent-failure
bug (empty `ps2_recompiled_functions.h` aborts).

## The recurring root cause: truncated functions

A Ghidra-exported `recomp/functions.csv` often declares most functions at only their
first instruction (size 4-8). ps2_recomp then emits a one-instruction body that runs the
prologue (`addiu sp,sp,-N`), **leaks stack, and never restores `$ra`**. The drifting
stack later makes some `lw ra,off(sp); jr ra` load garbage and jump into data (a
data-as-code spin). Two flavors:

1. **Truncated** — in the CSV but size too small (prologue present, size ≤ 12). Fix:
   correct its `end`/`size`.
2. **Missing** — called (`jal`/`jalr`) but absent from the CSV; runtime logs
   `Function at address 0x.. not found` and recovers via `recover-pc` (returns to `ra`,
   **dropping delay-slot side effects and tail-call `j` targets**). Fix: add the function.

Boundary convention (matches correct entries): `end` = next function's start (includes
trailing-nop padding). For a `jr ra`/`j`-first stub, `end = start + 8`. The helper prints
both the control-flow end and the next-start cap. Dispatch is **address-based** — renaming
a CSV function does nothing.

## Workflow

1. **Build** (full, if CSV changed) and wait for `Build complete`.
2. **Run**: `timeout 20 scripts/04_run_game.sh <game_dir>` → read `<game_dir>/tmp/run.txt`.
3. **Triage** the run log:
   - file size: a huge file ⇒ a spin (something dispatched in a tight loop).
   - `grep "Function at address" <game_dir>/tmp/run.txt | grep -oE "0x[0-9a-f]+" | sort -u`
     ⇒ missing functions on the frontier.
   - `[dispatch:first-bad-pc]` / `[dispatch:pc-zero]` lines carry a `trace=` of the guest
     call chain — read right-to-left from the failure point.
   - `[dispatch:recover-pc]` is non-fatal but means a call was skipped.
4. **Locate** with the helper (set `$F` to the engine's
   `.claude/skills/ps2recomp-fix-next-crash/funcs.py`; pass `--game <game_dir>` or export
   `PS2RECOMP_GAME=<game_dir>`):
   - `python3 $F find   0xADDR --game <game_dir>` — in CSV?
   - `python3 $F disasm 0xADDR [n] --game <game_dir>` — disassemble
   - `python3 $F bounds 0xADDR --game <game_dir>` — propose start/end/size
   - `python3 $F scan --game <game_dir>` — list all truncated suspects
5. **Fix** the game's `recomp/functions.csv`: correct a size, or append a new
   `FUN_00XXXXXX,0x00XXXXXX,0x00YYYYYY,SIZE` line. Check the range doesn't overlap an
   existing entry's start.
6. **Full rebuild**, run again, confirm the frontier advanced (new log activity, the old
   warning gone). Update `docs/progress.md`. Repeat.

## Override-based diagnostics (when a guest addr misbehaves)

Register a one-shot probe in the game's `src/register_overrides.cpp` — you have `rdram`,
`ctx`, and the register macros (`GPR_U32(ctx, n)`, `SET_GPR_*`, `ctx->pc/hi/lo`). Dump all
32 GPRs + `hi/lo`, walk the stack from `sp`, scan guest RAM for a suspicious pointer, then
`ctx->pc = 0` to stop cleanly (avoids the spin). Rebuild with `--skip-regen
--changed-recomp` for a fast cycle. Remove or gate the probe once done.

Leaving a tripwire override on a known-bad address (log + `ctx->pc=0`) is a cheap way to
catch regressions where control flow reaches data again.

## Profiling a silent spin (last resort)

If the EE is busy but quiet, the hot thread is the guest thread (`GameThread`). It runs
guest code via `PS2Runtime::run()`'s dispatch lambda; the *guest* call stack lives in
guest RAM (`sp`), not the host stack. Break on `PS2Runtime::lookupFunction` in gdb and
histogram the guest-address argument to see what's dispatched in the loop. gdb-driving is
slow — prefer the override-dump approach above.
