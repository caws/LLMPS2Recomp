#!/usr/bin/env bash
# Shared by 03_build_game.sh / 04_run_game.sh / verify_disc.sh: those scripts moved into
# the PS2Recomp fork (scripts/ps2x-*.sh) so every game repo can build with ONE command and no engine.
# The engine keeps its familiar names as thin wrappers: find the game's toolchain clone
# (tools/<game>/PS2Recomp, made by 01_setup.sh) and exec the fork's script with the same arguments.
# Usage (from a wrapper): source this, then: fork_exec <fork-script-name> "$@"
fork_exec() {
    local script="$1"; shift
    local game_dir="" a
    for a in "$@"; do case "$a" in -*) ;; *) game_dir="$a"; break ;; esac; done
    [[ -n "$game_dir" ]] || { echo "ERROR: no <game_dir> given (usage: $(basename "$0") <game_dir> ...)" >&2; exit 64; }
    [[ -d "$game_dir" ]] || { echo "ERROR: game dir not found: $game_dir" >&2; exit 64; }
    local engine; engine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    local fork="$engine/tools/$(basename "$(cd "$game_dir" && pwd)")/PS2Recomp"
    local target="$fork/scripts/$script"
    [[ -x "$target" ]] || { echo "ERROR: $target not found -- run scripts/01_setup.sh <game_dir> (or the clone predates the scripts move, fork)" >&2; exit 1; }
    exec "$target" "$@"
}
