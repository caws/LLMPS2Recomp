# Working rules

The cross-cutting principles for working on a PS2Recomp decomp — distilled from real bring-up.
These are *how to work*, complementing the *what to do* in [debugging.md](debugging.md),
[functions-csv.md](functions-csv.md), and [overrides.md](overrides.md).

## Verify, don't trust

**Ground truth = generated C++ AND disassembly, co-equal — cross-check BOTH; then validate
load-bearing claims with gdb.** The generated `tmp/generated/*.cpp` is what actually runs (commented
guest addresses, explicit `goto`/`switch(ctx->pc)` flow, resumable entries, visible dispatch), so it's
the natural entry point for any control-flow question — but corroborate it against `funcs.py
disasm/bounds` (raw bytes/bounds/instructions), since each surfaces things the other hides. Treat
everything else — docs, this repo's notes, a game's `docs/progress.md`, prior findings, even the CSV's
function *names* — as a **hypothesis to verify**, not fact. Re-check against both the generated code
and the disasm, then gdb, before asserting or acting.

- Notes drift and are sometimes just wrong; the binary is authoritative.
- **Runtime probes can lie too.** A classic trap: reading `v0` right after a tail-`jr` (e.g. the
  stage dispatcher `0x1d7f10` does `jr v0`) yields a bogus "return value" that contradicts the
  disasm — the disasm is right. When a probe disagrees with the disassembly, **suspect the probe**
  (tail-jr / yield / stale-read artifacts), re-verify, and don't override the disasm on a guess.
- The game ELF is typically **stripped** (empty `.symtab`), so the disassembly *is* the source of
  truth for "is X a real function / what does it return".

## The dispatch model

- **Dispatch is address-based.** `runtime.registerFunction(addr, fn)` hooks by guest address;
  ps2_recomp **ignores the CSV `name` column**, so renaming a CSV row changes nothing — only
  bounds and the address matter.
- **Generated symbols change per regen.** ps2_recomp picks `FUN_` vs `sub_` and casing run-to-run;
  to *call the original* from a wrapper, match the current symbol in
  `tmp/generated/ps2_recompiled_functions.h` after each regen.
- **`dispatchLoop` runs top-level PCs**: `pc=ctx->pc; fn=lookupFunction(pc); fn()`. Direct
  same-unit calls are nested C++ (not seen by the loop); cross-unit/indirect calls and returns go
  through the loop. That's why backtraces are shallow — and why dispatch-logging
  ([debugging.md §4](debugging.md)) is the way to see the real loop.
- **Resumable mid-function entries.** Generated code is `switch(ctx->pc){...goto...}` with entry
  labels at call-return addresses, so an override can set `ctx->pc` to a label *inside* a function
  to resume there (only at real resume points — verify against the `.cpp`).

## Discipline

- **`BUILD_TAG`.** Keep a bumpable tag string in `register_overrides.cpp`, print it at startup, and
  **confirm it in `run.txt` after every build**. This catches the most demoralizing failure —
  debugging a stale binary (and it has bitten us: `--changed-recomp` once silently kept an old
  override; `--skip-regen` without it forced the rebuild).
- **One frontier per cycle.** Fix the narrowest thing on the current execution frontier, rebuild,
  advance. Never mass-rewrite `functions.csv` or pile on speculative overrides.
- **Report long builds as a %.** Use [`scripts/build_progress.sh <game_dir>`](../scripts/build_progress.sh)
  — it shows done/phase/≈% + the cc1plus monster/OOM warning that flags an over-bound unit.
- **Background builds, `timeout` runs.** Builds take many minutes (launch in background, poll the
  log); a spinning runner emits hundreds of MB/s, so always run under `timeout` to a file.

## The bug/fix taxonomy (quick index)

- `functions.csv` wrong/missing → [functions-csv.md](functions-csv.md): **truncated** (fix size),
  **missing/CSV-gap** jalr-targets (collect→validate→add batchfix), **over-bound** auto-named
  (boundfix rename).
- A guest function spins / blocks / needs HLE → [overrides.md](overrides.md): wait-free
  replacement, clean skip, SIF/IOP handshake fakes, answering raw-transport RPCs via
  `runtime->iop().handleRPC`, and **identifying an unknown IOP service** from
  `gamefiles/MODULES/*.IRX`.
- "Where is it / what runs" → [debugging.md](debugging.md): log triage, gdb-under-parent,
  one-shot probes, dispatch-logging loop maps.
- **Validate analysis with gdb — routinely, not as a last resort.** A reading of the disasm/generated
  C++ is a hypothesis; confirm it live (breakpoint → read real args/registers/guest memory, or watch the
  address it should write). For "who writes X / what sets screen-ID / who enqueues" — where address-xref
  is blind to helper-based (pointer-arg) writes — use a **gdb hardware watchpoint** (host = `rdram +
  (guest & 0x01FFFFFF)`); a watchpoint that never fires is itself proof the write never happens. See
  operating-manual §4.
