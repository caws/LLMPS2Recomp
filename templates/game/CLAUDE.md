# @GAME@ — agent guide (a PS2Recomp game repo)

> **▶ BINDING FIRST STEP: before ANY work, read the engine's
> [`docs/operating-manual.md`](@ENGINE_REL@/docs/operating-manual.md) and
> [`docs/working-rules.md`](@ENGINE_REL@/docs/working-rules.md), then the engine's
> [`CLAUDE.md`](@ENGINE_REL@/CLAUDE.md) and this repo's [`docs/NEXT.md`](docs/NEXT.md).**
> The engine (`LLMPS2Recomp`) is a separate repo, usually the sibling folder `@ENGINE_REL@`;
> `.claude/settings.local.json` names it in `PS2RECOMP_ENGINE`. If it isn't there, ask the user
> where it is. A `SessionStart` hook in `.claude/settings.json` injects this directive every session.

This repo is the **per-game repo** for *@GAME@*: the ELF, `recomp/` (config + functions.csv),
`src/` (the faithful base game: control plane + override hook bodies), `mods/`, `docs/`. The
**engine** holds the recompiler scripts, the doctrine (`docs/`), the skills (`/frontier`,
`/ps2recomp-fix-next-crash`, …) and, under `tools/@GAME@/PS2Recomp`, this game's clone of the
private PS2Recomp fork. This repo follows the engine's `CLAUDE.md` in full; the notes below only
say how it applies **when the session's cwd is this repo**.

## Working from this repo

- **Build / run** go through the wrappers here (they forward to the engine using
  `PS2RECOMP_ENGINE`):
  ```
  scripts/build.sh                              # full: regen → install → build
  scripts/build.sh --skip-regen --changed-recomp   # override-only change (~110 s)
  timeout 20 scripts/run.sh run.txt             # log → tmp/run.txt (agent runs MUST log)
  ```
  Builds run in the **background** (`nohup scripts/build.sh … > tmp/build.log 2>&1 & disown`),
  poll for `tmp/.build_status`, then verify the binary: `strings @GAME@ | grep -oE 'bld-[a-z0-9-]+'`
  must show the `BUILD_TAG` you just bumped. The engine scripts may also be called directly by
  **absolute** path (`$PS2RECOMP_ENGINE/scripts/03_build_game.sh $PWD …`); a relative
  `scripts/03_build_game.sh` does not exist here.
- **Helpers** live in the engine and take the game explicitly (`PS2RECOMP_GAME` is set for you):
  `python3 $PS2RECOMP_ENGINE/.claude/skills/ps2recomp-fix-next-crash/funcs.py disasm 0x…`,
  `… check_registrations.py --game $PWD` after ANY control-plane change.
- **Skills** (`/frontier`, `/ps2recomp-fix-next-crash`, `/ps2recomp-toolchain-migration`) and the
  re-fanout workflow are the engine's; `.claude/skills` and `.claude/workflows` here are symlinks to
  them, so they work as usual.
- **Recompiler / runtime changes** are made in the fork clone
  `$PS2RECOMP_ENGINE/tools/@GAME@/PS2Recomp` (its own git repo): one focused commit there with a
  PCSX2 citation and a row in its `docs/llmps2recomp-patches.md`; bump `recomp/runtime.lock` here
  in the same change. **Never push.** Engine changes (scripts, docs, skills) are edited and
  committed in the engine repo the same way, by path.
- **Where findings go.** Game facts, frontiers, and the resume pointer: this repo's `docs/`
  (`NEXT.md` = the single resume entry, overwritten each cycle; `progress.md` = the journal;
  `elf.md` = static facts). **Generic lessons** (a technique, a trap, a runtime fix that any game
  needs) go to the engine's `docs/` or the fork, never only here.
- **Scope**: mutate only this repo, the engine repo and the fork clone. Nothing else, ever.
  Local commits on validated progress are fine; never push without explicit approval.
