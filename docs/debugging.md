# Debugging a frontier

How to figure out *where* and *why* a recompiled game stops. Start with the static log
triage; escalate to gdb only when the runner is hung with nothing useful in the log.

## 1. Run-log triage (`<game_dir>/tmp/run.txt`)

Always run under `timeout` — a spinning runner writes hundreds of MB/s, so the log goes to
a file, never the terminal. After the run, classify it by the log:

| Symptom in the log | Meaning | Where to go |
|---|---|---|
| File is **huge** (hundreds of MB) | A tight spin — something dispatched in a loop, often data-as-code after a corrupted `ra` | [functions-csv.md](functions-csv.md) (truncation), or gdb if unclear |
| `Function at address 0x.. not found` + `[dispatch:recover-pc]` | Called function missing from `functions.csv`; the runtime skipped it (returned to `ra`, dropping delay-slot + tail-call effects) | [functions-csv.md](functions-csv.md) → missing/CSV-gap batchfix |
| `[dispatch:first-bad-pc]` / `[dispatch:pc-zero]` with a `trace=` | A jump to a bad PC; the `trace=` is the guest call chain — **read it right-to-left** from the failure point | gdb or static trace |
| Log stops growing, **CPU at 0%** | A silent hang — a `WaitSema` / busy-wait blocked on a completion that never fires | **gdb-under-parent** (below) |
| Log stops growing, **CPU pegged** | A quiet spin with no logging | Profile the dispatch (below) |

Useful one-liners:

```bash
R=<game_dir>/tmp/run.txt
grep -oE 'Function at address 0x[0-9a-f]+' "$R" | sort -u          # distinct missing fns
grep -oE '^\[[a-zA-Z0-9:_ -]+\]' "$R" | sort -u                     # which markers fired
grep -c 'recover-pc' "$R"                                           # how much churn
tail -8 "$R"                                                        # where it ended
```

Confirm the **`BUILD_TAG`** line (`[build-id] tag=...`) matches what you just built —
otherwise you're looking at a stale binary.

## 2. gdb-under-parent — backtracing a hung runner

**Rule: always run the runner *under* gdb (gdb as the parent). Never gdb-*attach* to a
separately-launched runner.** Typical dev boxes set `kernel.yama.ptrace_scope=1`, so a
sibling gdb gets `ptrace: Operation not permitted`; a parent gdb can always trace its own
child. (A user *could* `sudo sysctl -w kernel.yama.ptrace_scope=0` to allow attach, but
running-under-gdb needs no sudo and is the standing rule.)

The recipe that works for a hung (0%-CPU / blocked) runner:

```bash
G=<game_dir>; OUT="$G/tmp/gdb_block.txt"; rm -f "$OUT"
PS2_GAMEFILES="$G/gamefiles" DISPLAY=:0 gdb -batch -nx \
  -ex 'set pagination off' -ex 'set confirm off' \
  -ex 'run' -ex 'thread apply all bt 22' -ex 'kill' -ex 'quit' \
  --args "$G/tmp/ps2EntryRunner" "$G/<ELF>" > "$OUT" 2>&1 &
GDBPID=$!            # <-- the whole point: capture the REAL gdb pid
sleep 15             # let it boot + hit the block
kill -INT "$GDBPID"  # signal GDB (not the inferior) -> gdb stops the child + runs the queued bt
sleep 7
kill -9 "$GDBPID" 2>/dev/null; pkill -9 -f 'tmp/ps2EntryRunner' 2>/dev/null
grep -E '^#[0-9]+ ' "$OUT"
```

Failure modes that waste time (don't repeat them):
- Wrapping gdb in `timeout` → `$!` becomes *timeout's* pid, so `kill -INT` hits the wrong
  process. Launch gdb directly.
- Using `pgrep` to find gdb → matches stale/duplicate gdb pids from earlier tries.
- `kill -INT` the **inferior** → the runner catches SIGINT itself; gdb never stops. Signal
  **GDB**, which then interrupts the child and runs the queued `thread apply all bt`.

### Reading the backtrace

`thread apply all bt` prints every thread. Ignore the runtime/GL plumbing — raylib
`WaitTime`/`nanosleep`, Mesa `iris_dri`, the IRQ worker (`interruptWorkerMain`), and
background SIF-event workers (a thread whose deepest game frame is a lone
`FUN_..._0x119d20 -> WaitSema`). The **main boot thread** is the one with the deepest stack
of recompiled frames (`sub_*` / `FUN_*` / `*_0x......` / `WaitSema_*`), reached through
`PS2Runtime::run -> dispatchLoop`. Read it bottom-up to see what the boot was doing when it
blocked, then cross-reference the frame addresses with `funcs.py disasm` and the generated
`<game_dir>/tmp/generated/*.cpp`.

A frame dispatched **directly by `dispatchLoop`** (no recompiled caller above it) is a
function the boot jumped to via the engine's dispatch — useful for spotting where an
indirect (`jalr`) call landed.

## 3. One-shot override probes (when a guest address misbehaves)

Register a temporary probe on the suspicious address in the game's
`src/register_overrides.cpp`. The lambda gets `rdram`, `ctx`, and the register macros
(`GPR_U32(ctx, n)`, `SET_GPR_*`, `ctx->pc/hi/lo`). Dump the GPRs + walk the stack from `sp`,
scan guest RAM for a suspect pointer, then set `ctx->pc = 0` to **stop cleanly** instead of
letting it spin out hundreds of MB. Rebuild with `--skip-regen --changed-recomp` for a fast
cycle; remove or gate the probe when done.

Leaving a **tripwire** override on a known-bad address (log `ra`/`sp`, then `ctx->pc = 0`)
is a cheap regression guard for "control flow reached data again".

## 4. Mapping the per-frame loop (dispatch-logging)

When the runner is *running* (not hung) but not progressing — e.g. stuck on a screen — you
need to know **what actually executes per frame**, not guess statically. The runtime's
`dispatchLoop` is a flat top-level loop: `pc = ctx->pc; fn = lookupFunction(pc); fn(...)`. So
**every top-level (cross-unit / return-to-loop) dispatch passes through `lookupFunction(pc)`** —
break there, log the guest PC, and rank by frequency to get the hot loop.

```bash
G=<game_dir>
cat > "$G/tmp/trace.gdb" <<'EOF'
set pagination off
set confirm off
break PS2Runtime::lookupFunction
commands
silent
printf "D %x\n", $esi      # 2nd arg (after `this`) = the dispatched guest PC
continue
end
run
EOF
PS2_GAMEFILES="$G/gamefiles" DISPLAY=:0 gdb -batch -nx -x "$G/tmp/trace.gdb" \
  --args "$G/tmp/ps2EntryRunner" "$G/<ELF>" > "$G/tmp/dispatch.log" 2>&1 &
GDBPID=$!; sleep 18
kill -INT "$GDBPID"; sleep 3; kill -9 "$GDBPID" 2>/dev/null; pkill -9 -f 'tmp/ps2EntryRunner'
grep '^D ' "$G/tmp/dispatch.log" | awk '{print $2}' | sort | uniq -c | sort -rn | head -30
```

The **high-frequency PCs are the hot loop**; map them to functions (`funcs.py` + the CSV) and
chase callers with the jal-search (search the ELF for `0x0C000000 | (target>>2)`). This is how
you tell a **busy animation/compute loop** (lots of FPU/math, runs fine, advance is gated
*elsewhere*) from a **flag-spin** (one function polling a never-set flag). It's slower than a
free run (a breakpoint per dispatch), so an 18 s window catches a couple of frames — enough.

Cheaper variants: a `ctx->pc`-sampling override on a suspect function, or just `bt` on a
per-frame anchor (e.g. the render fn) — but those give one sample; the dispatch-log gives the
whole loop. Prefer this over static guessing about "which loop am I in".
