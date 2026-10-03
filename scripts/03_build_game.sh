#!/usr/bin/env bash
set -euo pipefail

# Build a game with the PS2Recomp engine.
#
# Usage: 03_build_game.sh <game_dir> [flags]
#
# <game_dir> is a self-contained per-game repo with this layout:
#   <game_dir>/
#     <ELF>            # path declared in recomp/config.toml `input`
#     recomp/          # config.toml + functions.csv  (provided inputs)
#     src/             # register_overrides.cpp (+ .h) (our overrides)
#     tmp/generated/   # ps2_recomp output            (per-game scratch)
#
# All per-game paths come from recomp/config.toml; nothing about the game is
# hardcoded here. The engine (this repo) only contributes the toolchain + runner.

SKIP_BUILD=false
CHANGED_RECOMP=false
SKIP_REGEN=false
BUILD_TYPE=RelWithDebInfo   # dev default: -O2, no LTO (fast link). --release for LTO.
GAME_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-build)     SKIP_BUILD=true;     shift ;;
        --changed-recomp) CHANGED_RECOMP=true; shift ;;
        --skip-regen)     SKIP_REGEN=true;     shift ;;
        --release)        BUILD_TYPE=Release;  shift ;;
        -*) echo "Unknown option: $1"; exit 1 ;;
        *)  [[ -z "$GAME_DIR" ]] || { echo "Unexpected extra argument: $1"; exit 1; }
            GAME_DIR="$1"; shift ;;
    esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ -n "$GAME_DIR" ]] || { echo "ERROR: no <game_dir> given. Usage: $0 <game_dir> [flags]"; exit 1; }
[[ -d "$GAME_DIR" ]] || { echo "ERROR: game dir not found: $GAME_DIR"; exit 1; }
GAME_DIR="$(cd "$GAME_DIR" && pwd)"   # absolute

PS2RECOMP_ROOT="$ROOT_DIR/tools/PS2Recomp"
PS2_RECOMP_BIN="$PS2RECOMP_ROOT/out/build/ps2xRecomp/ps2_recomp"
RUNTIME_SRC="$PS2RECOMP_ROOT/ps2xRuntime/src/runner"
RUNTIME_INCLUDE="$PS2RECOMP_ROOT/ps2xRuntime/include"

CONFIG="$GAME_DIR/recomp/config.toml"

# Read a quoted string value from the [general] section of the config.
read_cfg() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$CONFIG" | head -1 | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/'; }

echo "========================================="
echo " PS2Recomp Build Pipeline"
echo " game: $GAME_DIR"
echo "========================================="

# --------------------------------------------------
# Validation
# --------------------------------------------------

[[ -d "$PS2RECOMP_ROOT" ]] || { echo "ERROR: PS2Recomp engine not found: $PS2RECOMP_ROOT"; exit 1; }
[[ -x "$PS2_RECOMP_BIN" ]] || { echo "ERROR: ps2_recomp not built (run scripts/01_setup.sh): $PS2_RECOMP_BIN"; exit 1; }
[[ -f "$CONFIG" ]]         || { echo "ERROR: config.toml not found: $CONFIG"; exit 1; }

ELF_REL="$(read_cfg input)"
OUT_REL="$(read_cfg output)"
[[ -n "$ELF_REL" ]] || { echo "ERROR: could not read 'input' from $CONFIG"; exit 1; }
[[ -n "$OUT_REL" ]] || { echo "ERROR: could not read 'output' from $CONFIG"; exit 1; }
ELF="$GAME_DIR/${ELF_REL#./}"
GENERATED="$GAME_DIR/${OUT_REL#./}"

[[ -f "$ELF" ]] || { echo "ERROR: ELF not found (from config 'input'): $ELF"; exit 1; }

# --------------------------------------------------
# Generate code
# --------------------------------------------------

if [[ "$SKIP_REGEN" == true ]]; then
    echo
    echo "[1/3] Skipping recompilation (--skip-regen)"
    if [[ ! -s "$GENERATED/ps2_recompiled_functions.h" ]]; then
        echo "ERROR: --skip-regen requires a prior successful recompilation ($GENERATED is empty or missing)"
        echo "  Run without --skip-regen first to regenerate."
        exit 1
    fi
else
    echo
    echo "[1/3] Generating recompilation output..."

    rm -rf "$GENERATED"
    mkdir -p "$GENERATED"

    # ps2_recomp resolves the config's relative paths (input, ghidra_output,
    # output) against CWD, so run it from the game dir.
    pushd "$GAME_DIR" >/dev/null
    "$PS2_RECOMP_BIN" "recomp/config.toml"
    popd >/dev/null

    # Guard against the silent-crash case: ps2_recomp swallows exceptions and
    # returns 0, leaving generated files empty.
    if [[ ! -s "$GENERATED/ps2_recompiled_functions.h" ]]; then
        echo "ERROR: ps2_recomp generated an empty ps2_recompiled_functions.h"
        echo "  Recompilation silently failed. Runner files were NOT modified."
        echo "  Check stderr above for the actual error."
        exit 1
    fi
fi

# --------------------------------------------------
# Install generated files + overrides into the runner
# --------------------------------------------------

echo
echo "[2/3] Installing generated files + overrides..."

# Copy a file only if new or changed, so cmake can skip unchanged TUs.
install_if_changed() {
    local src="$1" dst_dir="$2"
    local dst="$dst_dir/$(basename "$src")"
    if [[ ! -f "$dst" ]] || ! cmp -s "$src" "$dst"; then
        cp "$src" "$dst"
    fi
}

if [[ "$CHANGED_RECOMP" == true ]]; then
    # Smart install: only touch changed files. Remove stale runner files that are
    # no longer in the game's generated/ or src/ overrides.
    for f in "$RUNTIME_SRC"/*.cpp; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        if [[ ! -f "$GENERATED/$base" ]] && [[ ! -f "$GAME_DIR/src/$base" ]]; then
            echo "  removing stale: $base"
            rm "$f"
        fi
    done

    for f in "$GENERATED"/*.cpp "$GENERATED"/*.h; do
        [[ -f "$f" ]] || continue
        if [[ "${f##*.}" == "h" ]]; then install_if_changed "$f" "$RUNTIME_INCLUDE"
        else                              install_if_changed "$f" "$RUNTIME_SRC"; fi
    done
else
    # Full install: wipe runner and copy everything (clean slate).
    find "$RUNTIME_SRC" -maxdepth 1 -name "*.cpp" -delete
    cp "$GENERATED"/*.cpp "$RUNTIME_SRC"/
    cp "$GENERATED"/*.h "$RUNTIME_INCLUDE"/
fi

# Always install the game's overrides (they change independently of the recomp).
for f in "$GAME_DIR/src/"*.cpp; do install_if_changed "$f" "$RUNTIME_SRC"; done
for f in "$GAME_DIR/src/"*.h;   do [[ -f "$f" ]] && install_if_changed "$f" "$RUNTIME_INCLUDE"; done

# --------------------------------------------------
# Build runner
# --------------------------------------------------

if [ "$SKIP_BUILD" = false ]; then
    echo
    echo "[3/3] Building ps2EntryRunner..."
    echo "    build type: $BUILD_TYPE"
    cmake -S "$PS2RECOMP_ROOT" -B "$PS2RECOMP_ROOT/out/build" \
        -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
    cmake --build "$PS2RECOMP_ROOT/out/build" \
        --target ps2EntryRunner \
        --config "$BUILD_TYPE" \
        -j "$(nproc)"

    # Move (not copy) the runner into the game's tmp/ so each game keeps its own
    # binary — there is no stale shared runner to clash with another game being
    # decompiled. The engine build dir is left with no binary, so the next build
    # always relinks fresh (static recomp = one game per binary anyway).
    BUILT_RUNNER="$PS2RECOMP_ROOT/out/build/ps2xRuntime/ps2EntryRunner"
    GAME_RUNNER="$GAME_DIR/tmp/ps2EntryRunner"
    mkdir -p "$GAME_DIR/tmp"
    mv -f "$BUILT_RUNNER" "$GAME_RUNNER"

    echo
    echo "Build complete."
    echo
    echo "Runner:  $GAME_RUNNER"
    echo "Run:     scripts/04_run_game.sh $GAME_DIR"
else
    echo
    echo "[3/3] Skipping build (--skip-build)"
fi
