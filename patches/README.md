# Local PS2Recomp patches

Correctness fixes to upstream [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp) that we
carry locally until they are upstreamed. `scripts/01_setup.sh` applies every `*.patch` here
(idempotently) after cloning/re-using a per-game toolchain at `tools/<game>/PS2Recomp/`, so a
re-clone or a new game no longer silently loses them.

All were found root-causing the example game (LOTR EUR) with gdb against the generated
C++ + disassembly; each was the proven blocker for a rendering milestone.

| Patch | What it fixes | Found |
|---|---|---|
| `01-fpu-sqrt-rsqrt-read-ft.patch` | `ps2xRecomp` emitted `SQRT.S`/`RSQRT.S` reading **fs**, but R5900 `sqrt.s fd, ft` reads **ft** (rsqrt = `fs / sqrt(ft)`, with a divide-by-zero guard). ~130/~180 game sites wrong; zeroed the camera basis → every vertex at (2048,2048) → black screen. | cont.28/29 |
| `02-vu-opmula-opmsub-cross-product.patch` | `VOPMULA`/`VOPMSUB` were emitted component-wise; they are the outer-product halves of a **cross product**. Every VU0-macro cross product ≡ 0 → Y-degenerate camera/geometry. | cont.30 |
| `03-gs-execloadimage-vramadr-block-units.patch` | `sceGsExecLoadImage`/`ExecStoreImage` stubs converted `vram_addr` with ×8 (`*2048/256`); the SCE convention is already **64-word BLOCK units**. The 14-bit DBP field then truncated mod 16384, so every upload through the sceGs path landed at `addr*8 mod 16384` — fonts/CLUTs uploaded to aliased addresses; menu text invisible. | cont.37 |
| `04-gs-present-fairness-handoff.patch` | Host present starvation: the UI thread's `latchHostPresentationFrame()` waits on the GS `m_stateMutex` while the EE thread re-acquires it back-to-back per GIF packet (software rasterizer, no mutex fairness) — the window never updates despite correct VRAM. Adds a `m_presentWaiters` atomic; the GIF hot path yields at a packet boundary while a present is waiting (bounded, recursive-mutex-safe). | cont.39 |
| `05-iop-dbcman-pad2-hle.patch` | Controller input for pad2/libdbc games: HLEs DBCMAN.IRX (Sony DualShock2 manager, sid `0x80001300`) — answers `SetWorkAddr`/`CreateSocket`/`GetDepNumber`/`ReceiveData`/`SRData` and emulates the IOP's per-vblank DMA of pad state (DS2 digital+sticks, profile `FF FF 0F 00 00`) into the EE double buffers registered at socket creation, sourced from the existing `PSPadBackend` (host gamepad, else keyboard). Kill switch `PS2X_PAD2_HLE=0`. Protocol reversed from the EE-side libdbc/libpad2 disassembly. | cont.40 |
| `07-dma-scratchpad-spr-channels.patch` | **The EE scratchpad DMA channels (fromSPR ch8 `0x1000D000`, toSPR ch9 `0x1000D400`) were not implemented.** The CHCR `STR` write fell through the DMA dispatcher (which handled only GIF/VIF1/VIF0) with **no transfer**, and since `readIORegister` reports every channel's CHCR as `& ~0x100` (STR always clear), the guest's completion poll exited immediately — so the copy **silently never happened** and the destination kept stale data. Load-bearing for **all text**: LOTR builds each text GIF packet in the scratchpad and copies it to the display list in `13F030`, which **forks on size** (`0x13f05c: slti at,s1,0x41`) — `qwc <= 64` → plain `memcpy`, `qwc >= 65` → fromSPR DMA. A glyph costs 5 qw (+8 qw header), so every string over ~11 glyphs was dropped while short ones drew fine — the long-standing "only short strings render" symptom. Fixing it made the language-select header (25 glyphs = 133 qw) and the **entire legal screen** render. | cont.46 |
| `06-vu1-clip-mask-ftoi-saturate-fs-dest.patch` | Three VU1-interpreter correctness bugs, all mirrored from PCSX2 `pcsx2/VUops.cpp`. **(a) CLIP never masked its result to the architectural 24 bits** (`clip = (clip << 6) \| flags`), so junk accumulated above bit 23 and never cleared — which made `FCOR`'s `== 0xFFFFFF` test permanently false, silently disabling the microprogram's trivial-reject. Near-plane vertices (`w≈0`) were then drawn instead of rejected, as huge triangles streaking from one collapse point. `FCAND` only escaped by accident (its `&` masks implicitly). **(b) FTOI0/4/12/15 used a plain C cast**, which is UB on overflow/NaN and yields `0x80000000` on x86 SSE for *every* out-of-range input, including positive ones — must saturate to `0x7FFFFFFF`/`0x80000000`. **(c) FSEQ/FSAND/FSOR wrote the hardwired `VI01`** instead of the instruction's `It` register (only the clip ops `FCEQ`/`FCAND`/`FCOR` have a hardwired `VI01` destination), dropping the result and clobbering a live register. | cont.44 |

## Maintenance

- **Regenerate** a patch after editing the tools clone:
  `git -C tools/<game>/PS2Recomp diff -- <files> > patches/NN-name.patch`
- **Upstream-absorbed or conflicting** patches make `01_setup.sh` print a `WARNING: does not
  apply` line — check whether upstream fixed it (delete the patch) or drifted (rebase the
  patch by re-applying the change manually and regenerating).
- Patches apply with plain `git apply` from the PS2Recomp clone root; paths are relative to
  that root (`ps2xRecomp/…`, `ps2xRuntime/…`).
- A `functions.csv`-affecting patch does not exist and should not: game inputs stay in the
  game repo. Only engine-generic PS2Recomp fixes belong here.
