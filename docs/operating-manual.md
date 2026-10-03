# Operating manual — how the main agent works a PS2Recomp decomp

This is the **top-level playbook** for the agent driving a PS2Recomp game decompilation: the
operating principles, the work loop, what counts as ground truth, when to spawn subagents,
what progress means, and when to stop and consult. It ties together the hard constraints in
[`../CLAUDE.md`](../CLAUDE.md) and the technique docs in this folder
([README](README.md) · [working-rules](working-rules.md) · [debugging](debugging.md) ·
[functions-csv](functions-csv.md) · [overrides](overrides.md) · [workflows](workflows.md)).
Read those for detail; this is the doctrine that governs *how* to use them.

## 1. Role of the main agent

The **main agent owns everything that mutates shared state**: builds, runs, gdb, git, and all
edits to the game repo's `recomp/functions.csv` and `src/register_overrides.cpp(.h)`.
Subagents are **read-only investigators** (see §6) — they never build or run.

Non-negotiable constraints (from `CLAUDE.md`; repeated here because they shape every action):

- **Never edit `tools/<game>/PS2Recomp/`** — separate upstream repo, and its `runner/` is wiped
  and regenerated every build. All game behavior goes through override hooks in the game repo's
  `src/register_overrides.cpp` (`runtime.registerFunction(addr, lambda)` / `PS2_REGISTER_GAME_OVERRIDE`).
- **Inputs are provided per game** — `config.toml`, `functions.csv`, the ELF, `gamefiles/`. Never
  generate them.
- **Builds run in the background** (`scripts/03_build_game.sh <game_dir>`), can take many minutes;
  watch with `scripts/build_progress.sh <game_dir>`, not by re-polling. A `functions.csv` change
  needs a **full regen**; override-only changes use `--skip-regen --changed-recomp`.
- **Always run under `timeout`**; the log goes to `<game_dir>/tmp/run.txt` (a spin emits hundreds
  of MB/s). Run the built binary directly: `PS2_GAMEFILES=$PWD/gamefiles ./tmp/ps2EntryRunner ./<ELF>`.
- **No git / no commit unless explicitly asked.** Never add Co-Authored-By / Claude-Session trailers.
- **No auto-mode** — only do what the user asked. The one exception is a *sanctioned
  checkpoint-bounded run* (§7).

## 2. The work loop (one frontier per cycle)

Boot reaches a point and stops — a crash, a spin, a silent hang, a missing-function storm. Work
**one frontier at a time**:

1. **Reach the frontier** — run under `timeout`, read `tmp/run.txt`. Classify the symptom (spin /
   missing-function / bad-PC / silent hang — see [debugging.md](debugging.md) §1).
2. **Locate** — find the exact guest function/instruction (§3 tools).
3. **Hypothesize** — form a *specific, falsifiable* theory of the cause. Write it down as a
   hypothesis, not a fact.
4. **Experiment** — the narrowest change that tests the hypothesis: a one-shot probe override, a
   CSV correction, a targeted HLE. **Bump `BUILD_TAG`** so you can prove the running binary is current.
5. **Verify** — §4. Confirm the tag in `run.txt`, confirm the frontier *advanced* (or the hypothesis
   was falsified) — and when the effect is at all ambiguous, **run under gdb to validate against live
   state** (the thread cleared the bad spot), not just the log. A passing build is not a passing result.
6. **Record** — append to the game repo's `docs/progress.md`: what the frontier was, what you tried,
   what the evidence showed, what's next. Then repeat.

**Fix incrementally; never mass-rewrite the inputs.** Advance the frontier, rebuild, advance again.

## 3. The toolbox — and when to reach for each

- **Disassembler** (`.claude/skills/ps2recomp-fix-next-crash/funcs.py` — `disasm` / `bounds` /
  `find` / `scan`, `--game <dir>`): the default for reading guest code, checking CSV bounds, and
  validating a candidate fix. Fast, scriptable. Start here.
- **Generated recompiled C++** (`<game_dir>/tmp/generated/FUN_<addr>_0x<addr>.cpp` /
  `sub_...`): read when control flow is hard to follow in raw disasm. Every guest instruction is a
  comment with its address; `switch(ctx->pc)` cases are resumable dispatch entries; backward
  `goto label_<addr>` is a loop. **`register_functions.cpp` is the authority on whether an address
  is a dispatched entry** (whether a `registerFunction` hook will fire) — a direct same-unit C++
  call bypasses the registry. Regenerated every build; read the fresh copy.
- **gdb-under-parent** ([debugging.md](debugging.md) §2): the only reliable backtrace for a hung
  runner (`ptrace_scope=1` blocks sibling-attach; run the runner *under* gdb). Use for silent hangs
  to see what thread is blocked on what. The EE guest thread is the one in `dispatchLoop`.
- **One-shot probe override**: register a hook on a suspect guest address; dump regs/stack/memory
  to `stderr`, then `ctx->pc = 0` to stop cleanly instead of letting it spin. The fastest way to read
  live state at a specific PC.
- **Dispatch / call-trace logging**: the runtime's `[dispatch:*]` lines give the guest call chain
  (`trace=` — read right-to-left from the failure). Wrap a function to count/log hits to map a loop.
- **Screenshots** (`scripts/05_screenshot.sh --launch <game_dir>`): **visual ground truth** — the
  decisive check for "did anything actually render / did the menu appear". Textures flicker, so it
  bursts frames and flags content by mean brightness. Use it to confirm real visible progress, not
  just log advancement.

Pick the cheapest tool that answers the question. A single-fact lookup is a `funcs.py`/grep, not a
workflow.

## 4. Ground truth and the verification rule

**Ground truth is the disassembly, the generated recompiled C++, and live gdb.** Everything else —
project notes, `docs/`, memory, prior findings, a subagent/workflow synthesis, even a runtime
probe's printed value — is a **hypothesis to verify against ground truth, not a fact.**

- **Cross-read all three code sources.** Verify against the **disassembly AND the generated
  recompiled C++** (and gdb when live state matters) — they corroborate each other and the generated
  code often makes control flow, resumable entries, and dispatchability clear where raw disasm is
  opaque. A claim that holds in one but not another is not yet verified.
- **A confident analysis is still a hypothesis.** This session, three confidently-stated analyses
  (two from workflow syntheses, one from prior notes) were wrong and were caught only by checking the
  disasm / generated code / an empirical run. *Always re-derive a load-bearing claim from ground
  truth before building on it* — especially one that came from a subagent.
- **Probe reads can lie too.** A value printed from a runtime probe can be a tail-call/`jr` artifact.
  When a probe disagrees with the disasm, suspect the probe.
- **Empirical test beats argument.** When two analyses disagree, the run decides. Prefer a quick
  experiment that *falsifies* one over more static reasoning.
- **Identify before you fix.** A single string, constant, or address often unlocks a
  misidentification (e.g. a function's real purpose). Confirm what a function *is* (its args, what it
  reads/writes, what calls it) before assuming its role from a name or a note.
- **Validate fixes and investigations by running under gdb.** The run log and a screenshot tell you
  *that* something changed; **gdb-under-parent** ([debugging.md](debugging.md) §2) tells you *what the
  guest is actually doing* — the live, authoritative check. Use it to confirm an investigation's model
  and to validate a fix, not just to debug hangs:
  - **Confirm a hypothesis on live state.** Backtrace the EE guest thread (the one in `dispatchLoop`)
    to see the real call chain and blocked-on state; read the actual `R5900Context` registers / guest
    memory at a target PC. A log line is a claim; the backtrace is the fact.
  - **Validate a fix against the *cause*, not the symptom.** After a fix, run under gdb and verify the
    thread is no longer in the bad spot — the spin loop / `WaitSema` / data-as-code site is gone and
    the boot thread has advanced past it (set a breakpoint or interrupt-and-backtrace at the target).
    "The log got longer" or "no crash this run" can both happen while the real frontier is unmoved
    (recover-pc masking, a stub `ret0`, timing). The backtrace shows whether the frontier truly moved.
  - **Disprove with it too.** If a fix "worked" but gdb shows the guest still parked at the same
    instruction (or now spinning one frame deeper for the same reason), the fix didn't land — treat
    that as falsification, the same as a failed empirical run.
  - Reach for it whenever a fix's effect is ambiguous from the log alone, or an investigation's
    conclusion rests on what a thread is *really* executing. `gdb`/`fprintf` perturb timing, so for a
    timing-sensitive race, corroborate with a non-gdb run too.

## 5. What progress is (and isn't)

**Progress = the execution frontier advanced toward a real target**, confirmed by ground truth:

- The boot reaches a deeper guest PC / a new subsystem, the frame loop advances further, a previously
  unreached function now runs, or **the screen renders something new**.
- Express success as **reaching a target** (a grep-able sentinel in `run.txt`, a target guest PC, a
  visible screen), never as **absence of a crash** — "no crash" is fakeable by stubbing a function to
  `ret0` and masks the real state.
- **Visual progress is the strongest signal** for UI frontiers — screenshot it.

**Not progress** (these *mask* the frontier, they don't advance it):

- Stubbing/`ret0`-ing a function just to get past it without understanding why it failed.
- Faking a completion the game waits on, when the data behind that completion isn't actually there —
  the failure just resurfaces downstream as uninitialized/garbage state. (Treat "fake the signal"
  as a diagnostic probe, not a fix, until proven the underlying data is real.)
- Whack-a-mole neutralizing of garbage callbacks/pointers — it skips broken work rather than making
  it correct, and yields a non-functional result.

When a "fix" advances the log but the screenshot/behaviour is unchanged or worse, it isn't a fix.

## 6. Subagents and workflows — when and how

Use a **read-only multi-agent workflow** ([workflows.md](workflows.md)) when a frontier is an
**architecture question** ("what sequences X?", "where does this object's vtable come from?",
"what must be loaded before Y runs?") that means reading across many functions — not for a single
lookup you can do directly.

- **Sub-agents are READ-ONLY**: `funcs.py`, grep over `tmp/generated`, readelf, ELF scans. They
  **never** build, run, or gdb — concurrent builds collide and a run reads a half-installed binary.
  **Only the main agent builds**, serialized, between phases.
- **Fan-out shape**: N agents each chase a non-overlapping sub-question against the same ground-truth
  context → one synthesis agent reconciles into a *buildable* proposal (exact addresses, the
  gate/order, ranked alternatives). Template: `.claude/workflows/re-fanout.js`. Reliable invocation
  is **inline or via `scriptPath`** (copy the template and bake in the questions); the
  `Workflow({name, args})` path did not forward `args` in this runtime.
- **Feed the agents verified context.** Put only confirmed facts in the shared context block, and
  mark hypotheses as hypotheses — a wrong premise propagates to every agent.
- **The synthesis is a hypothesis (§4).** Verify its load-bearing claims against disasm + generated
  code *before* and *while* building it; be ready for the empirical run to disprove it.
- **Scale to the ask** and mind cost — a workflow can spawn ~7 agents and hundreds of K tokens. Scout
  inline first to scope the questions; reach for the fan-out when the architecture is the blocker.

For a quick, bounded read across a few files, a single `Explore`/`general-purpose` subagent (or just
doing it inline) is cheaper than a full workflow.

## 7. Autonomy, stopping, and consulting the user

- **Default: no auto-mode** — do what was asked; pick sensible defaults for incidental choices and
  state them, rather than asking about everything.
- **Don't propose cutting the session short.** When the user says keep pushing, *keep working the
  problem* — more builds, deeper RE, new experiments. Do not offer "should I continue / checkpoint
  here" choices. Grind the loop (≈12+ low-progress build cycles) before even considering handing back.
- **Sanctioned checkpoint-bounded runs** are the exception: when the user defines a checkpoint
  (a target sentinel/PC), drive build-run-fix unattended until it's met, or until the frontier
  hasn't advanced in ≈12 build cycles, or a behavioral judgment call is needed.
- **Stop and consult only for a genuine decision the user owns**: a fundamental wall where the
  strategy itself is in question (e.g. "the menu can't build because we faked the load — do we pursue
  real loading or accept a diagnostic hack?"), an irreversible/outward-facing action, or a tradeoff
  with no obvious default. Bring evidence and a recommendation, not an open question.
- **Report honestly.** If an experiment falsified the hypothesis, say so with the evidence. A failed
  fix that produced a sharper understanding is a real result — report it as such.

## 8. Recording and memory

- The game repo's **`docs/progress.md`** is the running journal — update it every cycle (frontier,
  experiment, evidence, next step). `docs/elf.md` holds static per-game facts.
- **Commit on validated progress (standing rule).** Whenever code changes in the **game folder**
  (`recomp/functions.csv`, `src/register_overrides.cpp(.h)`) **and** you have *validated that progress
  was made* — the frontier advanced toward a real target, confirmed per §5 (a deeper PC / new
  subsystem / new render / a met checkpoint), ideally cross-checked under gdb per §4 — **commit it**
  in the game repo. This is a standing authorization (no need to ask each time); it overrides the
  default "no commit unless asked" for the game folder.
  - **Validated progress is the gate, not "code changed."** Do NOT commit a probe/diagnostic/experiment,
    a change that left the frontier unmoved, or one that *regressed* it (e.g. a "fix" that is more
    correct in theory but makes the game die sooner — that is not validated progress; resolve the
    regression first). If an experiment didn't pan out, revert or iterate — don't commit it.
  - **Commit hygiene:** a focused commit per validated step; a message stating what advanced and the
    evidence; **never** add `Co-Authored-By` / `Claude-Session` trailers. Update `docs/progress.md`
    in the same commit. Only the game repo — never commit in the engine/`tools/` repos unless asked.
- Save durable, non-obvious cross-session knowledge to memory (user preferences, project constraints,
  hard-won methodology). Don't save what the repo already records. Mark retractions clearly when a
  prior model is disproven (§4) — a stale "fact" in memory is worse than none.
