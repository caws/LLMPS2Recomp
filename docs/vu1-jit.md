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

> **With the scheduler AND the flags both entirely free the interpreter lands near ~90–100 ns/pair
> (~2.3×) — still ~5× short of 30 fps. No interpreter-level work reaches the target. The JIT is
> not optional.**

## 2. Already landed (stage 1) — 1.37×, bit-exact

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

## 4. Step 1 — LAZY FLAGS (bit-exact, worth ~1.37×)

The MAC/status/clip flags are derived for **every** FMAC, but they are only *observable* if
something reads them. Skip the work when nothing can — unobservable ⇒ bit-exact.

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

## 5. Step 2 — the VU1 block JIT

The only route to ~60 host cycles/pair. Translate a **basic block** (start PC → branch / E-bit) to
native SSE once, cached per `(code generation, start PC)` — the decode cache already has exactly
that keying and invalidation.

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
| `PS2X_VU1_NOFLAGS=1` | ablation: all FMAC flag work free (~27%) — also the lazy-flags prototype |
| `PS2X_VU1_NOEXACT=1` | ablation: exact double recompute free (~7.5%) |
| `PS2X_VU1_FASTSCAN` | **default ON**, `=0` reverts stage 1a |
| `PS2X_VU1_PIPEVERIFY=1` | stage 1b valid-mask self-check |
| `PS2X_VU1_COMMITSKIP` | **default ON**, `=0` reverts the cont.158b watermark |
| `PS2X_VU1_EXACTLD=1` | exact lane math in `long double` instead of `double` |
