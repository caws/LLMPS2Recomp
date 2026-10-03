#!/usr/bin/env python3
"""Summarise [VU1:kickcensus] lines, split at the MOVIE-END era marker.

The aggregate fields are cumulative over ALL kicks, so an era's true counts are the
DIFFERENCE between the last aggregate of that era and the last aggregate before it.
That difference is the unbiased rate; the per-line `tag:` fields are a 1-in-N sample.
"""
import re, sys, collections

LINE = re.compile(
    r"\[VU1:kickcensus\] vu(\d+) #(\d+) (\w+) addr=(\d+) bytes=(\d+) iters=(-?\d+) \| tag: "
    r"nloop=(\d+) eop=(\d+) pre=(\d+) prim=(\d+) flg=(\d+) nreg=(\d+) \| ALL (\d+) kicks: "
    r"sub=(\d+) drop=(\d+) zero=(\d+) \| nloop0=(\d+) small=(\d+) huge=(\d+) \| "
    r"flg=(\d+)/(\d+)/(\d+)/(\d+)")
FLG = {0: "PACKED", 1: "REGLIST", 2: "IMAGE", 3: "DISABLE"}
AGG = ["kicks", "sub", "drop", "zero", "nloop0", "small", "huge", "f0", "f1", "f2", "f3"]


def parse(path):
    rows, era = [], None
    with open(path, "r", errors="replace") as f:
        for i, line in enumerate(f):
            if era is None and "MOVIE-END" in line:
                era = i
            m = LINE.search(line)
            if m:
                g = m.groups()
                rows.append(dict(line=i, vu=int(g[0]), outcome=g[2],
                                 nloop=int(g[6]), eop=int(g[7]), pre=int(g[8]),
                                 prim=int(g[9]), flg=int(g[10]), nreg=int(g[11]),
                                 agg=[int(x) for x in g[12:23]]))
    return rows, era


def show(label, rows, base):
    if not rows:
        print(f"  {label}: no samples")
        return base
    last = rows[-1]["agg"]
    delta = [a - b for a, b in zip(last, base)]
    k = delta[0] or 1
    print(f"  {label}: {delta[0]} kicks (unbiased), {len(rows)} sampled")
    print(f"      outcome : SUBMIT {delta[1]} ({100*delta[1]//k}%)  "
          f"DROP {delta[2]} ({100*delta[2]//k}%)  ZERO-byte {delta[3]} ({100*delta[3]//k}%)")
    print(f"      nloop   : ==0 {delta[4]} ({100*delta[4]//k}%)  "
          f"1..64 {delta[5]} ({100*delta[5]//k}%)  >64 {delta[6]} ({100*delta[6]//k}%)")
    print(f"      flg     : PACKED {delta[7]}  REGLIST {delta[8]}  "
          f"IMAGE {delta[9]}  DISABLE {delta[10]}")
    c = collections.Counter(
        (r["outcome"], r["nloop"], r["pre"], r["prim"], FLG.get(r["flg"]), r["nreg"])
        for r in rows)
    print("      sampled first-tags (1-in-N, NOT a rate):")
    for (out, nl, pre, prim, flg, nreg), n in c.most_common(6):
        print(f"        x{n:<4} {out:<6} nloop={nl:<6} pre={pre} prim={prim:<4} "
              f"{flg:<7} nreg={nreg}{'  (=16)' if nreg == 0 else ''}")
    return last


for path in sys.argv[1:]:
    rows, era = parse(path)
    print(f"\n=== {path.rsplit('/', 1)[-1]}  (MOVIE-END @ line {era}) ===")
    for vu in (1, 0):
        vrows = [r for r in rows if r["vu"] == vu]
        if not vrows:
            continue
        print(f"  --- VU{vu} ---")
        boot = [r for r in vrows if era is None or r["line"] < era]
        lvl = [r for r in vrows if era is not None and r["line"] >= era]
        base = show("BOOT/MOVIE era", boot, [0] * 11)
        show("LEVEL era", lvl, base)
