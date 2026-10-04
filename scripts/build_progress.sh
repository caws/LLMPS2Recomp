#!/usr/bin/env bash
# build_progress.sh — report progress of a background game build started by 03_build_game.sh.
#
#   Usage: scripts/build_progress.sh <game_dir>
#
# Reads <game_dir>/tmp/build.log + sccache stats + the live compiler/linker processes.
# Prints: completion (and the binary), or the build phase + an approximate % + a monster/OOM
# warning (the signal that actually matters) + any error/kill lines.
#
# Notes:
#   - The % uses sccache's "Compile requests" count vs the number of generated .cpp units.
#     That counter is cumulative across builds, so the % is APPROXIMATE — fine for "how's it
#     going", and it always increases within a build.
#   - The important early-warning is a single cc1plus ballooning past a few GB: that's an
#     over-bound auto-named function (a 13MB+ data-as-code translation unit). Fix it with the
#     boundfix rename (see the memory note reference-overbound-fix) so the build doesn't OOM/stall.
set -uo pipefail

GAME_DIR="${1:-}"
[[ -n "$GAME_DIR" ]] || { echo "Usage: $0 <game_dir>"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" 2>/dev/null && pwd)" || { echo "ERROR: game dir not found: $1"; exit 1; }

LOG="$GAME_DIR/tmp/build.log"
GEN="$GAME_DIR/tmp/generated"
RUNNER="$GAME_DIR/${PS2X_RUNNER_NAME:-$(basename "$GAME_DIR")}"   # named after the game dir (cont.346t)

[[ -f "$LOG" ]] || { echo "no build.log at $LOG — build not started?"; exit 0; }

errs=$(grep -ciE 'error:|Killed|Terminated|virtual memory exhausted' "$LOG" 2>/dev/null || true)

if grep -q 'Build complete' "$LOG" 2>/dev/null; then
    echo ">>> BUILD COMPLETE"
    [[ -x "$RUNNER" ]] && ls -la --time-style=+%H:%M:%S "$RUNNER" 2>/dev/null \
        | awk '{printf "    binary: %s bytes  (%s)\n", $5, $6}'
    [[ "${errs:-0}" -gt 0 ]] && echo "    note: $errs error/kill line(s) in the log"
    exit 0
fi

phase=$(grep -oE '\[[123]/3\][^[:cntrl:]]*' "$LOG" 2>/dev/null | tail -1)
echo ">>> BUILDING   ${phase:-(starting up)}"

total=$(ls "$GEN"/*.cpp 2>/dev/null | wc -l)
req=$(sccache --show-stats 2>/dev/null | awk '/^Compile requests / && !/executed/{print $3; exit}')
if [[ -n "${req:-}" && "${total:-0}" -gt 0 ]]; then
    awk -v r="$req" -v t="$total" 'BEGIN{printf "    progress: ~%d%% (%d / %d compile units, approx)\n", int(100*r/t), r, t}'
fi

ps -C cc1plus -o rss= 2>/dev/null | awk '{n++; if($1>m)m=$1} END{
    if(n) printf "    compilers: %d active, biggest %.1f GB%s\n", n, m/1048576, \
              (m/1048576>3.0 ? "   <<< OOM RISK — over-bound monster unit (apply boundfix)" : "")
    else  print  "    compilers: none active — linking or nearly done"}'

ps -C lld,ld.lld,ld.gold,ld -o etime= 2>/dev/null | head -1 | grep -q '[0-9]' && echo "    linker: active"

[[ "${errs:-0}" -gt 0 ]] && echo "    *** $errs error/kill line(s) — grep -iE 'error:|Killed' \"$LOG\""
exit 0
