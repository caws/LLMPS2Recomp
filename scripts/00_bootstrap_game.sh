#!/usr/bin/env bash
# Bootstrap a per-game repo so a Claude Code session can be opened FROM the game repo while
# relying on this engine (the "oracle" model): CLAUDE.md, .claude/settings.json (SessionStart
# hook), .claude/settings.local.json (env + permissions + shared auto-memory, gitignored),
# and .claude/{skills,workflows} symlinks to the engine's. Also drops in scripts/build.sh and
# scripts/run.sh wrappers if the game has none.
#
#   scripts/00_bootstrap_game.sh <game_dir> [--force]
#
# Existing files are left alone unless --force is given (settings.local.json is always
# rewritten: it holds machine-specific absolute paths). Run 01_setup.sh afterwards for a new game.
set -euo pipefail

ENGINE_ABS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -ge 1 ]] || { echo "usage: $0 <game_dir> [--force]" >&2; exit 2; }
GAME_ABS="$(cd "$1" && pwd)"
FORCE=0; [[ "${2:-}" == "--force" ]] && FORCE=1
GAME="$(basename "$GAME_ABS")"
ENGINE_REL="$(realpath --relative-to="$GAME_ABS" "$ENGINE_ABS")"
# Claude Code keys auto-memory by the project path; share THIS engine's memory with the game session.
MEMORY_DIR="$HOME/.claude/projects/$(echo "$ENGINE_ABS" | sed 's#/#-#g')/memory"
TPL="$ENGINE_ABS/templates/game"

subst() {  # subst <template> <dest>
    sed -e "s#@GAME@#$GAME#g" -e "s#@ENGINE_REL@#$ENGINE_REL#g" -e "s#@ENGINE_ABS@#$ENGINE_ABS#g" \
        -e "s#@GAME_ABS@#$GAME_ABS#g" -e "s#@MEMORY_DIR@#$MEMORY_DIR#g" "$1" > "$2"
    echo "wrote   $2"
}
put() {    # put <template> <dest>  (skip if present unless --force)
    if [[ -e "$2" && $FORCE -eq 0 ]]; then echo "kept    $2 (exists; --force to overwrite)"; else subst "$1" "$2"; fi
}
link() {   # link <target-rel> <dest>
    if [[ -L "$2" ]]; then rm "$2"; elif [[ -e "$2" ]]; then echo "kept    $2 (exists, not a symlink)"; return; fi
    ln -s "$1" "$2"; echo "linked  $2 -> $1"
}

mkdir -p "$GAME_ABS/.claude" "$GAME_ABS/scripts" "$GAME_ABS/docs" "$GAME_ABS/tmp"
put  "$TPL/CLAUDE.md"                    "$GAME_ABS/CLAUDE.md"
put  "$TPL/.claude/settings.json"        "$GAME_ABS/.claude/settings.json"
subst "$TPL/.claude/settings.local.json" "$GAME_ABS/.claude/settings.local.json"
# symlinks live in .claude/, so their targets are relative to THAT dir
DOT_REL="$(realpath --relative-to="$GAME_ABS/.claude" "$ENGINE_ABS")"
link "$DOT_REL/.claude/skills"    "$GAME_ABS/.claude/skills"
link "$DOT_REL/.claude/workflows" "$GAME_ABS/.claude/workflows"
for w in build run; do
    if [[ ! -e "$GAME_ABS/scripts/$w.sh" ]]; then
        cp "$TPL/scripts/$w.sh" "$GAME_ABS/scripts/$w.sh"; chmod +x "$GAME_ABS/scripts/$w.sh"; echo "wrote   $GAME_ABS/scripts/$w.sh"
    fi
done
[[ -e "$GAME_ABS/docs/NEXT.md" ]] || { printf '# NEXT — resume here\n\n(nothing yet: run 01_setup.sh, then 03_build_game.sh, and record the first frontier)\n' > "$GAME_ABS/docs/NEXT.md"; echo "wrote   $GAME_ABS/docs/NEXT.md"; }
GI="$GAME_ABS/.gitignore"; touch "$GI"
grep -q '^\.claude/settings\.local\.json$' "$GI" || { printf '\n# Claude Code: machine-specific env/permissions (scripts/00_bootstrap_game.sh regenerates it)\n.claude/settings.local.json\n' >> "$GI"; echo "updated $GI"; }
[[ -d "$MEMORY_DIR" ]] || echo "note: memory dir $MEMORY_DIR does not exist yet (created by the first engine session)"
echo "done. Open a session with: cd $GAME_ABS && claude"
