# External resources

Authoritative references for PS2 hardware behavior and emulation. **Use these to understand the
PS2 architecture AND as the basis for how PCSX2 implements a subsystem — then mirror that proven
implementation in our overrides** (rather than guessing or re-deriving from scratch). This applies
to *any* subsystem (IPU, GS, VIF/GIF, DMAC, EE/VU, SPU2, SIF/IOP, CDVD, timers, …): when an
override has to reimplement or work around hardware the runtime stubs or gets wrong, treat ps2tek
as the *spec* and the PCSX2 source as the *reference implementation* — read both before writing the
override, and cite the file/function you mirrored.

- **ps2tek** — comprehensive PS2 hardware reference (register maps, DMA channels, and per-subsystem
  behavior/quirks for the EE, IOP, GS, GIF/VIF, IPU, SPU2, CDVD, etc.):
  https://psi-rockin.github.io/ps2tek
- **PCSX2 source** — the most complete open-source PS2 emulator; the reference for *how* any
  subsystem actually behaves cycle-for-cycle: https://github.com/PCSX2/pcsx2
  - Browse the tree to find the subsystem you need (e.g. `pcsx2/IPU/`, `pcsx2/GS/`, `pcsx2/GIF*`,
    `pcsx2/Vif*`, `pcsx2/Dmac*`, `pcsx2/SPU2/`, `pcsx2/Sif*`, `pcsx2/CDVD/`).
  - Fetch raw files for grepping locally: `curl -sfL
    https://raw.githubusercontent.com/PCSX2/pcsx2/master/pcsx2/<path> -o <file>`.

> Ground-truth ranking still applies (see [operating-manual.md](operating-manual.md) §4): these
> references are a strong *hypothesis* for what the hardware does — verify the load-bearing claim
> against our generated C++ + disasm + gdb before building on it. PCSX2 tells you the *intended*
> behavior; the game's own data/flow is the final arbiter.
