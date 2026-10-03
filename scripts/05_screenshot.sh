#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C   # dot decimals for awk/printf/convert (host locale may use comma)

# Burst-screenshot the running PS2Recomp game window (the window ONLY, not the desktop).
#
# Usage: 05_screenshot.sh [--launch] <game_dir> [count] [interval_sec]
#
#   --launch     first launch the runner (timeout-wrapped, background), wait ~1s, then capture.
#                Without it, the runner must ALREADY be running.
#   count        number of shots (default 12).
#   interval_sec delay between shots (default 0.25).
#
# WHY A BURST: the game's textures FLICKER and e.g. the loading texture is on screen for only a few
# frames — most captures come back black. Take many and keep the ones with content. NOTE: file size is
# NOT a reliable content signal (a mostly-black frame with a small solid-colour blob PNG-compresses to
# ~500 bytes, same as pure black). Instead we score each shot by mean brightness (`fx:mean`): 0.0000 =
# pure black, anything above ~0.0005 has on-screen content. The script flags those.
#
# Shots go to <game_dir>/tmp/shot_NN.png. DISPLAY defaults to :0.
#
# CAPTURE METHOD: we crop the COMPOSITED root by the game window's geometry. `import -window <id>`
# returns black for this GL/double-buffered window, so that doesn't work. Root-crop needs the game
# window to be on top of its screen region — raylib raises+focuses it on launch, so capture soon
# after launching (use --launch) and don't click other windows over it.

LAUNCH=0
if [[ "${1:-}" == "--launch" ]]; then LAUNCH=1; shift; fi

GAME_DIR="${1:-}"
[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 [--launch] <game_dir> [count] [interval]"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"
COUNT="${2:-12}"
INTERVAL="${3:-0.25}"

export DISPLAY="${DISPLAY:-:0}"
WIN_TITLE_MATCH="PS2-Recomp"          # runner window title prefix (ps2xRuntime main.cpp)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTDIR="$GAME_DIR/tmp"

command -v xwininfo >/dev/null || { echo "ERROR: xwininfo not installed (apt install x11-utils)"; exit 1; }
command -v import   >/dev/null || { echo "ERROR: import not installed (apt install imagemagick)"; exit 1; }

if [[ "$LAUNCH" == "1" ]]; then
    echo "launching runner (background, 25s timeout)..."
    timeout 25 "$ROOT_DIR/scripts/04_run_game.sh" "$GAME_DIR" >/dev/null 2>&1 &
    sleep 1
fi

# Find the game window id by title (xwininfo -tree lists every window: `0xID "title": ...`).
find_win() { xwininfo -root -tree 2>/dev/null | grep -F "\"$WIN_TITLE_MATCH" | grep -oE '0x[0-9a-f]+' | head -1; }
WIN_ID="$(find_win || true)"
[[ -n "$WIN_ID" ]] || { echo "ERROR: no window titled '$WIN_TITLE_MATCH*' on $DISPLAY (is the runner running?)"; exit 1; }

# Best-effort raise so the crop sees the game, not an overlapping window (no-op if tool absent).
command -v wmctrl  >/dev/null && wmctrl -ia "$WIN_ID" 2>/dev/null || true
command -v xdotool >/dev/null && xdotool windowactivate "$WIN_ID" 2>/dev/null || true

mkdir -p "$OUTDIR"
echo "capturing $COUNT shots (${INTERVAL}s apart) of window $WIN_ID -> $OUTDIR/shot_NN.png"
content_frames=()
for i in $(seq -w 1 "$COUNT"); do
    INFO="$(xwininfo -id "$WIN_ID" 2>/dev/null || true)"
    X="$(awk '/Absolute upper-left X/ {print $NF}' <<<"$INFO")"
    Y="$(awk '/Absolute upper-left Y/ {print $NF}' <<<"$INFO")"
    W="$(awk '/^[[:space:]]*Width:/  {print $NF}' <<<"$INFO")"
    H="$(awk '/^[[:space:]]*Height:/ {print $NF}' <<<"$INFO")"
    out="$OUTDIR/shot_$i.png"
    if [[ -n "$X" && -n "$Y" && -n "$W" && -n "$H" ]]; then
        import -window root -crop "${W}x${H}+${X}+${Y}" +repage "$out" 2>/dev/null || true
        # Mean brightness is a reliable content signal (file size is not — a small solid blob on black
        # compresses as tiny as pure black).
        mean=$(convert "$out" -format '%[fx:mean]' info: 2>/dev/null || echo 0)
        flag=""
        if awk "BEGIN{exit !($mean>0.0005)}"; then flag="  <-- CONTENT"; content_frames+=("$out"); fi
        printf '  shot_%s.png  mean=%s%s\n' "$i" "$mean" "$flag"
    else
        echo "  shot_$i: window gone"
    fi
    sleep "$INTERVAL"
done
if (( ${#content_frames[@]} )); then
    echo "done. ${#content_frames[@]} frame(s) with content: ${content_frames[*]}"
else
    echo "done. no non-black frames caught — the texture flickers; try again or raise COUNT / lower INTERVAL."
fi
