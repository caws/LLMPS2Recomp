#!/usr/bin/env bash
set -euo pipefail

# Run this game (after building it with scripts/build.sh).
#
#   scripts/run.sh                  # runs until exit / Ctrl-C
#   timeout 20 scripts/run.sh       # recommended: cap a spin; an unfinished game
#                                   # can emit output very fast
#
# Output is written to this game's tmp/run.txt (not the terminal).
#
# Requires the PS2RECOMP_ENGINE env var pointing at the engine repo root:
#   export PS2RECOMP_ENGINE=/path/to/ps2recomp-engine

GAME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${PS2RECOMP_ENGINE:?Set PS2RECOMP_ENGINE to the engine repo root, e.g. export PS2RECOMP_ENGINE=/path/to/engine}"

RUN="$PS2RECOMP_ENGINE/scripts/04_run_game.sh"
[[ -x "$RUN" ]] || {
    echo "ERROR: engine run script not found or not executable:"
    echo "  $RUN"
    echo "Check PS2RECOMP_ENGINE points at the engine repo root."
    exit 1
}

exec "$RUN" "$GAME_DIR" "$@"
