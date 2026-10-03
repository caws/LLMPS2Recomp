# Local PS2Recomp patches

Correctness fixes to upstream [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp) that we carry
locally until they are upstreamed. `scripts/01_setup.sh` applies every `*.patch` here after
cloning a per-game toolchain into `tools/<game>/PS2Recomp/`.

## ★ ONE PATCH PER FILE — and why

Patches are partitioned **by source file**: each file is touched by exactly one patch. This is a
hard guarantee of disjointness, and it exists because the previous topical split was **not
reproducible** — overlapping full-file diffs of the same file made a fresh clone silently come up
with a PARTIAL patch stack. The cost of file-level patches is losing per-topic revert granularity,
which is largely moot: **every behavioural fix carries an env kill switch** (`PS2X_DMA_TTEQW`,
`PS2X_DMA_REFVIF`, `PS2X_VIF_DIRECTSPAN`, …), so A/B testing happens at runtime, not by
unapplying patches.

**Baseline commit: `14b1e5c` ("Start the main thread with COP0 Status.IE set (#214)").**

### upstream migration (ee14958 → 14b1e5c) — what was discarded and why

Upstream landed four large refactors (VU1 #191, GS #204, EE scheduler #184, EE timers #203) that
**absorbed most of our VU/GS correctness stack**. The following patches were verified absorbed (or
their machinery deleted) and **removed** — their full root-cause write-ups live in this file's git
history (`git log -p -- patches/README.md`, state at engine commit `72c4219`):

| Removed patch | Why |
|---|---|
| `02-recomp-vu-translation-helpers` | VOPMULA/VOPMSUB now emitted as true cross-product halves (yzx/zxy shuffles) |
| `03-recomp-instructions-header` | `VU0_CR_I = 21` now upstream |
| `06-kernel-mpeg-stub` | MPEG stub fully redesigned around the EE scheduler (PTS-timed GetPicture, CD-stream integration); our old callback-driving machinery no longer exists. Empirically re-check the FMV; re-derive a fix on the new design if it starves |
| `07-kernel-interrupt-syscall` | vblank worker deleted by #184; pad delivery re-homed → `07-kernel-ee-scheduler.patch` |
| `08-gs-gpu-header`, `09-gs-gpu` | files deleted by GS refactor. PRMODECONT-correct PRIM handling absorbed (`m_prim = m_prmodecont ? m_primRegister : m_prmodeRegister`); presentation moved off the contended state mutex (old present-fairness presumed obsolete — verify empirically); psm-table overrun (SIGSEGV) structurally fixed (64-slot table); census/displog/giflog/quadlog/desync-agg instruments dropped (re-instrument on demand) |
| `10-gs-rasterizer` | file deleted; undefined-psm sampling re-fixed in the new backend → `10-gs-cpu-backend.patch` |
| `14-runtime-core` | wired `GsPendingImageFn` via `m_gs.pendingImageQwc()` — that accessor was OUR addition to the deleted GS; the new frontend has no equivalent yet. **DIRECTCARRY is therefore inert** (patches 11/15 null-guard the unset fn; DIRECTSPAN remains active). If the FMV image-carry defect reproduces, port a `pendingImageQwc()` accessor into `gs_frontend` and re-add the wiring |
| `16..19-vu1-*` | the VU1 refactor is PCSX2-faithful and superseded the whole group: operand read-side clamp (`normalizeOperand` ≡ vuDouble), result clamp+flags from exact `long double` arithmetic (≡ `PS2X_VU_CLAMP`+`PS2X_VU_MACFLAGS`, plus cycle-accurate flag latency we never had), corrected flag-op decode table incl. FMOR/FCGET (≡ `PS2X_VU_FLAGFIX`), integer-domain CLIP **with** the denormal-w special case (≡ `PS2X_VU_CLIPINT` + the follow-up we had deferred), saturating FTOI, XGKICK walk bounded + reserved-format abort (≡ the 0x4000 cap). KICKCENSUS/FLAGCENSUS/MEMCENSUS instruments dropped with the code they measured |

Still-needed patches were re-verified against `14b1e5c` (symptom present upstream, fix absent)
and re-anchored where the refactors moved code. The level-era VU bistability investigation
(cycles 90–94) must be **re-based empirically**: the VU1 interpreter it measured no longer exists.

## Patch inventory

| Patch | File | Contains |
|---|---|---|
| `01-recomp-fpu-translator.patch` | `ps2xRecomp/src/lib/fpu_translator.cpp` | SQRT.S/RSQRT.S read **ft**, not fs (still fs upstream at 14b1e5c) |
| `04-runtime-cmakelists.patch` | `ps2xRuntime/CMakeLists.txt` | unity-build excludes game overrides (via `game_overrides.manifest`); builds `ps2_dbcman_hle.cpp` |
| `05-kernel-gs-stub.patch` | `ps2xRuntime/src/lib/Kernel/Stubs/GS.cpp` | `sceGsExecLoadImage`/`ExecStoreImage` vram_addr is in 64-word BLOCK units (×8 bug still at both sites upstream) |
| `06-kernel-mpeg-datacb.patch` | `ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp` | **`PS2X_MPEG_DATACB`** (default ON): drive libmpeg's non-stream type-1 "bitstream empty" data-request callback (`sceMpegAddCallback` registrations the refactored HLE stored but NEVER invoked — the triage wrongly presumed old patch 06's drive absorbed; the decoder starved forever). Port of the old drive onto the new scheduler design: `sceMpegGetPicture` queues ONE type-1 invocation (`GuestInvocationKind::RpcCallback`, same mechanism as the stream callbacks) before its external wait; the handler's `sceMpegAddBs` feeding wakes the waiter itself; `onComplete` re-arms while fed (v0≠0) and no frame decoded (≤512 rounds/picture), and on v0==0 marks exhaustion + wakes the waiter so GetPicture returns (the game's handler ends the movie through its own job engine). Real-libmpeg semantics: the decode driver pulls its elementary stream through this callback (LOTR ROTK handler 0x13B3A0) |
| `07-kernel-ee-scheduler.patch` | `ps2xRuntime/src/lib/Kernel/EeScheduler.cpp` | (a) per-vblank `ps2_dbcman_hle::deliverPadData()` in the `VBlankStart` event (frequency is load-bearing; re-homed from the deleted Interrupt.cpp vblank worker); (b) **`PS2X_HOSTPUMP_RTC`** (default ON): `ps2xBeginHostPump()/ps2xEndHostPump()` scope — while a game-side dispatch pump is open, `checkpointDue()` reports false so pumped guest chains run to completion instead of one basic block per dispatch (the latched `m_checkpointPending` is only cleared by the scheduler run loop, so manual pumps crawled and aborted chains mid-record — the cont.150 boot-parse wedge). PCSX2 parity: `R5900.cpp _cpuEventTest_Shared` delivers interrupts BETWEEN blocks via `cpuException`, never unwinding a half-run chain; (c) **`PS2X_VBLANK_WALLCLOCK`** (default ON): deadline pass floors `m_eeCycle` up to any deadline whose host time arrived — coarse per-backward-edge cycle crediting under-runs the emulated clock and collapsed vblank to ~1.5 ticks/s. PCSX2 parity: `Counters.cpp VSyncStart` paces vsync per cycle-credited frame + `Throttle()` to real time; (d) **`PS2X_INVOKE_COALESCE`** (default ON): `queueInvocation` drops an event-edge guest invocation (GsCallback/Interrupt/Alarm) when an identical one (same kind + entry pc) is already pending or stacked un-started — a >1s guest dispatch (e.g. a host-pump chain) backlogs 60+ vblank deadlines that then fire in one pass and stack 64 nested invocations, exhausting the 1MB per-depth async-stack arena ("EE invocation stack space exhausted"). PS2/PCSX2 parity: INTC latches ONE status bit per cause (`hwIntcIrq` ORs I_STAT) — N masked vblanks deliver one interrupt, and sceGsSyncVCallback misses vsyncs the EE slept through; (e) diagnostics: `PS2X_INVOKE_LOG` (default OFF) invocation-lifecycle trace + an error-path dump of the stacked invocations on the exhaustion throw |
| `08-gs-frontend.patch` | `ps2xRuntime/src/lib/gs/gs_frontend.cpp` | (a) GIF-tag **FLG=3 consumes NLOOP qwords** (ps2tek "Disable" == IMAGE; PCSX2 GIFPath shares one case for `GIF_FLG_IMAGE`/`IMAGE2`) — upstream consumed zero payload and re-read the data as tags. (b) **Per-path resumable GIF parse** (`PS2X_GIF_RESUME`, default ON; PCSX2 `Gif_Path` parity): a tag's payload legally spans DIRECT/DMA submission boundaries, so the active tag (format, loops left, reg cursor, image remainder) persists PER PATH and resumes on that path's next packet — the stateless parser dropped split-tag tails and misparsed the next packet from byte 0 (the movie's `[16 B bare IMAGE tag][data DIRECTs]` shape, the level's spanning draw lists). `ps2xGsSetGifPath()` (extern-C, no header change) selects the path; the native packed fast path defers while PATH3 owes data. Diagnostics: `PS2X_GIF_WALKDUMP`, `PS2X_GIF_SEEDLOG`, `[gif:susp]` |
| `09-gs-cpu-backend-header.patch` | `ps2xRuntime/include/runtime/gs/gs_cpu_backend.h` | `SampleTexture(..., uint32_t mip = 0)` signature for per-triangle mipmapping (safe header: not included by generated code). **cont.158c perf:** `ReadVramFunc`/`WriteVramFunc` are raw function pointers, not `std::function` (every table entry is a plain GSMem free function; the type-erasure trampoline was measured in the per-pixel path), + the `ResolvedDraw` per-batch cache struct (`m_draw`) |
| `10-gs-cpu-backend.patch` | `ps2xRuntime/src/lib/gs/gs_cpu_backend.cpp` | undefined texture PSM reads/writes as CT32, not Null — PCSX2 `GSLocalMemory.cpp` fills undefined `m_psm[64]` slots with PSMCT32; LOTR movie sprites carry literal TEX0 psm=0x20 (invisible FMV with a Null fallback). Per-triangle **mipmapping** (LOD from texel-area/pixel-area ratio, MIPTBP1/2 level bases, MXL clamp). Diagnostics: `PS2X_GS_CENSUS2` (draw/xfer censuses, `[gs2:samp]` 1-in-4096 draw sampler, `[gs2:target]`/`[gs2:present]` state traces, `[gs2:degen]` all-zero-XYZ counter), `PS2X_GS_VRAMDUMP` (one-shot 4MB dump), `PS2X_GS_PRESENT_SAVE` (rotating saves of the exact presented pixels), `PS2X_GS_ERA_FILE` (sentinel-file era gate that resets censuses + times the dump/saves to the post-FMV level era). **cont.158c perf (GS de-virtualization step 1):** `DrawPrimitive` resolves the frame/z/tex psm-table function pointers + `framePageBaseToBlock` bases ONCE per batch into `m_draw` (batch.state is immutable per batch — cannot go stale); `WritePixel`/`SampleTexture` call through the cached raw pointers instead of per-pixel table lookups; `if (!m_vram) return` at DrawPrimitive entry (behavior-identical: no VRAM = reads 0/writes no-op). Pure hoisting, pixel semantics untouched |
| `11-memory-header.patch` | `ps2xRuntime/include/runtime/ps2_memory.h` | `GsPendingImageFn` hook (cycle 83 DIRECT image carry; currently UNWIRED — see the 14 row above) |
| `12-memory-dma.patch` | `ps2xRuntime/src/lib/ps2_memory.cpp` | scratchpad SPR channels (ch8/ch9 — load-bearing for all long text); chain-walk cap 4096 → `1<<20` (`PS2X_DMA_MAXCHAINTAGS`); **`PS2X_DMA_TTEQW`** 16-byte parity-preserving TTE embeds; **`PS2X_DMA_REFVIF`** ref-tag (id 3/4) TTE embeds; DIRECTSPAN reset calls. Diagnostics (CHAINLOG/CHAINBIG/CHAINSRC/TAGVIF) dropped in the migration |
| `13-pad.patch` | `ps2xRuntime/src/lib/ps2_pad.cpp` | headless CROSS driver + `PS2X_PAD2_LOG` (both default-OFF, test infra) |
| `15-vif1-interpreter.patch` | `ps2xRuntime/src/lib/ps2_vif1_interpreter.cpp` | **`PS2X_VIF_DIRECTCARRY`** (inert until GsPendingImageFn is re-wired) + **`PS2X_VIF_DIRECTSPAN`** (cross-transfer DIRECT span state; PCSX2 `vif1.tag.size` persists); the DIRECT GIF walkers treat **FLG=3 as IMAGE** (companion to patch 08); with `PS2X_GIF_RESUME` the VIF-side image-continuation carry stands down (its synthesized IMAGE tags would corrupt a resuming stream); **★★ `DIRECT imm=0` consumes ZERO qwords** (`PS2X_VIF_IMM0DIRECT=65536` restores the nominal reading; `PS2X_VIF_IMM0SPAN=1` additionally re-arms its cross-buffer span) — the game ends EVERY frame chain with a terminal `[NOP][DIRECT imm=0]` TTE embed followed by nothing, and the fictional 56,370-qw span ate the first ~900 KB of every subsequent frame chain (the cont.156 phantom-DIRECTHL corruption cascade; the original imm=0-span evidence, run_c86A, was the half-phase misparse that PS2X_DMA_TTEQW fixed); sized (imm≠0) DIRECTs still span — the FMV's 32767-qw uploads legitimately split. **Offline proof (vifbuf_bad.bin): with imm=0→0 the captured 3.7 MB level chain parses PERFECTLY at both layers** (VIF walk consumes the buffer exactly, 0 unknown opcodes; the concatenated 3.16 MB GIF stream = 184 tags, 0 implausible, 0 overrun) — with the 65536 reading it swallowed ~1 MB per occurrence (embeds decode as GIF NOPs so it half-worked, until UNPACK float payloads were read as tags → the phantom-PACKED garbage register writes). Result: post-movie garbage FRAME writes 1024+→0, garbage targets 1442→0, presents restored to full cadence, the level renders fully textured gameplay. Diagnostics: `PS2X_VIF_CENSUS`, `PS2X_VIF_SEQLOG` (per-delivery + big-chain end forensics), `PS2X_VIF_DIRECTBIG` (+ command ring), `PS2X_VIF_BUFDUMP` |
| `16-vu1-core.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp` | Three XGKICK fixes (cont.155, all default ON with kill switches): **`PS2X_VU1_KICKSTALL`** — a second XGKICK while PATH1 still streams STALLS the VU until it completes (hardware behavior, PCSX2 models it); upstream reset `m_xgkick` unconditionally, silently destroying every in-flight kick's packet — with PATH1 at ~8 B/cycle and batch kicks a few dozen cycles apart this lost nearly every packet except each program's last (level-era kick-drops 1024+→24, scene coverage transformed). **`PS2X_VU1_KICKFLG3`** — GIF-tag FLG=3 is IMAGE ("Disable", ps2tek; PCSX2 `GIF_FLG_IMAGE2` falls through), not reserved; upstream aborted kick+program on every FLG=3 packet. **`PS2X_VU1_KICKDROP_SOFT`** — an oversized/garbage tag walk drops THE KICK (bounded-walk host safety) but no longer kills the whole microprogram via the reserved-instruction abort (hardware never stops the VU over GIF data content). Plus rate-limited `[VU1 kick-drop]` byte dumps + one-shot per-pc `[VU1 kick-dump]` deep dumps (VI regs + microcode window). **`PS2X_VU1_COMMITSKIP`** (default ON, `=0` reverts; cont.158a perf): pipeline-commit tracking — a file-static owner-guarded watermark (earliest readyCycle among valid entries) + per-array valid counts let `commitReadyPipelines()` return O(1) when nothing is due, skip empty arrays when scanning, and `pipelinesPending()` answer O(1); profiling measured the unconditional ~50-slot 7-array scan per VU cycle at 28% of total runtime (hot from the pair loop, which scans TWICE per cycle — top-of-loop + `advanceOneCycle`). Pure memoization: commit timing/order/values untouched (no hardware semantics changed, nothing to PCSX2-mirror); staleness is one-directional (can only add scans, never skip a due commit). **cont.158d:** `resetScheduler()` no longer clears the ready/latest-write bookkeeping (m_cycle + write sequence are monotonic across programs ⇒ stale entries inert; `reset()` — the only place m_cycle goes backward — clears it itself) and skips the pipeline-array clears when the tracking proves them empty; companion to patch 18's no-reset-per-VCALLMS (PCSX2 `VU0micro.cpp vu0ExecMicro`: no wholesale reset on VCALLMS) |
| `17-vu1-upper.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_upper.cpp` | `PS2X_VU1_FTOITRAP` (default OFF): rate-limited `[VU1 ftoi-sat]` saturation logging in FTOI0/4/12/15 with a one-shot code-window dump (NaN-vs-hwmax triage: `0x7FFFFFFF` from NaN input vs legit-range saturation) |
| `14-gif-arbiter.patch` | `ps2xRuntime/src/lib/gs/ps2_gif_arbiter.cpp` | `isImagePacket` also matches FLG=3 (IMAGE2); `drain()` announces each packet's path via `ps2xGsSetGifPath()` so the frontend resumes the right stream (companion to patch 08) |
| `18-runtime-core.patch` | `ps2xRuntime/src/lib/ps2_runtime.cpp` | **cont.158d perf:** drop the wholesale `m_vu0.reset()` per VCALLMS in `executeVU0Microprogram` — `copyVu0ContextToState` memsets + rebuilds the architectural state itself and `execute()` runs `resetScheduler()`, so the reset was 100% redundant work at math-library call rates (top memset in the cont.158c profile), and its `m_cycle=0` broke the cycle monotonicity patch 16's scheduler bookkeeping relies on. PCSX2 parity: `VU0micro.cpp vu0ExecMicro` does no reset on VCALLMS (flag/cycle sync + start) |
| `20-dbcman-hle-header.patch` | `ps2xRuntime/include/runtime/ps2_dbcman_hle.h` | DBCMAN.IRX HLE declarations (new file) |
| `21-dbcman-hle.patch` | `ps2xRuntime/src/lib/ps2_dbcman_hle.cpp` | DBCMAN.IRX HLE implementation (new file) |

### Verify reproducibility (do this after ANY patch change)

```bash
LIVE=tools/<game>/PS2Recomp; SC=tmp/verify_clone
rm -rf $SC && mkdir -p $SC && git -C $LIVE archive 14b1e5c | tar -x -C $SC
cd $SC && git init -q . && for p in ../../patches/*.patch; do git apply "$p" || echo "FAIL $p"; done
# then diff every patched file against $LIVE -- must be byte-identical
```
Last verified (upstream migration): **11/11 patches apply, 11/11 files byte-identical.**

## Topic details (kept patches)

### SQRT/RSQRT read ft (`01`)

`ps2xRecomp` emitted `SQRT.S`/`RSQRT.S` reading **fs**, but R5900 `sqrt.s fd, ft` reads **ft**
(rsqrt = `fs / sqrt(ft)`, with a divide-by-zero guard). ~130/~180 game sites wrong; zeroed the
camera basis → every vertex at (2048,2048) → black screen. | cont.28/29

### sceGsExecLoadImage BLOCK units (`05`)

`sceGsExecLoadImage`/`ExecStoreImage` stubs converted `vram_addr` with ×8 (`*2048/256`); the SCE
convention is already **64-word BLOCK units**. The 14-bit DBP field then truncated mod 16384, so
every upload through the sceGs path landed at `addr*8 mod 16384` — fonts/CLUTs uploaded to aliased
addresses; menu text invisible. | cont.37

### runner unity excludes overrides (`04`)

**Build-hygiene.** Reads `src/runner/game_overrides.manifest` (written by
`scripts/03_build_game.sh`) and sets `SKIP_UNITY_BUILD_INCLUSION` on exactly those files, so each
override module is its own TU (stable unity batches; no cross-module static collisions). No
manifest = plain upstream behavior. | cont.47

### headless CROSS driver (`13`)

**Test-infra, default-OFF.** `PS2X_PAD_CROSS_AT`/`_LEN` force a CROSS press→release edge counted
in `readState()` CALLS (not frames), no window-focus dependency; `PS2X_PAD2_LOG=1` host-sample
trace. | cont.49

### undefined psm decodes as CT32 (`10`)

The GS-refactor backend rebuilt the psm→function table with a `ReadNull`/`WriteNull` default for
undefined psm codes — the same defect class fixed pre-refactor (cont.69/cont.116e). **PCSX2
`GS/GSLocalMemory.cpp` defaults every undefined `m_psm[64]` slot to PSMCT32**; LOTR ROTK's movie
strips carry the game's LITERAL `psm=0x20` (hardcoded immediate @0x1fa3d4 fed raw into TEX0), so a
Null read makes every FMV texel 0 (invisible movie). Table sizing itself (64 slots) is correct
upstream now — only the fallback choice is patched. | re-derived

### IOP DBCMAN pad2 HLE (`20`/`21` + `07` wiring + `04` build entry)

Controller input for pad2/libdbc games — **DBCMAN.IRX HLE (Sony DualShock2 manager, sid
`0x80001300`)**: `answerDbcManRpc()` handles `SetWorkAddr`/`CreateSocket`/`GetDepNumber`/
`ReceiveData`/`SRData` (DS2 digital+sticks, profile `FF FF 0F 00 00`), and `deliverPadData()`
emulates the IOP's per-vblank DMA of pad state into the EE double buffers, sourced from
`PSPadBackend`. LOTR drives DBCMAN over the RAW SIF transport (guest `0x11bfa8/0x11c178`), which
the game override intercepts, calling `answerDbcManRpc()` directly. `deliverPadData()` MUST run at
vblank frequency: it re-asserts the per-port work table each tick, which the game wipes during its
BSS load; too-rare delivery ⇒ controller reads "removed" ⇒ self-exit. Kill switch
`PS2X_PAD2_HLE=0`. | cont.40 / ee14958 migration / re-homed 14b1e5c

### DMA scratchpad + chain walk + TTE embeds (`12`)

**(a) SPR channels** (fromSPR ch8 `0x1000D000`, toSPR ch9 `0x1000D400`) were unimplemented: the
CHCR STR write fell through with no transfer and STR reads back clear, so the copy silently never
happened. Load-bearing for **all text**: LOTR builds each text GIF packet in the scratchpad and
copies it out in `13F030`, which forks on size (`qwc<=64` → memcpy, `qwc>=65` → fromSPR DMA) — the
long-standing "only short strings render" symptom (legal screen, language-select header). | cont.46

**(b) Chain-walk cap.** `kMaxChainTags = 4096` silently severed long display lists (the 4th FMV
column quad → right quarter of every FMV frame black). PCSX2 (`pcsx2/Vif1_Dma.cpp`) ends a chain
ONLY on `TAG_REFE`/`TAG_END`/`IRQ&&TIE` — no tag ceiling. Raised to `1<<20` as a pure runaway
guard (`PS2X_DMA_MAXCHAINTAGS`). | cont.132 cycle 76

**(c) `PS2X_DMA_TTEQW`** (default ON): embed a TTE tag as a FULL 16-byte qword `[0,0,vif0,vif1]`
instead of the bare 8-byte upper half (upstream still embeds 8 bytes at 14b1e5c). The EE DMAC
transfers the whole 128-bit tag slot (PCSX2 `Vif1_Dma.cpp` `masked_tag._u64[0] = 0`); a bare
8-byte embed shifts stream phase by half a qword, which qword-granular DIRECT framing does not
tolerate — the level texture-desync family. | cycle 86

**(d) `PS2X_DMA_REFVIF`** (default ON): ref tags (id 3/4) with TTE carry 2 VIF codes in the
DMAtag's upper 64 bits (for LOTR: the `DIRECT imm=qwc` that wraps the ref'd GIF packet); PCSX2
sends qword-0 to VIF when TTE is set for EVERY chain tag, upstream only for local-data ids
1/2/5/6/7. | cycle 84

### VIF1 DIRECT carry/span (`11`/`15`)

**`PS2X_VIF_DIRECTSPAN`**: cross-transfer DIRECT span state (PCSX2 `vif1.tag.size` persists across
transfers); reset hooks live in `12` (VIF1 FBRST/initialize). **`PS2X_VIF_DIRECTCARRY`**:
payload-aligned Path2 image continuation via `GsPendingImageFn` (`11`) — currently INERT: the
wiring patch (old `14`) died with the GS refactor; both call sites null-guard the fn. Re-wire via
a `pendingImageQwc()` accessor on the new `gs_frontend` if the image-carry defect reproduces. |
cycles 83/86

## Maintenance

- **Regenerate one patch** after editing the clone:
  `git -C tools/<game>/PS2Recomp diff -- <the one file> > patches/NN-name.patch`
- **Never let two patches touch the same file** — that is what broke the previous stack.
- `01_setup.sh` **fails loudly** if any patch does not apply: a partial stack is not a usable
  toolchain. If upstream absorbed a fix, delete that patch deliberately; if upstream drifted,
  rebase it and re-run the verification above.
- Patches apply with plain `git apply` from the clone root; paths are relative to it.
