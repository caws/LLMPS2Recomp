#!/usr/bin/env bash
set -uo pipefail

# Verify everything is ready before building a game.
#
# Usage: 02_verify_setup.sh [game_dir]
#   no args    -> check host prerequisites + which game toolchains are built
#   game_dir   -> also check that game's toolchain, its provided inputs, and the
#                 player's disc copy (scripts/verify_disc.sh)
#
#   --deep     passed through to the disc check: checksum every file on the disc
#              copy, not just the executable. Reads the whole copy (~2.3 GB).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GAME_DIR=""; DEEP=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --deep) DEEP="--deep"; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 64 ;;
        *)  GAME_DIR="$1"; shift ;;
    esac
done

PASS=0
FAIL=0

check() {  # check <path> <description>  — pass if the path exists
    if [[ -e "$1" ]]; then printf "  [PASS] %s\n" "$2"; PASS=$((PASS + 1))
    else                   printf "  [FAIL] %s\n" "$2"; FAIL=$((FAIL + 1)); fi
}
check_cmd() {  # check_cmd <command> <description>
    if command -v "$1" >/dev/null 2>&1; then printf "  [PASS] %s\n" "$2"; PASS=$((PASS + 1))
    else                                      printf "  [FAIL] %s\n" "$2"; FAIL=$((FAIL + 1)); fi
}

echo "========================================="
echo " PS2Recomp Engine Verification"
echo "========================================="
echo
echo "Host prerequisites:"
check_cmd git    "git"
check_cmd cmake  "cmake"
check_cmd g++-13 "g++-13 (C++ compiler)"
check_cmd sha256sum "sha256sum (disc verification)"

# The toolchain is cloned and built PER GAME, at tools/<game>/PS2Recomp, so that builds
# for different games never collide and each clone can be re-pulled independently.
echo
if [[ -n "$GAME_DIR" ]]; then
    GAME="$(basename "${GAME_DIR%/}")"
    PS2RECOMP_ROOT="$ROOT_DIR/tools/$GAME/PS2Recomp"
    echo "Toolchain for '$GAME' (built by scripts/01_setup.sh $GAME_DIR):"
    check "$PS2RECOMP_ROOT"                                          "tools/$GAME/PS2Recomp cloned"
    check "$PS2RECOMP_ROOT/out/build/ps2xRecomp/ps2_recomp"          "ps2_recomp executable built"
else
    echo "Toolchains built so far (one per game, from scripts/01_setup.sh):"
    found=0
    for d in "$ROOT_DIR"/tools/*/PS2Recomp; do
        [[ -d "$d" ]] || continue
        found=1
        g="$(basename "$(dirname "$d")")"
        if [[ -x "$d/out/build/ps2xRecomp/ps2_recomp" ]]; then
            printf "  [PASS] %s\n" "$g"; PASS=$((PASS + 1))
        else
            printf "  [FAIL] %s (cloned, but ps2_recomp is not built)\n" "$g"; FAIL=$((FAIL + 1))
        fi
    done
    [[ $found -eq 1 ]] || echo "  (none yet — run scripts/01_setup.sh <game_dir>)"
fi

DISC_RC=0
if [[ -n "$GAME_DIR" ]]; then
    echo
    echo "Game repo: $GAME_DIR"
    if [[ -d "$GAME_DIR" ]]; then
        CONFIG="$GAME_DIR/recomp/config.toml"
        check "$CONFIG"                        "recomp/config.toml"
        check "$GAME_DIR/recomp/functions.csv" "recomp/functions.csv"
        check "$GAME_DIR/src"                  "src/ (overrides)"

        # The disc copy the player supplies. Its own script, because 03 and 04 run the
        # same check as a preflight — a build or a run against the wrong disc fails far
        # from the cause, so it is worth naming up front.
        echo
        "$ROOT_DIR/scripts/verify_disc.sh" "$GAME_DIR" $DEEP
        DISC_RC=$?
        case $DISC_RC in
            0) PASS=$((PASS + 1)) ;;
            5) ;;                      # no manifest: cannot verify, already warned
            *) FAIL=$((FAIL + 1)) ;;
        esac
    else
        check "$GAME_DIR" "game dir exists"
    fi
fi

echo
if [[ $FAIL -eq 0 ]]; then
    echo "Verification passed ($PASS checks)."
    [[ $DISC_RC -eq 5 ]] && echo "  (the disc copy could not be verified — see the warning above)"
    exit 0
else
    echo "Verification failed ($FAIL failure(s), $PASS pass(es))."
    if [[ -n "$GAME_DIR" ]] && [[ ! -x "$ROOT_DIR/tools/$(basename "${GAME_DIR%/}")/PS2Recomp/out/build/ps2xRecomp/ps2_recomp" ]]; then
        echo "  -> run scripts/01_setup.sh $GAME_DIR to build the toolchain."
    fi
    exit 1
fi
