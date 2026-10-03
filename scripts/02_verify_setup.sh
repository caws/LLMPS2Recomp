#!/usr/bin/env bash
set -euo pipefail

# Verify the engine is ready to build games.
#
# Usage: 02_verify_setup.sh [game_dir]
#   no args    -> check host prerequisites + the PS2Recomp toolchain
#   game_dir   -> also check that the game repo provides the required inputs

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS2RECOMP_ROOT="$ROOT_DIR/tools/PS2Recomp"
PS2_RECOMP_BIN="$PS2RECOMP_ROOT/out/build/ps2xRecomp/ps2_recomp"

GAME_DIR="${1:-}"

PASS=0
FAIL=0

check() {  # check <test> <description>   — <test> is a path; pass if it exists
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
echo
echo "Toolchain (built by scripts/01_setup.sh):"
check "$PS2RECOMP_ROOT" "PS2Recomp repository cloned"
check "$PS2_RECOMP_BIN" "ps2_recomp executable built"

if [[ -n "$GAME_DIR" ]]; then
    echo
    echo "Game repo: $GAME_DIR"
    if [[ -d "$GAME_DIR" ]]; then
        CONFIG="$GAME_DIR/recomp/config.toml"
        check "$CONFIG"                       "recomp/config.toml"
        check "$GAME_DIR/recomp/functions.csv" "recomp/functions.csv"
        check "$GAME_DIR/src"                  "src/ (overrides)"
        if [[ -f "$CONFIG" ]]; then
            ELF_REL="$(grep -E "^[[:space:]]*input[[:space:]]*=" "$CONFIG" | head -1 | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
            check "$GAME_DIR/${ELF_REL#./}"    "ELF present (config 'input': ${ELF_REL:-?})"
        fi
    else
        check "$GAME_DIR" "game dir exists"
    fi
fi

echo
if [[ $FAIL -eq 0 ]]; then
    echo "Verification passed ($PASS checks)."
    exit 0
else
    echo "Verification failed ($FAIL failure(s), $PASS pass(es))."
    [[ ! -x "$PS2_RECOMP_BIN" ]] && echo "  -> run scripts/01_setup.sh to build the toolchain."
    exit 1
fi
