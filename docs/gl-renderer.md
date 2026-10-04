# The GL renderer arc — playability and resolution

> **Status: DESIGN, not yet built.** Written (cont.329) from four read-only surveys of
> the runtime, the PCSX2 reference, and the measurements listed in §2. Nothing here is committed
> code. The CPU rasterizer remains the default and the only validated path until a phase gate says
> otherwise.
>
> Companion to [`vu1-jit.md`](vu1-jit.md) (the VU1 arc) — same shape: why the current design cannot
> reach the goal, what was measured, what is already disproven, and the phased plan.

## 1. Why this arc exists

Two user goals converge on one piece of work (user decision):

- **Playability.** The game runs, but not smoothly.
- **Higher resolution**, en route to a build people can use by dropping the disc files next to the
  executable.

These are the same project. Our renderer emulates PS2 video memory pixel-for-pixel at native
resolution: a 4 MB `m_vram` buffer in PS2 swizzle layout (`PS2_GS_VRAM_SIZE`, `ps2_memory.h:51`).
**Upscaling is not a missing setting, it is incompatible with that design** — an upscaled colour+Z
target does not fit in 4 MB and has no meaning in a layout defined on 8 KB pages of it. Higher
resolution requires render targets that live outside the VRAM model, as GL textures. That is the
same split PCSX2 draws between its software and hardware renderers.

**The user has explicitly accepted losing bit-exactness** in exchange for playability and
resolution. That is the decision that makes this arc viable — see §3.

## 2. What is already measured (do not re-derive)

### 2.1 The performance floor — why the CPU path cannot get there alone

cont.328, deterministic replay, guest frames 2000-3500 of the recorded fight (9.4-11.6 kprim/frame):

| arm | wall over the window | per guest frame |
|---|---|---|
| raster on | 55.5 s | **37.0 ms** |
| raster **entirely ablated** (`PS2X_GS_NORASTER=1`) | 35.5 s | **23.6 ms** |

Non-overlapping. So the raster is 36% of a light frame — and **with a free rasterizer the frame is
still 23.6 ms against a 20 ms vblank.** No single-sided optimisation reaches the 1-vblank step.
This is the number that justifies moving rendering wholesale rather than shaving it: the GL path
must take raster time to *near zero on the CPU* **and** free CPU cores for the EE side.

### 2.2 The previous GPU attempts failed under a constraint we are now dropping

| path, per level frame (cont.232/233, Intel Xe iGPU) | ms |
|---|---|
| CPU rasterizer, 3 threads | 127 |
| GPU tile rasterizer (compute, pixel-walks-prims) | 281 |
| GPU hardware raster, **bit-exact**, ordered interlock | 170 |
| GPU hardware raster, **no interlock** (output not bit-exact) | **101-130** |

The interlock alone was +40 ms; with the whole pipeline inside it, +700 ms. The shader was also
SSBO-gather-bound (a bilinear paletted texel = 16 dependent gathers) **because it sampled swizzled
VRAM by hand**. Both costs exist *only* to be bit-exact. A modern renderer has neither: blending
becomes GL ROP state (no read-modify-write in the shader, so no interlock), and textures become
real GL textures sampled by the texture unit.

⚠ Those numbers are on this development laptop (i7-1165G7, 4 physical cores, Intel Xe iGPU sharing
the package power budget, plus an unloaded GeForce MX330). **Do not treat 101-130 ms as the verdict
for end-user hardware.** Equally, do not assume a win here transfers — measure on both.

### 2.3 What this game actually draws

Every blend mode this game is known to use maps to GL fixed-function state:

| ABCD | equation | measured share (source-cited) | GL |
|---|---|---|---|
| abe=0 | `Cs` | clears/fills, 8.8% of raster ticks | `glDisable(GL_BLEND)` |
| **0101** | `Cd + (Cs-Cd)*As` | **the top-3 draw states = 89.7% of 48.8M draws** | `SRC_ALPHA, ONE_MINUS_SRC_ALPHA` |
| 0201 | `Cd + Cs*As` | 28% of raster ticks (terrain) | `SRC_ALPHA, ONE` |
| 2101 | `Cd*(1-As)` | 11.5% (sprite 24) | `ZERO, ONE_MINUS_SRC_ALPHA` |
| 0122 | `(Cs-Cd)*FIX>>7` | 13.4% (two full-screen sprites/frame) | `FUNC_SUBTRACT` + constant colour |
| 0221 | `Cd + Cs*FIX>>7` | 3.9% (12M tiny draws) | `CONSTANT_COLOR, ONE` |
| 0121 | `Cd + (Cs-Cd)*FIX>>7` | the render-target composite | `CONSTANT_ALPHA, ONE_MINUS_CONSTANT_ALPHA` |

Textures: **T4 82.6% + T8 11.1% + CT24 6.0% = 99.6%** of texel fetches; every CLUT is CT32/CSM1;
wrap is **REPEAT 96.15% / CLAMP 3.85%** only. Frame targets are CT32+Z24 overwhelmingly (the fast
path is gated on exactly that pair; uploads are CT32 99.9997%).

**Never used by this game** (run171 census, cited at `gs_cpu_backend.cpp:3934-3936`): **DATE, PABE,
CSM2**. The GPU path already rejects draws using them and the rejection counter reports none. That
removes the three hardest GL cases outright.

Render-to-texture, from an existing 199 s run log (`rawTex`/`rawSplits` on `[gsgpu:handoff]`):

| measure | value |
|---|---|
| primitives | 75.0 M |
| draws sampling a render target | 235,761 |
| share of primitives | **0.31%** |
| per presented frame | **~62** |

Negligible pixel work, but it happens every frame. It must be handled as *targets that stay GL
textures* — never by reading back, which would be ~62 GPU stalls per frame.

### 2.4 The draw-state census (cont.329, 28.9 M draws, `PS2X_GS_STATECENSUS` on the recorded fight)

Measured, not inferred. Percentages are of draws in the final cumulative dump.

| field | distribution | consequence for GL |
|---|---|---|
| **ABCD** | `0101` **94.3%**, `0221` 3.7%, `0121` 1.0%, `0201` 1.0%, `0122`/`2101` ~0% | all fixed-function (§2.3) |
| **FBMSK** | `A` (write RGB, keep alpha) **100.0%**; `mix` ~0% | `glColorMask(1,1,1,0)`. **The "mix" bucket is a non-problem.** |
| **DATE / DATM / PABE** | `0` = **100%** each | **confirmed never used** — the three hardest GL cases are absent |
| **DTHE / SCANMSK** | `0` = 100% | ignore |
| **FRAME PSM / ZBUF PSM** | CT32 **100%** / Z24 **100%** | **one render-target format**, no 16-bit path needed |
| **ZTE** | `1` = 100% | depth test always on in this era |
| **ZTST** | GEQUAL 95.6%, GREATER 3.7%, ALWAYS 0.7% | `glDepthFunc` |
| **CLAMP WMS/WMT** | REPEAT 95.4%, CLAMP 0.8% | `GL_REPEAT` / `GL_CLAMP_TO_EDGE` |
| **texture PSM** | T4 68.4%, T8 27.1%, T4HL 0.6% | paletted; host-decode to RGBA8 |
| **COLCLAMP** | `1` 96.3%, `0` 3.7% | GL always clamps; matches the CPU path, which also ignores COLCLAMP=0 |
| **ATE** | `1` 93.3% | alpha test is the norm |

**⚠ The one real complication: AFAIL.** The single largest draw class, **56.42% of all draws**, is
`ATE=1, ATST=GEQUAL, AFAIL=FB_ONLY, ZMSK=0, ZTST=GEQUAL` — fragments failing the alpha test still
write colour but **not** depth. GL cannot mask depth per fragment, so this is not a `discard`.

PCSX2 handles exactly this (`GSRendererHW.cpp:8960-9030`):
- it first collapses the trivial cases — `AFAIL_FB_ONLY` with depth writes already off is a **no-op**
  and becomes `ATST_ALWAYS` (we have none of those: our case has `ZMSK=0`);
- `simple_fb_only = (afail == AFAIL_FB_ONLY) && independent_z`, where `independent_z` holds if
  ZTST is ALWAYS, or GEQUAL with constant Z across the draw, or depth writes are off, or the
  primitives do not overlap;
- when simple, it does **two passes: "First pass is to update color; second pass is to update Z"**
  (`AlphaTestMode::SIMPLE_FB_ONLY`);
- otherwise it uses a **feedback** path that samples RT/depth in the shader behind barriers, which
  it only prefers when the barriers are already paid for (`prefer_two_pass`).

Our dominant case has varying Z and overlapping primitives, so it is *not* `independent_z`.
**Plan: implement the two-pass colour-then-Z shape anyway and accept the ordering approximation**
— that is precisely the kind of divergence the bit-exactness decision bought us. Cost is ~1.5x draw
calls overall. `GL_ARB_fragment_shader_interlock` is available on this host if the feedback path is
ever wanted, but it is the expensive option and should not be the first build.

**Alpha scale — a correctness detail that will bite silently.** PS2 blending divides by 128, so an
alpha byte of `0x80` means **1.0**; GL's `GL_SRC_ALPHA` divides by 255 and would read `0x80` as
0.502, i.e. **half-strength blending everywhere**. Alpha handed to GL must be rescaled ×255/128
(double, saturating). `PS2X_GS_BIGPRIM` on this run reports palette alpha `min=128 max=128
over0x80=0`, so nothing sampled exceeded 1.0 and the saturation is harmless — but ⚠ that instrument
samples only large primitives' *palette* alpha, so vertex alpha and small-primitive alpha remain
unmeasured. PCSX2 carries the same compensation (`BLEND_HW3`: "Multiply Cs by (255/128)").

### 2.5 Still unmeasured — measure before designing around them

- **Vertex and small-primitive alpha above `0x80`** — §2.4 measured only big-prim palette alpha.
- **local→host readback** (`sceGsExecStoreImage`, the guest reading rendered pixels into EE RAM):
  the path is fully wired but **there is no counter for transfer `direction==1`**. A two-line
  counter at `gs_cpu_backend.cpp:10791` settles whether a GPU→guest downsample is needed at all.
- **COLCLAMP=0** (wrap instead of clamp) is issued by this game on one full-screen sprite. Neither
  backend implements it today, so GL matching current behaviour is not a regression.

## 3. The architecture

**Authority model (this is the whole design).** Today one 4 MB CPU buffer is authoritative and
everything reads it: present, uploads, transfers, guest readback, even font rendering
(`font_text.cpp`, `dbcman.cpp`). A GL renderer cannot simply stop writing it.

Mirror PCSX2's `GSTextureCache`:

- **A `Target`** is a GL colour (+depth) texture owned by a `(fbp, fbw, psm)` key, rendered at
  `scale×` native. While a target is GL-resident it is authoritative for those VRAM pages, and the
  CPU copy is stale-but-marked.
- **A `Source`** is a GL texture decoded from VRAM for sampling. A draw that samples pages owned by
  a live `Target` **binds that target's texture directly** (render-to-texture, §2.3) — it never
  reads back.
- **CPU touches those pages ⇒ resolve.** Any VRAM read that intersects a live target
  (`PerformLocalToHostTransfer`, `GS::ReadVram`, present, local→local blits) forces a GL→CPU
  readback, downsampled to native, before proceeding. Any VRAM *write* invalidates the target.
- PCSX2's names for these are `InvalidateLocalMem` (read) and `InvalidateVideoMem` (write), with a
  dirty-rectangle list per target (`GSTextureCache.h:233-280`).

**Presentation.** Render at `scale×` into an FBO, **downsample on the GPU to display size, read
back at display size.** Readback cost is then constant regardless of internal resolution, and the
entire existing present path (`CopyFrameToHostRgba` → `UpdateTexture` → `DrawTexturePro`) is reused
untouched. This deliberately avoids GL context sharing, which the tree does not support: the GPU
device owns an isolated EGL context on its own thread precisely because the present thread already
holds raylib's GLX context (`gs_gpu_device.h:9-25`). Sharing is a later optimisation, not a
prerequisite.

**The seam.** Divert inside `GSCpuBackend::RasterRunFanOut` (`gs_cpu_backend.cpp:5864`), guarded at
the top — the single point where all draw work lands, receiving a contiguous array of complete
`GSPrimitiveBatch` in submission order. `PS2X_GS_NORASTER` already short-circuits there, so the
pattern exists. ⚠ Two bypasses must also be guarded: `Submit`'s inline single-threaded path
(`:3208-3210`) and `workerLoop`'s per-batch fallback when the band pool is empty (`:1214-1216`).

**What must be preserved at that seam** (each one is a known past bug):

- **Run splitting on target change** (`:5892-5915`) — two formats aliasing the same VRAM.
- **Flip ordering.** `OnDisplayFlip` exists because this game draws UI text onto the *front* buffer
  after the flip (cont.165). Present the pre-flip buffer at the FIFO point of the flip, or the menu
  text disappears again.
- **`Sync(Presentation)` must not drain** (`:3229-3242`) — a drain starved the main thread and
  killed X input. A GL renderer that blocks the present thread on a fence reintroduces exactly that.
- **HLE register ordering.** FRAME/ZBUF/TEST arrive from the kernel HLE via the ring, not the GIF
  stream (`Support.h:1893-1911`). Because each batch carries a full context snapshot, a renderer
  consuming batches is automatically correct — *provided it never samples live `GS` state*.
- **`rasterCapRecord`** (`:6143`) — keep the capture pre-pass or the deterministic bench dies.

**Known port traps.** The scissor bounds are *inclusive* while `glScissor` takes width/height.
`S.zte` is declared but never read by the existing GLSL, so that shader still drops the ZTE=0
full-screen sprites the CPU path was fixed to draw — fix before reuse. And the existing host-decode
spike `PS2X_GS_LINTEX` is **default OFF, known-incorrect and slower**: it rebuilt the texel buffer
every batch. Its recorded lesson is the design constraint here — *decode-once-per-batch cannot beat
per-texel reads without a persistent cache keyed on content plus VRAM generation, and building that
cache is most of the real work.*

## 4. Phase plan

Every phase is env-gated, independently testable, and leaves the CPU path default. Selector:
**`PS2X_GS_RENDERER`** = `cpu` (default) | `gl`.

| # | Phase | Gate (how we know it worked) |
|---|---|---|
| 0 | ~~**Measure** the state census~~ **DONE (§2.4)**. Remaining: the `direction==1` readback counter and the §2.5 alpha question | Numbers exist; no behaviour change; hashes unchanged |
| 1 | **Scaffolding + switch.** `PS2X_GS_RENDERER`, GL entry-point table extended (~30-50 fns: textures, blend, depth, FBO, vertex attrs, blit), context + FBO at native res, present via downsample+readback | Unset ⇒ byte-identical to today (bench hashes `3467e2e7823fcb8e` / `647a498ab2b94862`); `=gl` ⇒ a black window, no crash |
| 2 | **Geometry, untextured, native res.** Vertex transform, GL depth test, the blend modes of §2.3/§2.4, `glColorMask(1,1,1,0)`, scissor, the **alpha ×255/128 rescale**, and the **two-pass colour-then-Z AFAIL path** (56% of draws — not deferrable) | Recognisable geometry; screenshot comparison against the CPU path |
| 3 | **Textures.** Reuse `gs2TexFillBlocks`' block walk + the decoded CLUT to build RGBA8; persistent cache keyed as `texCacheResolve` already keys it, invalidated by `g_gs2PageGen` **plus** the per-draw stomp test (generations alone do **not** cover draw writes — `:5758-5765`) | The scene is recognisably correct; `[gs2:texcache]`-style hit rate reported |
| 4 | **Target cache + render-to-texture.** `Target`/`Source` promotion; CPU-touch resolve | The ~62 RTT draws/frame render without readback; no stalls |
| 5 | **Resolution scaling.** `PS2X_GS_SCALE` = 1/2/4. Fix the native-resolution assumptions inventoried in the survey (two independent `kHostFrameWidth` constants, the 640×512 upload buffer, the literal `640`/`512` **inside the GLSL string**, the raylib texture size, `decodeDisplaySize`'s clamp) | 2x and 4x render and present correctly; readback cost flat vs scale |
| 6 | **Quality + fit-and-finish.** Texture filtering (there is currently **no** `SetTextureFilter` call — the window upscale is nearest-neighbour), 4:3 pixel-aspect correction (absent today), optional anisotropy | Visibly better than native on the same scene |
| 7 | **The long tail.** FMV poke path; anything §2.5 turns up. FBMSK "mix", 16-bit targets, DATE/PABE/CSM2 and dither are **measured absent** and need no code | Each only if a measurement says this game needs it |

**Validation without bit-exactness.** The old hash oracle does not apply. Replacements:

1. The CPU path stays default and stays hash-gated — the GL path must never regress it.
2. **Perceptual A/B at native resolution**: same guest frame via `PS2X_GS_RASTERCAP` at a prim
   count (present-save slots are *not* frame-aligned), CPU vs GL, plus a luma-delta map. This is
   the method that discharged the V4-5 check in cont.328b — quantify and localise, then look.
3. **Speed on the matched-scene harness**: `vtab.sh` wall-per-guest-frame over a fixed window,
   which is the only trustworthy speed metric here.
4. **Real hardware / a human look** for anything perceptual, per the standing rule.

## 5. What would make this fail

Recorded up front so a later cycle does not rediscover them:

- **Readback stalls.** Any design that resolves a target per frame — or worse, per RTT draw —
  serialises CPU and GPU and loses. The target cache exists to make resolves rare.
- **The alpha rescale** (§2.4). Getting it wrong is silent: every blended draw comes out at half
  strength, which reads as "a bit dark" rather than as a bug. Gate it with a deliberate A/B.
- **AFAIL two-pass ordering.** 56% of draws take it, and our case is not PCSX2's `independent_z`,
  so overlapping geometry can resolve differently. Watch for depth-sorting artefacts specifically.
- **Judging the arc on this laptop.** The iGPU shares the package power budget with a CPU that has
  been observed at 92 °C, and the discrete GPU is not loaded. A no-go here is not a no-go on a
  desktop.
- **Scope.** Phases 4 and 7 are where emulator projects sink. If phases 1-3 do not show a clear
  win at native resolution, stop and reconsider rather than pushing into the texture-cache tail.
