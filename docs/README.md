# PS2Recomp methodology — pushing a decomp forward

This folder documents the **reusable, game-agnostic** strategies for taking a PS2Recomp
game decompilation from "won't boot" toward "runs". The engine is stateless and ships no
game (see the top-level [README](../README.md)); these are the techniques you apply to
**any** game repo you point it at. They were distilled from real boot-up work and are the
"orchestrator" knowledge a new contributor (or their coding agent) needs.

The unit of work is **one frontier per cycle**: the boot reaches some point and stops — a
spin, an early exit, a missing-function storm, or a silent 0%-CPU hang — and you diagnose
*that* frontier, apply the narrowest fix, rebuild, and advance. **Fix incrementally; never
mass-rewrite the inputs.**

## The core loop

1. **Build** — `scripts/03_build_game.sh <game_dir>` (full if `recomp/functions.csv`
   changed; `--skip-regen --changed-recomp` for override-only changes). Run it in the
   background (builds take many minutes); watch with `scripts/build_progress.sh <game_dir>`.
2. **Run** — `timeout N scripts/04_run_game.sh <game_dir> run.txt` → log at `<game_dir>/tmp/run.txt`
   (a spinning runner emits hundreds of MB/s, so it always goes to a file, never the
   terminal).
3. **Triage** — read the log: a spin (huge file), a missing-function storm (`Function at
   address ... not found`), an early exit, or a silent hang (0% CPU)?
4. **Locate** — `funcs.py` (disasm / bounds / find / scan) for static facts;
   [gdb-under-parent](debugging.md) for a live hang.
5. **Fix** — a [`functions.csv` correction](functions-csv.md) or an
   [override hook](overrides.md). **Bump the `BUILD_TAG`** so you can prove the running
   binary is current.
6. **Verify & record** — confirm the tag in `run.txt`, confirm the frontier advanced, then
   log it in the game repo's `docs/progress.md`. Repeat.

## The documents

- **[operating-manual.md](operating-manual.md)** — *the top-level playbook*: how the main agent
  operates — the role, the one-frontier work loop, the toolbox and when to use each, the
  **verification rule** (ground truth = generated recompiled C++ AND disassembly, co-equal — cross-check
  both, then validate with gdb; everything else is a hypothesis), what progress is and isn't, when to spawn read-only subagents, and when to
  stop and consult. **Read this first.**
- **[working-rules.md](working-rules.md)** — *how to work*: verify-don't-trust (disasm is ground
  truth, notes are hypotheses), the dispatch model, the `BUILD_TAG`/incremental/build-% discipline,
  and a quick index of the bug/fix taxonomy.
- **[debugging.md](debugging.md)** — diagnosing a frontier: run-log triage, the
  **gdb-under-parent** recipe (the only reliable way to backtrace a hung runner on a
  `ptrace_scope=1` box), one-shot override probes, and profiling a silent spin.
- **[functions-csv.md](functions-csv.md)** — the three `functions.csv` bug classes
  (**truncated**, **missing / CSV-gap**, **over-bound**) and their fixes, plus
  **disasm-as-ground-truth** validation (the game ELF is typically stripped — there are no
  symbols to trust, so the disassembly *is* the source of truth).
- **[overrides.md](overrides.md)** — HLE override patterns: wait-free replacement, clean
  skip, SIF/IOP handshake fakes, **answering raw-transport SIF RPCs via the runtime stub**,
  resumable mid-function entries, calling the original, and the `BUILD_TAG` discipline.
- **[vu1-jit.md](vu1-jit.md)** — *the VU1 performance arc*: why the interpreter cannot reach a
  playable frame rate (measured budget + ablation decomposition of its cost), what has landed,
  what has been **disproven** (don't re-attempt the FMAC shortcut), and the two concrete next
  steps — **lazy flags** (bit-exact ~1.37×, with the audited VU0/VU1 flag-visibility asymmetry
  that makes it safe) and the **block JIT**. Also carries the measurement discipline this arc
  needs: iterate on ns/pair, ablate to size a target, and never pick one from profile leaf share.
- **[gl-renderer.md](gl-renderer.md)** — *the GL renderer arc*: why higher resolution and
  playability are the same project (an upscaled target cannot live in the emulated 4 MB VRAM), the
  measured floor that rules out single-sided CPU work, why the two earlier GPU attempts failed under
  a bit-exactness constraint we have now dropped, the render-target/texture-cache authority model
  (mirroring PCSX2's `GSTextureCache`), and the env-gated phase plan. **Design only — not built.**
- **[workflows.md](workflows.md)** — *parallel static investigation*: the read-only
  multi-agent RE fan-out (N agents chase sub-questions → one synthesizes a buildable
  override). Use when a frontier is an **architecture question**, not a CSV/override bug.
  Sub-agents are READ-ONLY; **only the main agent builds**. Reusable template at
  `.claude/workflows/re-fanout.js`.
- **[upstream-wiki-reference.md](upstream-wiki-reference.md)** — distilled reference of the
  **ran-j/PS2Recomp wiki**: the analyzer→recompiler→runtime pipeline, the full TOML config
  schema (incl. `[patches].instructions` and `skip`=startup-code), the game-override-hook
  API, the runtime memory/dispatch model, and the stripped-game playbook — annotated with how
  each maps to our setup. Upstream's *intended* behavior; secondary to our own ground truth.
- **[resources.md](resources.md)** — external PS2 references: **ps2tek** (hardware spec) and the
  **PCSX2 source** (the reference implementation for *how* a subsystem decodes/behaves). When an
  override must reimplement hardware the runtime stubs (e.g. IPU `ipum` texture decode, a DMA
  quirk), use these to understand the architecture and mirror PCSX2's proven implementation
  rather than guessing — cite the file/function you mirrored. Still a hypothesis until verified
  against our generated C++ + disasm + gdb.

## Hard constraints (apply everywhere)

- **Never edit `tools/PS2Recomp/`** — it's a separate upstream repo, and
  `tools/PS2Recomp/ps2xRuntime/src/runner/` is wiped + regenerated every build. All
  game-specific behavior goes in the game repo's `src/register_overrides.cpp`.
- **Inputs are provided per game** — `recomp/config.toml`, `recomp/functions.csv`, the ELF,
  and `gamefiles/`. The engine never generates them.
- **A `functions.csv` change requires a FULL regen** (no `--skip-regen`). Override-only
  changes can use the fast path.

For the condensed, agent-facing version of this loop, see the
[`ps2recomp-fix-next-crash` skill](../.claude/skills/ps2recomp-fix-next-crash/SKILL.md) and
its `funcs.py` helper.
