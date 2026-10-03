#!/usr/bin/env bash
set -euo pipefail

# Set up a PER-GAME PS2Recomp toolchain clone.
#
# Usage: 01_setup.sh <game_dir> [--no-patches]
#
#   --no-patches  Skip applying patches/*.patch — build BARE upstream PS2Recomp.
#                 Use to establish a clean-upstream baseline before layering our
#                 local fixes back on (e.g. after an upstream pull that changed
#                 the files our patches touch).
#
# Each game gets its OWN clone at tools/<game>/PS2Recomp (game = basename of
# <game_dir>), so builds never collide between games and the clone can be
# re-pulled / re-cloned per game without disturbing others. PS2Recomp is still
# under active development, so a clean per-game clone keeps a tidy slate.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GAME_DIR=""
SKIP_PATCHES=0
for arg in "$@"; do
    case "$arg" in
        --no-patches|--skip-patches) SKIP_PATCHES=1 ;;
        -*) echo "ERROR: unknown flag: $arg. Usage: $0 <game_dir> [--no-patches]"; exit 1 ;;
        *)  if [[ -z "$GAME_DIR" ]]; then GAME_DIR="$arg";
            else echo "ERROR: unexpected extra argument: $arg"; exit 1; fi ;;
    esac
done
[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 <game_dir> [--no-patches]"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"
GAME="$(basename "$GAME_DIR")"

PS2RECOMP_DIR="$ROOT_DIR/tools/$GAME/PS2Recomp"

echo "=== PS2Recomp setup for game: $GAME ==="
echo "    clone: $PS2RECOMP_DIR"

echo "[1/4] Checking prerequisites..."
for cmd in git cmake; do
    command -v "$cmd" >/dev/null || { echo "ERROR: missing required command: $cmd"; exit 1; }
done
command -v g++-13 >/dev/null 2>&1 || { echo "ERROR: g++-13 is required."; exit 1; }

echo "[2/4] Cloning PS2Recomp (per-game)..."
mkdir -p "$(dirname "$PS2RECOMP_DIR")"
if [[ ! -d "$PS2RECOMP_DIR" ]]; then
    git clone https://github.com/ran-j/PS2Recomp.git "$PS2RECOMP_DIR"
else
    echo "    already cloned, skipping. (To re-pull: git -C \"$PS2RECOMP_DIR\" pull;"
    echo "     for a clean slate: rm -rf \"$PS2RECOMP_DIR\" and re-run this.)"
fi

if [[ $SKIP_PATCHES -eq 1 ]]; then
    echo "[2b] SKIPPING local patches (--no-patches): building BARE upstream PS2Recomp."
else
echo "[2b] Applying local PS2Recomp patches (patches/*.patch)..."
# Correctness fixes not yet in upstream PS2Recomp (see patches/README.md). Idempotent:
# skips patches already applied; warns (does not abort) on ones that no longer apply so
# an upstream pull that absorbed or conflicted a fix is surfaced instead of silently lost.
shopt -s nullglob
for p in "$ROOT_DIR"/patches/*.patch; do
    name="$(basename "$p")"
    if git -C "$PS2RECOMP_DIR" apply --check "$p" 2>/dev/null; then
        git -C "$PS2RECOMP_DIR" apply "$p"
        echo "    applied:         $name"
    elif git -C "$PS2RECOMP_DIR" apply --reverse --check "$p" 2>/dev/null; then
        echo "    already applied: $name"
    else
        echo "    WARNING: does not apply (upstream drift? fixed upstream?): $name"
    fi
done
shopt -u nullglob
fi

echo "[3/4] Configuring PS2Recomp..."
cmake -B "$PS2RECOMP_DIR/out/build" -S "$PS2RECOMP_DIR" \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-13 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_EXE_LINKER_FLAGS="-pthread" \
  -DCMAKE_CXX_FLAGS="-msse4.1"

echo "[4/4] Building ps2_recomp..."
cmake --build "$PS2RECOMP_DIR/out/build" --target ps2_recomp -j"$(nproc)"

echo "[5/6] Building ps2_analyzer..."
cmake --build "$PS2RECOMP_DIR/out/build" --target ps2_analyzer -j"$(nproc)"

echo
echo "Setup complete for $GAME."
echo "  ps2_recomp: $PS2RECOMP_DIR/out/build/ps2xRecomp/ps2_recomp"
echo "  Next:       scripts/03_build_game.sh $GAME_DIR"
