# PS2Recomp engine

A **stateless engine** for statically recompiling PlayStation 2 games to native
executables. It recompiles an EE MIPS R5900 ELF into C++ with
[`ps2_recomp`](https://github.com/ran-j/PS2Recomp) and runs it on `ps2xRuntime`
(which emulates the EE kernel, GS, SIF/IOP, threading).

The engine contains **no game**. You point it at a separate **per-game repo**, and
it builds a runnable binary from that game's inputs. Static recompilation means
**one game per built binary** — building a game produces that game's own runner.

> **You must supply your own game.** This engine ships no copyrighted material. You
> provide a legally-obtained ELF and disc data for any game you build.

## Prerequisites

- `git`, `cmake`
- `g++-13` (the toolchain builds with GCC 13 + SSE4.1)

Check your environment any time with `scripts/02_verify_setup.sh`.

## Setup (once)

```bash
scripts/01_setup.sh        # clones + builds the PS2Recomp toolchain into tools/
scripts/02_verify_setup.sh # confirms prerequisites + toolchain are ready
```

## A per-game repo

Each game is its own repository with this layout:

```
<game-repo>/
  <ELF>            # the PS2 executable; its path is declared in recomp/config.toml `input`
  recomp/          # config.toml + functions.csv   (the recompiler inputs)
  src/             # register_overrides.cpp (+ .h)  (game-specific override hooks)
  scripts/         # build.sh + run.sh wrappers     (call the engine via $PS2RECOMP_ENGINE)
  docs/            # human notes (static facts, progress journal)
  tmp/             # build outputs: generated/, the runner binary, run.txt  (gitignored)
  gamefiles/       # disc/CD data                   (gitignored; you provide it)
```

Committed in a game repo: `recomp/`, `src/`, `docs/`. Not committed (you provide):
the ELF and `gamefiles/`. Generated: everything under `tmp/`.

To build a new game you need its `recomp/config.toml` + `recomp/functions.csv`
(produced upstream by Ghidra + analysis), your ELF dropped in at the path the
config's `input` declares, and any overrides in `src/`.

## Build & run a game

From the **engine**, pass the game dir:

```bash
scripts/03_build_game.sh <game_dir>            # regen -> install -> build
timeout 20 scripts/04_run_game.sh <game_dir>   # run; output -> <game_dir>/tmp/run.txt
```

Or from inside the **game repo**, using its own wrappers (point `PS2RECOMP_ENGINE` at
this engine once):

```bash
export PS2RECOMP_ENGINE=/path/to/this/engine   # e.g. in ~/.bashrc
cd <game_dir>
scripts/build.sh                               # passes flags through to the engine
timeout 20 scripts/run.sh                       # run; output -> tmp/run.txt
```

Always wrap runs in `timeout` — an unfinished game can spin and emit output very
fast. The runner writes to `<game_dir>/tmp/run.txt`, not your terminal; read that
file afterwards.

Everything per-game is derived from `<game_dir>/recomp/config.toml`; nothing about a
game is hardcoded in the engine. The built runner is placed at
`<game_dir>/tmp/ps2EntryRunner`, so different games never clash.

### Useful flags (`03_build_game.sh`)

| Flag | Effect |
|------|--------|
| _(none)_ | Full: regenerate C++ from the CSV, install, build. **Required after any `functions.csv` change.** |
| `--skip-regen` | Reuse the game's existing `tmp/generated/` (skip `ps2_recomp`). |
| `--changed-recomp` | Install only changed files so cmake recompiles fewer translation units (fast for override-only changes). |
| `--release` | Full LTO `Release` build (slow link) instead of the default `RelWithDebInfo`. |
| `--skip-build` | Regenerate + install but don't run cmake. |

Builds can take many minutes (the recompiled C++ is large). The first build of a
game compiles everything; later override-only builds are much faster with
`--skip-regen --changed-recomp`.

## How a game gets worked on

Game-specific behavior is added **only** through override hooks in the game repo's
`src/register_overrides.cpp` — `PS2_REGISTER_GAME_OVERRIDE(...)` plus
`runtime.registerFunction(addr, lambda)`. The generated runner code under
`tools/PS2Recomp/` is regenerated on every build and must never be hand-edited.

The common bring-up bug is truncated/missing functions in `functions.csv` (a
function sized to only its first instruction leaks the stack and corrupts the
return address). Fixes go in the game repo's `recomp/functions.csv`, followed by a
full rebuild. See `CLAUDE.md` for the detailed methodology.

## Layout (engine)

- `scripts/` — `01_setup`, `02_verify_setup`, `03_build_game`, `04_run_game`
- `tools/PS2Recomp/` — the toolchain + runtime (cloned by setup; not tracked here)
- `tmp/` — engine scratch (build logs)
