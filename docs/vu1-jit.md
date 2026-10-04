# The VU1 performance arc — why a block JIT, and how to build it

Handoff document for the **VU1 interpreter → JIT** work. It records *why* the JIT is necessary
(measured, not assumed), what is already landed, what has been disproven, and the concrete next
two steps with their verified design inputs.

Game-agnostic: the VU1 interpreter lives in the toolchain clone
(`tools/<game>/PS2Recomp/ps2xRuntime/src/lib/vu/`) and is carried by engine
[`patches/16-vu1-core.patch`](../patches/README.md). The *measurements* below come from the LOTR
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

## 2. Already landed — stage 1 (1.37×) and lazy flags (1.20×)

Cumulative: **280.7 → 155.5 ns/pair = 1.81×**, all bit-exact, all default ON with kill switches.
Stage 1 is below; lazy flags (cont.177) is §4; dispatch + operand-prologue overhead (cont.178) is §4b.

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

## 5. Step 2 — the VU1 block JIT

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

What it must eliminate, in measured priority order:

1. **The cycle-accurate scheduler (~33%)** — resolve hazards and stall counts **statically at
   translate time**. Within a block the register-ready cycles are a static schedule; only block
   entry/exit needs dynamic state. This is the single biggest bucket and it is exactly what a JIT
   is good at.
2. **Flag derivation (~27%)** — fold in step 1's per-program analysis, and go further: a JIT can do
   it *per block*, and can compute flags only for the last writer before a reader.
3. **Per-instruction overhead** — keep operands in XMM registers instead of the save/restore
   `memcpy` shadow dance in `run()`; dest masks become blends; the decoded-pair by-value copy and
   the `switch` dispatch disappear entirely.

**Verification methodology — reuse what already worked.** Keep the interpreter in-tree forever as
the bit-exact oracle and shadow-verify the JIT against it, the same way the GPU rasterizer arc was
made trustworthy (per-primitive compare → whole-buffer compare → authority flip). Every wall in
that arc was found by a verify mode, not by reasoning; the same will be true here.

**Mirror PCSX2's microVU** for the translation strategy and the hardware corner cases, per the
standing PCSX2 rule in [resources.md](resources.md) — then verify against our own generated
code + disasm + gdb.

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
