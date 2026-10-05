#!/usr/bin/env bash
# Thin wrapper: the real script is the PS2Recomp fork's scripts/ps2x-build-game.sh, so game repos can
# build without the engine. Same arguments as before; see that script for usage.
source "$(dirname "${BASH_SOURCE[0]}")/_fork_wrapper.sh"
fork_exec ps2x-build-game.sh "$@"
