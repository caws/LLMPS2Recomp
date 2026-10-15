#!/usr/bin/env bash
# Where a game's PS2Recomp toolchain clone lives. Sourced by every script that needs it, so the rule
# is stated once:
#
#   <game_dir>/.toolchain/PS2Recomp        THE RULE: inside the game repo, gitignored there. The game
#                                          carries its own toolchain, so removing the game removes it. It is
#                                          the SAME place a player's build clones to, so a developer's clone
#                                          (with unpushed fork work) and a player's are one directory.
#   <engine>/tools/<game>/PS2Recomp        LEGACY, and it WINS whenever it exists: a development clone made
#                                          before the rule. rotk_recomp is one, and it ALSO has a stale player
#                                          clone in .toolchain/ (its own scripts/_toolchain.sh sends players
#                                          there), so preferring .toolchain/ would build ROTK against the wrong
#                                          clone. Moving a legacy clone costs a full rebuild (the CMake build
#                                          dir and sccache key on absolute paths).
#
# Usage: source this, then: dir="$(toolchain_dir <game_dir>)"  (game_dir must exist; any form)
toolchain_dir() {
    local game_abs; game_abs="$(cd "$1" && pwd)"
    local engine; engine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    local current="$game_abs/.toolchain/PS2Recomp"
    local legacy="$engine/tools/$(basename "$game_abs")/PS2Recomp"
    if [[ -d "$legacy" ]]; then echo "$legacy"; else echo "$current"; fi
}
