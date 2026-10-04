# PS2Recomp engine — project guide

> **▶ BINDING FIRST STEP — before ANY work, read [`docs/operating-manual.md`](docs/operating-manual.md)
> and [`docs/working-rules.md`](docs/working-rules.md), then [`docs/README.md`](docs/README.md) for the
> rest. Do not act until you have; they are binding doctrine, not background reading.** (A `SessionStart`
> hook in `.claude/settings.json` also injects this directive every session.)

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
  src/             # OUR work: register_overrides.cpp (the control plane: the one
                   #   PS2_REGISTER_GAME_OVERRIDE + every registerFunction call, in order)
                   #   + src/<domain>/ modules holding the hook BODIES (docs/overrides.md)
  docs/            # game-specific human docs (elf.md = static facts, progress.md = journal)
  tmp/             # per-game scratch: generated/ (ps2_recomp output), logs
  gamefiles/       # disc/CD data (for the eventual file/disk reads)
```

`config.toml`, `functions.csv`, the ELF, and `gamefiles/` are **provided per game** — never
generate them. Committed in the game repo: `recomp/` + `src/` + `docs/`. Ignored: the ELF
(copyright), `gamefiles/`, `tmp/`. The example game lives at
`~/Documents/projects/decompilations/rotk_recomp`.

## Hard constraints (do not violate)

- **ALWAYS run `scripts/…` from THIS engine repo, pointed AT the game dir — never from the
  game repo.** `scripts/` exists ONLY here (`LLMPS2Recomp/`); the game repo (`rotk_recomp/`,
  etc.) has **no `scripts/`**. Invoke as `scripts/03_build_game.sh <ABSOLUTE-game-dir> …` from
  the default cwd (this engine dir). **Never `cd <game_dir>` first** and never run the scripts
  while cwd is the game repo — `cd <game_dir> && scripts/03_build_game.sh …` fails with
  `No such file or directory`. The Bash tool cwd resets to this engine dir between calls, so a
  plain `scripts/…` from a fresh call is correct; pass **absolute** `<game_dir>` + log paths.
  (Full detail under "Build / run" below.)
- **`tools/<game>/PS2Recomp/` is a clone of OUR PRIVATE FORK of PS2Recomp**
  (`https://github.com/caws/PS2Recomp.git`, branch **`lotr`**; upstream `ran-j/PS2Recomp` is the
  `upstream` remote, merged in periodically with `git merge upstream/main`). Runtime/recompiler
  changes are made **in that clone and COMMITTED there** — one focused commit per change (★ **never push** — the user pushes, or approves each push explicitly),
  PCSX2 citation in the message, and a row in the fork's `docs/llmps2recomp-patches.md` (the
  former `patches/README.md`). The `patches/*.patch` stack was **retired (cont.230)**;
  never reintroduce it. The game pins the toolchain commit it was verified against in
  `<game_dir>/recomp/runtime.lock` (`01_setup.sh` warns on a mismatch) — update the lock in the
  same change that moves the branch. `tools/<game>/PS2Recomp/ps2xRuntime/src/runner/` is still
  **wiped and regenerated on every build**; never hand-edit it, and never commit it (the build
  also drops the game's override headers into `ps2xRuntime/include/`; leave those untracked).
- **All game-specific behavior goes through override hooks** in the game repo's `src/` — never
  patch generated runner files directly. `src/` has a **control plane + domain modules** shape:
  - `src/register_overrides.cpp` is the **CONTROL PLANE**. It owns the single
    `PS2_REGISTER_GAME_OVERRIDE(...)` descriptor and **every** `runtime.registerFunction(addr, fn)`
    call, in one ordered list. Nothing else may register a function.
  - Hook **bodies** live in `src/<domain>/` (`dbcman/`, `menu_flow/`, `loader/`, `font_text/`, …) as
    named `hook_<addr>` functions. New code: registration line here, body in the domain module —
    see [`docs/overrides.md`](docs/overrides.md) for which module (and when a new one is warranted).
  - **★ NEVER RELOCATE A REGISTRATION LINE.** Dispatch is last-wins (one slot per address) and many
    registrations sit inside a conditional, so a line's **position and guard are semantics, not
    formatting** — moving one changes which hook wins, or under which condition it installs. Same
    reason: keep a `#define` in the same TU as its `#if` (prefer **env vars**, which cannot drift).
    After ANY control-plane change run
    `.claude/skills/ps2recomp-fix-next-crash/check_registrations.py --game <game_dir>`.
- **Builds run in the background** and can take many minutes. Launch
  `scripts/03_build_game.sh <game_dir>` with a 600000 ms tool timeout and poll the log for
  `Build complete`. Do not block on it in the foreground.
- **Always run the game under `timeout` AND with an explicit log file.** `04_run_game.sh
  <game_dir> [run_log]` writes the runner's output to the given log (2nd arg or `PS2X_RUN_LOG`;
  relative paths land in `<game_dir>/tmp/`); with NO log target it streams to the CONSOLE (for
  the user's interactive terminal use). Agent/automated invocations MUST pass a log (canonically
  `run.txt`; a spinning runner emits hundreds of MB/s), and overlapping runs (background retry
  loop + manual run) must use DISTINCT log files so they never interleave. Read the log after;
  never pipe a raw run into `head`/`tail`.
- **Temp/scratch files go in the engine `tmp/`** (gitignored). Clean them up.
- **★ NEVER DELETE FILES OUTSIDE THIS ENGINE FOLDER OR THE GAME REPO FOLDER.** Deletion is
  confined to `LLMPS2Recomp/` and `<game_dir>/` (plus the session scratchpad). Anything else —
  the user's home, other projects, system paths, sibling repos — is **off limits**, no matter how
  stale, redundant, or "obviously junk" it looks, and regardless of disk pressure. If space is
  needed or a file elsewhere looks removable, **say so and let the user decide** — do not delete
  it yourself. Inside the two allowed folders, still prefer surfacing over deleting anything you
  did not create (see the `.bak` runner binaries).
- **★ REPO SCOPE: touch ONLY the repos the user has explicitly allowed** — currently this engine
  repo (`LLMPS2Recomp/`) and the game repo (`rotk_recomp/`, remote `caws/rotk_recomp`). Every other
  repo or folder (`rotk_decomp_usa/`, `PS2AIRecomp/`, the archived `caws/rotk_decomp`, sibling
  projects, the home dir) is **off limits for any mutation**: no edits, no git commands, no remotes,
  no deletes. If a task appears to need another repo, stop and ask (user directive).
- **Git: local commits on validated progress are fine (operating-manual §8); ★ NEVER push, force-push, create remote branches/tags or change repo settings without the user's explicit approval for that specific action** (user directive). No destructive git unless explicitly asked.
- No "auto mode" — only take actions the user has asked for.

## Engine layout

- `scripts/01_setup.sh <game_dir> [--upstream]` — clone our fork (branch `lotr`, or what
  `<game_dir>/recomp/runtime.lock` names) + build `ps2_recomp` (gcc-13, SSE4.1) into that game's
  own toolchain dir `tools/<game>/PS2Recomp` (`<game>` = basename of `<game_dir>`), add the
  `upstream` remote, and warn if the clone is not at the lock's commit. `--upstream` clones bare
  `ran-j/PS2Recomp` for a baseline build. Commit toolchain fixes in the clone (push only with approval), or a
  re-clone loses them.
- `scripts/03_build_game.sh <game_dir> [flags]` — regen → install → cmake build.
- `scripts/04_run_game.sh <game_dir> [run_log]` — run the last-built runner (ELF read from config); log to file if given, else console.
- `scripts/05_screenshot.sh [--launch] <game_dir> [count] [interval]` — burst-screenshot the **game
  window only** (crops the composited root by the window geometry; `import -window <id>` is black for
  this GL window). Textures FLICKER, so it takes a burst and flags frames with content by mean
  brightness (file size is NOT reliable — a small blob on black compresses as tiny as pure black).
- `tools/<game>/PS2Recomp/` — that game's own toolchain + runtime clone (separate repo,
  read-only to us; cloned per game so builds never collide and each can be re-pulled
  independently). The example game's clone is `tools/rotk_recomp/PS2Recomp/`.

Everything per-game (ELF, config, functions.csv, overrides, generated output) is derived
from `<game_dir>` and its `recomp/config.toml`; nothing about a game is hardcoded here.

## Build / run

```
scripts/03_build_game.sh <game_dir>                              # full: regen → install → build
scripts/03_build_game.sh <game_dir> --skip-regen --changed-recomp # fast: override-only changes
timeout 20 scripts/04_run_game.sh <game_dir> run.txt               # run → log at <game_dir>/tmp/run.txt (no log arg = console, interactive only)
```

**Invocation gotcha — run from the ENGINE repo dir, not the game dir.** `scripts/` lives in
this engine repo (the tool's default working dir), NOT in `<game_dir>` (which has no
`scripts/`). So invoke `scripts/03_build_game.sh <game_dir> …` from the engine dir; do **not**
`cd <game_dir>` first — `cd <game_dir> && scripts/03_build_game.sh …` fails with
`scripts/03_build_game.sh: No such file or directory`. Because the Bash tool's cwd **resets to
the engine dir between calls**, also pass an **absolute** log path when backgrounding
(`> <game_dir>/tmp/build.log`), and prefer absolute `<game_dir>` over relative paths. Canonical
launch + verify (override-only fast path):

```
# from the engine dir (default cwd); background, ~110s for an override-only change
nohup scripts/03_build_game.sh <game_dir> --skip-regen --changed-recomp > <game_dir>/tmp/build.log 2>&1 & disown
# poll: wait for the completion marker (NOT `pgrep -f 03_build_game.sh` — if the polling command
# itself is a wrapper shell whose command-line contains that literal string, pgrep matches its own
# wrapper and the loop never sees the real process exit; this bit us):
… until: [[ -f <game_dir>/tmp/.build_status ]]
cat <game_dir>/tmp/.build_status   # 0 = success; nonzero = build failed, check build.log
# VERIFY the binary is current — the embedded BUILD_TAG must match the source you just edited:
strings <game_dir>/tmp/ps2EntryRunner | grep -oE 'bld-eur-[a-z0-9-]+'   # then also confirm tag= in run.txt after a run
```

- `--skip-regen` reuses the game's `tmp/generated/` (skips ps2_recomp). **Any
  `functions.csv` change needs a FULL regen** — do not use `--skip-regen` after editing it.
- `--changed-recomp` installs only changed files so cmake recompiles fewer unity files (an
  override-only edit recompiles just one unity unit + links ≈ 110s; a full regen is many minutes).
- ps2_recomp runs with the game dir as CWD, so the config's relative paths resolve.
- The built runner is **moved** to `<game_dir>/tmp/ps2EntryRunner` (per-game, no cross-game
  clash); `04` runs that binary and writes the log to `<game_dir>/tmp/run.txt`.
- **Bump `BUILD_TAG` in `register_overrides.cpp` before every build and confirm the embedded
  tag** (`strings … tmp/ps2EntryRunner`) **+ `tag=` in `run.txt`** — `strings` on the binary
  proves which build is on disk *without* a run, catching the stale-binary trap directly.

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

The **game-agnostic methodology** (the techniques, not this game's facts) is documented in
[`docs/`](docs/README.md). Start with [`operating-manual.md`](docs/operating-manual.md) — the
top-level playbook for how the main agent operates (the work loop, the toolbox, the **verification
rule** — ground truth = **generated recompiled C++ AND disassembly together (co-equal; cross-check
BOTH), then validate load-bearing claims with gdb**; everything else is a hypothesis;
what progress is; when to spawn read-only subagents; when to stop). Then [`working-rules.md`](docs/working-rules.md)
(verify-don't-trust; the dispatch model; `BUILD_TAG` + incremental discipline) and: [`debugging.md`](docs/debugging.md) (run-log triage, the
**gdb-under-parent** recipe, **dispatch-logging loop maps**, profiling), [`functions-csv.md`](docs/functions-csv.md) (the
three CSV bug classes — truncated / missing-gap **batchfix** / over-bound **boundfix** — and
disasm-as-ground-truth validation), [`overrides.md`](docs/overrides.md) (HLE patterns:
wait-free replacement, clean skip, SIF/IOP handshake fakes, answering raw-transport RPCs via
`runtime->iop().handleRPC`), [`vu1-jit.md`](docs/vu1-jit.md) (the **VU1 performance arc**: why a block JIT is required, the ablation-measured cost decomposition, the disproven FMAC shortcut, and the lazy-flags design),
[`gl-renderer.md`](docs/gl-renderer.md) (**the GL renderer arc, DESIGN ONLY**: higher resolution and
playability are one project — an upscaled target cannot live in the emulated 4 MB VRAM; the measured
floor that rules out single-sided CPU work; why the two earlier GPU spikes failed under a
bit-exactness constraint the user has now dropped; the PCSX2-style target/texture-cache authority
model; the env-gated phase plan)
and [`workflows.md`](docs/workflows.md) (parallel **read-only**
multi-agent RE fan-out for architecture-question frontiers; sub-agents never build — only the
main agent does; template at `.claude/workflows/re-fanout.js`). Read those for the durable
strategy; this file + the skill are the quick reference.
