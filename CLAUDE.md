# PS2Recomp engine — project guide

A **stateless engine** for statically recompiling PS2 games. It recompiles an EE MIPS
R5900 ELF into C++ (`ps2_recomp`) and runs it on `ps2xRuntime` (which emulates the EE
kernel, GS, SIF/IOP, threading). It contains **no game** — you point it at a separate
**per-game repo**, build, and run.

Static recompilation = **one active game per build**. "Point at a game" means: select it,
regen, build, run. Switching games is a rebuild.

**Current phase (the example game, LOTR):** get the game running far enough to reach its
first disc/file read, fixing crashes/stalls one frontier at a time. Readability later.

## Per-game repo (the unit of work — separate repo, NOT here)

```
<game-repo>/
  <ELF>            # the PS2 executable; its path is declared in recomp/config.toml `input`
  recomp/          # PROVIDED inputs: config.toml + functions.csv
  src/             # OUR work: register_overrides.cpp (+ .h)
  docs/            # game-specific human docs (elf.md = static facts, progress.md = journal)
  tmp/             # per-game scratch: generated/ (ps2_recomp output), logs
  gamefiles/       # disc/CD data (for the eventual file/disk reads)
```

`config.toml`, `functions.csv`, the ELF, and `gamefiles/` are **provided per game** — never
generate them. Committed in the game repo: `recomp/` + `src/` + `docs/`. Ignored: the ELF
(copyright), `gamefiles/`, `tmp/`. The example game lives at
`~/Documents/projects/decompilations/lotr_decomp`.

## Hard constraints (do not violate)

- **Never modify PS2Recomp's tracked source** under `tools/PS2Recomp/` — it's a separate
  git repo. In particular `tools/PS2Recomp/ps2xRuntime/src/runner/` is **wiped and
  regenerated on every build**; never hand-edit it.
- **All game-specific behavior goes through override hooks** in the game repo's
  `src/register_overrides.cpp` only — `PS2_REGISTER_GAME_OVERRIDE(...)` +
  `runtime.registerFunction(addr, lambda)`. Never patch generated runner files directly.
- **Builds run in the background** and can take many minutes. Launch
  `scripts/03_build_game.sh <game_dir>` with a 600000 ms tool timeout and poll the log for
  `Build complete`. Do not block on it in the foreground.
- **Always run the game under `timeout`.** `04_run_game.sh` writes the runner's output to
  the game's own `tmp/run.txt` (a spinning build emits hundreds of MB/s — it goes to that
  file, never the terminal). Read `<game_dir>/tmp/run.txt` after; never pipe a raw run into
  `head`/`tail`.
- **Temp/scratch files go in the engine `tmp/`** (gitignored). Clean them up.
- **No `git commit` / destructive git** unless explicitly asked.
- No "auto mode" — only take actions the user has asked for.

## Engine layout

- `scripts/01_setup.sh` — clone + build `ps2_recomp` (gcc-13, SSE4.1).
- `scripts/03_build_game.sh <game_dir> [flags]` — regen → install → cmake build.
- `scripts/04_run_game.sh <game_dir>` — run the last-built runner (ELF read from config).
- `tools/PS2Recomp/` — the toolchain + runtime (separate repo, read-only to us).

Everything per-game (ELF, config, functions.csv, overrides, generated output) is derived
from `<game_dir>` and its `recomp/config.toml`; nothing about a game is hardcoded here.

## Build / run

```
scripts/03_build_game.sh <game_dir>                              # full: regen → install → build
scripts/03_build_game.sh <game_dir> --skip-regen --changed-recomp # fast: override-only changes
timeout 20 scripts/04_run_game.sh <game_dir>                       # run → log at <game_dir>/tmp/run.txt
```

- `--skip-regen` reuses the game's `tmp/generated/` (skips ps2_recomp). **Any
  `functions.csv` change needs a FULL regen** — do not use `--skip-regen` after editing it.
- `--changed-recomp` installs only changed files so cmake recompiles fewer unity files.
- ps2_recomp runs with the game dir as CWD, so the config's relative paths resolve.
- The built runner is **moved** to `<game_dir>/tmp/ps2EntryRunner` (per-game, no cross-game
  clash); `04` runs that binary and writes the log to `<game_dir>/tmp/run.txt`.

## The dominant bug class: truncated / missing functions

The Ghidra-exported `functions.csv` often has most functions sized to only their first
instruction (4-8 bytes). ps2_recomp then emits a one-instruction body that runs the prologue
(`addiu sp,sp,-N`), **leaks stack, and never restores `$ra`** — the corrupted return address
eventually jumps into data (a data-as-code spin).

Two flavors and their fixes (both require a full regen build), applied in the game repo's
`recomp/functions.csv`:
- **Truncated** (in CSV, prologue present, size ≤ 12): correct the `END`/`SIZE`.
- **Missing** (called but absent; runtime logs `Function at address 0x.. not found` and
  recovers via `recover-pc`, dropping delay-slot side effects and tail-call `j` targets):
  append a new CSV line.

Boundary convention: `END` = next function's start (covers trailing-nop padding); a
`jr ra`/`j`-first stub is `START + 8`. Dispatch is **address-based** — renaming a CSV
function does nothing (ps2_recomp ignores the name column).

**Fix incrementally** — fix the functions on the current execution frontier, rebuild, advance.

## Doing the work

Use the **`ps2recomp-fix-next-crash`** skill for the diagnostic loop + the helper
`.claude/skills/ps2recomp-fix-next-crash/funcs.py` (`disasm` / `bounds` / `scan` / `find`),
which takes `--game <game_dir>` (or `$PS2RECOMP_GAME`). For a misbehaving guest address, add
a one-shot probe in the game's `src/register_overrides.cpp` (you get `rdram`, `ctx`, register
macros), dump regs/stack, then `ctx->pc = 0` to stop cleanly instead of spinning. Record each
frontier/override in the game repo's `docs/progress.md`.
