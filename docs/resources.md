# External resources

Authoritative references for PS2 hardware behavior and emulation. **Use these to understand the
PS2 architecture AND as the basis for how PCSX2 implements a subsystem — then mirror that proven
implementation in our overrides** (rather than guessing or re-deriving from scratch). This applies
to *any* subsystem (IPU, GS, VIF/GIF, DMAC, EE/VU, SPU2, SIF/IOP, CDVD, timers, …): when an
override has to reimplement or work around hardware the runtime stubs or gets wrong, treat ps2tek
as the *spec* and the PCSX2 source as the *reference implementation* — read both before writing the
override, and cite the file/function you mirrored.

> ★ **BINDING: when a subsystem override hits a wall, re-check PCSX2's source before concluding the
> options are exhausted.** Don't stop at a generic spec, an external library's tables (FFmpeg, etc.),
> or a *related* PCSX2 function — find the function PCSX2 uses for the **exact command/mode** in play
> (e.g. the IPU has separate `mpeg2sliceIDEC()` vs generic `mpeg2_slice()`; grep by the command name).
> Concrete case: the IPU decoder was stuck at 436/1024 macroblocks across multiple
> sessions — the AC-VLC table was verified byte-perfect and the decode logic independently
> re-verified, so it looked exhausted — because PCSX2's `mpeg2sliceIDEC()` (not yet read) contained a
> per-macroblock structure (`macroblock_modes` + `macroblock_address_increment`) nothing else surfaced.
> Reading it fixed the decoder in one pass. Treat "I've checked the spec/tables and I'm still stuck" as
> a sign to go read PCSX2's actual code for that exact path, not as a stopping point.

- **ps2tek** — comprehensive PS2 hardware reference (register maps, DMA channels, and per-subsystem
  behavior/quirks for the EE, IOP, GS, GIF/VIF, IPU, SPU2, CDVD, etc.):
  https://psi-rockin.github.io/ps2tek
- **PCSX2 source** — the most complete open-source PS2 emulator; the reference for *how* any
  subsystem actually behaves cycle-for-cycle: https://github.com/PCSX2/pcsx2
  - Browse the tree to find the subsystem you need (e.g. `pcsx2/IPU/`, `pcsx2/GS/`, `pcsx2/GIF*`,
    `pcsx2/Vif*`, `pcsx2/Dmac*`, `pcsx2/SPU2/`, `pcsx2/Sif*`, `pcsx2/CDVD/`).
  - Fetch raw files for grepping locally: `curl -sfL
    https://raw.githubusercontent.com/PCSX2/pcsx2/master/pcsx2/<path> -o <file>`.
- **RetroReversing — PS2** — curated index of PS2 reverse-engineering resources (SDK/libpad/libgs
  internals, homebrew toolchains, decompilation tooling, hardware docs): https://www.retroreversing.com/ps2

> Ground-truth ranking still applies (see [operating-manual.md](operating-manual.md) §4): these
> references are a strong *hypothesis* for what the hardware does — verify the load-bearing claim
> against our generated C++ + disasm + gdb before building on it. PCSX2 tells you the *intended*
> behavior; the game's own data/flow is the final arbiter.
