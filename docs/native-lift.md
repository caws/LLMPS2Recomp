# The native-lift arc — from recompilation to standalone

> **DESIGN ONLY, and GAME-AGNOSTIC.** Nothing here is built. This file is the *method*; each game's
> numbers, ordered work, module ranking and enhancement targets live in that game's
> `docs/native-lift-plan.md` (for the example game,
> `rotk_recomp/docs/native-lift-plan.md`). Keep this file free of any one game's facts. This doc exists so the arc is decided before it is
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

**Definition of done (decided, not aspirational):** a native target that links **no PS2
emulation** — which is the *starting line* for §11's remaster arc, not the end of the road. It may still read the ELF for its static data (§3), and it links ordinary game
infrastructure — a renderer, audio, ffmpeg, a window/input library — like any native game.

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
- **Scale is per game and must be measured, not assumed** — generated function count and line
  count vs the game's current real (non-diagnostic) hook count. Record it in that game's
  `docs/native-lift-plan.md`; it is the honest denominator for every progress claim, and for a
  mid-size PS2 title it is a five-digit function count against a three-digit hook count.
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
| 6 | **remaster** — gateable enhancements on the lifted code (§11) | — (this rung *adds*) |

**Nothing here is deleted.** "Stops needing" means *stops linking* — see §8 and §10. `ps2xRuntime`
stays intact and buildable throughout, because it is both the fallback and the oracle.

**Rungs 3 and 4 are independent axes.** Dropping the runtime and dropping the ELF are separate
achievements, and the ELF is the harder one: zero `rdram` requires every table, string, float
constant and function-pointer table in `.data`/`.rodata` lifted to a *typed* native declaration,
and a table's real shape is only learnable from all the code that reads it. Some will stay opaque.

**Decision (user): retaining the ELF as a static-data source is an ACCEPTED end
state**, to be revisited later. The disc is required for assets regardless (§1 of the portability
discussion), so a native build that still reads the ELF for its data sections costs the user
nothing. Plan for that; treat full data lifting as optional upside, never as the definition of done.

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

- env-gated (`<GAME>_DIFF`), opt-in per address;
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

### ★ The blind spot: the oracle cannot validate the scheduler

The method compares **outputs for given inputs**. Timing is not an output. The EE scheduler's
correctness is *when* things run relative to the vblank ladder, and that is load-bearing here, not
academic: the PAL **50 Hz** vblank is mandatory for play, and the ladder fall-off bug was precisely
a "reached the top" event delivered late by a 60 Hz vblank
(see the game repo's `docs/progress.md` on the PAL vblank rate and the `pc=0x3` scheduler race).

So the single subsystem that most needs proof is the one this technique cannot provide it for.
`lib/Kernel` therefore needs a **separately designed** validation approach — recorded timing traces
and replay divergence against `PS2X_VIRTUAL_TIME` (row 93: two runs identical over 788 events),
not re-execution diffing. Design it before lifting the kernel, not during.

## 6. Phase plan, with gates that can actually be met

"Once everything is working properly" is not a gate — a commercial-game recompilation is never
done; there is always another level, cutscene, or edge case. Gate on something testable.

- **Phase 0 — reach a game that can be played through.** Its *content* is per game and belongs in
  that game's `docs/native-lift-plan.md`; what is general is the shape: close out whatever
  performance work still has a measured ceiling (and **stop** when the ceiling is small — size it
  before investing), settle any subsystem whose flags are still experiment-gated so the playthrough
  exercises them, **measure each newly-enabled subsystem's cost on the same binary with the flag
  off** before the playthrough (otherwise its frame numbers cannot be read against the old
  baseline), and only then play the game through. ⚠ A playthrough yields a defect *list*, not a
  green light: playthrough → fix → replay → clean. **Gate:** completable start → finish, in every
  supported mode, at playable framerate under deterministic replay.
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

- **First:** small, table-driven modules with a narrow interface and self-contained data.
- **Late:** anything with a broad interface into gameplay state.
- **Last:** anything touching VU microcode or the EE threading model.

The per-game ranking belongs in that game's `docs/native-lift-plan.md`.

**Do not faithfully reimplement middleware.** A large share of the 890k lines is Sony SDK and
linked middleware, not EA game code. That code should be *deleted and replaced* with a native
equivalent, not transliterated — it is the cheapest large win in the arc and the only place where
"lift" should mean "throw away".

## 8. What the native target stops linking

**No runtime code is ever removed.** `ps2xRuntime` remains whole, on `lotr`, still building the
emulator-backed runner — because it is the fallback, the oracle's reference arm, and a *general*
PS2 runtime that the next game will need. Deleting a subsystem would saw off the branch the arc
stands on and would poison every future `git merge upstream/main`.

Rung 4 is therefore a **link-time** question, and the runtime splits three ways, not two
(measured, excluding the generated runner):

| Fate | Unit | Lines |
|---|---|---|
| **Dissolves** as consumers lift | `lib/vu` (microVU + interpreter) | 31,023 |
| | `gs_cpu_backend` + `gs_frontend` + GS memory | ~17,700 |
| | `ps2_memory` (MMIO, scratchpad, RDRAM model) | 3,112 |
| | `ps2_runtime` (dispatch + the function table) | 2,859 |
| | `ps2_vif1_interpreter` | 1,836 |
| | IOP / SIF host | ~1,280 |
| **Reimplemented natively** (not deleted) | `lib/Kernel` — threads, semaphores, scheduler, vblank | 24,817 |
| **Kept by choice — not emulation** | `gs_gpu_device.cpp` (the GL renderer) | 4,533 |
| | `ps2_audio` + `ps2_audio_vag` (host audio, ADPCM) | — |
| | ffmpeg (FMV), raylib (window/input/audio device) | — |

**The third row is the one that surprises.** Of ~22k lines that look like "GS emulation", only
~17.7k is emulation — the other 4,533 is the GL renderer, which a native port *wants*. A standalone
build links ordinary game infrastructure like any native game; the goal is not "links nothing", it
is **"links no PS2 emulation."**

The units that dissolve do so one at a time, each unlocked when its last consumer is lifted:

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
- **Assuming the oracle covers the scheduler.** It does not (§5). Lifting `lib/Kernel` on the
  strength of a method that cannot see timing would be the single most dangerous step in the arc.
- **Lifting VU without a census.** Microprograms are uploaded *as data*, so lifting VU means having
  lifted every program that can ever be uploaded — across all 44 archives, not just the levels that
  have been exercised. This is exactly the same trap as "the sequencer is inert", which came from
  one level and died to an offline census of all 44 archives; do the
  offline census before committing to the VU rung.
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

Per subsystem: **a `<GAME>_LIFT_<SUBSYS>` env flag, default OFF** → **oracle first** (the cont.250
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
| `rotk_recomp` | `src/` — lifted code | trunk + short-lived `lift/<subsys>`; flags carry risk |
| `PS2Recomp` (`lotr`) | seams and native backends — **additive only** | stays a long-lived branch vs `upstream/main`, merging *from* upstream and never back; no subsystem is ever removed, so `git merge upstream/main` keeps working |
| `LLMPS2Recomp` | docs, scripts | trunk; it barely moves |

`recomp/runtime.lock` remains the game↔fork coupling: any lift needing a runtime seam moves the
lock in the same change.

### Housekeeping before the arc starts

Both `main` branches are 3-month-old fossils with zero unique commits. "Which branch is the truth"
should not be ambiguous when a multi-year arc begins — either fast-forward `main` to the live
branches or rename and accept the live branches as trunk.

## 11. Enhancements — the remaster arc (rung 6)

**Goal (user):** once the native build is releasable and the recompiler and runtime are
no longer needed, take this to **remaster level** — every improvement individually gateable so a
player chooses the original or the remaster, on an open codebase. Named targets: **HUD textures that
work at widescreen aspect ratios**, and **hi-res HUD art** replacing today's low-res source.

### Why it comes after the lift, not before

Before the lift, a HUD texture is a VRAM upload decoded by the GS path: you would be intercepting
*decoded texel blocks*, not "the HUD atlas", and an aspect change means patching the game's own GS
coordinate math (`XYOFFSET`, the projection setup) through override hooks. After the lift, the HUD is
native code drawing native quads with native textures — hi-res is a different asset and widescreen is
a different layout. **The same change is an emulator hack before and an ordinary code change after.**

### Separate render resolution from asset resolution

They are different problems on different timelines, and conflating them wastes the earlier one:

- **Render resolution is available now.** `PS2X_GS_SCALE=4` supersamples for 13.6%, and
  [`gl-renderer.md`](gl-renderer.md) phases 5-6 are designed. cont.332's ~14 ms of headroom in a
  40 ms quantum already pays for it.
- **Asset resolution waits for the lift**, because replacing the art means owning the draw call.

### Gating at remaster scale

Same pattern as everything else: one flag per enhancement, default OFF = original behaviour. But env
vars are not a player-facing UI — a user picking "Original" or "Remastered" needs a config file or a
menu. **Design item: a settings layer above the env-flag mechanism**, with the flags remaining the
ground truth underneath so the A/B discipline (and every harness script) keeps working unchanged.

### ★ The consequence for the oracle: it never retires

If the player can choose the original, **the original path must stay correct forever** while
enhancements accumulate around it. The differential oracle (§5) therefore outlives the lift arc: it
stops being a migration tool and becomes the permanent regression net that protects "Original" mode.
Budget for it as a kept asset, not as scaffolding — which also retroactively raises how well it is
worth building in phase 1.

### What is, and is not, yours to ship

The lifted game code is derived from the game's own code, and the ELF is retained anyway (§3), so the
distribution shape is the established one for this space: publish the source, require the player to
supply their own disc. **The enhancement assets are the exception — hi-res HUD art you author is
yours**, and is the one part of the project that can ship as a binary without qualification.
Structure the tree so that boundary is obvious from day one: authored enhancement assets in their own
directory, never mixed with anything extracted from the disc.

### Candidate scope (the first two are the user's; the rest are candidates only)

- **HUD widescreen** — not only textures: anchor/stretch rules, safe areas, element layout
- **HUD hi-res art** — new assets against the lifted draw path
- Widescreen proper — FOV and projection, not just UI
- Texture filtering / anisotropy on the GL path
- Input — remapping, modern controllers
- **Co-op — local AND network** (user). Both are goals. See below.
- ⚠ **Frame rate is NOT a free enhancement.** The game's own `dt` is 0.04 s and its logic is
  timing-coupled — the ladder fall-off bug was a vblank delivered late. Uncapping is a change to game
  logic, not a renderer setting. High risk; out of initial scope.

### Co-op — local and network

**Both are goals** (user), and they are two very different projects that happen to share
a first step. **Local co-op has no determinism requirement whatsoever** — one machine, one
simulation, two input devices. Every hard problem below belongs to the network half alone.

**Check the ELF's own strings before designing anything** — a co-op mode announces itself
(selection screens, per-player HUD markers, shared-life and score counters). Record the evidence in
the game's plan doc.

#### Step 1 — local co-op (a shipped feature, not a stepping stone)

**★ The blocker is usually not network code — it is that port 2 does not exist.** `PSPadBackend::readState`
takes `port` and `slot`, and in this runtime both are **commented out**, so every port returns the
same pad. What this step needs:

- honour `port`/`slot` in the pad backend;
- real device enumeration — map N physical gamepads onto ports (the host layer plus any
  keyboard-split or scripted-pad paths model **one** device, not several);
- **validate the game's own co-op entry flow.** It has almost certainly never been exercised —
  ⚠ **co-op is an entire untested game path, and it belongs in the Phase 0 playthrough**, not
  discovered afterwards.

**Placement: independent of the lift** — input plumbing plus a menu path, so it can go anywhere.
Doing it before the playthrough is what makes the playthrough cover co-op as well as solo.

#### Step 2 — network co-op

**Three rollback prerequisites already exist here, built as debugging tools:**

| Need | What already exists |
|---|---|
| deterministic simulation | `PS2X_VIRTUAL_TIME` (row 93) — two runs identical over 788 events |
| frame-indexed input | `PS2X_PAD_SCRIPT` — vblank-indexed, replay-deterministic |
| cheap state deltas | `PS2X_WRITE_WATCH` (row 44) — per-word last-writer table |

That is an unusually strong starting position for GGPO-style rollback.

**But it is not small, and three things are genuinely open:**
1. **The deterministic config is not the playable config.** `PS2X_VIRTUAL_TIME` is default OFF and
   NEXT.md says to drop it for the live configuration. Netplay needs determinism *and* playability at
   once — today they are different runs.
2. **Cross-machine determinism is stronger than cross-run.** Same-binary replay determinism does not
   imply two different hosts agree. Rendering may diverge freely; **simulation may not**.
3. **Frame-granular save/restore does not exist yet** in any form.

**⚠ OPEN DECISION — the network half may not belong at rung 6 at all.** Rollback is *easier before
the lift than after*: pre-lift, game state is one contiguous 32 MB `rdram` blob you can `memcpy`
(with `WRITE_WATCH` for deltas). Post-lift it is scattered native objects needing explicit
serialise/deserialise per subsystem, where **every missed field is a desync**. So the lift makes the
HUD work easier and network co-op *harder* — the opposite of every other item in §11. Decide it
consciously rather than inheriting rung 6's placement. (Step 1 is unaffected either way.)
