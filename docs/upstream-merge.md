# Pulling upstream into the fork — evaluation + landing checklist

`tools/<game>/PS2Recomp` is a clone of **our private fork** (`caws/PS2Recomp`, branch `lotr`);
`ran-j/PS2Recomp` is the `upstream` remote. Upstream pushes land as **squashed PR commits**, so a
pull is never a stream of small changes — it is one architectural drop that has to be evaluated as a
whole. This document is the reusable procedure: **how to decide whether to pull, how to measure what
it would cost, what to audit in the game repo, and the order to land it in.** §6 is the worked
instance of that procedure — keep it as the example, and add a new section when the
next pull is evaluated.

Nothing here is a standing decision to merge. **The default is to stay pinned** at the commit in
`<game_dir>/recomp/runtime.lock` and to pull only when the evaluation says the pull buys something on
the current critical path.

---

## 0. The fork's branch model (user decision) — ★ read first

**Target model** — adopted when the first game's (LotR's) recompilation is DONE, not before:

| branch | role |
|---|---|
| `main` | the SHARED line: upstream + every validated game's runtime work. **Kept current with `upstream/main`** (§1-4 per sync). Every NEW game starts here (`runtime.lock branch=main`). |
| `<game>` | a game's own branch, created only **when that game needs runtime changes** (named after it, like today's `lotr`). The game's lock points at it. |
| `upstream/main` | the clean upstream reference (remote-tracking; `git fetch upstream`). A local clean copy of upstream is NOT needed. |

**Lifecycle:** game starts on `main` → needs a runtime change → branch `<game>` → the game's
recompilation is done → **evaluate merging `<game>` into `main`** (separate the general-purpose work
from game-specific traces first) → `main` absorbs it and stays synced with upstream.

**Now: POSTPONED.** LotR keeps working on `lotr` (270 commits on upstream `14b1e5c`).
`main` in the fork is still the untouched upstream copy, and the pending upstream pull
(`75d729c`, §6) waits. When LotR is done: merge `upstream/main` into `lotr` on a branch (§4 landing
sequence), then fast-forward `main` to it and switch LotR's lock to `main`.

**Not part of this model, and the user's call alone:** anything public -- pushing `main` (it is the
fork's default branch, so it changes what visitors see), publishing the fork, or contributing
general-purpose changes back to `ran-j/PS2Recomp` as pull requests. The merge brings upstream's work
to us; only those public routes bring ours to other people.

## 1. Decide whether to pull at all

Upstream and this project diverge by *purpose*: upstream is a general recompiler + runtime; we are one
game plus a renderer. The parts where upstream keeps producing value for us are **`ps2xRecomp` and
`ps2xAnalyzer`** (codegen correctness, entry discovery, the Ghidra exporter) — small, shared, cheap to
track. The **runtime** is where we have gone our own way irreversibly (renderer, microVU, IPU, raster,
audio, pad) and upstream will never converge back.

Ask, in order:

1. **Does it fix something on the current frontier?** If not, the pull is optional by definition.
2. **Does it change `ps2xRecomp`/`ps2xAnalyzer`?** If yes, landing it forces a **full regen** and a
   full revalidation pass — that, not conflict resolution, is the real cost.
3. **Is there a cheaper subset?** Upstream squashes to `main`, but the unsquashed commits usually
   survive on the feature branch (`git log --oneline upstream/main..upstream/<branch>`), so a
   self-contained fix can be cherry-picked without the architecture it shipped with.
4. **Could it wait for the next game?** `tools/<game>/PS2Recomp` is **per game**. A new game can clone
   `upstream/main` fresh while the current game stays pinned. For anything whose value is mostly
   "a better starting position" (new HLE frameworks, exporters, entry discovery), this is usually the
   right answer.

## 2. Measure the divergence before touching anything

All read-only. Run from the clone.

```bash
git fetch upstream
BASE=$(git merge-base HEAD upstream/main)
git log --oneline $BASE..upstream/main          # what is in the pull
git log --oneline --graph -1 upstream/main      # squashed? (one parent = fast-forward chain)
git log --oneline upstream/main..upstream/<feature-branch>   # the unsquashed originals
git diff --stat $BASE upstream/main | tail -5   # size of the pull

# our side, and the overlap that becomes conflicts
git diff --name-only $BASE HEAD        | sort > /tmp/ours.txt
git diff --name-only $BASE upstream/main | sort > /tmp/theirs.txt
comm -12 /tmp/ours.txt /tmp/theirs.txt          # files BOTH sides changed

# conflict count WITHOUT creating a merge (git < 2.38 has no --write-tree)
git merge-tree $BASE HEAD upstream/main > /tmp/mt.txt
grep -c '^+<<<<<<<' /tmp/mt.txt

# drift health: additive (cheap) vs invasive (expensive)
git diff --diff-filter=A --numstat $BASE HEAD | awk '{s+=$1} END{print "new files +"s}'
git diff --diff-filter=M --numstat $BASE HEAD | sort -k1 -rn | head -10
```

**Read the numbers this way:** lines in *new* files cost nothing at merge time. Lines added to files
upstream also owns are the liability, and they are always concentrated — find the two or three files
that carry most of it, because those are where every future merge will hurt.

## 3. The compatibility audit — four buckets

Do this **before** merging. It is all static and cheap, and it turns "will the game still work?" into
a list.

### A. Verify what cannot break (don't assume it)

```bash
# the recompiled function signature — if this changed, every override breaks
for r in $BASE HEAD upstream/main; do git show $r:ps2xRecomp/src/lib/function_emitter.cpp \
  | grep -n 'ss << "void '; done
# the override API
git diff $BASE upstream/main -- ps2xRuntime/include/ps2_runtime.h \
  | grep -E 'registerFunction|REGISTER_GAME_OVERRIDE'      # empty = unchanged
```

Also confirm the accessors the game actually calls are untouched:

```bash
grep -rhoE '(runtime|rt)->[a-zA-Z_]+\(' <game_dir>/src <game_dir>/mods | sort | uniq -c | sort -rn
```

### B. Game-repo breakage (compile/link)

- **Symbols.** Count what the game couples to: `grep -rhoE '\b(FUN|sub|ps2)_[0-9A-Za-z_]*_0x[0-9a-f]+\b'
  <game_dir>/src <game_dir>/mods | sort -u | wc -l`. Generated names are `<csv name>_0x<addr>`, so they
  are stable as long as `functions.csv` is — but a codegen change can alter *which* functions are
  emitted.
- **Deleted runtime stubs vs our `config.toml` — ★ this fails SILENTLY.** Diff the stub/syscall
  surface and cross-check every name in the config's `stubs` array:

  ```bash
  names() { git show "$1:ps2xRuntime/include/ps2_call_list.h" \
            | grep -oE 'X\([A-Za-z_0-9]+\)' | tr -d 'X()' | sort -u; }
  names $BASE > /tmp/calls.base; names upstream/main > /tmp/calls.up
  comm -23 /tmp/calls.base /tmp/calls.up          # names upstream REMOVED

  grep -oE '"[A-Za-z_0-9]+@0x[0-9A-Fa-f]+"' <game_dir>/recomp/config.toml \
    | tr -d '"' | sed 's/@.*//' | sort -u > /tmp/cfg.stubs
  comm -12 /tmp/cfg.stubs <(comm -23 /tmp/calls.base /tmp/calls.up)   # ← the ones that affect us
  ```

  A name in `stubs` is **not** required to exist in the runtime. `PS2Recompiler::isStubFunction()`
  selects the function, then `hasResolvedStubHandler()` decides the body:
  `ps2_runtime_calls::resolveSyscallName/resolveStubName` hit → `ps2_syscalls::<name>` /
  `ps2_stubs::<name>`; neither → `ps2_stubs::TODO_NAMED("<name>")` (a no-op that still links), unless
  the function is *correctness-critical*, in which case the recompiler warns
  ("Unresolved initializer stub ignored") and recompiles the guest function instead. **So a stub
  upstream deleted turns into a silent no-op, not a build error** — nothing will fail loudly, which is
  exactly why this diff has to be run by hand on every pull.
- **Direct stub uses in the game:** `grep -rhoE 'ps2_stubs::[A-Za-z_]+' <game_dir>/src <game_dir>/mods`.
- **Include style / paths** if the emitter changed how generated TUs include the generated headers.

### C. Fork breakage (blocks the build before the game repo gets a chance)

- The conflicted files from §2, plus **anything of ours that uses a global or helper upstream deleted**
  (grep our runtime for each removed symbol).
- **New pure virtuals** on an interface we implement — a `= 0` added to `GSBackend` makes our backends
  abstract until implemented.
- **Signature changes under us.** Where upstream narrows a signature we extended, the default is to
  **keep ours and reject theirs**, but say why in the merge commit: keep a parameter that carries a
  feature we built, or a per-draw hoist we measured; adopt theirs only if the narrower form can still
  express both. A defaulted parameter is free to keep — it stays call-compatible with upstream's shape.

### D. Behavioural (only findable by running)

List the things whose *contract* changed even though they still compile — transport layers, memory
mirroring, scheduler semantics, module-load failure modes. Hooks that short-circuit before the
affected path are insulated; everything we let **fall through** is exposed. Write this list down
before the merge so the first run has something to check against.

## 4. Landing sequence

1. **Scratch branch in the clone** (`git switch -c merge-<upstream-sha>`). Cheap to abandon; never on
   `lotr` directly.
2. Resolve the conflicts, fix bucket C, and get the runtime to **build with no game involved**.
3. **Full regen** (mandatory whenever `ps2xRecomp` changed — never `--skip-regen`).
4. **Diff the generated tree against the previous one** — function count, names, newly synthesized
   entries, changed bounds. *This diff is the real risk report for the game repo*, and it is cheap to
   produce and cheap to read. Do it before fixing anything downstream of it.
5. Fix bucket B, rebuild, **bump `BUILD_TAG`**, confirm the tag in `run.txt`.
6. **Run the oracles** — the bit-exact raster hash, the texture-decode verifier, the VU0 differential
   oracle, a recorded replay on **both** memory-card states. A merge is validated by the oracles, not
   by "it booted".
7. Only then: fast-forward `lotr`, bump `<game_dir>/recomp/runtime.lock` **in the same change**, and
   add the row to the fork's `docs/llmps2recomp-patches.md`.
8. Record the outcome in the game repo's `docs/progress.md` (and `NEXT.md` if the pin moved).

Steps 1-4 are reversible and answer most of the question. If the generated-tree diff looks bad, delete
the branch and nothing was lost.

## 5. Drift hygiene (what keeps the next pull cheap)

- **Additive beats invasive.** New files cost nothing at merge time; lines inside upstream's files cost
  forever. When a change can live in our own translation unit behind an existing interface, put it
  there.
- **Extract our big subsystems out of upstream-owned files.** If most of our invasive lines sit in one
  or two files that upstream also edits, moving that work behind the interface is the single highest-
  leverage drift reduction available — and the bit-exactness oracles make it a safe, verifiable
  refactor.
- **Keep `ps2xRecomp`/`ps2xAnalyzer` near-vanilla** and sync them often; they are small, and they are
  where upstream's portable value arrives.
- **Prefer a seam to an edit.** Needing game-specific behaviour inside a runtime subsystem is an
  argument for a registration seam (the `registerFunction` pattern) whose body lives in the game repo,
  not for an inline modification.
- **Sync cadence matters more than sync size.** One month of divergence resolved in 28 hunks; a year
  of the same rate would not.

---

## 6. Worked instance — upstream `75d729c` "Feature/iop emulator (#244)" (evaluated)

**Verdict: do not pull now.** Nothing in it is on the current critical path (Stage 1, playable base),
and it forces a full regen + revalidation of the renderer. Revisit for game #2, where it is a better
*starting* position than our EE-side HLE was.

**The pull.** One squashed commit on `main` (parents: `14b1e5c`), merged, 139 files,
+15,592/−9,145; the only upstream commit since our base `14b1e5c`. Unsquashed originals
live on `upstream/feature/iop-emulator` (22 commits). Contents: an LLE IOP (R3000A interpreter +
virtual kernel, real IRX modules, imports HLE'd at the stub boundary, `iop_cdvd` over a new `PS2Vfs`);
IOP RAM split from EE RAM and SIF rewritten; ~1,150 lines of callable-entry discovery in `elf_parser`
plus an `entry_points` config key and a rewritten Ghidra exporter; `SET_GPR_ZE32` for LBU/LHU/LWU;
`PS2Vfs`/`PS2RomDevice`/`ps2_path`; a `LoadClut` pure virtual on `GSBackend` and a one-page texture
read cache; deletion of the plugin loader, the game-profile mechanism and the HLE modules
(`clfile`, `cri_dtx`, `sdrdrv`, `tsnddrv`, `sound_update_stub`).

**Divergence at the time.** 231 commits / 202 files on our side: **155 new files (+38,634)** vs
**47 modified upstream files (+26,435/−1,211)**. 23 files overlap; `git merge-tree` reports **28
conflict hunks in 8 files** (gs_cpu_backend.cpp 9, gs_cpu_backend.h 5, EeScheduler.cpp 4,
ps2_memory.cpp 4, ps2_vif1_interpreter.cpp 3, + three one-liners). 77% of our invasive lines are in
three files (`gs_cpu_backend.cpp` 14.8k, `ps2_vu1_core.cpp` 4.6k, `ps2_vif1_interpreter.cpp` 1.1k);
86% in six. **`ps2xIOP`: zero files changed by us** — our IOP HLE is all game-repo code.

**Bucket A — verified safe.** The recompiled signature is identical on both sides
(`void f(uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime)`, `function_emitter.cpp:86`);
`registerFunction` / `PS2_REGISTER_GAME_OVERRIDE` diff is empty; `ps2_runtime.h` changes are purely
additive. So the game's 422 registrations and 347 distinct generated-symbol references keep their
shape.

**Bucket B — game repo.** ⚠ **Behavioural, not a build break** (corrected after reading the
emitter): `GetRomName` is the **only** name removed from the 658-entry call list, and our
`recomp/config.toml:181` stubs `"GetRomName@0x00263618"` — our generated `FUN_00263618_0x263618.cpp`
currently emits `ps2_syscalls::GetRomName(...)`. After the merge that name resolves to neither a
syscall nor a stub, so the emitter falls back to `ps2_stubs::TODO_NAMED("GetRomName", ...)`, which
exists — **it still links**. What is lost is the HLE's `"ROMVER 0100"` reply, which `IsT10K`
(`0x2636a4`) reads. Cheapest fix is **not in the game repo**: keep our `System.cpp` body and its
`ps2_call_list.h` row during conflict resolution (both are files the merge touches anyway), and the
game repo needs no change at all. Alternative: drop the config line and let the guest function
recompile. ⚠ **To check:** the emitter switched generated TUs to
`#include <ps2_recompiled_functions.h>` (angle brackets); and the new discovery pass synthesizes extra
entry points, which interacts with our curated CSV bounds (the swallowed / over-bound classes) — that
is what the §4 step-4 generated-tree diff is for. `src/` also calls `ps2_stubs::sceMpegAddBs` /
`sceMpegCreate`, both in `Kernel/Stubs/MPEG.cpp`, which upstream did not touch.

**Bucket C — fork.** `ps2_debug_panel.cpp:1770-1772` uses `g_fileDescriptors`/`g_fd_mutex`, deleted
from `Helpers/State.h` when FileIO moved onto `PS2Vfs`. `GSBackend::LoadClut(...) = 0` makes our GL
backend abstract. `PixelStorageTraits::Read()` gains a `TexturePageCache*` and a `const u8*`.
Signatures: **`SampleTexture`'s `mip` is ours-only** (base and upstream both lack it) and is a
defaulted parameter carrying the TEX1.MXL mip chain — keep it, it costs nothing. **`LookupCLUT`'s
`cbp` is the one upstream removed** (it exists at base); all 8 of our call sites pass `tex.cbp` from
the per-draw resolved descriptor, so their narrower form would re-derive the CLUT base per texel
inside an instruction-bound rasterizer — keep ours.

**Bucket D — behavioural.** IOP RAM no longer mirrors into equal-numbered EE addresses; SIF rewritten
(−412 lines); unknown module loads now **fail** instead of no-op'ing (our `src/loader`, 26 hooks);
a new `SifCommand` EE-invocation kind. Our raw-transport hooks (`0x11BFA8`, `0x11C178`) answer and
`jr ra` *before* any DMA, so they are structurally insulated; the exposure is every sid we let fall
through to the real transport.

**Worth taking independently of the merge:** `SET_GPR_ZE32` (`08dc217`, one file, 11 lines) — our
`SET_GPR_U32` sign-extends (`(int64_t)(int32_t)val`), so **LWU with bit 31 set writes `0xFFFFFFFF`
into the upper half**, which is wrong for MIPS64. LBU/LHU are unaffected (their values can't reach the
sign bit). ~15 generated files contain `lwu`. Cheap and self-contained, but it is codegen — it costs a
full regen and a hash re-verify, so fold it into a cycle that is already regenerating.

**Also noted:** upstream independently disabled the MMIO hint address and rewrote the analyzer's
constant-producing backward scan — the same two conclusions we reached in cont.247, which is why two
of the conflicts resolve trivially as "keep ours" (ours carries the diagnosis, theirs carries a TODO).

## 7. Re-evaluation 2026-10-06 — upstream `2c5fbb9` (75d729c + c5a9d02 + a5d3049 + 2c5fbb9) onto the squashed `lotr`

`lotr` is now upstream `14b1e5c` + one squash commit `1e4126e` (+ `70afe64`, `d71e4da`); the pull is the same `75d729c`
plus three small commits. Measured read-only (merge-tree): **30 hunks in 9 files** (§6's 8 + `FileIO.cpp`, our row 250,
which upstream moved onto `PS2Vfs::hostMode` — take theirs). The §6 resolutions still hold (keep ours on the rasterizer,
VIF1 PATH2 carry, analyzer; no-op `LoadClut` on `GSCpuBackend`). §6's `g_fileDescriptors` break was a false alarm: that
debug-panel code is base code upstream rewrote itself.

**Silent merge outcomes (fix by hand; no conflict marker shows them):**
- `ps2_gs_memory.h`: upstream's `Read(..., const u8*, ..., TexturePageCache*)` lands on our `ReadAt(u8*)` body — compile error.
- `ee_scheduler.h`: we inlined `accountCycles`, so upstream's `advanceIopEeCycles(elapsed)` is lost — the IOP emulator never
  ticks. Restoring it puts the IOP interpreter on the EE thread (1 IOP cycle per 8 EE cycles): a cost to measure.
- `GetRomName` (call-list removal): keep our `System.cpp` body + call-list row (§6).
- **`entry_points` (formerly `untracked_stubs`) is LOAD-BEARING -- keep it, under the new key.** ⚠ The first reading
  ("a trap: rename it away") was WRONG, and the run proved it: the new recompiler stops bodies at the CSV end and registers
  only evidenced resume points, so a function swallowed by a gapfix row is dispatchable only through this list. With it
  renamed away, rotk died at its first level load on `missing branch target 0x112BE0` (a pointer call into `gapfix_1129c0`).
- **Generated names change:** `<csv name>_0x<addr>` (`ps2_` prefix for a CSV name starting with `_`), e.g.
  `sub_001C9AA0_0x1c9aa0` -> `FUN_001c9aa0_0x1c9aa0`. Every override that calls a generated function by name breaks at
  compile time; the rename is mechanical from the game's `functions.csv`.
- **BIOS-resident RPC sids:** upstream binds only routed or IRX-registered sids (the old runtime faked a server for every
  sid). LOADFILE / FILEIO / IOP heap are registered by the BIOS on hardware, so a game binding them before any module load
  loops forever (rotk USA). Fork row 264 binds those three.

**Running real IRX on upstream's IOP (rotk experiment, `LOTR_IOP_LLE=1`):** all of a game's disc IRX can load and start,
but (1) upstream's IOP RAM was two bump arenas (images < 0x120000, heap above) -- a large module exhausts the heap while
the image arena's tail sits unused; fork row 267 makes it one first-fit pool, as the IOP's sysmem keeps it; (2) the IOP
side of `sifcmd` is stubbed (ordinals 4-11 return 0: no SIF registers, no command handlers) and the EE `sceSifSendCmd`
delivers nothing, so any driver fed by SIF COMMANDS (not RPC) -- rotk's AUDIOPF -- starts and then waits forever on its
SET_SREG handshake; (3) no SPU2/SIO2 behind the drivers. A static census of each IRX's import stubs (magic 0x41E00000,
then `jr ra; addiu zero,zero,<ordinal>` pairs) against the emulator's handled ordinals sizes this before any run.

**Landed 2026-10-06 on `chore/merge_upstream`** (fork rows 263-267, PR into `lotr`; rotk PR into `main`):
deterministic replays on BOTH discs bit-identical to the pre-merge binaries (EUR fresh-card fight 10,186 flips, EUR
saved-card Min02 2,998-3,006 flips, USA saved-card Min02 3,334 flips). Process lessons: the auto-merge silently DROPPED a
line from one of our lambdas and spliced upstream's tag walk into our VIF1 function -- read the merged result of every
both-sides file, not just the conflict hunks; and a dev-mode game build must rebuild `ps2_recomp` when the clone moves,
or the regen uses the old recompiler.

**Small commits:** `a5d3049` (standalone `entry_*` resume points) applies cleanly, no output change today (no `entry_*`
rows) but interacts with the trap above. `2c5fbb9` (syscalls 0x79/0x7A) merges cleanly, inert for rotk (`sceSifInitCmd` is
stubbed). `c5a9d02` is README only. `SET_GPR_ZE32` (LWU zero-extension) comes in with `75d729c` — a real correctness fix.

**Can the game fake less by running its real IRXs on upstream's IOP?** Not with upstream as it is. The IOP emulator is an
R3000A interpreter with HLE'd kernel libraries (thbase/thsema/thevent/sysmem/intrman/timrman/vblank/cdvdman/sysclib) and
**no hardware model**: no SPU2 (no voices, no output — a DMA start only raises the IRQ), no SIO2 (pads, cards), no
DEV9/SMAP, `dmacman` all-zero. `sifcmd` on the IOP side cannot receive EE→IOP commands (`AddCmdHandler` is a no-op), RPC
servers run synchronously on the EE thread, vblank is fixed NTSC 59.94. Every module rotk uses (SIO2MAN, SIO2D, DS2U_D,
DBCMAN, MCMAN, MCSERV, LIBSD, AUDIOPF; + the USA network stack) is on the disc and needs exactly the missing pieces.
rotk is insulated today because the game fakes each IRX load at its own EE helper (`0x154AB0`) and answers DBCMAN/cdvd binds
before the transport. What running the real modules would take, by payoff: (1) SPU2 (port PCSX2 `SPU2/`) + EE→IOP SIF
commands + PAL vblank → real LIBSD + AUDIOPF, retiring the ~4.5k-line AUDIOPF port and its reply/priming/shortcut bugs; (2)
SIO2 + pad + memcard (+ multitap for 4 players) → real SIO2MAN/DS2U_D/DBCMAN/MCMAN; (3) DEV9/SMAP: not worth it (USA netplay
hooks the EE gateway).
