# The native-lift arc — from recompilation to standalone

> **DESIGN ONLY.** Nothing here is built. This doc exists so the arc is decided before it is
> started, because its failure modes are ordering mistakes that are expensive to unwind.
> Companion to [`gl-renderer.md`](gl-renderer.md) and [`vu1-jit.md`](vu1-jit.md).

## 1. Why this arc exists

The stated goal: eventually build and run the game **without `ps2_recomp` and without
`ps2xRuntime`** — the game's own code, in the game repo's `src/`, compiled directly.

The proposed sequence was:

1. get the recompilation working, porting to `src/` only what is needed to make it run;
2. once it works, port **all** functions from `tmp/generated/` into `src/`;
3. once ported, decouple from the PS2 subsystems so it compiles standalone.

**Verdict: viable, with one structural change** — steps 2 and 3 must be merged and worked
**subsystem-wise, not function-wise**. §4 makes that case. §5 is the prerequisite neither step
survives without. §10 is the repo and build strategy: **no long-lived branches, and additive
only — no line is ever removed from `ps2xRuntime`.** The rest is the phase plan and the honest
scale.

## 2. What is already verified (do not re-derive)

- **Every guest call routes through the runtime.** Generated code never calls another generated
  function directly — not even a `JAL` whose target is a compile-time literal:
  `tmp/generated/boundfix16cda0_0x16cda0.cpp:449` emits
  `runtime->dispatchGuestBranch(rdram, ctx, 0x18C200u, ..., GuestBranchKind::DirectCall, "JAL")`.
- **One table, one signature.** `ps2_runtime.h:316` —
  `using RecompiledFunction = void (*)(uint8_t *, R5900Context *, PS2Runtime *);`
  with `registerFunction` / `lookupFunction` at `:344-347`.
- **⇒ A hook at any address intercepts every caller, from anywhere.** Full replacement needs no
  new dispatch machinery. This is the single fact that makes the arc possible at all.
- **Scale.** `tmp/generated/`: **5,608 functions / 889,469 lines**. `src/` today: **403
  registrations** (75 of them `diagnostics/`, so ~**328 real**) across 22 domain modules /
  **20,986 lines**. We are at roughly **5.8%** of the function count.
- **Guest static data is loaded from the ELF at runtime** (`ps2_runtime.h:304` `loadELF`). Hooks
  receive `rdram` and read `.data`/`.rodata` out of it. **Lifting code does not lift data.**
- **The emulated-console layer is ~90k lines** (GS, VU0/VU1, VIF, IPU, DMA, SIF/IOP, EE kernel,
  scheduler) — all of it still required by a hook that operates on `rdram`/`ctx`.

## 3. The ladder

| Rung | State | What the native target stops needing |
|---|---|---|
| 1 — today | ~328 real hooks; generated code does the work | — |
| 2 | all 5,608 in `src/`, still `(rdram, ctx, runtime)` | **`ps2_recomp`** (build-time tool) |
| 3 | functions lifted off guest memory — native structs, not `rdram` offsets | the ELF's data sections |
| 4 | subsystems replaced semantically — code calls a renderer, not the GS | runtime units, one at a time |
| 5 | standalone | the engine (assets still come from the disc) |

**Nothing here is deleted.** "Stops needing" means *stops linking* — see §8 and §10. `ps2xRuntime`
stays intact and buildable throughout, because it is both the fallback and the oracle.

Rung 2 is the one that is purely mechanical. Rungs 3-5 require understanding, and that is the
whole difficulty — see next.

## 4. The central decision: the subsystem is the unit of work, not the function

The proposed order does rung 2 to completion, then rung 3. Four reasons to merge them instead.

1. **Rung 2 alone buys almost nothing.** A rung-2 hook has the same signature, reads the same
   `rdram`, emits the same GIF packets. Identical behavior, identical portability, strictly higher
   risk. Its only durable gain is deleting `ps2_recomp` — a build-time tool.
2. **The two passes do not compose.** Transliteration *does not require understanding*;
   decoupling requires *nothing but* understanding. A function-wise pass therefore teaches you
   none of what rung 3 needs, and you touch all 5,608 functions twice.
3. **Worse, the first pass is in the wrong shape.** Rung-2 code is written against guest memory
   layout — the exact shape rung 3 has to undo. You would hand-write 890k lines of the thing you
   intend to delete.
4. **Data cannot be lifted function-wise at all.** A struct's real shape is learnable only from
   *all* the code that touches it. That is a subsystem-sized question, never a function-sized one.

**⇒ Lift a subsystem end to end in one pass**: its functions, its data, and its hardware contact
together. Then delete what it used and move to the next. The game keeps running throughout.

The current `src/` layout already has this shape — 22 **domain** modules (`dbcman/`, `menu_flow/`,
`font_text/`, `loader/`, `audio/`, …), not address ranges. The architecture already anticipates
the merged plan; the CSV-address view is the one that does not survive.

## 5. The prerequisite: the differential oracle

Transliterated code is **correct by construction** — it need not be understood to be right. Every
replacement trades that guarantee for a claim, and this arc makes **5,608 claims**. The memory
index is a case file on what unverified claims cost here ("approximate" meaning fabricated; every
automated check green while the wrong level played).

The same fact from §2 that makes lifting possible also makes it checkable: dispatch is **one
funnel with one signature**. So:

- env-gated (`LOTR_DIFF`), opt-in per address;
- at `dispatchGuestBranch`, snapshot `ctx` + a dirty-tracked `rdram` window, run the override,
  snapshot, restore, run the generated function, diff register file + memory writes;
- report divergence as address + first differing register/offset.

**Known limit, decide before building:** functions with irreversible side effects (DMA kicks, GS
register writes, IOP RPCs) cannot be run twice. Those need recorded-trace comparison instead of
re-execution, so the harness needs an effects classification per address from day one.

Build this **before lifting anything**, and run it against the ~328 hooks that already exist —
it is worth having for the current arc regardless of whether the rest of this doc ever happens.

**This is not a new idea here — it is a repeat.** `runtime.lock` row 48:
*"cont.250 `PS2X_VU0_PROGVERIFY`: the VU0 differential oracle cleared microVU over 46.3M programs
(vf/vi/qp/pc/mem all 0), so `PS2X_VU0_MICROVU` is DEFAULT ON."* Row 53 records `PS2X_GS_TEXVERIFY`
being *"built FIRST per the cont.250 resume rule."* The method is proven in this tree at subsystem
scale; the lift arc applies it at game scale.

## 6. Phase plan, with gates that can actually be met

"Once everything is working properly" is not a gate — a commercial-game recompilation is never
done; there is always another level, cutscene, or edge case. Gate on something testable.

- **Phase 0 — now.** The EE/VU1 performance arc; the renderer has left the critical path
  (cont.331s). **Gate:** game completable start → finish at playable framerate under
  deterministic replay.
- **Phase 1 — the oracle.** §5. **Gate:** every existing hook either passes differential
  validation or has its divergence understood and written down.
- **Phases 2..N — one subsystem per arc**, narrow-interface-first (§7). **Gate per arc:** that
  subsystem's functions native, its data native, its hardware contact replaced, oracle green,
  and its generated functions no longer reachable.
- **Terminal.** When the last subsystem lands, `ps2_recomp` goes; then each runtime subsystem
  goes as its final consumer is lifted.

## 7. Which subsystem first

Criteria: narrow interface to un-lifted code; self-contained data; already has a `src/` module;
low hardware contact; cheaply verifiable.

- **Good first targets:** `font_text/` (14 hooks, table-driven, narrow), `memcard/` (5),
  `pad_input/` (7).
- **Late:** `menu_flow/` (67), `igc_event/` (49), `hero/` — broad interfaces into gameplay state.
- **Last:** anything touching VU microcode or the EE threading model.

**Do not faithfully reimplement middleware.** A large share of the 890k lines is Sony SDK and
linked middleware, not EA game code. That code should be *deleted and replaced* with a native
equivalent, not transliterated — it is the cheapest large win in the arc and the only place where
"lift" should mean "throw away".

## 8. What the native target stops linking

**No runtime code is ever removed.** `ps2xRuntime` remains whole, on `lotr`, still building the
emulator-backed runner — because it is the fallback, the oracle's reference arm, and a *general*
PS2 runtime that the next game will need. Deleting a subsystem would saw off the branch the arc
stands on and would poison every future `git merge upstream/main`.

Rung 4 is therefore a **link-time** question. The ~90k-line runtime is not one wall; it is ~6
independent units that drop out of the native target as their last consumer is lifted:

- **IPU** → ffmpeg is already linked; FMV becomes a native decode.
- **VU0/VU1** → the microprograms *are* game code. Lifting them to native vector math also removes
  the microVU x86-emitter blocker that today makes an ARM build impossible (`x86emitter.cpp`,
  linked unconditionally at `ps2xRuntime/CMakeLists.txt:634`). **This arc and the portability arc
  share this item.**
- **GS** → lifted code calls the renderer instead of building GIF packets.
- **SIF/IOP** → largely HLE'd already.
- **EE kernel + scheduler** → hardest, and correctly last: the game's threading model is baked
  into its own code.

## 9. What would make this fail

- **Starting before the game is finishable** — lifting on top of behavior we do not yet understand.
- **Lifting without the oracle** — unverifiable claims at 5,608x scale.
- **Function-wise ordering** — §4: double work, wrong shape.
- **Scale denial.** 889,469 transliterated lines. Even at the much higher density of hand-written
  code this is a multi-year arc. It is tractable *only* because it is incremental, verifiable per
  subsystem, and leaves a running game after every step. Any plan that loses one of those three
  properties should be rejected.

## 10. Repository and build strategy: no branches, no deletions

**The arc needs zero long-lived branches, and removes zero lines from `ps2xRuntime`.**

### Why not a `recompilation` → `decompilation` branch pair

- **It destroys the oracle.** §5 works because both arms run in **one process** on a cloned
  `(rdram, ctx)`. Two branches means two binaries, and the equivalence proof becomes impossible.
  Memory, cont.330b: *the A/B control is the same binary, flag off*.
- **The branch point never arrives.** "Recompilation is done" is not a state a commercial-game
  recompilation reaches (§6).
- **The merge never happens.** Trunk keeps fixing functions the branch has already rewritten;
  every fix becomes a re-derivation. Unbounded over a multi-year arc.
- **Observed, not hypothetical.** Arc-sized branches have already been run here implicitly, and
  `main` is now a fossil in both repos — 131 commits behind in the engine, **926 behind** in the
  game repo, with *zero* commits unique to `main`. The branch quietly became the trunk. Doing it
  deliberately, with two *live* branches, is the same failure with twice the surface.

### The pattern instead — the one used ~137 times already

Per subsystem: **`LOTR_LIFT_<SUBSYS>` env flag, default OFF** → **oracle first** (the cont.250
resume rule) → prove equivalence → flip the default → row in `runtime.lock` and the fork's
`docs/llmps2recomp-patches.md`, with a build tag. Short-lived `lift/<subsys>` branches (days to
weeks, merged on gate) are fine; **the flag carries the risk, not the branch.**

### The seam pattern is already proven in this tree

`runtime.lock` row 100: *"cont.329 `PS2X_GS_RENDERER` — the selector and SEAM for the GL renderer
arc; default `cpu` (unchanged, bench hash exact)."* An entire alternative renderer was added
**without removing the CPU rasterizer**, both still build, and the two are A/B-able at the same
guest instant. `PS2X_GS_RENDERER=cpu|gl|native` is the exact template for every subsystem lift.

### Two targets, not two branches

At rung 4-5 there are two products, and they coexist in one source tree on one branch:

| Target | Links | Role |
|---|---|---|
| `ps2EntryRunner` | everything (generated code + full `ps2xRuntime`) | today's build; the oracle's reference arm; **never stops working** |
| `ps2NativeRunner` | lifted `src/` + native backends only | the standalone product |

`ps2_recomp` and the generated code are kept to the very end **on purpose**: the recompiler's last
job is not to run the game, it is to *be the oracle*. Retire it only once nothing is left to prove.

### Per repo

| Repo | Role | Strategy |
|---|---|---|
| `rotk_decomp` | `src/` — lifted code | trunk + short-lived `lift/<subsys>`; flags carry risk |
| `PS2Recomp` (`lotr`) | seams and native backends — **additive only** | stays a long-lived branch vs `upstream/main`, merging *from* upstream and never back; no subsystem is ever removed, so `git merge upstream/main` keeps working |
| `LLMPS2Recomp` | docs, scripts | trunk; it barely moves |

`recomp/runtime.lock` remains the game↔fork coupling: any lift needing a runtime seam moves the
lock in the same change.

### Housekeeping before the arc starts

Both `main` branches are 3-month-old fossils with zero unique commits. "Which branch is the truth"
should not be ambiguous when a multi-year arc begins — either fast-forward `main` to the live
branches or rename and accept the live branches as trunk.
