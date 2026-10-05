Templates for `scripts/00_bootstrap_game.sh <game_dir>`: the files a per-game repo needs so a
Claude Code session can be opened **from the game repo** while relying on this engine (the
"oracle" model). Placeholders: `@GAME@` (basename of the game dir), `@ENGINE_REL@` (engine path
relative to the game dir), `@ENGINE_ABS@`, `@GAME_ABS@`, `@MEMORY_DIR@` (this engine's auto-memory
folder, shared with the game session).

- `CLAUDE.md` and `.claude/settings.json` — committed in the game repo (machine-neutral).
- `.claude/settings.local.json` — gitignored (absolute paths, env, permissions).
- `.claude/skills`, `.claude/workflows` — created as relative symlinks to the engine's.
