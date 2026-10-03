#!/usr/bin/env python3
"""
find_swallowed.py — find functions SWALLOWED INSIDE an existing functions.csv row.

The fourth CSV bug variant, and the one our own tooling is blind to. `find_missing.py`
(both `gaps` and `bounds-scan`) only ever examines address space NOT covered by a CSV
row, so a row whose END overshoots into the *next* function hides that function
permanently: it is "covered", therefore never a candidate, yet it is not a dispatch
entry either — ps2_recomp registers only the row's START address. Any `jalr` to the
swallowed function logs `[guest-branch:missing-target]`, recover-pc drops the call's
side effects (notably the delay slot and the callee's writes through out-pointers),
and `v0` is left holding the callee ADDRESS — which downstream code then uses as a
size/handle. Found: gapfix_1dd5a0 (0x1DD5A0-0x1DD64C) swallowed the
table-dispatched handler at 0x1DD610 (called via the gp-0x67C0 stride-68 descriptor
dispatcher at 0x185B00).

DETECTION. Inside each row, a `jr ra` (or `j`) + its delay slot ends a basic block. If
the next non-padding address is NOT the target of any branch *within the same row*,
nothing can reach it by fallthrough or by a local branch — so it is the start of a
separate function that the row has swallowed. (A function with several `return`s is
handled correctly: the code after an early `jr ra` is a local branch target, so it is
not reported.)

Usage (run from the engine root; --game points at the game repo):
  find_swallowed.py            [--game DIR] [--min HEX] [--max HEX] [--jonly] [--emit]

  --emit   print the REPLACEMENT csv rows for each affected row (the original row
           split into N rows). Rewrite by hand or with a script; then FULL regen.
  --jonly  ALSO treat `j` (uncond jump) terminators as split points. Off by default:
           `j` is common as a tail-call/loop inside one function, so it yields more
           false positives than `jr ra` does. Review these by disasm before applying.
"""
import sys, os, struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from funcs import resolve_game, load_elf, word_at, dis, load_csv, is_prologue

BRANCH_OPS = {0x01, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17}


def branch_target(w, addr):
    """Return the target address of a branch/jump at `addr`, or None."""
    if w is None:
        return None
    op = (w >> 26) & 0x3F
    if op in BRANCH_OPS:
        imm = w & 0xFFFF
        simm = imm - 0x10000 if imm >= 0x8000 else imm
        return addr + 4 + simm * 4
    if op == 0x02:                      # j
        return ((addr + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
    if op == 0x11 and ((w >> 21) & 0x1F) == 0x08:   # bc1f/bc1t
        imm = w & 0xFFFF
        simm = imm - 0x10000 if imm >= 0x8000 else imm
        return addr + 4 + simm * 4
    return None


def is_return(w):
    return w == 0x03E00008


def is_j(w):
    return w is not None and (w >> 26) == 0x02


def starts_with_backward_branch(w, addr):
    """A first instruction that branches BACKWARD is a loop tail, not a function start.

    Nothing inside a function can be reached by branching before its own entry, so a
    candidate opening with a backward branch belongs to the code above it — it is reached
    from there, and the `jr ra` we split on was just an early return. Confirmed by disasm
    review: 0x12D730 (`bnel v1,zero,-44`) and 0x12C69C (`beq v1,zero,-24`) were
    the only two false positives out of 32 candidates; both open exactly this way.
    """
    t = branch_target(w, addr)
    return t is not None and t <= addr


def plausible_start(w):
    """Filter out data / jump-table words masquerading as a function start."""
    if w is None or w == 0:
        return False
    if is_prologue(w):
        return True
    op = (w >> 26) & 0x3F
    # leaf functions commonly open with an immediate load, a move, a load, or a branch
    if op in (0x08, 0x09, 0x0F, 0x23, 0x24, 0x25, 0x2B, 0x0C, 0x0D, 0x0E, 0x30, 0x37):
        return True
    if op == 0 and (w & 0x3F) in (0x08, 0x09, 0x21, 0x25, 0x2D, 0x00):
        return True
    if op in BRANCH_OPS or op == 0x02:
        return True
    return False


def scan_row(data, segs, start, end, jonly, csv_starts=frozenset()):
    """Return the list of swallowed-function start addresses inside [start,end).

    `csv_starts` = every START address in the CSV. A candidate that already HAS its own row
    is dispatchable, so it is not swallowed — the enclosing row is merely over-bound and
    overlaps it (a different bug, fixed by shortening END alone). Skipping these matters:
    without it the split emits a DUPLICATE row for an address that already has one, which
    is how 4 duplicate starts got into rotk_decomp's CSV before validation
    caught them. The enclosing row still gets shortened, which removes the overlap.
    """
    targets = set()
    a = start
    while a < end:
        w = word_at(data, segs, a)
        t = branch_target(w, a)
        if t is not None and start < t < end:
            targets.add(t)
        a += 4

    splits = []
    a = start
    while a < end:
        w = word_at(data, segs, a)
        terminator = is_return(w) or (jonly and is_j(w))
        if terminator:
            p = a + 8                       # skip the delay slot
            while p < end and word_at(data, segs, p) == 0:
                p += 4                      # skip inter-function padding nops
            wp = word_at(data, segs, p)
            if (p < end and p not in targets and p not in csv_starts
                    and plausible_start(wp)
                    and not starts_with_backward_branch(wp, p)):
                splits.append((p, 'jr ra' if is_return(w) else 'j'))
                a = p
                continue
        a += 4
    return splits


def main():
    args = sys.argv[1:]
    game = None
    lo, hi = 0, 0xFFFFFFFF
    jonly = emit = False
    i = 0
    while i < len(args):
        if args[i] in ('--game', '-g'):
            game = args[i + 1]; i += 2
        elif args[i] == '--min':
            lo = int(args[i + 1], 16); i += 2
        elif args[i] == '--max':
            hi = int(args[i + 1], 16); i += 2
        elif args[i] == '--jonly':
            jonly = True; i += 1
        elif args[i] == '--emit':
            emit = True; i += 1
        else:
            i += 1

    elf, csv = resolve_game(game)
    data, segs = load_elf(elf)
    rows = load_csv(csv)
    csv_starts = frozenset(r[1] for r in rows)

    affected = 0
    swallowed = 0
    for name, start, end, size in rows:
        if end <= start or start < lo or start >= hi:
            continue
        if end - start < 16:
            continue
        splits = scan_row(data, segs, start, end, jonly, csv_starts)
        if not splits:
            continue
        affected += 1
        swallowed += len(splits)
        print(f"{name},0x{start:08X},0x{end:08X},{size}  -> {len(splits)} swallowed:")
        for p, kind in splits:
            w = word_at(data, segs, p)
            print(f"    0x{p:08X}  after {kind:5s}  first insn: {dis(w)}")
        if emit:
            bounds = [start] + [p for p, _ in splits] + [end]
            print("  REPLACE WITH:")
            for k in range(len(bounds) - 1):
                s, e = bounds[k], bounds[k + 1]
                nm = name if k == 0 else f"gapfix_{s:x}"
                print(f"    {nm},0x{s:08X},0x{e:08X},{e - s}")

    print(f"\n{swallowed} swallowed function(s) inside {affected} CSV row(s) "
          f"in [0x{lo:X},0x{hi:X}) (jonly={jonly})")
    print("Each is a live `jalr` target that can never resolve. Split the rows, then FULL regen.")


if __name__ == '__main__':
    main()
