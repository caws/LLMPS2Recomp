# `scripts/` — the build/run pipeline

These live **only in this engine repo**. Always invoke them from the engine directory and
point them *at* a game dir; never `cd` into the game repo first (it has no `scripts/`):

```sh
scripts/03_build_game.sh /abs/path/to/<game-repo>        #  ✅
cd <game-repo> && scripts/03_build_game.sh .             #  ❌ No such file or directory
```

**Numbered** scripts are pipeline steps you run in order. **Unnumbered** ones are helpers
that the numbered scripts also call.

| Script | What it does |
| --- | --- |
| `01_setup.sh <game_dir> [--upstream]` | Clone our PS2Recomp fork at the commit `<game_dir>/recomp/runtime.lock` pins and build `ps2_recomp`, into that game's own `tools/<game>/PS2Recomp`. |
| `02_verify_setup.sh [game_dir] [--deep]` | Check host prerequisites, the toolchain, the game's provided inputs, and the player's disc copy. Run this before reporting a problem. |
| `03_build_game.sh <game_dir> [flags]` | regen → install → cmake build. `--skip-regen --changed-recomp` is the ~110 s override-only path; **any `functions.csv` change needs a full regen**. |
| `04_run_game.sh <game_dir> [run_log]` | Run the last-built runner. With a log argument it writes there; with none it streams to the console. Automated callers must pass a log — a spinning runner emits hundreds of MB/s. |
| `05_screenshot.sh [--launch] <game_dir> [count] [interval]` | Burst-capture the game window and flag frames that have content. |
| `06_sendkey.py` | Inject input via XTEST (`xdotool`/`wmctrl` are not installed here). |
| `verify_disc.sh <game_dir> [--deep|--write]` | Validate the player's disc copy against `recomp/disc.manifest`. Called as a preflight by 02, 03 and 04. |
| `build_progress.sh` | Report a running build as a percentage. |

## Verifying a disc copy

No game data ships with this project: the player copies their own disc into
`<game_dir>/gamefiles/`. A static recompilation is tied to **one exact executable** —
`recomp/functions.csv` is a list of addresses inside it — so the wrong disc does not fail
cleanly, it fails a long way from the cause. `verify_disc.sh` names which problem it is:

| Exit | Meaning |
| --- | --- |
| 0 | verified |
| 1 | no disc files at all |
| 2 | wrong disc (another region, or another revision of the same one) |
| 3 | incomplete copy (the executable, or data files, were not copied) |
| 4 | corrupt (a file is the wrong size, or `--deep` found the wrong bytes) |
| 5 | cannot verify — this game has no `recomp/disc.manifest` yet |

The default check reads the executable and stats everything else, so it is effectively free
and runs on every build and every launch. `--deep` also checksums every file: it is the
answer to "is my dump good?", and the only way to catch a **right-sized file with wrong
bytes** — a bad read the copying tool did not notice.

Maintainers create the manifest once, from a copy known to be good:

```sh
scripts/verify_disc.sh <game_dir> --write
```

It records the executable's size and SHA-256 plus a size and hash per file. That describes a
disc without containing any of it — the same thing preservation databases publish openly —
so it is committed to the game repo while the disc itself never is.
