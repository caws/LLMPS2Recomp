#!/usr/bin/env bash

# --- ENGINE-DIR GUARD (added after hitting this three times in one session) -------------------
# scripts/ exists ONLY in the engine repo. A compound command that starts `cd <game_dir> && ...`
# leaves the shell in the GAME dir, where `scripts/03_build_game.sh` does not exist -- the failure
# is `No such file or directory` from nohup, which looks like a missing script rather than a wrong
# cwd. Re-exec from the engine dir instead of failing.
__ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$PWD" != "$__ENGINE_DIR" ]; then
    cd "$__ENGINE_DIR" || { echo "cannot cd to engine dir $__ENGINE_DIR" >&2; exit 1; }
fi
# ---------------------------------------------------------------------------------------------

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
GAME="$(basename "$GAME_DIR")"        # per-game toolchain clone lives at tools/<game>/PS2Recomp

# Completion marker: written on EVERY exit path (success, build failure, validation error) via
# the trap below, containing the exit code. `pgrep -f 03_build_game.sh` is NOT a reliable
# "is it still running" check — if the poll command itself is a wrapper shell whose command-line
# contains the literal string "03_build_game.sh" (e.g. `until ! pgrep -f 03_build_game.sh; do ...`),
# pgrep matches ITS OWN wrapper and the loop never sees the real process exit. Poll for this file
# instead: `until [[ -f <game_dir>/tmp/.build_status ]]; do sleep 5; done; cat <game_dir>/tmp/.build_status`
# (0 = success). Removed up front so a stale marker from a previous run can never be mistaken for
# this run's result.
mkdir -p "$GAME_DIR/tmp"
rm -f "$GAME_DIR/tmp/.build_status"
trap 'echo "$?" > "$GAME_DIR/tmp/.build_status"' EXIT

PS2RECOMP_ROOT="$ROOT_DIR/tools/$GAME/PS2Recomp"
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

# The game's overrides may be organized into SUBFOLDERS (src/overrides/..., src/hle/..., etc.),
# but the runner's CMake glob is FLAT and non-recursive ("src/runner/*.cpp", and headers are
# included as "foo.h" from a single include dir), so we collect the tree recursively and install
# by BASENAME. Two files sharing a basename would flatten onto each other and one would silently
# vanish from the build — so that is a hard error, not a warning. Same for a basename that
# collides with a generated file (ours would clobber the recompiled function, or vice versa).
GAME_SRC_FILES=()
while IFS= read -r -d '' f; do GAME_SRC_FILES+=("$f"); done \
    < <(find "$GAME_DIR/src" -type f \( -name '*.cpp' -o -name '*.h' \) -print0 | sort -z)

declare -A GAME_SRC_BY_BASE=()
for f in "${GAME_SRC_FILES[@]}"; do
    base="$(basename "$f")"
    if [[ -n "${GAME_SRC_BY_BASE[$base]:-}" ]]; then
        echo "ERROR: duplicate override basename '$base' — the flat install would clobber one:"
        echo "    ${GAME_SRC_BY_BASE[$base]}"
        echo "    $f"
        echo "  Override sources are installed into the runner by basename; keep them unique."
        exit 1
    fi
    if [[ -f "$GENERATED/$base" ]]; then
        echo "ERROR: override '$f' collides with generated file '$GENERATED/$base'."
        echo "  Rename the override; it would overwrite a recompiled function."
        exit 1
    fi
    GAME_SRC_BY_BASE[$base]="$f"
done

if [[ "$CHANGED_RECOMP" == true ]]; then
    # Smart install: only touch changed files. Remove stale runner files that are
    # no longer in the game's generated/ or src/ overrides. NOTE: the override check is
    # against the recursive basename map above — testing "$GAME_DIR/src/$base" would treat
    # every file living in an src/ SUBFOLDER as stale and delete it on each fast build.
    for f in "$RUNTIME_SRC"/*.cpp; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        if [[ ! -f "$GENERATED/$base" ]] && [[ -z "${GAME_SRC_BY_BASE[$base]:-}" ]]; then
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

# Always install the game's overrides (they change independently of the recomp). Flattened by
# basename from the whole src/ tree (see GAME_SRC_FILES above): .cpp -> runner/, .h -> include/.
OVERRIDE_CPP_BASENAMES=()
for f in "${GAME_SRC_FILES[@]}"; do
    if [[ "${f##*.}" == "h" ]]; then install_if_changed "$f" "$RUNTIME_INCLUDE"
    else                             install_if_changed "$f" "$RUNTIME_SRC"
                                     OVERRIDE_CPP_BASENAMES+=("$(basename "$f")"); fi
done

# Tell the runtime's CMake which runner sources are hand-written game overrides, so it can keep them
# OUT of the unity build (engine patch 08). Without this, adding/removing an override module
# re-shuffles the unity batches and forces a near-full rebuild, and file-scope statics from separate
# modules can collide inside a shared unity TU. Written via install_if_changed semantics (only
# rewritten when the set changes) so an unchanged manifest never triggers a cmake reconfigure.
MANIFEST_TMP="$(mktemp)"
printf '%s\n' "${OVERRIDE_CPP_BASENAMES[@]}" > "$MANIFEST_TMP"
MANIFEST="$RUNTIME_SRC/game_overrides.manifest"
if [[ ! -f "$MANIFEST" ]] || ! cmp -s "$MANIFEST_TMP" "$MANIFEST"; then
    mv "$MANIFEST_TMP" "$MANIFEST"
    echo "  override manifest updated (${#OVERRIDE_CPP_BASENAMES[@]} module(s), excluded from unity build)"
else
    rm -f "$MANIFEST_TMP"
fi

# --------------------------------------------------
# Build runner
# --------------------------------------------------

if [ "$SKIP_BUILD" = false ]; then
    echo
    echo "[3/3] Building ps2EntryRunner..."
    echo "    build type: $BUILD_TYPE  (jobs: ${BUILD_JOBS:-6})"
    # Remove the game's runnable binary BEFORE building, so a failed/incomplete build can't
    # leave a STALE ps2EntryRunner that 04_run_game.sh would silently run as if it were fresh.
    # The new binary is mv'd into place only after a successful cmake build below; if the build
    # fails, tmp/ps2EntryRunner is simply absent (04 errors out) rather than running old code.
    rm -f "$GAME_DIR/tmp/ps2EntryRunner"
    # Link with lld — the ~900MB runner relinks in seconds vs minutes on GNU ld.
    # Fallback if lld misbehaves: -fuse-ld=gold, or drop the flag entirely.
    # NOTE: debug info (-g, from RelWithDebInfo) is kept on purpose — gdb on the runner
    # is part of the workflow. Jobs capped (default 6) to avoid swap/OOM thrash on this
    # 8-core/15GB box; override with BUILD_JOBS=N.
    cmake -S "$PS2RECOMP_ROOT" -B "$PS2RECOMP_ROOT/out/build" \
        -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
        -DCMAKE_EXE_LINKER_FLAGS="-pthread -fuse-ld=lld"
    cmake --build "$PS2RECOMP_ROOT/out/build" \
        --target ps2EntryRunner \
        --config "$BUILD_TYPE" \
        -j "${BUILD_JOBS:-6}"

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
