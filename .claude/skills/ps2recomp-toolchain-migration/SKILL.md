---
name: ps2recomp-toolchain-migration
description: Migrate a game's PS2Recomp toolchain clone to a newer upstream commit (git pull) while preserving our local patches + game overrides. Use when upstream ran-j/PS2Recomp has meaningful changes to adopt (bug fixes, refactors) and `tools/<game>/PS2Recomp` needs updating. Covers the bare-baseline method, fixing game overrides against removed/changed APIs, and re-introducing engine patches verify-then-apply. Distilled from the ee14958 "IOP refactor" migration.
---

# Migrate the PS2Recomp toolchain to a newer upstream

> **★ (cont.230): the patch stack is RETIRED.** The clone at `tools/<game>/PS2Recomp`
> is now our private fork (`caws/PS2Recomp`, branch `lotr`) with the former patches as commits and
> upstream as the `upstream` remote. A migration is therefore a git merge:
> `git -C tools/<game>/PS2Recomp fetch upstream && git merge upstream/main`, resolve conflicts per
> file, rebuild (FULL regen if `ps2xRecomp/` changed), verify the boot per the method below, push,
> and bump `<game_dir>/recomp/runtime.lock`. The bare-baseline / re-apply-patches steps below are
> the historical method; the *verification* discipline (what to check, in what order) still applies.

Upstream `ran-j/PS2Recomp` (the recompiler `ps2xRecomp/` + runtime `ps2xRuntime/` + now
`ps2xIOP/`) evolves. Our setup keeps a **per-game clone** at `tools/<game>/PS2Recomp` with our
local correctness fixes (formerly **`patches/*.patch`**, now commits on the fork) and all
game-specific behavior in the **game repo's `src/` overrides**. A migration = move the clone to a
newer upstream commit and get our fixes + overrides working on it again — **without regressing
the boot**.

This is an occasional, multi-step task with a specific method. Do NOT wing it — bad migrations
silently drop a patch or an override and the boot regresses in confusing ways.

## The core method: BARE BASELINE, then layer back verify-then-apply

The winning insight: **isolate each layer**. Get bare upstream building first, then game overrides,
then patches one at a time — so every break is attributable to exactly one thing.

### Phase 0 — before you touch anything

- **Back up the last-good runner binary:** `cp <game>/tmp/ps2EntryRunner <game>/tmp/ps2EntryRunner.<tag>.bak`.
  This is your **ground-truth oracle** for the rest of the migration (see the ground-truth rule below).
  Do this FIRST; a rebuild overwrites `tmp/ps2EntryRunner`.
- Note the current upstream HEAD and read the new commits: `git -C <clone> log --oneline HEAD..origin/main`,
  then `git show --stat <commit>` for each. Understand what changed (bug fix? refactor? removed API?).

### Phase 1 — get bare upstream building

1. **Undo our applied patches** so a pull is clean. Patches are working-tree edits, not commits:
   `git -C <clone> checkout -- .` (reverts all tracked mods; untracked build cruft is fine).
2. **`git -C <clone> pull --rebase`** (a fast-forward if we have no local commits — we don't).
   Optionally `git clean -fdx` or a full `rm -rf <clone>` + re-clone for a truly pristine tree
   (removes stale untracked copies of game modules that reference the OLD API).
3. **Set up bare:** `scripts/01_setup.sh <game_dir> --no-patches` (the `--no-patches` flag builds
   bare upstream — recompiler + analyzer, NO patches). Confirms upstream itself builds.
4. **Full build bare:** `scripts/03_build_game.sh <game_dir>` (no `--skip-regen`). It WILL fail —
   but the compile errors are the **exact game-override break surface**. That's the point.

### Phase 2 — fix the game overrides (game repo `src/`)

The break surface is usually tiny (ee14958: exactly **2 lines**). For each break:
- **Removed/changed runtime API** → check whether a public replacement exists. **READ THE HEADER
  for the access specifier** — a game override can only call PUBLIC `PS2Runtime` methods. If the
  replacement is `private` (transport/friend-only), you CANNOT call it → **inline the logic** in the
  override instead (mirror what the removed function did; the native module / old patch is the reference).
- **HLE that native upstream now provides:** only helps if the game actually reaches native the same
  way. LOTR drives DBCMAN over the **raw SIF transport** (guest `0x11bfa8/0x11c178`), which native
  ee14958 (SID-routed off `sceSifCallRpc`) never sees — so native DBCMAN was useless to us and the
  HLE had to stay in our override. **Verify the game's actual transport path before assuming native
  supersedes anything.**
- **Some HLE can't fully inline into an override.** A per-vblank job that ran in the runtime's
  **interrupt worker** fires far more often than any guest-function hook — re-homing it to a guest
  vsync hook changed its frequency and broke pad polling. Such pieces may need to STAY a tiny runtime
  patch. (Open item at time of writing; noted so the next migrator doesn't repeat the surprise.)
- Rebuild fast (`--skip-regen --changed-recomp`) and confirm the boot depth vs the **backup oracle**.

### Phase 3 — re-introduce engine patches, VERIFY-THEN-APPLY, one at a time

For EACH patch, in rough **boot order** (early-boot blockers first, deep render last):
1. **Verify the bug still reproduces** on the current build (its symptom is present) AND the fix is
   **not already upstream** (`grep` the clone for the fix's marker; 0 hits = still needed).
2. **Check it still applies:** `git -C <clone> apply --check "$(pwd)/patches/NN.patch"` — **use an
   ABSOLUTE patch path** (`-C` resolves relative paths against the clone dir — this bug bit us twice).
   Context drift on `git apply`'s strict check? **Re-anchor:** apply with fuzz / hand-edit, then
   regenerate the patch: `git -C <clone> diff <file> > patches/NN.patch`, verify `--check` is clean.
3. **Apply it, rebuild, confirm the symptom is gone.** Attribute the behavior change to that patch.
4. Skip a patch whose bug no longer reproduces / is fixed upstream.

**Recompiler vs runtime patches — different build cost:**
- **Recompiler patches** (`ps2xRecomp/…`, e.g. fpu/vu translation) change code generation → rebuild
  `ps2_recomp` (`cmake --build <clone>/out/build --target ps2_recomp`) THEN a **FULL regen**
  (`03_build_game.sh` with NO `--skip-regen`). Slow.
- **Runtime patches** (`ps2xRuntime/…`, GS/VU-runtime/DMA/SIF) → `--skip-regen` (reuse the regen);
  only the runtime lib recompiles.

**Interdependent patches apply together, not one-at-a-time.** The render patches are a set: applying
the camera/geometry fixes (fpu/vu) ALONE made things *worse* (correct geometry = real triangles = more
GS load = worse present starvation) until the clipping/present patches went in too. One-at-a-time is
for attribution; when patches only make sense as a group, batch them and verify the aggregate.

**Do NOT run `01_setup.sh` mid-migration** — it re-applies ALL `patches/*.patch` (including any you're
mid-verifying, and any retired one that now fails). Use targeted `cmake --target ps2_recomp` +
`03_build_game.sh` while iterating.

## Ground-truth rule (the single most useful habit)

**Keep the pre-migration binary and RUN IT for comparison before diagnosing any "regression."** We
wasted effort chasing a phantom "ee14958 made the frame rate collapse" — until we ran the backup
binary with the same env and saw the **identical** frame rate. The frame rate was always that low.
Assumptions about the old build's behavior (fps, boot depth, timings) are hypotheses; the backup
binary is the fact. Establish it EARLY, not after a wrong-headed chase.

Corollaries:
- **Env-flag semantics are ground truth too** — read them. `PS2X_PAD_CROSS_AT` counts `readState()`
  CALLS, not frames; we misread it as frame-based and mis-tuned it.
- **Cross-check agent findings against the code.** Read-only `Explore` agents mapping the new
  architecture were efficient, but two agents DISAGREED on whether the new RPC entry was callable; the
  header (`private:`) settled it. Verify load-bearing agent claims yourself (operating-manual §4).

## What worked / what to repeat

- ★ **Bare-baseline `--no-patches` build** → the compile errors ARE the exact break surface. Clean isolation.
- ★ **Backup binary as oracle** (see above).
- ★ **verify-then-apply** patches (reproduce symptom + confirm not-upstream) — confirmed every patch
  still needed and attributed each to a symptom (present-fairness→cur advances, CROSS-driver→input,
  psm-fix→SIGSEGV clears, gs-loadimage→menu text visible via screenshot).
- ★ **Screenshots as visual ground truth** for render patches (proved the frontend renders).
- ★ **Read-only Explore fan-out** to map the new upstream architecture (RPC routing, break surface)
  before editing.

## What bit us (don't repeat)

- Assuming the removed API had a callable public replacement (it was `private`) — READ THE HEADER first.
- Assuming the old build's fps without running the backup — establish the oracle EARLY.
- `git -C <clone> apply` with a RELATIVE patch path (resolves against the clone) — use absolute paths.
- Not realizing `01_setup` re-applies all patches mid-migration.
- Treating render patches as independent when they're a set.
- Retiring a patch (inlining it) without checking ALL of it inlines — the per-vblank/interrupt-worker
  half may not. Keep the retired `patches/NN.patch` on disk as reference until fully re-homed.

## Verification (per step and at the end)

- Bump `BUILD_TAG` every build; confirm the embedded tag on disk (`strings tmp/ps2EntryRunner | grep bld-`)
  AND `tag=` in `run.txt` — the stale-binary trap is worse mid-migration.
- End state: boot reaches at least the pre-migration depth (compare `run.txt` sentinels + a screenshot
  against the backup oracle), NOT merely "it compiles" or "no crash".
- Update the game repo `docs/progress.md` + `NEXT.md` with the migration record (what upstream changed,
  which patches re-applied, what inlined, any residual runtime patch, env tuning) — a future session
  must be able to reconstruct the state.

## Commit discipline

- **Game repo** (standing auth): override edits + `BUILD_TAG` + the docs record.
- **Engine repo** (needs explicit ask): `01_setup.sh` changes, re-anchored `patches/NN.patch`.
- The clone (`tools/`) is NOT tracked — the migration state lives in `patches/` + `01_setup` (they
  re-apply) + the game overrides. Make sure that combination reproduces the working state.
