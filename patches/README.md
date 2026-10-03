# Local PS2Recomp patches

Correctness fixes to upstream [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp) that we carry
locally until they are upstreamed. `scripts/01_setup.sh` applies every `*.patch` here after
cloning a per-game toolchain into `tools/<game>/PS2Recomp/`.

## ★ ONE PATCH PER FILE — and why

Patches are partitioned **by source file**: each file is touched by exactly one patch. This is a
hard guarantee of disjointness, and it exists because the previous topical split was **not
reproducible** — `04`/`10`/`14`/`17` were all overlapping full-file diffs of `ps2_gs_gpu.cpp`, so
on a fresh clone `10` and `17` simply failed to apply and the stack silently came up partial.

Splitting that file topically was considered and rejected: its 23 hunks **physically interleave**
topics (5 hunks contain two topics each), so a topical split needs hand-surgery on diff hunks —
fragile in exactly the way that yields a subtly wrong runtime. The cost of file-level patches is
losing per-topic revert granularity, which is largely moot: **every behavioural fix carries an env
kill switch** (`PS2X_VU_CLAMP`, `PS2X_DMA_TTEQW`, `PS2X_VIF_DIRECTSPAN`, …), so A/B testing happens
at runtime, not by unapplying patches.

**Baseline commit: `ee149581aac999ab22791b3900377c0b028a5840`.**

### Verify reproducibility (do this after ANY patch change)

```bash
LIVE=tools/<game>/PS2Recomp; SC=tmp/verify_clone
rm -rf $SC && mkdir -p $SC && git -C $LIVE archive ee149581 | tar -x -C $SC
cd $SC && git init -q . && for p in ../../patches/*.patch; do git apply "$p" || echo "FAIL $p"; done
# then diff every patched file against $LIVE -- must be byte-identical
```
Last verified: **21/21 patches apply, 21/21 files byte-identical.**

## Patch inventory

| Patch | File | Contains |
|---|---|---|
| `01-recomp-fpu-translator.patch` | `ps2xRecomp/src/lib/fpu_translator.cpp` | SQRT.S/RSQRT.S read **ft**, not fs |
| `02-recomp-vu-translation-helpers.patch` | `ps2xRecomp/src/lib/vu_translation_helpers.cpp` | VOPMULA/VOPMSUB are cross-product halves |
| `03-recomp-instructions-header.patch` | `ps2xRecomp/include/ps2recomp/instructions.h` | `ctc2 $vi21` IS the VU0 I register (PCSX2 `REG_I = 21`) |
| `04-runtime-cmakelists.patch` | `ps2xRuntime/CMakeLists.txt` | unity-build excludes game overrides; builds `ps2_dbcman_hle.cpp` |
| `05-kernel-gs-stub.patch` | `ps2xRuntime/src/lib/Kernel/Stubs/GS.cpp` | `sceGsExecLoadImage` vram_addr is in 64-word BLOCK units |
| `06-kernel-mpeg-stub.patch` | `ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp` | MPEG data-empty callback is DRIVEN (else the decoder starves) |
| `07-kernel-interrupt-syscall.patch` | `ps2xRuntime/src/lib/Kernel/Syscalls/Interrupt.cpp` | per-vblank `deliverPadData()` (frequency is load-bearing) |
| `08-gs-gpu-header.patch` | `ps2xRuntime/include/runtime/ps2_gs_gpu.h` | declarations for the GS fixes + instruments below |
| `09-gs-gpu.patch` | `ps2xRuntime/src/lib/ps2_gs_gpu.cpp` | **4 topics, interleaved** — present-fairness handoff; ReadVram/WriteVram psm table `0x3F`->`0x40`; PRIM write respects PRMODECONT; census/displog/giflog/quadlog instruments + `PS2X_GS_IMGCONT2` |
| `10-gs-rasterizer.patch` | `ps2xRuntime/src/lib/ps2_gs_rasterizer.cpp` | undefined texture PSM samples as CT32, not magenta |
| `11-memory-header.patch` | `ps2xRuntime/include/runtime/ps2_memory.h` | `GsPendingImageFn` hook (cycle 83 DIRECT image carry) |
| `12-memory-dma.patch` | `ps2xRuntime/src/lib/ps2_memory.cpp` | scratchpad SPR channels + chain-walk cap; **`PS2X_DMA_REFVIF`, `PS2X_DMA_TTEQW`** (16-byte parity-preserving TTE embeds) |
| `13-pad.patch` | `ps2xRuntime/src/lib/ps2_pad.cpp` | headless CROSS driver + `PS2X_PAD2_LOG` (both default-OFF) |
| `14-runtime-core.patch` | `ps2xRuntime/src/lib/ps2_runtime.cpp` | wires `setGsPendingImageFn` (where `m_gs` is in scope) |
| `15-vif1-interpreter.patch` | `ps2xRuntime/src/lib/ps2_vif1_interpreter.cpp` | **`PS2X_VIF_DIRECTCARRY`** (payload-aligned Path2 image continuation) + **`PS2X_VIF_DIRECTSPAN`** (cross-transfer DIRECT span state; PCSX2 `vif1.tag.size` persists) |
| `16-vu1-core.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp` | **`PS2X_VU_CLAMP`** — PS2 VU float semantics (no NaN/Inf/denormals; PCSX2 `vuDouble()`); `PS2X_VU1_MEMCENSUS` diagnostic |
| `17-vu1-detail-header.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_detail.h` | VU1 clip/FTOI helpers |
| `18-vu1-lower.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_lower.cpp` | XGKICK 0x4000 packet cap (PCSX2 `Gif_Unit.h`); FSEQ/FSAND/FSOR write It |
| `19-vu1-upper.patch` | `ps2xRuntime/src/lib/vu/ps2_vu1_upper.cpp` | CLIP 24-bit mask; FTOI saturation |
| `20-dbcman-hle-header.patch` | `ps2xRuntime/include/runtime/ps2_dbcman_hle.h` | DBCMAN.IRX HLE declarations (new file) |
| `21-dbcman-hle.patch` | `ps2xRuntime/src/lib/ps2_dbcman_hle.cpp` | DBCMAN.IRX HLE implementation (new file) |

## Topic details (carried forward verbatim from the per-topic stack)

These are the original root-cause write-ups, including the PCSX2 cross-checks. The heading names
the OLD patch; the inventory above maps each topic to the file-patch that now carries it.

### SQRT/RSQRT read ft (was `01-fpu…`)

`ps2xRecomp` emitted `SQRT.S`/`RSQRT.S` reading **fs**, but R5900 `sqrt.s fd, ft` reads **ft** (rsqrt = `fs / sqrt(ft)`, with a divide-by-zero guard). ~130/~180 game sites wrong; zeroed the camera basis → every vertex at (2048,2048) → black screen. | cont.28/29

### VOPMULA/VOPMSUB cross product (was `02-vu-opmula…`)

`VOPMULA`/`VOPMSUB` were emitted component-wise; they are the outer-product halves of a **cross product**. Every VU0-macro cross product ≡ 0 → Y-degenerate camera/geometry. | cont.30

### sceGsExecLoadImage BLOCK units (was `03-gs-execloadimage…`)

`sceGsExecLoadImage`/`ExecStoreImage` stubs converted `vram_addr` with ×8 (`*2048/256`); the SCE convention is already **64-word BLOCK units**. The 14-bit DBP field then truncated mod 16384, so every upload through the sceGs path landed at `addr*8 mod 16384` — fonts/CLUTs uploaded to aliased addresses; menu text invisible. | cont.37

### GS present fairness (was `04-gs-present…`)

Host present starvation: the UI thread's `latchHostPresentationFrame()` waits on the GS `m_stateMutex` while the EE thread re-acquires it back-to-back per GIF packet (software rasterizer, no mutex fairness) — the window never updates despite correct VRAM. Adds a `m_presentWaiters` atomic; the GIF hot path yields at a packet boundary while a present is waiting (bounded, recursive-mutex-safe). | cont.39

### VU1 clip mask / FTOI saturate / Fs dest + XGKICK cap (was `06-vu1-clip…`)

**(d) XGKICK built an unbounded GS packet out of garbage VU1 memory** (added cont.135 cycle 77). The XGKICK tag walk accumulates `totalBytes` over a `for (safety = 0; safety < 256; ...)` loop that bounds ITERATIONS but not BYTES, and a single tag with `flg=0` contributes `16 + nloop*nreg*16`. When VU1 memory holds garbage the walk never meets a valid EOP, wraps the 16 KB circularly, and materialises a huge buffer which is then handed to the GIF parser as a "GS packet" — measured post-FMV: **34 of 40** oversized packets originated here (host backtrace via the new `PS2X_GS_BIGPKT`), one being **229,888 B = VU1's 16 KB repeated ~14x** and filled with `0xFFC00000` (float -NaN), its `tag0` carrying `flg=3` (a RESERVED/invalid GIFtag format). Walking that garbage is what produced the long-standing malformed `BITBLTBUF`/`TRXREG` (`dpsm=0x06`/`0x3F`, dims like 2732x2013, and all-bits-set `dbp=16383 dbw=63`). **PCSX2 cross-check (binding rule, done BEFORE finalising): `pcsx2/Gif_Unit.h` `GetGSPacketSize()` has EXACTLY this guard — `if (pathIdx == GIF_PATH_1 && curSize >= 0x4000) return 0;`** (0x4000 = VU1 memory size; our `PS2_VU1_DATA_SIZE = 16*1024` matches), and its comment "Bios does this..." confirms a runaway walk is a NORMAL condition whose hardware-faithful response is to DROP the packet, not a workaround. Fix: abort the packet once `totalBytes >= 0x4000`. Kill switch `PS2X_VU1_XGKICK_LIMIT=0`. **Validated by A/B on identical env/run-length: malformed transfer descriptors 1224 -> 67 (-94.5%) while VALID transfers rose by 3157** (`bad=1224 ok=1752776` -> `bad=67 ok=1755933`). ⚠ Does NOT by itself prove the level-texture sentinel (textured terrain) — the residual 67 come from other submitters, and the upstream question (why VU1 memory is NaN at FMV teardown) is still open. Three VU1-interpreter correctness bugs, all mirrored from PCSX2 `pcsx2/VUops.cpp`. **(a) CLIP never masked its result to the architectural 24 bits** (`clip = (clip << 6) \| flags`), so junk accumulated above bit 23 and never cleared — which made `FCOR`'s `== 0xFFFFFF` test permanently false, silently disabling the microprogram's trivial-reject. Near-plane vertices (`w≈0`) were then drawn instead of rejected, as huge triangles streaking from one collapse point. `FCAND` only escaped by accident (its `&` masks implicitly). **(b) FTOI0/4/12/15 used a plain C cast**, which is UB on overflow/NaN and yields `0x80000000` on x86 SSE for *every* out-of-range input, including positive ones — must saturate to `0x7FFFFFFF`/`0x80000000`. **(c) FSEQ/FSAND/FSOR wrote the hardwired `VI01`** instead of the instruction's `It` register (only the clip ops `FCEQ`/`FCAND`/`FCOR` have a hardwired `VI01` destination), dropping the result and clobbering a live register. | cont.44

### DMA scratchpad + chain walk (was `07-dma-scratchpad…`)

**(b) The DMA source-chain walker truncated long chains at 4096 tags, silently dropping every descriptor after the cut** (added cont.132 cycle 76; the file already carried fix (a) below, so both live in this patch — a second patch on the same file would collide in `01_setup`). `kMaxChainTags = 4096` was a hard ceiling on the tag-walk loop: on overrun it simply `break`s, so the **tail of a long display list is discarded** with no error. Measured with the new `PS2X_DMA_CHAINLOG=1` (default OFF): VIF1 chains were terminating with `end=maxtags tags=4096` while movie-strip `ref` tags were still pending. LOTR ROTK draws its FMV as **four 128×512 column quads**, each committed as one `ref` tag by `0x13F1E0`; the 4th fell past the cut, so **the right quarter of every FMV frame was black** (measured: content spanned 384 of 512 px; the title card read "The Lord of the RIN"). Chain-walk provenance was nailed end-to-end — guest emits 4 (`[movemit:13f3f0]`), commits 4 (`[movstrip:commit]`), VIF1 submitted 3, GS drew 3. **PCSX2 cross-check (`pcsx2/Vif1_Dma.cpp:494`): the chain ends ONLY on `TAG_REFE`, `TAG_END`, or `IRQ && TIE` — there is no tag-count ceiling anywhere in the walk**, which matches our `endChain` conditions exactly minus the cap. Fix: raise the ceiling to `1<<20` (a pure runaway guard against a malformed self-referencing chain, overridable via `PS2X_DMA_MAXCHAINTAGS`) instead of letting it truncate real work. Sentinel: `[GS:quad] STRIP` col=3 appears and the FMV fills the full 512 px ("The Lord of the RINGS" complete). **(a) The EE scratchpad DMA channels (fromSPR ch8 `0x1000D000`, toSPR ch9 `0x1000D400`) were not implemented.** The CHCR `STR` write fell through the DMA dispatcher (which handled only GIF/VIF1/VIF0) with **no transfer**, and since `readIORegister` reports every channel's CHCR as `& ~0x100` (STR always clear), the guest's completion poll exited immediately — so the copy **silently never happened** and the destination kept stale data. Load-bearing for **all text**: LOTR builds each text GIF packet in the scratchpad and copies it to the display list in `13F030`, which **forks on size** (`0x13f05c: slti at,s1,0x41`) — `qwc <= 64` → plain `memcpy`, `qwc >= 65` → fromSPR DMA. A glyph costs 5 qw (+8 qw header), so every string over ~11 glyphs was dropped while short ones drew fine — the long-standing "only short strings render" symptom. Fixing it made the language-select header (25 glyphs = 133 qw) and the **entire legal screen** render. | cont.46

### runner unity excludes overrides (was `08-runner-unity…`)

**Build-hygiene (not a correctness fix).** The runner's unity build batched the game's hand-written override modules together with the thousands of generated recompiled functions. Two bad consequences: (a) unity batches are formed from the source list, so **adding or removing one override module re-shuffled every later batch and forced a near-full rebuild** (~8 min instead of ~110 s) — which made splitting `register_overrides.cpp` into modules prohibitively slow; (b) batched files are `#include`d into one TU, so file-scope `static`s from unrelated modules — and the `__LINE__`-keyed names `PS2_REGISTER_GAME_OVERRIDE` generates — could collide. The patch reads `src/runner/game_overrides.manifest` (written by `scripts/03_build_game.sh` when it installs the overrides) and sets `SKIP_UNITY_BUILD_INCLUSION` on exactly those files, so each override module is its own TU. No manifest = plain upstream behavior. | cont.47

### headless CROSS driver (was `09-pad-headless…`)

**Test-infra (not a correctness fix), default-OFF.** Two pad-backend diagnostics in `PSPadBackend::readState`. (a) `PS2X_PAD_CROSS_AT=<n>` / `PS2X_PAD_CROSS_LEN=<len,def 100>`: a **headless CROSS driver** — forces the CROSS button pressed for `len` `readState()` calls starting at call `n`, then releases (a clean press→release edge), with **no dependency on window focus**. XTEST key injection is hopelessly racy on a live desktop (focus is stolen by the real user/other windows, so keys silently never reach raylib — the repeated false "input doesn't work" results); this drives the language-select → legal transition deterministically. (b) `PS2X_PAD2_LOG=1`: the host-sample trace (`[pad:sample]`) that was previously living as an **uncommitted clone edit not captured by any patch** — folded in here so a re-clone no longer loses it. Both default-OFF ⇒ shipped behavior unchanged. | cont.49

### GS ReadVram psm bounds (was `10-gs-readvram…`)

**GS `ReadVram`/`WriteVram` read one past the end of the psm→function table on an undefined PSM → SIGSEGV.** The tables are indexed by `psm & 0x3F` (range 0..0x3F = 0..63) but were sized `std::array<…, 0x3F>` (63 slots, indices 0..62), so `psm==0x3F` dereferenced a garbage `std::function`. Hit while rasterizing page-14 ("Loading") content on the way to the EA FMV: the per-pixel z-test read uses `zpsm = ((ZBUF>>24)&0xF)|0x30`, which is `0x3F` when the ZBUF psm nibble is `0xF`. Provenance nailed with gdb: the game **never** writes `FRAME.PSM=0x3f` (0 hits across a run that reached page-14 render), and `reset()` zeroes the draw context, so `0x3F` is the ZBUF-derived z psm, not a color format or a stale default. Fix: size both arrays `0x40` and extend the ctor init loop to `< 0x40`, so index `0x3F` resolves to the pre-existing `ReadNull`(→0)/`WriteNull`(no-op) default like every other undefined PSM. **PCSX2 cross-check** (`GS/GSLocalMemory.cpp`): its table is exactly `psm_t m_psm[64]` (confirms the `0x3F`→`0x40` sizing) and defaults every undefined slot to `PSMCT32`; and (rev. cont.116e) we now MATCH that: every undefined psm slot falls back to `ReadCT32`/`WriteCT32`. The original revision used `ReadNull`(0), reasoned from the only then-known undefined psm (`0x3F`, an invalid Z read where 0 is benign) — but LOTR's movie sprite textures with TEX0.PSM=`0x20` (also undefined) and a Null read made every texel 0 (invisible movie); PCSX2's CT32 default covers both cases. | cont.69

### IOP DBCMAN pad2 HLE (was `11-iop-dbcman…`)

Controller input for pad2/libdbc games — **DBCMAN.IRX HLE (Sony DualShock2 manager, sid `0x80001300`)**. Adds a new runtime unit `ps2_dbcman_hle.cpp/.h`: `answerDbcManRpc()` handles `SetWorkAddr`/`CreateSocket`/`GetDepNumber`/`ReceiveData`/`SRData` (DS2 digital+sticks, profile `FF FF 0F 00 00`), and `deliverPadData()` emulates the IOP's per-vblank DMA of pad state into the EE double buffers, sourced from `PSPadBackend`. `Interrupt.cpp` calls `deliverPadData()` in the vblank interrupt worker (per emulated vblank — the frequency is load-bearing: it re-asserts the per-port work table each tick, which the game wipes during its BSS load; too-rare delivery ⇒ controller reads "removed" ⇒ self-exit). Kill switch `PS2X_PAD2_HLE=0`. **Supersedes the old `05-iop-dbcman-pad2-hle.patch`** (removed): ee14958's IOP refactor deleted the old `ps2_iop_dbcman.cpp` and removed `runtime->iop().handleRPC`; since LOTR drives DBCMAN over the RAW SIF transport (guest `0x11bfa8/0x11c178`) that native ee14958 never sees, the game override intercepts that transport and calls `ps2_dbcman_hle::answerDbcManRpc()` directly (a free function). | cont.40 / ee14958 migration

### MPEG GetPicture / data callback (was `12-mpeg-getpicture…`)

**The MPEG HLE stored the game's `sceMpegAddCallback` registrations and never invoked them — so the decoder starved (no bitstream ever fed) and movie playback could not work.** libmpeg pulls its elementary stream through the registered **type-1 "data empty" callback**: LOTR ROTK registers `func=0x13B3A0, data=job` at decode-init (`0x13B4C0` passes `t0=0x13B3A0` into `0x144020`); the handler pops one queued demux MPG2 section per call and feeds it via `sceMpegAddBs` (`sec+0x40`), and on an empty queue sets the job's pump-bypass flag `[job+0x102F]=1` and returns 0 — which is how the movie ENDS through the game's own job engine (`0x13B7B0 → +0x102E → UPDATE 0x205FF0 @0x206098 → END path 0x2059C0`). The patch adds `matchingDataCallbacks` + `dispatchGuestDataCallback` (pumped guest call in its own ctx, returns v0) and drives the callback from `sceMpegGetPicture` while `decodedFrames` is empty (bounded 512/call; **no `!streamEnded` gate** — after ffmpeg EOF the game must still discover exhaustion through the callback, else the player parks in state 4 forever). Also clears `[impl+0]` on GetPicture entry (mirrors guest `0x1175FC`) and — reverting this patch's earlier revision — **must NOT write `[impl+0]=1` per delivered picture**: that word is the WHOLE-JOB completion the game's poll (`0x143FD0` reads `[[job+0x10A0]]` = `[0x473300]`) treats as movie-over, and the per-picture write ended the movie after its first (blank) frame. Adds a capped `[MPEG:GetPicture:serve]` log — which (rev. cont.132) also prints the **decoded** frame dims alongside the declared ones (`size=` is `playback.width/height` from the sequence header; `decoded=` is `frame.width/height`). The two are load-bearingly different: `writeDecodedFrameToGuest()` sizes its column-strip store from `frame.width` (`macroblockColumns = align16(frame.width)/16`) and **never touches strips beyond it**, so a short decode silently leaves the right edge of the picture unwritten. Logging both is what exonerated the decoder when the FMV's right quarter went black (`decoded=512x512` on every serve ⇒ the defect was a skipped draw in the guest, `FUN_001fa390`). Validated: all **5,120 frames of HELMFMV1 decode, serve (`have=1 512x512`) and are consumed** (UPDATE runs every frame; natural END fires at stream exhaustion). **PCSX2 cross-check n/a** — PCSX2 executes the real libmpeg on an emulated IPU and has no HLE counterpart; the reference implementation is the game's own registration/refill disassembly cited above. | cont.115l cycle 55, reworked cont.116 cycle 57

### VU0 ctc2 $vi21 I register (was `13-vu0-ctc2…`)

**`ctc2 $rt,$vi21` (write the VU0 I register) was translated to a write of the dead `vu0_info` field, so every subsequent I-register use (`vmuli`/`vaddi`/…) multiplied by a stale `vu0_i` (0.0).** Upstream's `VU0ControlRegisters` enum fabricated most numbers: it had `VU0_CR_I = 4` and a nonexistent `VU0_CR_INFO = 21`, while on the real EE the COP2 control register `$vi21` IS the I register (**PCSX2 `pcsx2/VU.h`: `REG_I = 21`** — the binding cross-check). The CFC2/CTC2 translator switches on the RAW rd (0-31), so the game's one and only COP2 CCR access — the quad emitter `0x13F3F0`'s `ctc2 $t0,$vi21` loading 255.0 before `vmuli` scales the vertex color float4 to 0-255 — hit the INFO case and the emitted RGBAQ was always 0,0,0,0 (movie strips rasterized as flat BLACK quads; found cycle 58 via emitted-packet dump). Fix: `VU0_CR_I = 21`, `VU0_CR_INFO` parked at 40 (outside the encodable 0-31 range → dead case). Scope verified: the whole LOTR ELF contains exactly one cfc2/ctc2 CCR access ($vi21), so the surgical swap is complete for this game; the rest of the enum is still PCSX2-unfaithful (STATUS/MAC/CLIP/R/TPC/… numbers) — renumber upstream if another game needs them. Codegen patch ⇒ needs ps2_recomp rebuild + FULL regen. | cont.116o cycle 58

### GS PRIM write / PRMODECONT (was `14-gs-prim…`)

**GS `PRIM` register writes clobbered the PRMODE-owned attribute flags.** The runtime's `writeRegister(GS_REG_PRIM)` unconditionally applied ALL bits (type + IIP/TME/FGE/ABE/AA1/FST/CTXT/FIX); on real GS, while `PRMODECONT.AC == 0` the flags are owned by `PRMODE` and a PRIM write applies ONLY the primitive type — **PCSX2 `GSState::ApplyPRIM`: `if (PRMODECONT.AC == 1) { m_env.PRIM.U32[0] = prim; } else m_env.PRIM.PRIM = prim & 0x7;`** (and `GIFRegHandlerPRMODE` conversely preserves the type). LOTR ROTK toggles PRMODECONT 1↔0 and sets `PRMODE=0x58` (IIP+TME+ABE) for textured passes (gdb writeRegister trace, cycle 59), then every quad-emitter GIFtag (`PRE=1 PRIM=4`, plain tristrip) stripped TME/ABE in our GS ⇒ the movie strips (and any PRMODE-mode textured pass) rasterized flat/untextured. Fix: gate the flag-bit assignment on `m_prmodecont` (type + vertex-queue reset stay unconditional); the existing PRMODE handler already writes flags only when `!m_prmodecont` (type untouched — equivalent to PCSX2's preserve). | cont.116q cycle 59

### undefined psm samples CT32 (was `15-gs-sample…`)

**The rasterizer's texture-sample format dispatch returned a magenta debug texel for undefined PSM codes.** `GSRasterizer::sampleTexture::samplePoint` switches on `tex.psm` after `ReadVram`; psm values outside the defined set fell through to `0xFFFF00FF`. LOTR ROTK's movie-strip TEX0 carries the game's LITERAL `psm=0x20` (a hardcoded immediate @0x1fa3d4 fed raw into TEX0 bits 20-25) — an undefined code a real GS decodes as PSMCT32 (**PCSX2 `GSLocalMemory`: undefined psm table entries take the PSMCT32 layout — the same fallback patch 10 gave `ReadVram`**, which already returned a CT32 texel for the read; only this post-read dispatch discarded it). Fix: undefined psm → `applyTexa(..., GS_PSM_CT32, out)`. With patch 14 (TME via PRMODE honored) this turned the movie strips from solid magenta into the sampled CT32 texture. | cont.116q cycle 59

### GS census / displog instruments (was `17-gs-debug…`)

**Diagnostics (not a correctness fix), default-OFF.** Two GS instruments that cracked cycle 73. **(a) `PS2X_GS_CENSUS=<path>`** streams one line per GS debug event (`D`raw / `T`ransfer / `P`resent / `G`iftag / `R`egister) to a file, with `_AT`/`_FRAMES`/`_EVERY` frame windowing (periodic sampling — the interesting frames are ~10 min into the boot and the debug frame index is neither the game's `c` counter nor wall-clock, so a single absolute window is unusable), `_KINDS` bitmask and a `_MAX` line cap. It streams rather than reusing the ImGui panel's `writeGsDebugDump()`, because the 512-entry debug ring cannot hold a whole frame's draws. Hooked at the single point where an entry is stamped with seq/frameIndex, so it sees every kind exactly once; it also un-pauses `m_debugHistoryPaused` (capture is off by default and only the ImGui panel un-paused it). **(b) `PS2X_GS_DISPLOG=1`** prints PMODE/DISPLAY1/DISPLAY2/DISPFB1/DISPFB2 with their decoded WxH on every change — needed as a separate print because `applyGsDispEnv()` assigns `m_privRegs->display1` directly rather than through `writeRegister()`, so a DISPLAY change never appears as a Register debug event. Together these proved (i) the presentation is a genuine **512×512** letterboxed by the host viewer into the 640×448 window (retiring the "partial presentation" defect) and (ii) GS image transfers are **100.0% valid during the FMV and 76–100% malformed within 10 frames of its end**. Both wholly inert when the env vars are unset. **(c) `PS2X_GS_GIFLOG=1`** (added cont.130) counts/prints two GIF-parser pathologies: `IMAGE-CLAMPED` (a GIFtag declaring more IMAGE payload than its packet carries — the remainder is silently DROPPED) and `TAG-WHILE-INCOMPLETE` (a new tag parsed while the previous image transfer is still short of its pixel count). It measured **~1,744,000 clamps, essentially all with `avail=0`** — i.e. **the generic GIF image path delivers nothing at all**; textures only ever reach VRAM via the native fast paths. The `processGIFPacket()` header comment records the root cause and the FALSIFIED naive fix (see the cont.130 row in the game repo's progress.md). **(d) `PS2X_GS_QUADLOG=1`** (added cont.132) decodes the four vertex XYs of every 4-vertex PACKED primitive as `processGIFPacket` parses it, filtered to movie-strip geometry (~128 px wide, >400 px tall) and reported as a column index. It is the **sentinel for the "FMV right quarter is black" defect**: the FMV is drawn as four 128×512 column quads and this probe measured `col=0 ×16, col=1 ×15, col=2 ×15, col=3 ×0` — proving the 4th quad never reaches the parser at all (so it is not scissored, depth/alpha-killed, or rasterizer-rejected). Keep it until that column renders. All four wholly inert when unset. | cont.129 cycle 73 / cont.130 cycle 74 / cont.132 cycle 76

## Maintenance

- **Regenerate one patch** after editing the clone:
  `git -C tools/<game>/PS2Recomp diff -- <the one file> > patches/NN-name.patch`
- **Never let two patches touch the same file** — that is what broke the previous stack.
- `01_setup.sh` **fails loudly** if any patch does not apply: a partial stack is not a usable
  toolchain. If upstream absorbed a fix, delete that patch deliberately; if upstream drifted,
  rebase it and re-run the verification above.
- Patches apply with plain `git apply` from the clone root; paths are relative to it.
