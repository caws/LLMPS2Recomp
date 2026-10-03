---
name: frontier
description: Autonomous frontier-mode for the PS2Recomp decomp loop. Invoke (/frontier or /frontier on) to drive toward the next frontier WITHOUT checking in at each step — investigate, verify, fix, build, test, commit, repeat — stopping only at a real wall, a notable fork, no-progress-in-3, or an irreversible action. /frontier off ends it. Session-scoped (one push per invocation).
---

# Autonomous Frontier Mode

The user has switched on **session-scoped autonomous mode**. Drive the decomp toward the
**next frontier on your own**. Do not return to the user after every step; keep working
the loop until a **STOP condition** below is hit. `/frontier off` (or an interrupt) ends it.
This is a *push within the current thread* — when you stop, hand back with a crisp summary;
the user re-invokes `/frontier` to continue.

This mode changes only your *check-in cadence*. Every hard constraint in
[`operating-manual.md`](../../docs/operating-manual.md), [`working-rules.md`](../../docs/working-rules.md),
CLAUDE.md, and memory **still fully applies**.

## The loop (run it without asking)

1. **Pick the frontier** — the current execution wall / open blocker (from `progress.md`,
   the last run, memory). State it to yourself in one line.
2. **Investigate → form a hypothesis → VERIFY it against ground truth** (disassembly +
   generated C++ + gdb) before acting. A synthesis/note/probe is a hypothesis, not a fact
   (this project has overturned many confident ones). Multiple runs for any noisy metric.
3. **Implement** the fix in the game repo's `src/register_overrides.cpp` only (overrides /
   `functions.csv`; never the runtime/tools repos).
4. **Build** — ONE at a time, via the script, and **wait for `Build complete`** + a valid
   binary before running (don't use `nohup … &` inside `run_in_background` — the ping then
   fires on launch, not completion). Run under `timeout`; read `tmp/run.txt`.
5. **Validate** with stable signals (does the target fn fire? screen-ID? a screenshot?) and
   gdb — validate against the *cause*, not the symptom. Multiple runs.
6. **Commit validated progress** to the GAME repo (strip probes first; focused message with
   evidence; update `progress.md` in the same commit; **NO** co-author/Claude trailer; never
   `push`). A more-correct change that reaches fewer frames but exposes more behaviour still
   counts. Update memory when a durable fact/frontier changes.
7. **Advance** to the next wall and repeat.

## STOP and hand back when

- **A real strategic wall** — the next move is a genuine architecture/strategy decision, not
  a mechanical fix.
- **A notable fork** — two+ plausible approaches with *different risk/trade-offs* (e.g. a
  narrow recover vs. a broad reimplementation). Pause, lay out the options + your
  recommendation, let the user steer — even though you *could* pick a default.
- **No progress in 3 build-iterations** — report what you tried and the ground truth.
- **Anything irreversible / user-only** — `git push`, destructive git, touching the
  runtime/tools repos, or an ambiguous requirement with no sensible default.
- **Environment broken** — build unrecoverable, runner won't run, etc.

Keep going through anything you *can* resolve from ground truth + sensible defaults
(mechanical CSV fixes, override tweaks, build/run/verify cycles, committing clean wins).

## On stop

Hand back with: the frontier reached or wall hit, what landed (commits), the ground-truth
evidence, and the recommended next step (or the fork options). Then wait — do not auto-continue
across the hand-back. `/frontier` again resumes.
