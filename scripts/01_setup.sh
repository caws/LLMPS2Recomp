#!/usr/bin/env bash
set -euo pipefail

# Set up a PER-GAME PS2Recomp toolchain clone.
#
# Usage: 01_setup.sh <game_dir> [--upstream]
#
#   --upstream    Clone BARE upstream (ran-j/PS2Recomp, branch main) instead of our fork.
#                 Use to establish a clean-upstream baseline for A/B or bisecting.
#
# Each game gets its OWN clone at tools/<game>/PS2Recomp (game = basename of <game_dir>), so
# builds never collide between games and each clone can be re-pulled independently.
#
# ★ The toolchain is OUR FORK of PS2Recomp (private repo, branch `lotr`), not upstream plus a
# patch stack (the patches/*.patch mechanism was retired, cont.230; its change log
# lives in the fork as docs/llmps2recomp-patches.md). Runtime/recompiler changes are made IN
# the clone and COMMITTED + PUSHED there. Upstream is kept as the `upstream` remote so it can
# be merged periodically:  git -C tools/<game>/PS2Recomp fetch upstream && git merge upstream/main
#
# The game records the exact toolchain commit it was built against in
# <game_dir>/recomp/runtime.lock (keys: repo=, branch=, commit=). This script honours it:
# repo/branch select what is cloned; commit is CHECKED after clone/fetch and a mismatch is a
# loud warning (never a silent divergence), not an error -- a developer moving the branch
# forward updates the lock in the same change.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FORK_REPO_DEFAULT="https://github.com/caws/PS2Recomp.git"
FORK_BRANCH_DEFAULT="lotr"
UPSTREAM_REPO="https://github.com/ran-j/PS2Recomp.git"
UPSTREAM_BRANCH="main"

GAME_DIR=""
USE_UPSTREAM=0
for arg in "$@"; do
    case "$arg" in
        --upstream) USE_UPSTREAM=1 ;;
        --no-patches|--skip-patches)
            echo "NOTE: $arg is obsolete (the patch stack was retired; the clone IS the fork). Ignored." ;;
        -*) echo "ERROR: unknown flag: $arg. Usage: $0 <game_dir> [--upstream]"; exit 1 ;;
        *)  if [[ -z "$GAME_DIR" ]]; then GAME_DIR="$arg";
            else echo "ERROR: unexpected extra argument: $arg"; exit 1; fi ;;
    esac
done
[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 <game_dir> [--upstream]"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"
GAME="$(basename "$GAME_DIR")"

PS2RECOMP_DIR="$ROOT_DIR/tools/$GAME/PS2Recomp"
LOCK="$GAME_DIR/recomp/runtime.lock"

# Resolve what to clone: env overrides > lock file > defaults.
REPO="${PS2RECOMP_REPO:-}"
BRANCH="${PS2RECOMP_BRANCH:-}"
LOCK_COMMIT=""
if [[ -f "$LOCK" ]]; then
    lock_repo="$(sed -n 's/^repo=//p' "$LOCK" | head -n1)"
    lock_branch="$(sed -n 's/^branch=//p' "$LOCK" | head -n1)"
    LOCK_COMMIT="$(sed -n 's/^commit=//p' "$LOCK" | head -n1)"
    [[ -n "$REPO" ]]   || REPO="$lock_repo"
    [[ -n "$BRANCH" ]] || BRANCH="$lock_branch"
fi
[[ -n "$REPO" ]]   || REPO="$FORK_REPO_DEFAULT"
[[ -n "$BRANCH" ]] || BRANCH="$FORK_BRANCH_DEFAULT"
if [[ $USE_UPSTREAM -eq 1 ]]; then
    REPO="$UPSTREAM_REPO"; BRANCH="$UPSTREAM_BRANCH"; LOCK_COMMIT=""
fi

echo "=== PS2Recomp setup for game: $GAME ==="
echo "    clone:  $PS2RECOMP_DIR"
echo "    source: $REPO  (branch $BRANCH)${LOCK_COMMIT:+  pinned by $LOCK to $LOCK_COMMIT}"

echo "[1/5] Checking prerequisites..."
for cmd in git cmake; do
    command -v "$cmd" >/dev/null || { echo "ERROR: missing required command: $cmd"; exit 1; }
done
command -v g++-13 >/dev/null 2>&1 || { echo "ERROR: g++-13 is required."; exit 1; }

echo "[2/5] Cloning PS2Recomp (per-game)..."
mkdir -p "$(dirname "$PS2RECOMP_DIR")"
if [[ ! -d "$PS2RECOMP_DIR" ]]; then
    git clone --branch "$BRANCH" "$REPO" "$PS2RECOMP_DIR"
else
    echo "    already cloned, skipping. (To update: git -C \"$PS2RECOMP_DIR\" pull;"
    echo "     for a clean slate: rm -rf \"$PS2RECOMP_DIR\" and re-run this.)"
fi
# Keep upstream reachable for periodic merges (fork clones only).
if [[ $USE_UPSTREAM -eq 0 ]] && ! git -C "$PS2RECOMP_DIR" remote get-url upstream >/dev/null 2>&1; then
    git -C "$PS2RECOMP_DIR" remote add upstream "$UPSTREAM_REPO"
    echo "    added remote 'upstream' -> $UPSTREAM_REPO"
fi

echo "[3/5] Verifying the toolchain commit against $LOCK ..."
HEAD_COMMIT="$(git -C "$PS2RECOMP_DIR" rev-parse HEAD)"
HEAD_BRANCH="$(git -C "$PS2RECOMP_DIR" branch --show-current || true)"
echo "    HEAD = $HEAD_COMMIT (branch ${HEAD_BRANCH:-detached})"
if [[ -n "$LOCK_COMMIT" && "$HEAD_COMMIT" != "$LOCK_COMMIT"* ]]; then
    echo
    echo "  ★ WARNING: the clone is NOT at the commit the game was last built/verified against."
    echo "    lock:  $LOCK_COMMIT"
    echo "    clone: $HEAD_COMMIT"
    echo "    Either check it out (git -C \"$PS2RECOMP_DIR\" checkout $LOCK_COMMIT) or, if the"
    echo "    branch moved forward on purpose, update $LOCK in the same change. Continuing."
    echo
fi
if [[ -n "$(git -C "$PS2RECOMP_DIR" status --porcelain --untracked-files=no)" ]]; then
    echo "    NOTE: the clone has uncommitted tracked changes (git -C \"$PS2RECOMP_DIR\" status)."
    echo "          Commit + push them to the fork; a re-clone would lose them."
fi

echo "[4/5] Configuring PS2Recomp..."
cmake -B "$PS2RECOMP_DIR/out/build" -S "$PS2RECOMP_DIR" \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-13 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_EXE_LINKER_FLAGS="-pthread" \
  -DCMAKE_CXX_FLAGS="-msse4.1"

echo "[5/5] Building ps2_recomp + ps2_analyzer..."
cmake --build "$PS2RECOMP_DIR/out/build" --target ps2_recomp -j"$(nproc)"
cmake --build "$PS2RECOMP_DIR/out/build" --target ps2_analyzer -j"$(nproc)"

echo
echo "Setup complete for $GAME."
echo "  ps2_recomp: $PS2RECOMP_DIR/out/build/ps2xRecomp/ps2_recomp"
echo "  Next:       scripts/03_build_game.sh $GAME_DIR"
