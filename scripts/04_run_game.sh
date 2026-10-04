#!/usr/bin/env bash
set -euo pipefail

# Run a game built with the PS2Recomp engine.
#
# Usage: 04_run_game.sh <game_dir> [run_log]
#
# The ELF path is read from <game_dir>/recomp/config.toml `input`. The runner
# binary is whatever was last built by 03_build_game.sh (one active game at a
# time — static recompilation).
#
# run_log: optional log-file target (2nd arg, or PS2X_RUN_LOG env). With NO log
# target the runner's output goes to the CONSOLE (stdout/stderr passthrough) —
# for interactive terminal use. Automated/agent invocations MUST pass a log file
# (canonically tmp/run.txt; a spinning runner emits hundreds of MB/s), and runs
# that may OVERLAP (background retry loop + manual run) should use DISTINCT
# paths so logs don't interleave. Relative paths resolve against <game_dir>/tmp.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GAME_DIR="${1:-}"
[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 <game_dir>"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"

RUNNER="$GAME_DIR/ps2EntryRunner"   # per-game binary, placed here by 03_build_game.sh
# Log target: 2nd arg > PS2X_RUN_LOG env > default = console passthrough (no redirect).
RUN_LOG="${2:-${PS2X_RUN_LOG:-}}"
if [[ -n "$RUN_LOG" && "$RUN_LOG" != /* ]]; then RUN_LOG="$GAME_DIR/tmp/$RUN_LOG"; fi
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

# Wrap the invocation in `timeout` to cap a spin:
#   timeout 20 scripts/04_run_game.sh <game_dir> run.txt
mkdir -p "$GAME_DIR/tmp"
# Game-side overrides (e.g. DBCMAN HLE serving) read gamefiles/ from this env var, so the
# path is never hardcoded and survives a game-folder rename.
export PS2_GAMEFILES="$GAME_DIR/gamefiles"
# cont.346p: the ELF lives INSIDE gamefiles/, and memory cards default to the ELF's own directory
# -- so mc0/ and mc1/ live in gamefiles/ too. EVERYTHING for the game is one folder the player can
# drop their disc into (user). No pin here: the runtime default already does this.
# PS2X_MC_ROOT names a directory CONTAINING mc0/ and mc1/; set it only to switch cards (the
# two-card test harness does).
if [[ -n "$RUN_LOG" ]]; then
    mkdir -p "$(dirname "$RUN_LOG")"
    # ALWAYS remove the old log first, so the log that exists afterward is GUARANTEED to be
    # from THIS run. If the runner fails to launch/write, it will be absent/empty rather than
    # a stale leftover that looks like a fresh result (this bit us: a stale run.txt was mistaken
    # for the current run, hiding a fresh binary's output).
    rm -f "$RUN_LOG"
    echo "log: $RUN_LOG"
    exec "$RUNNER" "$ELF" > "$RUN_LOG" 2>&1
else
    # Interactive/console mode: output streams to the terminal. NB a spinning runner can emit
    # hundreds of MB/s — automated invocations should always pass a log file instead.
    echo "log: (console)"
    exec "$RUNNER" "$ELF"
fi
