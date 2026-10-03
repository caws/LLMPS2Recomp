# Multi-agent RE workflows — parallel static investigation

Some frontiers are not a single bug to fix but a **question to answer** about how the game
is *architected*: "what sequences the menu construction?", "what naturally invokes this
pump?", "where do these vtables come from?". One agent reading one call-chain at a time is
slow and serial. A **read-only multi-agent workflow** fans the question out: N agents each
chase a different sub-question against the *same* disasm/generated-code ground truth, in
parallel, then one synthesis agent reconciles them into a concrete next override.

This is the **investigation** counterpart to the build-run-fix loop. It produces a *plan*
(which guest fn to hook, what to read/write/return, the gate) — it never builds. Use it when
the architecture is the blocker, not a truncated CSV row.

## The hard rule: sub-agents are READ-ONLY; only the main agent builds

A build regenerates `tools/<game>/PS2Recomp/ps2xRuntime/src/runner/` and the per-game
`tmp/generated/` — **two concurrent builds collide** (and a run reads a half-installed
binary). So workflow sub-agents get **static RE tools only**:

- `funcs.py` (`disasm` / `bounds` / `find` / `scan`), `Grep`/`Read` over `tmp/generated/*.cpp`
  and `recomp/`, `readelf`, ad-hoc `python` ELF scans.
- **NEVER** `scripts/03_build_game.sh`, **NEVER** `./tmp/ps2EntryRunner`, **NEVER** `gdb`.

Builds and runs stay on the **main agent**, serialized, between workflow phases. State this
constraint verbatim in the shared context block — agents otherwise reach for a build.

## Anatomy of the script

```js
export const meta = { name, description, phases: [{title:'Investigate'},{title:'Synthesize'}] }

const CTX = `...`        // shared context: project paths, funcs.py path, gp value, the
                         // READ-ONLY constraint, the state-of-play, and the GOAL. Verified
                         // facts only — mark hypotheses as hypotheses (verify-don't-trust).
const SCHEMA = {...}     // JSON Schema forcing each agent to return structured findings
                         // (summary / key_addresses / trigger / proposed_override / confidence)
const QUESTIONS = [ {key, q}, ... ]   // one focused sub-question per agent, non-overlapping

phase('Investigate')
const findings = (await parallel(QUESTIONS.map(it => () =>
  agent(`${CTX}\n\n=== YOUR QUESTION (${it.key}) ===\n${it.q}`,
    { label:`re:${it.key}`, phase:'Investigate', schema:SCHEMA }).then(r => ({key:it.key, ...r}))
))).filter(Boolean)

phase('Synthesize')
const synthesis = await agent(
  `${CTX}\n\nFindings JSON:\n${JSON.stringify(findings,null,2)}\n\nReconcile into ONE concrete override...`,
  { label:'synthesize', phase:'Synthesize', schema: SYN })

return { findings, synthesis }
```

Run it in the **background** (`Workflow({script})` returns a task id and notifies on
completion) so the main agent stays free. To iterate, edit the saved script file and re-run
with `{scriptPath}` (resume with `{scriptPath, resumeFromRunId}` to reuse cached agents).

## What makes the findings trustworthy

- **Ground truth = disasm + generated C++** (see [working-rules.md](working-rules.md)). The
  CTX must say so, and must label its own "state of play" claims as verified-vs-hypothesis —
  a confidently-wrong premise propagates to every agent.
- **Non-overlapping questions.** Each `key` owns one angle (e.g. natural-pump / op-submit /
  vtable-origin / state-transition / resource-structuring). Overlap wastes the fan-out.
- **Schema-forced output.** Free-text findings are hard to reconcile; the schema makes every
  agent emit the same shape (notably `proposed_override` + `confidence`), so synthesis is a
  merge, not a re-read.
- **Synthesis demands a buildable override**, not a summary: exact guest addresses, the gate
  / ordering, ranked alternatives — something the main agent can turn into a
  `registerFunction` hook.

## After the workflow

The synthesis is a **hypothesis**, not a verified fix. The main agent takes the recommended
override, implements it in the game repo's `src/register_overrides.cpp`, **bumps `BUILD_TAG`**,
builds (serialized, on the main agent), runs under `timeout`, and checks whether the frontier
advanced — the normal core loop. Record the workflow's conclusion + the build result in the
game repo's `docs/progress.md`.

## Reusable template

`.claude/workflows/re-fanout.js` is an **args-driven** version of the above: pass
`{ context, questions:[{key,q}], synthesis }` and it runs the read-only Investigate→Synthesize
pipeline. Invoke with `Workflow({ name:'re-fanout', args:{...} })`, or copy it inline and
hard-code the questions when they're game-specific (often clearer). Either way the
**read-only / main-agent-builds** rule is non-negotiable.
