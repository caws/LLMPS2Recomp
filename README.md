# LLMPS2Recomp

An **LLM-assisted workflow** for porting PlayStation 2 games to PC with
[PS2Recomp](https://github.com/ran-j/PS2Recomp). It is not a recompiler and contains no game:
it is the method, the tooling and the AI-agent setup that drive PS2Recomp through the long
work of getting a game to boot, play and stay faithful.

It was built while porting *The Lord of the Rings: The Return of the King*
([`caws/rotk_recomp`](https://github.com/caws/rotk_recomp)), with
[Claude Code](https://claude.com/claude-code) doing much of the work under the rules written
down here. The docs and tools are game-agnostic; that game is the worked example throughout.

## Where this sits

| Repository | What it is | Who needs it |
| --- | --- | --- |
| [PS2Recomp](https://github.com/ran-j/PS2Recomp) (our fork: [`caws/PS2Recomp`](https://github.com/caws/PS2Recomp), branch `lotr`) | The **toolchain**: `ps2_recomp` translates the game's MIPS R5900 code to C++, and `ps2xRuntime` provides the PS2 hardware it runs on (EE kernel, GS, VU, SIF/IOP, audio, pads). | Everyone. A game repo's build clones it at a pinned commit. |
| A **game repo**, e.g. [`rotk_recomp`](https://github.com/caws/rotk_recomp) | One game: the recompiler inputs (`config.toml`, `functions.csv`), its override hooks and mods, its build/run scripts and notes. | Players and contributors of that game. It builds on its own. |
| **LLMPS2Recomp** (this repo) | How the work is done: methodology docs, Claude Code skills and workflows, reverse-engineering helpers, and the scripts that set up a toolchain clone per game and drive builds and runs. | Anyone doing the porting work, especially with an AI agent. **Players don't need it.** |

> **No game content.** Nothing here comes from a game disc. Every game repo built with it
> requires the player's own, legally obtained copy.

## What's inside

- **`docs/`**: the reusable methodology. Start at [`docs/README.md`](docs/README.md).
  - [`operating-manual.md`](docs/operating-manual.md) and [`working-rules.md`](docs/working-rules.md): the
    loop (find the frontier, verify against disassembly and generated code, fix, build, test) and the
    rules that keep it honest.
  - [`functions-csv.md`](docs/functions-csv.md): the function-table bug classes (truncated, missing,
    swallowed functions) and how to find and fix them.
  - [`overrides.md`](docs/overrides.md): override hooks and HLE patterns, with their pitfalls.
  - [`debugging.md`](docs/debugging.md): debugging a hung or crashing runner, gdb under a parent process,
    RAM diffs against PCSX2.
  - Design notes: [`gl-renderer.md`](docs/gl-renderer.md), [`vu1-jit.md`](docs/vu1-jit.md),
    [`native-lift.md`](docs/native-lift.md), [`upstream-merge.md`](docs/upstream-merge.md).
- **`.claude/`**: the Claude Code setup.
  - Skills: `ps2recomp-fix-next-crash` (diagnose and fix the next crash or stall),
    `ps2recomp-toolchain-migration` (move a fork onto a newer upstream).
  - The `re-fanout` workflow: read-only parallel reverse-engineering for architecture questions.
  - Helper tools in `.claude/skills/ps2recomp-fix-next-crash/`: `funcs.py` (disassemble and look up
    functions), `find_missing.py` / `find_swallowed.py` (function-table bugs), `xmap.py` (map addresses
    between two releases of a game), `check_registrations.py` (audit a game's hook registrations), and
    GS/VU1 inspection tools.
- **`scripts/`**: set up and drive a game repo, see [`scripts/README.md`](scripts/README.md).
  - `00_bootstrap_game.sh <game_dir>`: wire a game repo for agent sessions opened from it (its
    `CLAUDE.md`, hooks, skill symlinks, build/run wrappers).
  - `01_setup.sh <game_dir>`: clone the PS2Recomp fork for that game, at the commit its
    `recomp/runtime.lock` pins, into `tools/<game>/PS2Recomp`, and build the recompiler.
  - `02_verify_setup.sh`, `03_build_game.sh`, `04_run_game.sh`, `verify_disc.sh`: check, build and run
    a single-disc game repo (they forward to the scripts in the toolchain clone).
  - `05_screenshot.sh`, `06_sendkey.py`, `build_progress.sh`: capture frames, inject input, follow a
    build.
- **`templates/game/`**: the starting `CLAUDE.md`, README and wrappers for a new game repo.
- **`CLAUDE.md`**: the agent guide for sessions opened in this repo.

## Using it

Requirements: Linux, `git`, `cmake`, GCC 13; [Claude Code](https://claude.com/claude-code) for the
agent workflow (the docs and tools also work by hand).

```bash
git clone https://github.com/caws/LLMPS2Recomp.git
git clone https://github.com/caws/rotk_recomp.git        # or your own game repo, next to it
LLMPS2Recomp/scripts/00_bootstrap_game.sh "$PWD/rotk_recomp"   # agent wiring for the game repo
LLMPS2Recomp/scripts/01_setup.sh "$PWD/rotk_recomp"            # its toolchain clone
export PS2RECOMP_ENGINE="$PWD/LLMPS2Recomp"              # the game's scripts then use that clone
```

Then work from the game repo: its `README.md` and `CONTRIBUTING.md` say how it builds and runs
(`rotk_recomp`: `scripts/build.sh --iso <disc>`), and a Claude Code session opened there follows its
`CLAUDE.md`, which points back to the docs here. With `PS2RECOMP_ENGINE` set, a game repo builds
against the clone in `tools/<game>/PS2Recomp`, where toolchain changes are made and committed;
without it, it clones the pinned fork commit itself, as a player's build does.

**A new game** needs its recompiler inputs first (`recomp/config.toml` and `recomp/functions.csv`,
from PS2Recomp's analyzer and/or Ghidra); start from `templates/game/` and
[`docs/README.md`](docs/README.md).

## License

[MIT](LICENSE). This covers the scripts, tools, docs and templates here. PS2Recomp, which
`01_setup.sh` clones into `tools/`, is a separate project under its own licence (GPL-3.0).
