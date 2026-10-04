#!/usr/bin/env python3
"""
find_missing.py — find functions ABSENT from a PS2Recomp game's functions.csv.

Companion to funcs.py. The Ghidra-exported functions.csv routinely OMITS whole
functions (especially small vtable-method stubs and no-prologue leaf functions).
When such a function is jalr'd at runtime, ps2_recomp can't find it, the runtime
recover-pc's, and `v0` is left holding the callee ADDRESS — which downstream code
then uses as a return value (e.g. a size), producing absurd values and crashes.
(That was the post-loading pc-zero stack-stomp in rotk_recomp: 12 per-type
size functions at gp-0x67E0 were missing, so each "size" came back as fnptr+4 ~= 1.9MB.)

This tool finds the gaps two ways and proposes gapfix CSV rows you can append
(then do a FULL regen build):

  gaps   — list address ranges NOT covered by any CSV entry that begin with a clean
           function start (prologue `addiu sp,-N`, `jr ra` stub, or `j` trampoline).
           Misses no-prologue leaf functions and functions sitting after data in a gap.

  bounds-scan (default) — ROBUST: walk the code segment, treat every `jr ra`/`j`
           terminator (+delay slot, +trailing nops) as a function end, and test the
           next word as a function start. Catches no-prologue leaf fns and vtable
           methods that `gaps` misses. May surface false positives where a function
           is followed by a jump table / data, so it filters to plausible first
           instructions and lets you bound the region/size.

Usage (run from the engine root; --game points at the game repo):
  find_missing.py gaps         [--game DIR] [--min HEX] [--max HEX]
  find_missing.py bounds-scan  [--game DIR] [--min HEX] [--max HEX] [--max-size N] [--emit]
  find_missing.py verify HEX   [--game DIR]      # is HEX covered? show neighbors

--emit prints ready-to-append CSV rows: `gapfix_<addr>,0xSTART,0xEND,SIZE` (CRLF).
Pipe to the CSV:  find_missing.py bounds-scan --game DIR --emit >> DIR/recomp/functions.csv
ALWAYS sanity-check the emitted rows (esp. large sizes = possible data-as-code
over-bound), then do a FULL regen build. Bounds = control-flow end (last jr/j before
the next start), capped at the next CSV/candidate start. Names are arbitrary
(ps2_recomp dispatches by address; the name column is ignored) — `gapfix_` matches
the repo convention.
"""
import sys, os, bisect

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import funcs as F


def _load(game):
    elf, csv = F.resolve_game(game)
    data, segs = F.load_elf(elf)
    rows = F.load_csv(csv)
    return data, segs, rows, csv


def _is_jtail(w):
    return w is not None and (w >> 26) == 0x02  # j target (tail call / trampoline)


# Plausible first instruction of a real function (excludes op=0 "special", which is
# what jump-table address words decode to — the main false-positive source).
_GOOD_OPS = {0x09, 0x0f, 0x23, 0x2b, 0x0d, 0x04, 0x05, 0x03, 0x0c, 0x0a, 0x0b,
             0x08, 0x24, 0x25, 0x21, 0x28, 0x29, 0x2c, 0x2d, 0x26, 0x27,
             0x37, 0x3f, 0x1e, 0x1f}  # addiu/lui/lw/sw/ori/beq/bne/jal/.../sq/ld/lq/sd


# SPECIAL (op=0) register-move / compare funcs that legitimately OPEN a function -- the
# arg-shuffling forwarding thunk (`daddu a0,a1,zero ... j target`) and functions whose
# `addiu sp,-N` prologue is only the SECOND instruction. op=0 is excluded wholesale above
# because jump-table address words decode as SPECIAL, but those words are discriminated by
# two fields a real move never fails: the shift-amount field must be 0, and rd must not be
# $zero (writing $zero is a no-op, the signature of misdecoded data). Added after
# 0x1D1290 -- a live `jalr` target reached from dispatcher 0x1d2a30 -- proved unfindable by
# BOTH `gaps` and `bounds-scan`; the discriminator yields 13 candidates game-wide, 8 real.
_SPECIAL_START_FUNCS = {0x21, 0x2d, 0x25, 0x24, 0x2a, 0x2b, 0x20, 0x22, 0x23}


def _is_special_move_start(w):
    if (w >> 26) != 0:
        return False
    sa = (w >> 6) & 0x1f
    rd = (w >> 11) & 0x1f
    return sa == 0 and rd != 0 and (w & 0x3f) in _SPECIAL_START_FUNCS


def _valid_start(w):
    if w is None or w == 0:
        return False
    if F.is_prologue(w) or F.is_return(w) or _is_jtail(w):
        return True
    if _is_special_move_start(w):
        return True
    return (w >> 26) in _GOOD_OPS


def _coverage(rows):
    covered = sorted((s, e) for _, s, e, _ in rows)
    cs = [c[0] for c in covered]

    def is_covered(a):
        i = bisect.bisect_right(cs, a) - 1
        return i >= 0 and covered[i][0] <= a < covered[i][1]
    return is_covered


def _bound(data, segs, s, cap):
    w0 = F.word_at(data, segs, s)
    if F.is_return(w0) or _is_jtail(w0):
        return s + 8
    last = None
    for x in range(s, min(cap, s + 0x2000), 4):
        ww = F.word_at(data, segs, x)
        if F.is_return(ww) or _is_jtail(ww):
            last = x + 8
            break
    return min(last, cap) if last else cap


def cmd_gaps(game, args):
    mn = int(_opt(args, '--min', '0'), 16)
    mx = int(_opt(args, '--max', 'ffffffff'), 16)
    data, segs, rows, _ = _load(game)
    ivals = sorted((s, e) for _, s, e, _ in rows)
    merged = []
    for s, e in ivals:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    starts = sorted(r[1] for r in rows)
    n = 0
    for i in range(len(merged) - 1):
        gs, ge = merged[i][1], merged[i + 1][0]
        a = gs
        while a < ge:
            w = F.word_at(data, segs, a)
            if w is None:
                break
            if w == 0:
                a += 4
                continue
            if F.is_return(w) or _is_jtail(w) or F.is_prologue(w):
                if mn <= a < mx:
                    e = _bound(data, segs, a, ge)
                    print(f"  0x{a:06X}-0x{e:06X} ({e-a}B)  {F.dis(w)}")
                    n += 1
                a = _bound(data, segs, a, ge)
                while a < ge and F.word_at(data, segs, a) == 0:
                    a += 4
                continue
            break  # unrecognized -> assume data; use bounds-scan to look past it
    print(f"\n{n} gap candidates (start-of-gap only; use bounds-scan for the rest)")


def cmd_bounds_scan(game, args):
    mn = int(_opt(args, '--min', '130000'), 16)
    mx = int(_opt(args, '--max', 'ffffffff'), 16)
    max_size = int(_opt(args, '--max-size', '0') or '0')
    emit = '--emit' in args
    data, segs, rows, _ = _load(game)
    starts = sorted(r[1] for r in rows)
    is_covered = _coverage(rows)
    lo = min(s[0] for s in segs)
    hi = max(s[0] + s[1] for s in segs)
    ends = []
    a = lo
    while a < hi:
        w = F.word_at(data, segs, a)
        if w is None:
            a += 4
            continue
        if F.is_return(w) or _is_jtail(w):
            nxt = a + 8
            while F.word_at(data, segs, nxt) == 0:
                nxt += 4
            ends.append(nxt)
            a = nxt
            continue
        a += 4
    cand = sorted(set(s for s in ends if mn <= s < min(mx, hi)
                      and not is_covered(s) and _valid_start(F.word_at(data, segs, s))))
    out = []
    for s in cand:
        cap = F.next_start(starts, s) or (s + 0x800)
        j = bisect.bisect_right(cand, s)
        if j < len(cand):
            cap = min(cap, cand[j])
        e = _bound(data, segs, s, cap)
        if max_size and (e - s) > max_size:
            continue
        out.append((s, e))
    if emit:
        for s, e in out:
            sys.stdout.write(f"gapfix_{s:06x},0x{s:08X},0x{e:08X},{e-s}\r\n")
    else:
        big = [(s, e) for s, e in out if e - s > 0x600]
        print(f"{len(out)} missing-function candidates in [0x{mn:X},0x{mx:X})")
        print(f"  sizes: stub(8B)={sum(1 for s,e in out if e-s==8)}  >0x600={len(big)} (review: possible data-as-code)")
        for s, e in big:
            print(f"    LARGE 0x{s:06X}-0x{e:06X} ({e-s}B)")
        print("  re-run with --emit to print CSV rows (append, then FULL regen build)")


def cmd_verify(game, args):
    a = int(args[0], 16)
    _, _, rows, _ = _load(game)
    for name, s, e, sz in rows:
        if s <= a < e:
            print(f"0x{a:06X} is inside {name} (0x{s:06X}-0x{e:06X})"
                  + ("" if s == a else "  [NOT the start -> mid-function / bad boundary]"))
            return
    print(f"0x{a:06X} is NOT covered (missing function)")


def _opt(args, flag, default):
    return args[args.index(flag) + 1] if flag in args else default


_CMDS = {'gaps': cmd_gaps, 'bounds-scan': cmd_bounds_scan, 'verify': cmd_verify}


def main():
    argv = sys.argv[1:]
    game = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] in ('--game', '-g'):
            game = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if not rest or rest[0] not in _CMDS:
        print(__doc__)
        sys.exit(1)
    _CMDS[rest[0]](game, rest[1:])


if __name__ == '__main__':
    main()
