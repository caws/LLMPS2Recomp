#!/usr/bin/env bash
set -euo pipefail

# Run a game built with the PS2Recomp engine.
#
# Usage: 04_run_game.sh <game_dir>
#
# The ELF path is read from <game_dir>/recomp/config.toml `input`. The runner
# binary is whatever was last built by 03_build_game.sh (one active game at a
# time — static recompilation).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GAME_DIR="${1:-}"
[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 <game_dir>"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"

RUNNER="$GAME_DIR/tmp/ps2EntryRunner"   # per-game binary, placed here by 03_build_game.sh
RUN_LOG="$GAME_DIR/tmp/run.txt"
CONFIG="$GAME_DIR/recomp/config.toml"

[[ -f "$CONFIG" ]] || { echo "ERROR: config.toml not found: $CONFIG"; exit 1; }

ELF_REL="$(grep -E "^[[:space:]]*input[[:space:]]*=" "$CONFIG" | head -1 | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
[[ -n "$ELF_REL" ]] || { echo "ERROR: could not read 'input' from $CONFIG"; exit 1; }
ELF="$GAME_DIR/${ELF_REL#./}"

echo "========================================="
echo " PS2Recomp Run"
echo " game: $GAME_DIR"
echo "========================================="
echo

if [[ ! -x "$RUNNER" ]]; then
    echo "ERROR: ps2EntryRunner not found: $RUNNER"
    echo "Run:   scripts/03_build_game.sh $GAME_DIR"
    exit 1
fi
[[ -f "$ELF" ]] || { echo "ERROR: ELF not found (from config 'input'): $ELF"; exit 1; }

# Runner output (often hundreds of MB/s when spinning) goes to the game's own
# tmp/run.txt, NOT the terminal. Wrap the invocation in `timeout` to cap a spin:
#   timeout 20 scripts/04_run_game.sh <game_dir>
mkdir -p "$GAME_DIR/tmp"
echo "log: $RUN_LOG"
exec "$RUNNER" "$ELF" > "$RUN_LOG" 2>&1
