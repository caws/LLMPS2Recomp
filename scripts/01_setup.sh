#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/tools"
PS2RECOMP_DIR="$TOOLS_DIR/PS2Recomp"

echo "=== LOTRDecomp Setup ==="

mkdir -p "$TOOLS_DIR"

if [[ ! -d "$PS2RECOMP_DIR" ]]; then
    echo "[1/4] Cloning PS2Recomp..."
    git clone https://github.com/ran-j/PS2Recomp.git "$PS2RECOMP_DIR"
else
    echo "[1/4] PS2Recomp already exists, skipping clone."
fi

echo "[2/4] Checking prerequisites..."

for cmd in git cmake; do
    command -v "$cmd" >/dev/null || {
        echo "ERROR: Missing required command: $cmd"
        exit 1
    }
done

if ! command -v g++-13 >/dev/null 2>&1; then
    echo "ERROR: g++-13 is required."
    echo "Install it before continuing."
    exit 1
fi

echo "[3/4] Configuring PS2Recomp..."

cd "$PS2RECOMP_DIR"

#cmake -B out/build \
#    -DCMAKE_C_COMPILER=/usr/bin/gcc-13 \
#    -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
#    -DCMAKE_EXE_LINKER_FLAGS="-pthread"
    
cmake -B out/build \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-13 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_EXE_LINKER_FLAGS="-pthread" \
  -DCMAKE_CXX_FLAGS="-msse4.1"    

echo "[4/4] Building ps2_recomp..."

cmake --build out/build --target ps2_recomp -j"$(nproc)"

echo
echo "Setup complete."
echo
echo "ps2_recomp:"
echo "  $PS2RECOMP_DIR/out/build/ps2xRecomp/ps2_recomp"
