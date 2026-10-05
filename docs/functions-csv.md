# Fixing `functions.csv`

`recomp/functions.csv` is the recompiler's map of the ELF: one row per function,
`name,start,end,size` (hex addresses). ps2_recomp emits exactly the functions it lists, at
exactly the bounds it gives. Almost every "won't boot" frontier traces back to a wrong or
missing row. There are **three** distinct bug classes. All fixes require a **FULL regen**
(no `--skip-regen`) because the recompiler must re-emit code.

> **The ELF is your ground truth, not the CSV.** Game ELFs are usually stripped (empty
> `.symtab`) — the CSV was produced by an external analyzer (Ghidra / a plugin), which is
> fallible. When the CSV and the disassembly disagree, **the disassembly wins**. Use
> `funcs.py disasm/bounds` to verify every row you touch.

## Boundary convention

Match the convention correct rows use: a function's `end` = the **next** function's start
(this absorbs trailing-nop padding). For a `jr ra`-first or `j`-first stub, `end = start + 8`
(the instruction + its delay slot). `funcs.py bounds 0xADDR` prints both the control-flow end
(last `jr ra` + 8) and the next-CSV-start cap. Dispatch is **address-based** — the recompiler
ignores the `name` column, so renaming a row changes nothing; only the bounds matter.

---

## Class 1 — Truncated (row present, size too small)

The classic Ghidra export sizes most functions to only their **first instruction** (size
4–8). ps2_recomp then emits a one-instruction body that runs the prologue
(`addiu sp,sp,-N`), **leaks the stack, and never restores `$ra`** — the drifting stack later
makes some `lw ra,off(sp); jr ra` load garbage and jump into data (a **data-as-code spin**,
the dominant symptom of a bad CSV).

- **Detect:** `funcs.py scan` lists rows whose size is suspiciously small but whose first
  instruction is a prologue. A data-as-code spin (huge run-log) is the runtime symptom.
- **Fix:** correct the row's `end`/`size` to the real control-flow end (`funcs.py bounds`).

## Class 2 — Missing (CSV gap — function exists but no row covers it)

The function is real and **called** (via `jal` or, most often, `jalr`) but absent from the
CSV. The runtime logs `Function at address 0x.. not found` and recovers via
`[dispatch:recover-pc]` — returning to `ra`, which **drops the function's work** (and any
delay-slot / tail-call side effects). The boot limps on in a degraded state.

These are predominantly **`jalr`-target functions** — vtable methods / function pointers
reached only through computed jumps. Static analysis can't discover them (nothing references
them by a resolvable address, and the ELF has no symbols), so the analyzer simply omits
them. They appear in *pockets* as the boot advances into new code.

### Collect → validate → add (the batchfix)

1. **Collect** the distinct missing addresses from the run:
   ```bash
   grep -oE 'Function at address 0x[0-9a-f]+' <game_dir>/tmp/run.txt | sort -u
   ```
2. **Validate** each is a real function (disasm is ground truth): its first instruction is
   valid code (not `0x00000000`/data), **and** it ends with a clean `jr ra`/`j` (or is a
   `jr`/`j`-first stub). Being a live `jalr` target is itself strong evidence it's real.
   Reject anything that fails and look at it by hand — it may be a bad computed address, not
   a function.
3. **Add** a `gapfix<addr>,0xSTART,0xEND,SIZE` row for each. Compute `end` as the
   control-flow end (last `jr ra` + 8), **capped at the next address in the combined set of
   {existing CSV starts} ∪ {all missing addresses}** — so a run of consecutive gaps doesn't
   over-bound each other.

Back up the CSV first (`cp functions.csv functions.csv.bak`). A reusable batch script
(loads the ELF once via `funcs.py`'s helpers, validates, and appends the rows) — adapt the
addresses/paths:

```python
import importlib.util, bisect, re
spec = importlib.util.spec_from_file_location('funcs', '<engine>/.claude/skills/ps2recomp-fix-next-crash/funcs.py')
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
G = '<game_dir>'
data, segs = fm.load_elf(G + '/<ELF>')
w = lambda a: fm.word_at(data, segs, a)
missing = sorted(set(int(m,16) for m in re.findall(r'Function at address (0x[0-9a-f]+)', open(G+'/tmp/run.txt').read())))
csv_starts = set(r[1] for r in fm.load_csv(G+'/recomp/functions.csv'))
combined = sorted(csv_starts | set(missing))
added, bad = [], []
for a in missing:
    if a in csv_starts: continue
    cap = combined[bisect.bisect_right(combined, a)] if bisect.bisect_right(combined,a) < len(combined) else a+0x800
    first = w(a)
    if first is None or first == 0: bad.append((hex(a),'no/zero')); continue
    if fm.is_return(first) or fm.is_uncond_jump(first):
        end = a + 8
    else:
        last = None
        for x in range(a, min(cap, a+0x2000), 4):
            if fm.is_return(w(x)): last = x + 8
        end = min(last, cap) if last else cap
    last2 = [w(end-8), w(end-4)]
    clean = any(x == 0x03e00008 or (x is not None and (x>>26)==0x02) for x in last2) or fm.is_return(first) or fm.is_uncond_jump(first)
    if end <= a or end-a > 0x2000 or not clean: bad.append((hex(a), 'bad bounds/return')); continue
    added.append((a, end))
with open(G+'/recomp/functions.csv', 'a') as f:
    for a, end in added: f.write(f"gapfix{a:06x},0x{a:08X},0x{end:08X},{end-a}\n")
print('added', len(added), 'rejected', bad)
```

**Trampolines** are a sub-case the auto-validator rejects ("bad bounds/return"): a function that
ends in `j target` (a tail-call) rather than `jr ra` — e.g. arg setup then `j 0x1f3d40` + delay
slot, ~32 bytes. The validator above only looks for a `jr ra`, so disasm those rejects by hand
and add them with `end` = the `j` + its delay slot + trailing-nop padding (= next function start).
These matter: `recover-pc` drops both the tail-call target *and* the args the trampoline set up.
(A stronger validator would also accept "first unconditional `j` in the body" as an end.)

### `find_missing.py` — the packaged tool (supersedes the inline script above)

`.claude/skills/ps2recomp-fix-next-crash/find_missing.py` (companion to `funcs.py`, same
`--game` resolution) does all of the above and adds a **proactive** mode so you don't have to
limp the boot one missing `jalr` at a time:

- `find_missing.py bounds-scan --game DIR [--min HEX --max HEX --max-size N] [--emit]` — walks
  the code segment treating every `jr ra`/`j` (+delay slot, +trailing nops) as a function end
  and tests the next word as a start. This catches the cases the run-log and a naive gap-scan
  miss: **no-prologue leaf functions** and **functions sitting after data inside a gap** (e.g.
  `0x158570`, a `jalr`-only vtable method preceded by another fn + padding). `--emit` prints
  ready-to-append `gapfix_<addr>,0xSTART,0xEND,SIZE` rows; bounds are control-flow-end capped at
  the next CSV/candidate start. Handles `jr ra` stubs and `j` trampolines (accepts a leading `j`
  as both a start and an end) — no by-hand trampoline pass needed.
- `find_missing.py gaps --game DIR` — lighter: only the start-of-gap candidates (after pure
  padding); misses the post-data cases that `bounds-scan` finds.
- `find_missing.py verify HEX --game DIR` — is an address covered / which entry / is it the start.

**Use it scoped and reviewed.** Restrict to the game-code region (`--min 130000`; below that is
crt0/kernel/libc the runtime stubs — don't add CSV functions there or you fight the stubs) and
**`--max-size 600`** to drop large candidates that are almost certainly **data-as-code**
over-bounds (a "function" that runs hundreds of bytes with no `jr ra` is the §over-bound smell).
Behaviorally a false-positive small entry is inert (nothing dispatches to it; the bytes are
unchanged), so a scoped mass-add is safe — the real risk is an over-bound blowing up the build,
which `--max-size` guards. Always: append → **FULL regen** → confirm the not-found storm shrank
and the boot advanced. (rotk_recomp: this found ~133 missing fns in 0x130000-0x222000
in one pass — the vtable-method tail behind the post-loading pc-zero — after the run-log only
ever revealed them one stalled frontier at a time.)

It's **iterative**: each regen advances the boot, which uncovers the next gap pocket. sccache
keeps a small gap-batch's rebuild fast (only new/renumbered units miss).

## Class 3 — Over-bound (auto-named function whose body runs past its CSV end)

ps2_recomp treats **auto-generated names** (`FUN_`, `sub_`, `LAB_`, `DAT_`) as
non-authoritative: for such a row it *discards the CSV bound* and instead uses the size from
any oversized ELF symbol, so the emitted body runs far past the real function end into the
next functions and raw data. Result: a single **huge translation unit** (a 13 MB+
data-as-code `.cpp`), 30–45 min to compile and OOM-prone, which also **swallows the
neighboring functions** (they vanish from the build) and masks generated-code greps.

- **Detect:** a `cc1plus` ballooning past a few GB during the build (the
  [`build_progress.sh`](../scripts/build_progress.sh) monster/OOM warning), or a generated
  `.cpp` whose address range vastly exceeds its CSV `end`.
- **Fix (boundfix):** **rename the CSV row to a non-auto name** (e.g. `boundfix<addr>`). A
  name that doesn't match the `FUN_/sub_/LAB_/DAT_` pattern makes the CSV bound
  authoritative again, so the row is emitted at its declared size and its swallowed
  neighbors reappear. (The address-based dispatch is unaffected by the rename.)

---

## After any change

Full regen + rebuild, run again, and confirm: the old warning is gone, the frontier
advanced (new log activity), and no new monster unit appeared. Then record it in the game
repo's `docs/progress.md`. Keep `functions.csv.bak*` backups until the change is proven.

## Porting a CSV (and overrides) from another region: `xmap.py`

When a second regional build of the same game needs bringing up, port the working repo instead of
re-deriving it. `.claude/skills/ps2recomp-fix-next-crash/xmap.py --src <game_dir> --dst <game_dir>`:

- `map HEX…` — code address -> target, by a relocation-insensitive signature (jal targets, lui halves
  and gp offsets masked; `$zero`/`$sp`-relative immediates kept), with forward-window and
  neighbour-anchor fallbacks. Only UNIQUE matches are accepted; ambiguity is reported, never guessed.
- `csv [--prefix gapfix,boundfix] [--emit]` — maps the source's fix rows; `--emit` prints rows absent
  from the target. Insert them; never rewrite existing rows (Ghidra's `Size` is not `End-Start`).
- `xlate HEX…` — any address, code OR data: data pairs come from the relocated operands of every
  function whose normalized body matches exactly in both ELFs.

Validate the map on an independent fact before trusting it (EUR->USA: the three jump
tables `ps2_analyzer` finds in each ELF mapped exactly). Region code can be restructured: a mapped
address is not mapped semantics. Worked example: the USA game repo's `docs/progress.md` (usa-1).
