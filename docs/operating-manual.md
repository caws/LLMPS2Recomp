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

- **External references** ([resources.md](resources.md)): **ps2tek** (PS2 hardware spec) and the
  **PCSX2 source** (the reference implementation for *how* a subsystem decodes/behaves). Reach for
  these when an override must reimplement hardware the runtime stubs (IPU `ipum` texture decode, a
  DMA quirk, a register side-effect): read ps2tek for the spec and mirror PCSX2's proven code rather
  than guessing — then verify against our ground truth (§4). Cite the file/function you mirrored.

Pick the cheapest tool that answers the question. A single-fact lookup is a `funcs.py`/grep, not a
workflow. **And for a live-behavior question, the cheapest tool is log instrumentation, not gdb** — a
one-shot probe, dispatch logging, or an `fprintf` counter gives a scriptable, diffable `run.txt`. gdb
is the *expensive* tool (it perturbs the timing races we debug, is manual and serial, and needs the
parent-attach dance), so **instrument the log first and escalate to gdb only when the log can't answer
it** — a hung-thread backtrace, a hardware watchpoint on an arbitrary-address writer, or live-state
validation of a load-bearing claim (§4). See working-rules.md (Discipline) for the full rule.

## 4. Ground truth and the verification rule

**Ground truth is the generated recompiled C++ AND the disassembly — co-equal; cross-check BOTH.
Then validate load-bearing claims on live state with gdb.** Everything else — project notes, `docs/`,
memory, prior findings, a subagent/workflow synthesis, even a runtime probe's printed value — is a
**hypothesis to verify against ground truth, not a fact.**

> **USE BOTH THE GENERATED CODE AND THE DISASM.** The generated `tmp/generated/FUN_<addr>_0x<addr>.cpp`
> is what actually runs, with every guest instruction as a commented address, explicit
> `goto`/`switch(ctx->pc)` control flow, resumable entry labels, and dispatchability you can see — so
> it's the natural entry point for any non-trivial control-flow question. But it is not the *only*
> ground truth: corroborate against the disasm (`funcs.py disasm/bounds`) for raw bytes, bounds, and
> exact instructions. Each surfaces what the other hides — reading *only* disasm hides resumable
> entries and cross-unit dispatch; reading *only* the generated code can miss a truncated/over-bound
> CSV bound that the disasm reveals. Then **gdb to verify** any load-bearing claim on live state.

- **Cross-read both, then verify with gdb.** Read the generated C++ and the disassembly together and
  make sure they agree; verify on live state with gdb when it matters. The generated code often makes
  control flow, resumable entries, and dispatchability clear where raw disasm is opaque, while the
  disasm is authoritative on bytes/bounds. A claim that holds in one but not the other is not yet
  verified.
- **A confident analysis is still a hypothesis.** This session, three confidently-stated analyses
  (two from workflow syntheses, one from prior notes) were wrong and were caught only by checking the
  disasm / generated code / an empirical run. *Always re-derive a load-bearing claim from ground
  truth before building on it* — especially one that came from a subagent.
- **A static caller-grep is NOT a verified call chain — use all three (disasm + generated + gdb).**
  Grepping the generated `.cpp` for who calls a function gives *candidate* callers, not the path
  actually taken: it is blind to fn-ptr/vtable/table dispatch (target has *no* static caller) and
  ambiguous under recursion or many callers. This bit us: a caller-grep fingered
  `0x17a570` as the registry-build selector — but a hook proved it never runs, and a gdb `bt` at the
  target gave the true path (`…←0x1387a0←0x139A80←dispatchLoop`). **For any dispatch/caller chain:
  read the disasm of the actual dispatch site, cross-check the generated code, and confirm with gdb.**
  The cheap, decisive tool is **`bt` at the target function** — the runtime calls recompiled functions
  as nested host calls, so the host stack *is* the guest call chain (`break FUN_<addr>` + `bt`; see
  [debugging.md](debugging.md) / the gdb recipe). Prefer it over static xref for caller questions.
- **Probe reads can lie too.** A value printed from a runtime probe can be a tail-call/`jr` artifact.
  When a probe disagrees with the disasm, suspect the probe.
- **Empirical test beats argument.** When two analyses disagree, the run decides. Prefer a quick
  experiment that *falsifies* one over more static reasoning.
- **A noisy metric needs MULTIPLE runs before you conclude.** Many signals here are non-deterministic
  run-to-run — recovered data-as-code counts, how far the boot gets, timing-race outcomes — because the
  guest's cooperative-yield/preemption interacts with host scheduling. Comparing **single runs** of two
  configs (e.g. "probes off = 992 events, probes on = 10") can show a difference that is **pure variance**,
  not a real effect. Before attributing a change to your edit: run **each** config several times and look at
  the *distribution* (range/spread), not one sample. If the spreads overlap, there's no effect. *(Concrete
  miss: a "probes are load-bearing" conclusion drawn from one 992-vs-10 pair was retracted once both configs
  were run ~4× and both ranged 5–1158.)* Pick a metric that's robust to the noise (does the target PC get
  reached at all? does the screen render?), not a count that drifts.
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
  - **Use gdb to VALIDATE your analysis of the code — both the disassembly and the generated C++ — not
    just to debug.** A reading of disasm/generated code is a hypothesis about what the code *does*; gdb
    is how you confirm it against live execution. Before building on any non-trivial claim ("this fn sets
    screen-ID", "this loop iterates table-C", "this callback fires every frame", "a1 is the current
    screen"), check it under gdb: breakpoint the function and read the real args/registers/guest memory,
    or watch the address it supposedly writes. Make gdb a routine step for *validating* load-bearing
    claims — but **instrument the run log first to locate and answer** the question; gdb is the expensive
    escalation (§3 / working-rules.md Discipline), not the first reach.
  - **Before concluding a hardware-subsystem override is stuck or "options are exhausted," consult
    PCSX2's actual reference implementation** ([resources.md](resources.md)) — not just the bare spec
    or a table pulled from FFmpeg/similar. This bit us: the IPU MPEG decoder was stuck at
    436/1024 macroblocks for multiple sessions (AC-VLC table verified byte-perfect, decode logic
    independently re-verified) because it was missing a per-macroblock structure
    (`macroblock_address_increment`) that was sitting in PCSX2's `mpeg2sliceIDEC()` the whole time —
    the *IDEC-specific* function, not the generic `mpeg2_slice()`. Read the actual function PCSX2 uses
    for the specific command/mode in play (grep by the command name, e.g. `IDEC`/`BDEC`/`FDEC`), not
    just a table or a related-but-different code path, before treating a subsystem as a dead end.
  - **gdb also resolves what static xref structurally CANNOT.** Address-xref has blind spots: a value
    written through a **helper that receives a pointer argument** (not a direct base-load) is invisible
    to it; a function being a registered call target says nothing about whether it's *reached at
    runtime*; "dispatchable" hooks miss same-unit direct calls. For "who writes X / what sets screen-ID /
    who enqueues the first op", **set a hardware watchpoint on the target guest address** (host addr =
    `rdram + (guest & 0x01FFFFFF)`; break once on a per-frame fn to capture `rdram`, then `watch`). It
    catches the real writer's backtrace regardless of how the address was computed — or, if it never
    fires across a full run, that is itself ground-truth proof the write never happens (gated off
    upstream). A backtrace at the watchpoint/breakpoint is often worth more than hours of xref.

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
  - **Validated progress is the gate, not "code changed."** Progress = the frontier advanced in
    **correctness or depth** — a real bug fixed, execution driven into new code/subsystems, or something
    new rendered. **This counts even if the game now reaches FEWER frames before stopping:** a fix that is
    more correct and exposes more behaviour but uncovers a new *deeper* wall (the game dies sooner, at a
    point further along) **IS validated progress and SHOULD be committed** — commits are revertible, so
    capture the forward step and undo later if it proves improper. Do NOT commit: a probe / diagnostic /
    one-shot experiment (strip or gate it first, keep the fix), a change that left the frontier unmoved, or
    **pure breakage** (less correct, less behaviour, no new ground reached). When torn between
    "more-correct-but-fewer-frames" and "this just broke it", lean toward committing the former.
  - **Commit hygiene:** a focused commit per validated step; a message stating what advanced and the
    evidence; **never** add `Co-Authored-By` / `Claude-Session` trailers. Update `docs/progress.md`
    in the same commit. Only the game repo — never commit in the engine/`tools/` repos unless asked.
- Save durable, non-obvious cross-session knowledge to memory (user preferences, project constraints,
  hard-won methodology). Don't save what the repo already records. Mark retractions clearly when a
  prior model is disproven (§4) — a stale "fact" in memory is worse than none.
