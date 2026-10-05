#!/usr/bin/env bash
set -euo pipefail

# Build this game with the PS2Recomp engine.
#
#   scripts/build.sh [flags]        # flags pass through to the engine, e.g.
#   scripts/build.sh --skip-regen --changed-recomp
#
# Requires the PS2RECOMP_ENGINE env var pointing at the engine repo root:
#   export PS2RECOMP_ENGINE=/path/to/ps2recomp-engine

GAME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${PS2RECOMP_ENGINE:?Set PS2RECOMP_ENGINE to the engine repo root, e.g. export PS2RECOMP_ENGINE=/path/to/engine}"

BUILD="$PS2RECOMP_ENGINE/scripts/03_build_game.sh"
[[ -x "$BUILD" ]] || {
    echo "ERROR: engine build script not found or not executable:"
    echo "  $BUILD"
    echo "Check PS2RECOMP_ENGINE points at the engine repo root."
    exit 1
}

exec "$BUILD" "$GAME_DIR" "$@"
