# The VU1 performance arc — why a block JIT, and how to build it

Handoff document for the **VU1 interpreter → JIT** work. It records *why* the JIT is necessary
(measured, not assumed), what is already landed, what has been disproven, and the concrete next
two steps with their verified design inputs.

Game-agnostic: the VU1 interpreter lives in the toolchain clone
(`tools/<game>/PS2Recomp/ps2xRuntime/src/lib/vu/`), committed on our fork's `lotr` branch (the
former engine patches 16/17/19/24/25; write-ups in the fork's `docs/llmps2recomp-patches.md`). The
*measurements* below come from the LOTR
bring-up (`rotk_decomp`, level era) and live in that game's `docs/progress.md` cont.175–176.

## 1. Why: the budget says the interpreter cannot get there

The EE thread is **99–100% busy and ~97% inside `VU1Interpreter::run`, with 0% GS raster** — VU1
is the sole wall (the GS worker and presenter both have idle headroom, and ablating the CPU
rasterizer entirely buys nothing). The interpreter sustains ~4.9M instruction pairs/s at ~204
ns/pair, and one guest frame costs ~1.96M pairs:

| target | needs | vs today | host cycles/pair @3.5GHz |
|---|---|---|---|
| 10 fps | 51.1 ns/pair | 4.0× | 179 |
| 20 fps | 25.6 | 8.0× | 90 |
| **30 fps** | **17.0** | **12.0×** | **60** |
| 60 fps | 8.5 | 24.0× | 30 |

**Cost decomposition by ablation** (each env flag makes one subsystem *free*; all default OFF, all
deliberately unfaithful — they are measurement instruments, never correctness runs):

| ablation | ns/pair | saving |
|---|---|---|
| `PS2X_VU1_NOSCHED=1` — the whole cycle-accurate scheduler | 143.4 | ~33% |
| `PS2X_VU1_NOFLAGS=1` — **all** FMAC flag work | 149.4 | ~27% |
| `PS2X_VU1_NOEXACT=1` — only the per-lane exact double recompute | 197.0 | ~7.5% |

> ⚠ **The `NOFLAGS` 27% is NOT all recoverable, and the `NOEXACT` instrument no longer exists.**
> `NOFLAGS` skips `normalizeFmacResult`, which does not merely *derive* flags — it **clamps the lane
> values in place** (`fmacClampExact` / `normalizeResult` take the float by reference), and the PS2
> has no NaN/Inf, so dropping it changes architectural VF/ACC contents. `NOFLAGS` is therefore
> **value-unfaithful**, not flag-unfaithful, and only the ~17% that is genuinely flag-only can be
> taken losslessly — which is exactly what §4 measured (cont.177). `PS2X_VU1_NOEXACT` was removed
> along with the reverted FASTFMAC experiment; only its measurement survives, above.

> **With the scheduler AND the flags both entirely free the interpreter lands near ~90–100 ns/pair
> (~2.3×) — still ~5× short of 30 fps. No interpreter-level work reaches the target. The JIT is
> not optional.**

### ★ Decomposition REFRESHED at the current baseline (cont.180, 139.7 ns/pair)

The table above was measured at a ~204 ns/pair baseline and is now **stale** — cont.177–180 removed
most of the flag bucket. Re-measured on build 238:

| ablation | ns/pair | saving | was (at ~204) |
|---|---|---|---|
| baseline (shipped) | 139.72 | — | — |
| `PS2X_VU1_NOSCHED=1` — scheduler free | **103.13** | **26.2%** | ~33% |
| `PS2X_VU1_NOFLAGS=1` — all flag work free | 130.15 | **6.8%** | ~27% |

Two conclusions:
1. **The flag bucket is essentially spent** — 27% → 6.8%, taken by lazy flags (§4) and the PCSX2
   result model (§4c). There is no third bite there.
2. **Scheduler bookkeeping is now the single biggest remaining bucket (26.2%)** — and cont.179b
   showed a *piece* of it (per-write visibility resolution) yields ~0 on its own, because the cost
   is the whole per-pair queue/commit/mark structure rather than any one step. It only pays when a
   block is translated as a unit with no pipelines at all.
3. **Even with BOTH entirely free (~95–100 ns/pair) the target is still ~5.7× away.** No remaining
   interpreter-level work, in any combination, reaches 17.0 ns/pair.

## 2. Already landed — stage 1 (1.37×) and lazy flags (1.20×)

Cumulative: **280.7 → 120.5 ns/pair = 2.33×**, all default ON with kill switches. Stage 1 is below;
lazy flags (cont.177) is §4; dispatch + operand-prologue overhead (cont.178) is §4b; the PCSX2 FMAC
result model (cont.180, the one change that is *not* bit-exact — adopted on a user decision with the
divergence measured at 0.001% of lanes) is §4c; the SSE quad clamps (cont.181, **1.206×**) are §4d.
The JIT itself is §5, where it is now **built and verified** rather than proposed.

### Stage 1 — 1.37×, bit-exact

`280.7 → 204.6 ns/pair`, ~54% of the `NOSCHED` ceiling. Both default ON with kill switches:

- **`PS2X_VU1_FASTSCAN`** — `calculatePairReadyCycle`, `markPairWrites` and `run()`'s VI-write
  probe walk only the SET bits of the lane/VI masks instead of scanning all 15 VI regs ×2 usages +
  4 lane-components per read. **1.35×.** Bit-exact by construction: same slots visited, and every
  consumer is `std::max()` or a write to an independent slot, so visit order is immaterial.
- **`PS2X_VU1_PIPEVERIFY`** (default OFF) + the `PipeTrack` **valid-slot bitmask** rework —
  `commitReadyPipelines` retires O(valid) not O(capacity); `queue*` picks a slot with one `ctz`.
  **~1.5%.** Verified 0 MASK MISS over a full run.

## 3. Disproven — do not re-attempt

**A "fast FMAC" shortcut that skips the exact double recompute.** The FMAC value is computed twice
per lane: once in float by `execUpper` (from operands it already normalized), then again in double
by `fmacExactLane`, solely so `fmacClampExact` can decide clamping and the MAC flags. It *looks*
provable that, under round-toward-zero and with identical operands, a float result with exponent in
`[1,0xFE]` and magnitude ≠ `0x7F7FFFFF` implies the exact result is in `[FLT_MIN, FLT_MAX]`, where
the exact path leaves the value alone and returns sign-only flags.

**It is false for multi-rounding ops.** `PS2X_VU1_FMACVERIFY` (run both paths, compare value+flags)
produced the counterexample immediately:

```
MISMATCH op=0x29 lane=2  fast(bits=7f7ffffe fl=0)  slow(bits=7f7fffff fl=8)
```

`op 0x29` is MADD = `acc + vs*vt` — **two roundings**. The float multiply rounds down first, so the
float sum sits just below FLT_MAX while the true sum overflows. Cancellation is the same hazard in
the other direction (exact 0 or denormal hiding behind a normal float result). Restricted to
single-rounding ops the shortcut verified clean (0 mismatches) — and measured **zero** benefit,
because MADD/MSUB dominate this workload (matrix transforms are MULA + MADDA chains). Reverted.

The multi-rounding ops, for reference: `0x08–0x0F` (MADD/MSUB bc), `0x21`, `0x23`, `0x25`, `0x27`,
`0x29`, `0x2D`, and the `0x2E` OPMULA/OPMSUB family — plus the same values in the `special` (≥0x3C)
decode.

## 4. Step 1 — LAZY FLAGS ✅ **LANDED (cont.177): 1.20×, scan-gated, default ON**

`210.7 → 175.5 ns/pair` at matched cumulative pairs (`PS2X_VU1_LAZYFLAGS`, default 1, `=0` reverts).

The MAC/status flags are derived for **every** FMAC, but they are only *observable* if something
reads them. `rebuildDecodedCodeCache` — which already walks the whole buffer and re-runs on every
code-generation change — now also answers "does this program contain a MAC/status reader?"; when it
does not (and the unit is VU1), `applyFmacDest`/`applyFmacDestAcc` skip
`calculateFmacProductSticky` + `updateFmacFlags`.

**What it does NOT skip, and why the win is 1.20× rather than the 27% `NOFLAGS` suggested:**
`normalizeFmacResult` stays on both paths, because it **clamps the lane values in place**. Only the
flag-only work is removed. The gap between lazy flags (175.5) and full `NOFLAGS` (149.1) is exactly
that clamp — real architectural work that cannot be dropped.

**Measured (build 227, one binary, matched cumulative pairs; the end-of-run number is NOT comparable
because runs reach different eras in a fixed wall time — compare at equal `pairs=`):**

| config | ns/pair @300M pairs |
|---|---|
| `LAZYFLAGS=0` (baseline of this build) | 210.70 |
| **`LAZYFLAGS=1` (shipped)** | **175.47** |
| `LAZYFLAGS=2` (force ablation) | 208.59 |
| `NOFLAGS=1` (value-unfaithful reference) | 149.12 |

⚠ **The `=2` "force" ablation is worthless as a ceiling instrument here** — feeding stale flags to
the ~1M MAC reads per run *changes what the guest does*, so it is not the same workload. The
scan-gated mode is both the correct one and the fast one.

### Correctness argument (structural) + how it was verified

Skipping touches **only** `m_state.mac` and `m_state.status` (the flag-pipeline entry sets
`writesMac`/`writesStatus`; `writesClip` is set solely by `queueClip`/`queueFcset`). VF/ACC/VI are
untouched, so the only question is whether anything reads mac/status — answered by the scan plus:

- `PS2X_VU1_LAZYVERIFY=1` over a full run: **0 MISS** (no reader ever issued while the fast path
  was armed) — the scan is exactly right.
- **The cross-program hole is real and was closed separately.** mac/status persist *across*
  programs, so a reader in a later program could observe flags an earlier fast-path program skipped.
  Reader programs exist here (122 of 1166 scanned), so this is not hypothetical. Measured:
  **`preWrite=0`** — no reader ever issues before its own program has written flags — and
  **`stsRd=0`** — no status reader (FSEQ/FSAND/FSOR) executes at all, so the accumulating sticky
  bits are moot; all 1.01M reads are MAC reads. An **always-on guard** at the reader-issue site
  latches the optimization off for the session (and reports) if a reader ever issues while flags are
  stale — `s_macDirty` clears on the next real flag write, `s_stickyDirty` only on FSSET, because a
  later commit *preserves* status bits 4..11. It never tripped (`off=0 dirtyReads=0`).
- 0 kick-drops / 0 degenerate / 0 reserved; MOVIE-END reached; correct level-era frames.

⚠ **A VRAM byte-compare at a fixed draw index does NOT work as a proof here** — the control
(same config, two runs) also differs, so the runner is not deterministic at a fixed draw index.
Run that control before trusting any such comparison.

Residual, documented: skipping the flag-pipeline entry frees a slot, so end-of-program drain cycles
can differ marginally. Both configs show identical health counters.

### Verified design inputs (audited; re-verify before building)

**Who reads the flags inside a microprogram** — all in `ps2_vu1_lower.cpp`, by lower opcode:

| opcode | insn | reads |
|---|---|---|
| `0x10` | FCEQ | clip |
| `0x12` | FCAND | clip |
| `0x13` | FCOR | clip |
| `0x1C` | FCGET | clip |
| `0x14` | FSEQ | status |
| `0x16` | FSAND | status |
| `0x17` | FSOR | status |
| `0x18` | FMEQ | mac |
| `0x1A` | FMAND | mac |
| `0x1B` | FMOR | mac |

(`0x11` FCSET and `0x15` FSSET *write* flags; they do not force flag derivation by themselves, but
treat them conservatively on the first pass.)

**★ Who reads the flags outside the interpreter — the decisive VU0/VU1 asymmetry:**

- **VU0: EE-VISIBLE.** `copyVu0StateToContext` / `copyVu0ContextToState` in `ps2_runtime.cpp`
  (~lines 236–238 and 266–269) copy `state.mac` → `ctx->vu0_mac_flags`, `state.clip` →
  `ctx->vu0_clip_flags` *and* `vu0_clip_flags2`, `state.status` → `ctx->vu0_status`, on every
  VCALLMS. **Lazy flags must be disabled for `Unit::VU0`.**
- **VU1: NOT EE-visible.** No `vu1_mac` / `vu1_clip` / `vu1_status` field exists anywhere in the
  runtime (grep returns nothing), and the only external reads of `m_vu1.state()` are
  `dBitEnabled` / `tBitEnabled` / `stoppedByD` / `stoppedByT`. So VU1 flags are observable **only**
  through the ten instructions above.
  ⚠ `PS2Runtime::vu1()` is a public accessor, so a *game override* could read them. Nothing in the
  runtime does today — re-check the game repo's `src/` before relying on this.

VU1 is the hot unit (the EE thread is ~97% VU1), so restricting the optimization to VU1 keeps
essentially all of the win.

### The implementation

`rebuildDecodedCodeCache(vuCode, codeSize, memory, generation)` already walks the **whole**
microprogram and is re-run whenever the code generation changes — that is the natural place for a
one-shot scan. Set a single "this program reads flags" bit; when it is clear and the unit is VU1,
have `applyFmacDest` / `applyFmacDestAcc` skip `normalizeFmacResult` + `updateFmacFlags` entirely
(exactly what `PS2X_VU1_NOFLAGS` already does — that flag is the ablation *and* the prototype of
the fast path).

Gate it on a new env flag, default OFF until verified, then flip. **Verification before defaulting
ON:** a shadow-compare mode in the spirit of `PS2X_VU1_PIPEVERIFY` / `PS2X_VU1_FMACVERIFY` — run
both paths and compare the architectural state a flag-reading instruction would see — plus a
re-run of the EE-visibility audit above.

Caveats to settle while building: the program-level scan must cover the *whole* code buffer (a
branch can reach anywhere), so be conservative — any flag reader anywhere in the program disables
the optimization for that program; and `flushPipelines()` at program end still commits queued flag
entries, so the skip must also avoid *queueing* them, not just deriving them.

## 4b. Dispatch overhead ✅ **LANDED (cont.178): 1.061×** — the JIT's third bucket, taken early

`177.9 → 167.6 ns/pair` (`PS2X_VU1_FASTDISPATCH`, default ON, `=0` reverts). A 100-sample profile
of the lazy-flags build showed the EE thread (96% busy) is no longer dominated by any single
computation — the biggest identifiable block is **byte-moving and dispatch**:

```
normalizeOperand 10%  execUpper 7%  <lambda>operator() 6%  run 6%  __memset_avx2_erms 6%
memcpy 5%  commitReadyPipelines 4%  queueVfWrite 4%  fmacNormOperand 4%
normalizeFmacResult 4%  getDecodedInstructionPairForPc 4%  fmacExactLane<double> 4%  memmove 2%
```

Two pieces were takeable without a JIT, both pure "same result, less work":

- **Decode cache resolved once per `run()`, not per pair.** The loop re-ran a five-field freshness
  check and returned the ~80-byte `DecodedInstructionPair` **by value** every iteration; it now
  holds a `const &` into `m_decodedCodeCache`. Safe to hoist because the guest cannot upload
  microcode while its own VU program is running (same argument as arming lazy flags once per run).
- **`run()`'s six 16-byte vf/acc scratch arrays are no longer zero-initialised** — 96 bytes of dead
  memset per pair; each is `memcpy`-filled before its only read, under the identical guard.

Verified: identical health counters both ways, clean lazy tallies, and a fully correct level-era
gameplay frame.

**And a third, larger piece from the same profile — `execUpper`'s operand prologue (1.109×).**
`normalizeOperand` was the biggest leaf (10%, plus `fmacNormOperand` 4%) because `execUpper`
normalised **fourteen floats** (`vs[4]`, `vt[4]`, `acc[4]`, `q`, `i`) unconditionally *before
looking at the opcode* — and the census says **26.4% of executed pairs have no upper op at all**.
`PS2X_VU1_FASTUPPER` (default 2; `0` = old, `1` = NOP skip only, `2` = NOP + acc skip) returns
early for upper NOP and normalises `acc` only for the ops that read it. Sound by construction,
verified mechanically against every case label: the only `acc[...]` readers are
`{0x08–0x0F, 0x21, 0x23, 0x25, 0x27, 0x29, 0x2D, 0x2E}` (main) and the same minus `0x2E` (special).
`172.39 → 155.47 ns/pair` at matched pairs.

> ⚠ **A confound worth naming, because it produced a false regression call.** A 6-frame screenshot
> burst under the new flag showed a **black 3D scene with a correct HUD** — the dropped-geometry
> signature — and the same-binary `=0` control rendered fine. That looked conclusive and was wrong:
> **when a change alters speed, wall-clock-matched samples are not state-matched** (a faster build
> sits at a different game moment). A 20-frame burst then rendered a fully detailed scene, *brighter*
> than the control. It is the same family as the ns/pair end-of-run confound. The check that actually
> settles such a question is **guest-work-matched**: `PS2X_VU1_JITCENSUS=1` on both configs compared
> at equal cumulative `pairs=` — if a value were corrupted, branch outcomes and the executed-opcode
> mix would diverge. They agreed within ±0.5pp on every bucket and on branch density (1 per 6.7 vs
> 6.8 pairs). **Prefer a guest-work-matched signature over a screenshot whenever the change is
> also a speedup.**

**Lesson for the JIT:** with flags, dispatch and the operand prologue trimmed, no remaining leaf is
large — the profile is flat. Flat profiles are exactly what a JIT fixes and an interpreter cannot:
the cost is spread across per-instruction plumbing, not concentrated in one routine worth rewriting.

## 4d. SSE quad clamps ✅ **LANDED (cont.181): 1.206×, bit-identical**

`145.3 → 120.5 ns/pair` (`PS2X_VU1_SIMD`, default ON, `=0` reverts). Unlocked directly by §4c:
once both clamps are pure functions of a lane's bits, all four lanes go at once. Engine patch
**19-vu1-detail.patch** adds `vuNormOperandBits`, `vuNormResultQuad` and `vuNormResultQuadValue`
(the last skips flag derivation entirely when lazy flags proved it dead). `PS2X_VU1_SIMDVERIFY=1`
sweeps every exponent × mantissa × sign × all 16 dest masks: **264,192 cases, 0 mismatches**.

Two further interpreter micro-optimizations were built, measured and **reverted** in this period —
per-write visibility resolution (cont.179b, 26.5% of pairs, ~0 net) and a targeted
`resetScheduler` clear (cont.181b, 0.990×). Together with §4d's success they mark the end of
interpreter-level work: the profile is flat and only a code generator changes its shape.

## 5. Step 2 — the VU1 block JIT (BUILT AND VERIFIED — see §5b for the measured verdict)

The only route to ~60 host cycles/pair. Translate a **basic block** (start PC → branch / E-bit) to
native SSE once, cached per `(code generation, start PC)` — the decode cache already has exactly
that keying and invalidation.

### Measured workload (cont.178 groundwork, `PS2X_VU1_JITCENSUS=1`, 400M pairs of real play)

```
pairs=400,017,473  branches=63,477,115  → 1 branch per 6.3 pairs  ibit=0.6%  ebit=282,899
upper:        3f=37.0% 3d=12.6% 3e=9.9% 1c=6.8% 3c=5.4% 1f=3.6% 29=3.5% 08=3.3% 00=3.1% 02=3.0%
upperSpecial: 2f=26.4% 1b=6.9%  09=6.2% 0a=5.2% 12=4.6% 1f=3.7% 15=3.3% 1d=2.9% 14=1.6% 18=1.6%
lower:        40=46.4% 01=15.1% 29=12.5% 13=6.7% 00=5.3% 08=4.6% 12=3.6% 28=2.4% 04=1.0%
```

Design consequences, in order of impact:

1. **★ Mean basic block is only ~6 pairs, and a program run averages ~1,400 pairs (≈220 blocks).**
   Per-block entry/exit overhead therefore dominates unless blocks are **chained/linked** — a
   translate-and-return-to-dispatcher design would spend most of its time in dispatch. This is why
   PCSX2's microVU compiles whole programs with linked blocks; mirror that, do not build a
   one-block-at-a-time trampoline.
2. **Coverage is cheap to reach.** `upper 0x3C–0x3F` (the `special` block) is **64.9%** of pairs,
   and within it `special 0x2F` — *no upper op* — is 26.4% of all pairs. On the lower side
   `0x40` (NOP) is 46.4%. So roughly a quarter of pairs have no upper work and nearly half have no
   lower work: a first JIT slice covering NOP + the top ~10 upper and ~8 lower opcodes already
   covers the overwhelming majority of executed instructions.
3. **Loads/stores are the single busiest real lower op** (`0x01` store 15.1%, `0x00` load 5.3%),
   ahead of everything except branches — so VU-memory addressing must be fast from day one, not an
   afterthought.
4. **★ Clip-flag readers are ~10.3% of all pairs** (`0x13` FCOR 6.7% + `0x12` FCAND 3.6%). This
   independently confirms the §4 decision to exclude CLIP readers from the lazy-flag gate: had they
   been included, essentially no program would have qualified and lazy flags would have measured
   nothing. The JIT must treat clip as a *live* value while mac/status stay skippable.

### ★★ The scheduler measurement that reshapes the design (cont.179)

`PS2X_VU1_JITCENSUS=1` now also reports a **scheduler census**. Over 440M pairs of real play:

```
[vu1:schedcensus] readyCalls=441,349,149 (1.003 per pair)
                  stalledPairs=1,287,242 (0.3%)  stallCycles=3,115,407 (2.42 per stalled pair)
```

**Only 0.3% of pairs ever stall.** That is not a surprise in hindsight — VU1 has no VF interlocks,
so the game's microcode is *compiler-scheduled* to avoid hazards; the stall model almost never
fires. It has three consequences the JIT design must absorb:

1. **The `NOSCHED` 33% is NOT stalls — it is bookkeeping.** What costs is `markPairWrites` writing
   the ready tables, `queueVfWrite`/`queueAccWrite` filling pipeline slots, and
   `commitReadyPipelines` retiring them (profile: queueVfWrite 4% + commit 4% + retire lambda 6% +
   markPairWrites 2% + calculatePairReadyCycle 2% ≈ 18%). Optimising the *stall computation* is
   therefore near-worthless — a hoist of the provably-invariant reduction inside the stall loop
   (the ready tables are written only by `markPairWrites`/`reset()`, never by `advanceTo`) would
   save 0.003 calls per pair. **Measured before it was built; not built.**
2. **The pipeline exists for WRITE VISIBILITY, not for stalls, and that part is architecturally
   load-bearing.** A queued VF write becomes visible only after its latency, so a read inside that
   window legitimately returns the old value (this is what `run()`'s save/restore "shadow dance"
   implements). The JIT cannot simply drop it.
3. **⇒ The JIT's answer is COMPILE-TIME REGISTER RENAMING, not a faster runtime pipeline.** Within
   a block, which read sees which prior write is a *static* property once the issue cycles are
   known — and with stalls at 0.3% the issue schedule is essentially "one pair per cycle" with a
   rare, statically-computable correction. So a translated block should carry no pipeline arrays at
   all: each read is wired directly to the correct producer at translate time, and only block
   entry/exit reconciles with the interpreter's dynamic state. That eliminates buckets 1 and 2
   together, which is where the 33% actually lives.

What it must eliminate, in measured priority order:

1. **The cycle-accurate scheduler's BOOKKEEPING (~33%, per the `NOSCHED` ablation)** — not by
   computing stalls faster, but by **compile-time register renaming** so translated blocks carry no
   ready tables and no write pipelines (see the census above). Only block entry/exit needs dynamic
   state.
2. **Flag derivation (~27%)** — fold in step 1's per-program analysis, and go further: a JIT can do
   it *per block*, and can compute flags only for the last writer before a reader.
3. **Per-instruction overhead** — keep operands in XMM registers instead of the save/restore
   `memcpy` shadow dance in `run()`; dest masks become blends; the decoded-pair by-value copy and
   the `switch` dispatch disappear entirely.

## 4c. PCSX2 FMAC result model ✅ **ADOPTED (cont.180): 1.104×** — the JIT's value representation

`158.1 → 143.2 ns/pair` (`PS2X_VU1_FLOATCLAMP`, default ON, `=0` restores the exact model).
**This is the one change in the arc that is not bit-exact**, taken on a user decision after the
divergence was measured rather than assumed.

Our model computed each lane twice — float, then **double** so the clamp and flags came from the
exact result. PCSX2 derives both purely from the float result's bits (`VUflags.cpp VU_MAC_UPDATE`:
sign; `f==0` → Z; `exp==0` → Z|U and flush to signed zero; `exp==255` → O and clamp to
`sign|0x7F7FFFFF`). **Our `normalizeResult` already was a bit-exact re-implementation of that
function**, so adopting PCSX2's model meant routing every lane through it and deleting the double
recompute — no new arithmetic.

**Measured divergence over 801,234,541 lanes** (`PS2X_VU1_FLOATVERIFY=1` runs both and tallies while
keeping the exact answer):

| | count | rate |
|---|---|---|
| value differs | 8,391 | **0.001047%** (1 in ~95,000) |
| flags differ | 23,602 | 0.002946% (1 in ~34,000) |

The dominant case is **flags-only with an identical value** — `op=0x1c raw=7f7fffff
float=…/f0 exact=…/f8`: the float result saturates to exactly FLT_MAX without becoming Inf, so the
float model sees a normal number where the exact model flags overflow. Same narrow multi-rounding
class as the §3 counterexample. With lazy flags those MAC flags are unobserved in ~89% of programs
and no status reader executes at all. Verified: 0 degenerate primitives and 0 kick-drops over 14.3M
sampled draws, MOVIE-END, correct level frames.

**Why it matters far more for the JIT than the 10% suggests:** the exact path is four *scalar*
double computations plus per-lane branches per instruction — unvectorisable, and it would dominate a
translated block. The float model is a pure function of the result bits, which becomes a handful of
SSE ops across all four lanes at once. That is the shape ~60 host cycles/pair requires.

### (settled) The decision native codegen ran into: EXACTNESS vs VECTORISATION

Our FMAC model computes each lane **twice**: once in float (`execUpper`), then again in **double**
(`fmacExactLane`) so `fmacClampExact` can decide the clamp and the flags from the exact result.
That is *more* precise than the reference implementation — PCSX2's `VUops.cpp vuDouble` computes VU
arithmetic in plain **float**. Lazy flags did not remove it, because the exact value also drives the
**clamp**, which is architecturally visible in VF/ACC (§4).

For an interpreter that costs ~8% (`fmacExactLane<double>` 4% + `normalizeFmacResult` 4%). **For a
JIT it is the difference between vectorised and not:**

- **Float-only (PCSX2 model):** one quad FMAC + a clamp sequence — a handful of SSE instructions
  covering all four lanes at once. This is the shape that reaches ~60 host cycles/pair.
- **Exact (current model):** four *scalar* double computations plus per-lane comparisons and
  branches, per instruction. It cannot be vectorised, and in a translated block it would dominate
  everything else the JIT saves.

So the 30 fps target and the current exactness model are in tension, and this is a **judgement call
about what the project values**, not something measurement settles:

| option | fidelity | reaches 30 fps? |
|---|---|---|
| **A — keep exact double** | bit-exact with today's interpreter; strictly more precise than PCSX2 | very unlikely — the exact path alone would dominate a translated block |
| **B — PCSX2 float model in the JIT** | matches the reference emulator that ships this game at full speed; differs from our interpreter in rare double-rounding cases | this is the shape that gets there |
| **C — B with the interpreter as an opt-in oracle** | ship B; keep the exact interpreter in-tree and shadow-verify against it, accepting known divergence classes | same as B, with the divergence measured rather than assumed |

The disproven FMAC shortcut (§3) is evidence the divergence is real but narrow: it showed up only
for **multi-rounding ops** (MADD/MSUB), one ULP at the FLT_MAX boundary. **C is the recommended
route** — build the float path, and use the existing verify machinery to *measure* how often and how
far it diverges on real frames instead of arguing about it. Settle this before writing the emitter,
because it decides the entire value representation (XMM quad vs per-lane scalar).

**Verification methodology — reuse what already worked.** Keep the interpreter in-tree forever as
the bit-exact oracle and shadow-verify the JIT against it, the same way the GPU rasterizer arc was
made trustworthy (per-primitive compare → whole-buffer compare → authority flip). Every wall in
that arc was found by a verify mode, not by reasoning; the same will be true here.

**Mirror PCSX2's microVU** for the translation strategy and the hardware corner cases, per the
standing PCSX2 rule in [resources.md](resources.md) — then verify against our own generated
code + disasm + gdb.

## 5i. The lower slot is DONE, and tier 6 sizes block assembly (cont.194)

IADDIU/ISUBIU (4.6%) and the four clip readers (10.3%) are emitted and verified, which **finishes
the lower slot** for everything block assembly can use. Lower-slot codegen now covers
**NOP 46.4% + SQ 15.1% + LQ 5.3% + clip readers 10.3% + IADDIU/ISUBIU 4.6%**.

### The VI-write question, settled

§5h recorded the constraint; here is the resolution, in two independent parts.

**(a) An immediate VI write IS equivalent to the queued one — for these ops.** Every VI write goes
through `queueViWrite(reg, value, latency)`, and for all six emitted ops `decodeLowerUsage` gives
`PipelineIalu, latency = 1` (cases `0x08/0x09`, `0x10/0x12/0x13`, `0x1C`), so
`readyCycle = m_cycle + 1`. The run loop calls `advanceOneCycle()` at the **bottom** of each pair's
iteration (`ps2_vu1_core.cpp:3149`), and that does `++m_cycle; commitReadyPipelines();` — so the
write commits before pair i+1 begins, and nothing between the queue and that commit reads VI (a pair
has exactly one lower instruction, and upper ops never read VI). This is the same argument SQ used,
and it is *tighter* than §5h stated: the commit happens at the end of pair i, not the top of i+1.

**(b) The branch-read backup is NOT equivalent, and is now a reported per-instruction fact.**
`LowerPlan::delaysNextBranchRead` carries it, and it is **not uniform**:

| op | `delaysNextBranchRead` |
|---|---|
| IADDIU / ISUBIU | **true** |
| FCEQ / FCAND / FCOR / FCGET | false |

The emitters deliberately do **not** write the backup — it lives in the interpreter object, not in
`VU1State`, and the emitted functions take only the state pointer. **Honouring it is the block
compiler's contract**: exclude a `delaysNextBranchRead` plan from a block's final pair, or replicate
`recordViWriteForBranch` at the block exit.

### ★★ ILW cannot use that equivalence — and tier 5 was therefore not reachable

`ILW` (0x04) is the **first lower op whose VI write is not latency-1**: its usage is `PipelineLsu`
with **`latency = 4`**, so its value is invisible for four cycles and an immediate write is wrong.
Modelling it needs a real VI write pipeline inside a block. That makes the old tier 5 figure
misleading, because tier 5 is cumulative and silently assumed ILW/ISW.

So the census grew a **tier 6 — the emitted-today lower set plus branch terminators** (tier 3 +
branch, deliberately *not* cumulative with tier 4). Measured over 800M pairs of real play:

| tier | compilable | mean run | pairs in runs ≥2 |
|---|---|---|---|
| 3 (+clip readers) — no branch terminators | 43.4% | 1.41 | 17.0% |
| 4 (+ILW/ISW) | 44.7% | 1.46 | 18.5% |
| 5 (+branch, **assumes ILW/ISW**) | 61.1% | 2.82 | 51.9% |
| **6 — EMITTED TODAY + branch (reachable now)** | **59.8%** | **2.71** | **50.3%** |

**⇒ ILW/ISW is worth 1.3 points of compilable and 1.6 of runs≥2. Do NOT build a latency-4 VI write
pipeline before block assembly** — go straight to blocks and treat ILW as a terminator like a branch.

Sizing with §5d's measured block cost (2-pair block 7.90 ns/pair vs the 120.5 interpreter):
`0.503 × 7.9 + 0.497 × 120.5 ≈ 64 ns/pair ≈ 1.9×` **if transitions were free — they are not.**
17 ns/pair still needs ~85% coverage, which only arrives once blocks LINK.

### How it was verified — and the step that mattered

- **70,161 cases, 0 mismatches**, driven through the real `classifyLowerPlan` → `emitLowerPlan` path
  (not the emitters directly), against a reference **copied** from `ps2_vu1_lower.cpp`.
- **★ MUTATION TESTING, and it earned its keep.** A test that passes on the first try has proven
  nothing until it is shown capable of failing. Ten deliberate defects were injected — dropped
  `movsx` truncation, an extra mask on FCOR, `sete` for `setne`, `0xFFFFFF` for FCGET's `0xFFF`, a
  sign-extended imm15, ISUBIU emitted as add, a dropped `it != 0` guard, and both directions of the
  branch-delay flag. Nine died immediately; **the tenth survived** — swapping SQ's base register
  from `VIT` to `VIS`. That exposed a real gap: `selfTestLQ` calls `emitLQ`/`emitSQ` **directly**, so
  nothing tested which instruction FIELD feeds which argument. A decode-contract block now asserts
  every plan field against the interpreter's own accessors (`ps2_vu1_detail.h`), and all ten die.
- **Disassembly cross-check.** The hand-rolled encodings were dumped and run through `objdump`, since
  emitted bytes that happen to work for the tested registers can still be wrong in general. Offsets
  confirmed against the struct: `vi[1]` = `rdi+0x204`, `vi[4]` = `+0x210`, `clip` = `+0x268`.
- **Live, in-game, on the guest thread**: all five self-tests PASS (no `SIGBUS` — the `static`
  discipline from §5h held), and the JIT shadow-verify reached **540,981,434 executions, 0
  mismatches**, its highest yet.

**Next: block assembly** — emit a run of pairs as one function with operands in registers across
pairs, branches and ILW as terminators, honouring `delaysNextBranchRead` on the final pair.

## 5j. BLOCK ASSEMBLY — built, verified, and coverage-limited (cont.195)

`PS2X_VU1_BLOCK=1` (**default OFF**) replaces the interpreter's inner loop, for a run of consecutive
pairs, with a single call into natively compiled code. It is **correct** — 24.6 billion
shadow-verified slot comparisons, 0 mismatches — and it is **not yet a speedup**, for a reason the
census pins down exactly.

### The correctness argument that makes a block cheap

The interpreter defers every write through `queueVfWrite`/`queueAccWrite`/`queueViWrite` and only
makes it visible at `readyCycle`, so a block would seem to need the whole pipeline. It does not,
because **a reader always stalls until its operand is ready**: `calculatePairReadyCycle` takes `max`
over `m_vfReady[reg][lane]` for every read lane (and `m_viReady`/`m_accReady`), and `run()` then does
`while (readyCycle > m_cycle) advanceTo(...)`, which advances a cycle at a time calling
`commitReadyPipelines()`. **A stale read cannot happen.** So applying writes IMMEDIATELY inside a
block yields the same values, and the pipeline is reproduced only as *timing* (cycle count + ready
tables), computed in C++ after the call. WAW agrees too: immediate writes make the last writer win,
which is where the interpreter's `m_vfLatestWrite == sequence` supersession check also lands.

**Intra-pair ordering** is handled by exploiting opcode orthogonality rather than replicating the
`upperVfShadowReg` dance: SQ / IADDIU / ISUBIU / clip readers are emitted BEFORE the upper (SQ must
see the pre-pair VF value; the VI/clip ops touch neither VF nor ACC, and the upper touches neither VI
nor clip), and LQ AFTER it (the upper must see the pre-pair value of LQ's destination). When LQ's
destination *is* the upper's destination the interpreter suppresses the lower write entirely, so the
emitter skips it. No scratch register, no cut, and every classified pair compiles.

**Stalls are absorbed, not avoided.** The first version cut a block at any pair that would stall on
an earlier pair of the same block — which throws away most of the reach at FMAC latency 4. Stalls are
static once issue cycles are known, so `planBlock` schedules them: each pair carries its issue cycle
relative to block entry, ready-table entries are expressed against that, and the block consumes
`lastIssue + 1` cycles. (This did *not* lengthen blocks — mean stayed 3.4 — confirming block length
is bounded by opcode coverage, not hazards.)

**The cont.194 branch-delay contract is honoured**: a branch in the pair after a block must still
read the OLD value of a VI register the block's final pair wrote, so the driver captures it before
the call and replays `recordViWriteForBranch` after. `planBlock` only allows that when no earlier
pair of the block wrote the same register; otherwise it drops the final pair.

### ★★ The measured verdict: 6.4% coverage, ~1% — inside the noise

| | |
|---|---|
| block coverage | **6.4% of executed pairs** |
| mean block | 3.55 pairs |
| shadow-verified | **24,654,237,535 comparisons, 0 mismatches** |
| ns/pair (matched cumulative pairs, in-binary A/B) | 126.1–126.6 on vs 127.6–128.2 off |
| honest verdict | **~1%, and the baseline itself swings 124.8–128.2 across the same sweep ⇒ not resolvable** |

This is exactly what §5e predicted: **partial coverage cannot pay.** At 6.4% coverage the ceiling is
~6%, and guard checks plus block-entry overhead eat most of that.

### ★★★ The single lever, measured: pending VF writes (99.7% of rejections)

Entry is attempted far more often than it succeeds — `guardBlocked=14,355,964` vs `entered=8,362,044`
— and the per-condition census is unambiguous:

| guard condition | rejections |
|---|---|
| **pending VF write intersects the block's touched slots** | **14,318,821 (99.7%)** |
| pending clip-writing flag entry (block reads clip) | 32,397 |
| FDIV pending and the block reads Q | 4,232 |
| pending VI write | 514 |
| pending ACC write / pending store / budget | 0 |

The guard rejects a block when an incoming pending write targets a slot the block reads or writes
(read → the interpreter would have stalled for it; write → the pending write commits later and
clobbers the block's newer value). With FMAC latency 4 and continuous issue, ~4 VF writes are always
in flight, so almost every block intersects one.

**The fix is already argued, and it is the same stall argument:** committing an intersecting pending
write EARLY is value-equivalent, because any pair that would read that slot before its `readyCycle`
stalls until it commits anyway — so no pair can observe the old value. What early commit costs is
*cycle fidelity*, and that is recomputable at entry (the block's per-pair read sets are static; the
incoming ready cycles are the only dynamic input). The alternative is a truncatable block — a
`dec/jz` early exit after each pair — so the driver can run the longest safe prefix instead of
rejecting the whole block.

**Next, in order:** (1) admit blocks past pending VF writes by early-commit-plus-reschedule or by
prefix truncation; (2) **link blocks**, so execution stops returning to the interpreter between them;
(3) keep widening opcode coverage, which is what bounds block length (mean 3.55 vs the tier-6 mean
run of 2.71).

⚠ Three harness traps this cost a cycle each to find, all in the verifier rather than the codegen:
**never start a nested verification** (the pair after a block entry usually has a block of its own,
and overwriting the reference mid-window compares the wrong two states — 2.67M bogus mismatches);
**skip slots with a write pending at block ENTRY** (the block cannot see them, the interpreter commits
them during the window); and **give blocks their own code buffer** (they are invalidated on every
microcode upload, and emitted code otherwise accumulates until the 8 MB buffer is exhausted and every
later block is silently rejected — which froze coverage mid-run).

## 5k. Admitting blocks past pending VF writes (cont.196) — and the ceiling's hidden assumption

§5j measured the one lever: 99.7% of block-entry rejections were a pending VF write intersecting the
block's touched slots. That guard is now gone, and the result is **exactly double the coverage and
about double the (still small) gain** — plus the discovery of why the gain is small.

### How a pending VF write is handled instead of rejected

Three pieces, all following from the same stall argument that made immediate writes safe in §5j:

1. **Apply it early.** Any pair that would read that slot before the write's `readyCycle` stalls
   until it commits, so the old value is *unobservable* — applying it at block entry cannot change
   any value the block reads. Uses the interpreter's own supersession rule
   (`m_vfLatestWrite[reg][lane] == write.sequence`), so only the live write to a lane is applied.
2. **Re-derive the schedule.** What early application costs is *cycle fidelity*: the reader really
   would have stalled. Each pair's reads are static (stored per pair in `Block::sched`) and the
   incoming ready cycles are the only dynamic input, so one forward pass shifts the static schedule:
   `want = issueStatic[j] + shift`, raised by each read's incoming ready cycle, and `shift` carried
   forward. `shift` is monotonic and the static schedule already encodes the block's internal
   dependencies, so shifting preserves them and one pass suffices.
3. **Bump `m_vfLatestWrite` afterwards** for every slot the block wrote, so a write still sitting in
   the pipeline cannot commit later and clobber the block's newer value.

Only **1.4% of block entries actually needed a schedule shift** (`resched` 125,821 of 12,548,156) —
so the overwhelming majority of the rejected intersections had been *writes*, not reads.

| | cont.195 (strict) | cont.196 |
|---|---|---|
| guard rejections | 14,355,964 | **407,520** (VF: 14,318,821 → **0**) |
| coverage | 6.4% | **12.8%** |
| shadow-verified | 24.7 G comparisons | **34.3 G comparisons, 0 mismatches** |
| in-binary A/B, matched pairs | ~1.2% | **~2% (1.005–1.051×)** |

★ The verifier now also checks **cycle fidelity** — the register comparison cannot catch a
scheduling error, and the schedule is the risky part. `PS2X_VU1_BLOCKSTRICT=1` restores the old
guard for same-binary A/B.

### ⚠ A measurement trap that nearly produced two false conclusions

Absolute ns/pair **is not comparable across measurement sessions**. Over the hours this arc took, the
machine drifted: the *same* config read ~128 ns/pair early on and ~145–152 later. Two intermediate
readings ("132, a regression!" then "142, worse still!") were pure drift — the matched in-binary
baselines were 145.6 and 148–152 respectively, i.e. blocks were *ahead* both times. **Only
BLOCK=1-vs-BLOCK=0 in the SAME binary at MATCHED cumulative `pairs=` means anything**, and even that
needs several sample points (the baseline alone swings 147.3–152.7 across one sweep).

### ★★★ Why 12.8% coverage buys only 2%: the ceiling assumed register allocation

§5d measured a compiled 6-pair block at **7.72 ns/pair** and concluded 30 fps is reachable. That
benchmark kept operands **in XMM registers across pairs**. The emitter as built does not: every
instruction loads its operands from `VU1State` and stores its result back, because `emitUpper` is
addressed entirely off `rdi` + displacement. So a real block is nowhere near 7.72 ns/pair, and at
12.8% coverage the arithmetic works out to roughly the ~2% observed rather than the ~9% a 7.72
ns/pair block would give.

**⇒ The next lever is not more coverage — it is cross-pair REGISTER ALLOCATION inside a block**,
which is what §5d actually measured. Then block linking, so execution stops returning to the
interpreter between blocks (mean block is only 3.47 pairs). Per-entry bookkeeping (guard, reschedule,
ready replay, supersession) is third: it was cut down by driving every loop off the cont.175
`PipeTrack` valid-slot bitmasks and by skipping the mask computation entirely for the ~88% of pairs
with no compiled block, which is worth having but did not move the number outside the noise.

## 5l. Where the block path actually spends its time (cont.197) — the emitted code is NOT the problem

§5k guessed that blocks were slow because the emitter keeps no operands in registers across pairs.
**That guess was wrong**, and one instrument settled it: `PS2X_VU1_BLOCKPROF=1` rdtsc-times each
*segment* of the block fast path. (Timing, not ablation — ablating a block would corrupt VU1 output,
which feeds the geometry the guest then processes, so the workload itself would diverge and the
comparison would be meaningless.)

| segment | cycles/entry (before) | cycles/entry (after §5l fix) |
|---|---|---|
| **lookup + guard** | **300** | **38** |
| schedule re-derive | 72 | 78 |
| early-apply | 27 | 27 |
| **`fn` — the emitted block itself** | **52** | 59 |
| retire (ready tables + supersession) | 65 | 65 |

★★ **The emitted code runs at ~16–19 cycles/pair (≈5 ns/pair) — better than the 7.72 ns/pair §5d
measured.** Codegen was never the bottleneck. Everything around it was.

**The fix:** every arming condition for the fast path (`s_vu1Block`, unit, lazy-flag arming, decode
cache, `vuData`, the `dataSize` power-of-two test) is **loop-invariant**, and so is the block store's
generation check — the guest cannot upload microcode while its own program runs (the cont.178
argument). Hoisting them out of the pair loop, and re-encoding `byPair` so that **0 means
known-uncompilable** (so the per-pair test is a single array load and a compare against zero,
with no call), took `lookupMiss` from **248,134,039 to 237,365** — a thousandfold — and the segment
from 300 to 38 cycles.

⚠ **But the end-to-end number barely moved (≈1.03× both before and after), and the reason is a
measurement lesson**: the 300-cycle figure was **largely the profiler's own cost**. The lookup
segment ran on all 263M pairs, and each miss paid two `rdtsc` (~20–30 cycles each), so the
instrument inflated precisely the path it was measuring. *An rdtsc probe on a path that executes
hundreds of millions of times measures itself.* The restructure is still right — it removes real
per-pair instructions — but its share of the win was much smaller than the profile implied.

### Current state and the real remaining costs

**~1.03× at 12.5% coverage** (matched in-binary A/B: 116.17/115.72 ns/pair with blocks vs
120.50/120.53 without, against a very stable baseline), **39,574,159,276 shadow-verified
comparisons, 0 mismatches** including cycle fidelity.

Per entry the driver now costs roughly `sched 78 + retire 65 + apply 27 + lookup 38 = 208` cycles
around `fn`'s 59 — so **the bookkeeping still outweighs the compiled code 3.5:1**, over a mean block
of only 3.16 pairs. In order:

1. **`retire` (65)** — replaying ready-table entries and bumping `m_vfLatestWrite`, ~16 scattered
   writes into two 1 KB arrays per entry. This is work the interpreter also does; a block should be
   able to collapse it (one write per *slot*, not per write).
2. **`sched` (78)** — the entry re-derivation. Only **1.4% of entries actually need a shift** (§5k),
   so the common case should be a cheap proof that no shift is needed rather than a full pass.
3. **Amortise all of it over more pairs** — mean block is 3.16. This is what **linking** buys, and it
   is the only lever that changes the ratio structurally rather than shaving constants.

## 5m. The arc completed: 91.2% coverage, 12.4× frame-matched — and VU1 is no longer the wall

cont.198–205 took the block JIT from "correct but not a speedup" to the thing that removes VU1 as
the bottleneck. Every step was shadow-verified with zero mismatches.

| step | what | coverage | note |
|---|---|---|---|
| cont.198 | **branches execute inside blocks** (branch pair + delay slot, block writes `pc`) | 12.5% → 36.2% | 1.03× → 1.14× |
| cont.199 | lower-special: IADD/ISUB/IADDI/IAND/IOR, MOVE/MR32, MTIR/MFIR | → 64.9% | 1.27× |
| cont.200 | ILW/ISW/ILWR/ISWR, LQI/SQI/LQD/SQD | → 74.1% | |
| cont.201 | **pending clip/Q applied early when provably due** | → 81.6% | guard blocks 41.3M → 826K |
| cont.204 | **flag-emitting mode** — blocks run in non-lazy programs | → 90.8% | 9.3 → 18.2 fps |
| cont.205 | CLIP | → **91.2%** | |

### The two ideas that did the work

**1. Branches belong INSIDE the block.** A branch every ~6.1 pairs meant leaving the branch *and its
delay slot* to the interpreter was most of what a block could otherwise cover. The block now runs
both and writes `m_state.pc` itself. Targets are compile-time constants except JR/JALR; the selected
target lives in **ECX**, which no other emitter touches, so it survives the delay slot. ★ That
reservation is load-bearing and was violated once — a VI ALU form used ECX as a scratch, so an IADD
in a delay slot clobbered the pending target and the block jumped to a raw VI value (`pc=0x2`,
`0xffff8000`). The second operand now comes straight from memory.

**2. Stash the inputs, replay the awkward part in C++.** MAC flags, CLIP and the product-sticky
question all looked like they needed flag-pipeline emission. They did not. The block stores the
value the interpreter's own helper needs — an FMAC's **pre-clamp result**, or CLIP's two operand
quads — and the driver replays `updateFmacFlags` / `queueClip` **at that instruction's own issue
cycle** (by setting `m_cycle` around the call). The result is bit-exact *by construction* because it
runs the interpreter's code, and it converted the single largest remaining bucket: blocks were
disabled wholesale for programs with a flag reader, which was **68.8% of all still-interpreted
cycles**.
- Its one deliberate gap: `calculateFmacProductSticky` needs the operands, gone by replay time, and
  only affects `status` — so flag-emitting mode requires the program to contain no STATUS reader.
  cont.177 measured `stsRd=0` across full runs, so that is the common case.

### ★★★ VU1 IS NO LONGER THE WALL — the GPU arc must be UNPAUSED

**VU1 is now 11.6% of wall time (34.59 s of 297.3 s).** It was ~97% of the EE thread when this arc
started. *Making VU1 entirely free could not reach 30 fps.*

Ablating the CPU rasterizer (`PS2X_GS_NORASTER=1`) on **matched guest-frame intervals**:

| interval | raster on | raster ablated | ratio |
|---|---|---|---|
| f=1920→2040 | 6.6 s | 7.7 s | 0.86× |
| f=2040→2160 | 12.6 s | 11.8 s | 1.07× |
| **f=2160→2280** | **58.4 s** | **16.7 s** | **3.50×** |

**⇒ In heavy scenes the GS CPU rasterizer is now the bottleneck.** §1's standing conclusion — that
the GPU arc stays paused because raster ablation bought nothing — was measured *while VU1 dominated*
and no longer holds. That is the next frontier, not more VU1 work.

### ⚠ Measurement lessons from this arc (all of them cost a cycle)

1. **Cumulative ns/pair is useless here** and even *interval* ns/pair is confounded: the game is
   real-time driven, so a faster build reaches different content at the same cumulative pair count.
   The only honest metric is **wall time for a FIXED guest-frame interval** (`[loadkick:frame] f=`
   correlated with the `[gsgpu:thruput]` elapsed stamp). It showed 6.4× where cumulative ns/pair
   showed a *regression*.
2. **Absolute ns/pair is not comparable across measurement sessions** — machine drift moved the same
   config from ~128 to ~152 over this arc.
3. **An rdtsc probe on a path that runs hundreds of millions of times measures itself** (§5l).
4. **Verify what you just changed, not what you already trusted.** Three separate "mismatch storms"
   were bugs in the *verifier*, not the codegen — a nested verification overwriting the reference, a
   flag derivation reading the scratch **before** `blk->fn` ran, and comparing committed
   `mac`/`clip` in a mode where the driver's replay is deliberately skipped. Each was diagnosed by
   the pattern of the wrong values (they belonged to a *neighbouring* block).

## 6. Measurement discipline (non-negotiable for this arc)

- **Iterate on `PS2X_VU1_PERF` — ns per issued instruction pair.** 0.2% run-to-run noise. Wall-clock
  frame timing is **bimodal** here (baseline runs cluster at ~95 s *or* ~161 s by scene regime) and
  read a real 1.35× change as "inconclusive".
- **Confirm end-to-end with frame-matched guest frames**, never cumulative presents/s (diluted ~4:1
  by the ~217 s boot/movie era: a +38% level-era change reads as +1.7%) and never prims/s.
  See [debugging.md](debugging.md) and the game repo's `tmp/ab175ts.sh`.
- **Re-measure the baseline OF THE BUILD UNDER TEST.** A per-lane loop restructure cost 4%
  (204.6 → 212.9) and, measured against the *previous* build, made a zero-value change look
  like +4.3%.
- **★ Never pick a target from sampling-profile leaf share.** Three times now a large leaf
  delivered a fraction of its share: an 18% `commitReadyPipelines` leaf bought 1.5% (the work moved
  to a lambda's `operator()`); FMAC leaves summing to ~15% bought 7.5% when fully ablated. A hot
  leaf is often *call frequency*, not body cost. Use the profile to find candidates; **ablate the
  subsystem to size it**, and let ns/pair decide.

## 7. Instruments (all default OFF unless noted)

| flag | what it does |
|---|---|
| `PS2X_VU1_PERF=1` | ns/pair + pairs/call throughput report |
| `PS2X_VU1_NOSCHED=1` | ablation: scheduler free (~33%) |
| `PS2X_VU1_NOFLAGS=1` | ablation: all FMAC flag work free (~27%) — **value-unfaithful** (skips the clamp), so it is an upper bound, not a target |
| `PS2X_VU1_LAZYFLAGS` | **default 1 (ON)**, `=0` reverts; `=2` forces the fast path (behaviour-changing ablation, see §4) |
| `PS2X_VU1_LAZYVERIFY=1` | lazy-flag self-check + tallies (scan MISS, macRd/stsRd, preWrite, dirtyReads) |
| `PS2X_VU1_FASTSCAN` | **default ON**, `=0` reverts stage 1a |
| `PS2X_VU1_PIPEVERIFY=1` | stage 1b valid-mask self-check |
| `PS2X_VU1_COMMITSKIP` | **default ON**, `=0` reverts the cont.158b watermark |
| `PS2X_VU1_EXACTLD=1` | exact lane math in `long double` instead of `double` |


## 5b. What was actually built (cont.182–183), and the number that decides the rest

**Engine patch 24-vu1-jit.patch** — `ps2_vu1_jit.h`: an RWX code buffer, a minimal x86-64/SSE4.1
emitter, an instruction compiler and a cache. Included only by `ps2_vu1_core.cpp` and
`ps2_vu1_upper.cpp`, so no new translation unit and no CMakeLists change.

Deliberate constraints that removed bug classes: **xmm0–xmm7 and rdi/rsi/rdx/rcx only, so no REX
prefix is ever emitted**; `pand`/`pandn`/`por` for select rather than `blendvps` (which would pin
the mask to xmm0); everything branchless.

> ⚠ **The first version segfaulted for a reason worth remembering: RIP-relative displacements are
> signed 32-bit, but `mmap` puts the code buffer far more than ±2GB from the binary's data
> segment**, so the displacement to a `static` constant pool truncated silently. The pool now lives
> **inside the mapping**. `PS2X_VU1_JITDUMP=1` prints the emitted bytes.

**Verification, three independent layers, all zero-mismatch:**

| layer | cases | result |
|---|---|---|
| emitted operand clamp vs C++ | 10,240 | 0 mismatches |
| emitted **full FMAC pair** (5 ops × 5 broadcasts × all 16 dest masks × 24 random states) | 38,400 | 0 mismatches |
| **live shadow-verify against the interpreter during real play** | **138,889,257** | **0 mismatches** |

The live harness runs the JIT, captures its writes, restores the state, re-enters `execUpper`
behind a recursion guard so the interpreter yields the authoritative result, and compares.

**★★ The measurement that decides the architecture: 40.8% of all executed pairs were compiled, and
it bought 1.032×.**

A pair costs ~420 host cycles. After §4d, `execUpper` is only ~15–20% of that; the rest is the
interpreter **loop** — decode fetch, the scheduler's queue/commit/mark (26.2% by ablation), the
shadow-dance memcpys, `execLower`, run() bookkeeping. Replacing `execUpper` alone therefore cannot
pay, whatever the coverage, and the per-pair call plus cache lookup eats most of what it does save.

**⇒ The remaining work is not more codegen — it is making a compiled block subsume the loop:**

1. **Compile runs of consecutive pairs into one function** (mean block ≈ 6 pairs, §5 census), so
   there is one call per block instead of per pair and operands stay in registers between pairs.
2. **Drop the pipeline inside a block** using the cont.179b write-visibility analysis — with its two
   hard-won details: **branch delay slots must be excluded** (their forward window never executes,
   and the branch target lands inside the latency window), and the backward direction needs no
   analysis because bumping `m_vfLatestWrite` supersedes older pending writes.
3. **Cover the common lower opcodes** so runs are not cut short: `0x01` store 15.1%, `0x00` load
   5.3%, `0x08` IADDIU 4.6%.

The JIT ships **default OFF** (`PS2X_VU1_JIT=1` to enable): +3% does not justify running
hand-encoded machine code against guest state by default. It is the verified foundation for step 1
above, not a shippable optimization at instruction granularity.


## 5c. The block-JIT ceiling, measured before building it (cont.184)

§5b concluded a JIT pays only when a block subsumes the loop. The obvious first block shape — runs
of consecutive pairs whose **lower is NOP** (no upper/lower interleaving to model) and whose upper
is a covered FMAC op — was measured first:

```
[vu1:blockcensus] compilablePairs=14572797 (4.0%) runs=9511643 meanRun=1.53
                  pairsInRunsGe2=6718607 (1.9%)
                  hist= 1:7854190  2:770437  3:186225  4:398142  10:302649
```

**Only 1.9% of executed pairs sit in a compilable run of length ≥ 2.** Against the **40.8%** the
per-instruction JIT covered on uppers alone, the entire gap is the lower slot: VU1 microcode uses
dual issue heavily, so `lower == NOP` discards ~95% of the opportunity.

> **⇒ An upper-only block JIT is a dead end.** The block compiler must translate the LOWER
> instructions as well — by executed share: `0x01` store 15.1%, clip readers `0x13`+`0x12` 10.3%,
> `0x00` load 5.3%, `0x08` IADDIU 4.6% — plus the branches that terminate blocks.

**Ceiling refreshed at the current baseline** (build 246): `PS2X_VU1_NOSCHED=1` gives **91.70 vs
123.65 ns/pair (25.8%)** — so **even with the scheduler entirely free, 30 fps is still 5.4× away.**
No single subsystem is the answer any more. Reaching 17 ns/pair requires a compiled block to
eliminate *essentially all* per-pair interpreter work — decode fetch, dispatch, the scheduler, the
shadow dance and **both instruction slots** — rather than to speed any one of them up.

### The build order this leaves

1. **Lower-slot codegen** for store / load / IADDIU / clip readers — the coverage blocker, and the
   thing that decides whether runs get long enough to matter. Re-run the block census after each
   opcode lands; it is the coverage meter.
2. **Block assembly**: runs of consecutive fully-covered pairs → one function, operands kept in
   registers across pairs, one call per block.
3. **Pipeline elimination inside a block** via cont.179b's write-visibility analysis — with the
   branch-delay-slot exclusion and the `m_vfLatestWrite` sequence bump.
4. **Block linking** (mean basic block ≈ 6 pairs, §5).

Each stage has a ready-made verification path: the three-layer harness in §5b (emitter self-test,
FMAC self-test, live shadow-verify) already exists and generalises to every new opcode.


## 5d. ★★★ The JIT ceiling, measured (cont.185) — 30 fps is reachable

Everything left turned on one unknown: what does a **compiled** pair cost against the ~420 host
cycles (120.5 ns) an interpreted one costs? §5b could not answer it — at instruction granularity
the per-pair call hid the answer. The emitter now chains pairs into a single function
(`emitBlockChain`) and the cost is measured directly, **swept over block length**, because the mean
basic block is only ~6 pairs (§5) and benchmarking only a long block would flatter the result.

The block mimics the T&L inner loop the census shows dominating — a MULAbc + MADDAbc chain, i.e. a
4x4 matrix transform.

| block length | ns/pair | vs interpreter |
|---|---|---|
| 1 | 11.17 | 10.7x |
| 2 | 7.90 | 15.2x |
| **6 — the real mean block** | **7.72** | **15.6x** |
| 16 | 8.26 | 14.5x |
| 256 | 8.99 | 13.3x |

1. **The target is reachable.** 30 fps needs **17.0 ns/pair**; a compiled 6-pair block runs at
   **7.72**, leaving headroom for the lower-slot work this benchmark omits. First hard evidence
   that 30 fps is not merely hoped for.
2. **Per-block call overhead is a non-issue** — the curve is flat from 2 pairs up, so the short
   blocks this game actually has lose nothing. The worry that ~6-pair blocks would sink the design
   is disproved.
3. **Long blocks are slightly worse.** At ~150 bytes/pair, 256 pairs is ~38 KB and overflows the
   32 KB L1i. Do **not** chase maximal block length — another argument for linking short cached
   blocks rather than unrolling.

Codegen quality is adequate: at 256 pairs the JIT (9.29 ns/pair) is level with hand-written C++ SSE
using the same helpers (9.60), so the emitter is not leaving performance on the table.

> ⚠ **Not included in 7.72**, and it must not be read as a projection of the finished system: the
> lower slot (loads/stores/branches — §5c showed that is the coverage blocker), block entry/exit
> state sync, write-visibility at block boundaries, and branch dispatch.


## 5e. Coverage build-out (cont.186-188) — 40.8% → 59.2% upper, 21.5% → 44.5% block-compilable

§5c said the lower slot was the blocker. Tiering the lower opcodes and measuring what each would
unlock (**before** emitting any) showed it was not the whole story:

| tier | lower ops added | compilable (cont.186) | after upper build-out (cont.188) |
|---|---|---|---|
| 0 | NOP only | 4.2% | 8.7% |
| 1 | +SQ/LQ | 16.0% | 28.5% |
| 2 | +IADDIU | 18.3% | 33.0% |
| 3 | +clip readers | 21.2% | 43.0% |
| 4 | +ILW/ISW | 21.5% | **44.5%** |

**Implementing every listed lower opcode reached only 21.5% while the upper slot was at 40.8%** —
the *upper* slot was the binding constraint. Three extensions fixed that, each verified against the
interpreter on live data before being believed:

1. **q/i-operand family, MAX/MINI, ABS** — the emitter became plan-driven (`UpperPlan` /
   `OperandSrc`), so the second operand can be a vt lane, the vt quad, or a broadcast of `q`/`i`.
2. **Upper NOP counted as covered** — it emits nothing, but it is **26.4% of executed pairs**;
   treating it as an opcode gap was breaking nearly every run. This one change took block-compilable
   pairs from 26.5% to 43.5%.
3. **ITOF0/4/12/15** — `cvtdq2ps` + an exact 1/2^n multiply.

**Semantic traps found and handled (each would have silently corrupted state):**
- `MAX`/`MINI`/`ABS`/`ITOF` use `applyDest`, **not** `applyFmacDest` — no result clamp, no flags.
- `ABS` and `ITOF` write **`vf[ft]`**, not `vf[fd]`.
- **`ITOF` reads `vf[fs]` as RAW INT32 BITS**, so it must bypass the operand clamp entirely.
- The main and `special` switches **diverge at 0x10–0x17**: MAXbc/MINIbc vs ITOF/FTOI.
- State offsets now come from `offsetof(VU1State, …)` rather than hardcoded numbers.

**Cumulative verification: >250M shadow-verified executions against the interpreter, 0 mismatches.**
That harness is what makes adding opcodes safe — each new one is checked on real guest data the
moment it executes.

### What is left, in order

1. **FTOI (3.3%)** — *not* a one-instruction translation: the interpreter saturates via
   `vuFloatToInt` (double math, clamping to `INT32_MIN`/`INT32_MAX`), while `cvttps2dq` yields
   `0x80000000` for out-of-range in **both** directions. Positive overflow needs an explicit
   compare-and-blend to `0x7FFFFFFF`.
2. **CLIP (3.7%)** and OPMULA/OPMSUB.
3. **★ Branches as block TERMINATORS rather than opcode gaps** (15.5% of lowers). The census still
   counts a branch lower as uncovered, which *understates* what a real block compiler reaches — a
   block should end at a branch, not refuse to include it.
4. **Lower-slot codegen** (SQ 15.1%, clip readers 10.3%, LQ 5.3%, IADDIU 4.6%) — needs VI registers
   in GPRs and VU-memory addressing.
5. **Block assembly** proper: emit a run as one function, keep operands in registers across pairs,
   and replace the interpreter loop (pc/cycle advance, pipeline) for the run's duration. §5d's
   **7.72 ns/pair at 6-pair blocks (15.6×)** is what this unlocks.


## 5f. Branches are TERMINATORS, not gaps (cont.190) — the single largest coverage lever

Every census through §5e counted a **branch** in the lower slot as an uncovered opcode, so a branch
*split* every run. That is simply the wrong model: a block **ends** at a branch. Reclassifying:

| tier | compilable | mean run | pairs in runs ≥ 2 |
|---|---|---|---|
| 4 (+ILW/ISW) | 45.7% | 1.53 | 20.7% |
| **5 (+branch as terminator)** | **61.3%** | **2.85** | **52.2%** |

**That one change is worth more than every opcode added in §5e combined** (+15.6 pp compilable,
+31.5 pp in runs ≥2, mean run finally above 2). It follows from the block shape: branches occur
every ~6 pairs, so treating them as gaps capped every run at the inter-branch distance *minus* the
branch.

### Revised build order — ⚠ CORRECTED in §5g

The first draft of this list put branch terminators first. **That was wrong** — see §5g: tier 5 is
*cumulative*, so its 52.2% presumes the lower slot is already implemented. Lower-slot codegen comes
first.

### Sizing, with §5d's measured block cost

A 2-pair block is 7.90 ns/pair, a 6-pair block 7.72, versus 120.5 interpreted. At 52.2% coverage:
`0.522 × 7.9 + 0.478 × 120.5 ≈ 62 ns/pair ≈ 1.95×` — **if transitions were free, which they are
not.** Reaching 17 ns/pair still requires coverage in the 85%+ range, which is what keeps the lower
slot on the list. But 52.2% is the first figure high enough that block assembly is worth *building*
rather than simulating.


## 5g. ⚠ Build-order correction, and the LQ/SQ asymmetry (cont.191)

§5f's ordering ("branch terminators first") **was wrong, and the error is worth naming**: the tier
table is **cumulative**, so tier 5's 52.2% assumes SQ/LQ/IADDIU/clip-readers/ILW/ISW are already
emitted. The figure that actually governs a *first* block compiler is **tier 0 — all-NOP lowers —
which is only 8.9% compilable and 6.6% in runs ≥ 2.**

With §5d's measured block cost that is `0.066 × 7.9 + 0.934 × 120.5 ≈ 113 ns/pair` = **1.066×**,
which does not justify the integration cost (cycle accounting, pipeline reconciliation,
write-visibility analysis, block cache).

**Correct order: (1) lower-slot codegen, (2) block assembly with branch terminators, (3) CLIP and
OPMULA/OPMSUB.**

### LQ and SQ are not symmetric — this shapes the block design

```
addr = ((uint32_t)(int32_t)(vi[base] + imm)) * 16;   // imm = IMM11, sign-extended
addr &= (dataSize - 1);                              // dataSize is a power of two
if (addr + 16 <= dataSize) { ... }                   // guard survives the mask
```

| | base reg | visibility |
|---|---|---|
| **LQ** | `vi[is]` | **immediate** — `applyDest`, no pipeline |
| **SQ** | `vi[it]` | **queued** — goes through `queueStore`, the store pipeline |

So LQ compiles to a masked load + dest blend, while **SQ's visibility must either replicate the
store pipeline or be proven unobservable within the block** by the same forward-window argument used
for VF writes (§5b / cont.179b) — *including its branch-delay-slot exclusion*.

Both need what the emitter has never touched: **VI registers** (`int32_t vi[16]`, so GPR handling
plus the `+imm`, `*16`, `&(dataSize-1)` arithmetic) and **`vuData` as a second argument** — the
emitted functions currently take only the state pointer, so the calling convention changes.


## 5h. Lower-slot codegen: LQ, and two harness traps (cont.192)

First lower-slot instruction. It required the emitter's first **GPR** and **SIB** encodings and a
wider calling convention — still no REX, since every register used is a low one:

```
void f(VU1State *rdi, uint8_t *vuData rsi, uint32_t dataSizeMask edx)
```

**★ The interpreter's bounds guard compiles away.** `addr = ((uint32)(int32)(vi[base]+imm))*16`
masked with `(dataSize-1)` is always `≤ dataSize-16`: `dataSize` is a power of two ≥ 16, `x*16` has
its low four bits clear, and masking preserves that. So `if (addr + 16 <= dataSize)` is a tautology
after the mask and **emitted LQ contains no branch**.

Verified: **1,920 cases** — 6 VI bases (incl. negative and wrapping) × 5 immediates × all 16 dest
masks — **0 mismatches**.

### Two traps, both in the harness, both worth remembering

1. **Never mirror `VU1State` with a look-alike struct.** The first test declared its own
   (`vf, acc, q, i, vi`) while the emitter uses `offsetof(VU1State, …)` (`vf, vi, acc, q, p, i`), so
   `kOffVi` addressed the wrong field — 640 mismatches with the codegen entirely correct. Tests use
   **the real `VU1State`** so offsets agree by construction.
2. **★ Self-tests run on the GUEST thread's small stack** (they execute from inside
   `VU1Interpreter::run`). Two extra `VU1State` locals exhausted it, corrupting the frame and
   surfacing as **`SIGBUS` at the top of `run()` with unreadable locals** — which looks precisely
   like emitted-code memory corruption and is nothing of the sort. gdb showed the smashed frame.
   **All large self-test state must be `static`.**

LQ is **not wired into execution**: there is no lower-slot path until block assembly exists, and a
per-instruction lower JIT would repeat the ~0 payoff §5b measured. It is verified groundwork with a
fixed contract.

**SQ landed too (cont.193): 15.1% of pairs, 1,920 cases, 0 mismatches.** And the store pipeline
turned out **not to need modelling**: `queueStore` uses `readyCycle = m_cycle + 1` while
`commitReadyPipelines()` runs at the **top** of the next pair's iteration, so the store lands before
pair i+1 either way, and nothing else runs in pair i's own cycle to observe the difference (one
lower instruction per pair; uppers never touch VU memory). SQ emits as address → masked blend
against existing memory → store: no pipeline, no branch. *Residual:* XGKICK streams VU memory per
cycle, so a store it is concurrently reading could be seen one cycle early — a race on hardware too.

Lower-slot codegen now covers **SQ 15.1% + LQ 5.3% = 20.4%**, on top of NOP's 46.4%.

### ★★ The constraint governing everything left in the lower slot: VI writes and the branch delay

Every VI-writing lower op — IADDIU/ISUBIU (4.6%), the clip readers (10.3%), ILW — is **not** like SQ:

- `execLower` writes `m_state.vi[it]` immediately, and `run()` then shadow-dances it into
  `queueViWrite`.
- Critically, the interpreter keeps a **branch-read backup** (`m_viBranchBackupValue/Reg/Valid`):
  when an instruction marked `delaysNextBranchRead` writes a VI register, a branch in the
  **immediately following** pair must read the **OLD** value. That is the VU's branch-delay hazard,
  and it is real architectural behaviour.

**Why this bites block assembly specifically:** branches *terminate* blocks, so a block's final pair
is exactly the pair immediately before a branch. A JIT'd VI write in that position that bypasses the
backup would hand the branch the **new** value and take the wrong path. **The block compiler must
either exclude VI writes from a block's final pair, or replicate the backup** — settled here, before
any VI-writing op is emitted, rather than discovered from a wrong-path bug later.

**Next:** IADDIU + clip readers under that constraint, then block assembly.

## Next arc: the program compiler (cont.230)

The block JIT's design floor is measured (244–375 host cycles per 4.4-pair entry plus the loop around
it; 7% of pairs interpreted at ~700 cycles, 83% of them DIV). The example game needs ≈ 6 ns per pair
all-in for 30 fps (5.17 M pairs per frame), i.e. a whole-program compiler in PCSX2's microVU shape:
blocks keyed by a compile-time pipeline state and linked directly, a static stall model, a 4-instance
lazy flag ring, Q/P as pipelined lanes, XGKICK as a deferred call. Design note and the microVU survey
live in the fork: `tools/<game>/PS2Recomp/docs/vu1-program-compiler.md` and `docs/microvu-survey.md`.

