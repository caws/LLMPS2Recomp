#!/usr/bin/env python3
"""Analyse a PS2X_GS_CENSUS file (cycle 73: 3D scene geometry fidelity).

Record shapes (tab-separated, see the file's own `#` header):
  F  frame tick
  D  seq frame tick | prim iip tme fge abe fst ctxt | fbp fbw fpsm | zbp zpsm zmsk |
     test | alpha | tbp tpsm tw th cbp | nv | xMin xMax yMin yMax | zMin zMax | aMin aMax
  T  seq frame tick | dbp dbw dpsm | sbp sbw spsm | ssax ssay dsax dsay dir | rrw rrh | trxdir pixels
  P  seq frame tick | displayFbp sourceFbp w h usedPreferred
"""
import sys, collections

PRIM = {0: "point", 1: "line", 2: "linestrip", 3: "tri", 4: "tristrip",
        5: "trifan", 6: "sprite", 7: "invalid"}


def parse(path):
    frames = collections.OrderedDict()   # frame -> {"draws": [...], "xfers": [...], "present": [...]}
    cur = None
    for line in open(path, errors="replace"):
        if line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        k = f[0]
        if k == "F":
            cur = int(f[1])
            frames.setdefault(cur, {"draws": [], "xfers": [], "present": [], "tick": int(f[2])})
            continue
        if cur is None:
            continue
        try:
            parse_record(frames[cur], k, f)
        except (IndexError, ValueError):
            # The census is streamed, so reading it while the run is live can catch a
            # half-written final line. Skip it rather than abort the whole analysis.
            continue
    return frames


def parse_record(fr, k, f):
        if k == "D":
            # Field layout is dictated by the fprintf in GS::emitDebugCensusUnlocked:
            # 0:D 1:seq 2:frame 3:tick 4:prim 5:"iip tme fge abe fst ctxt" 6:"fbp fbw psm"
            # 7:"zbp zpsm zmsk" 8:test 9:alpha 10:"tbp tpsm tw th cbp" 11:nv
            # 12:"xMin xMax yMin yMax" 13:"zMin zMax" 14:"aMin aMax"
            prim = int(f[4])
            iip, tme, fge, abe, fst, ctxt = map(int, f[5].split())
            fbp, fbw, fpsm = f[6].split(); zbp, zpsm, zmsk = f[7].split()
            tbp, tpsm, tw, th, cbp = f[10].split()
            xmin, xmax, ymin, ymax = map(float, f[12].split())
            fr["draws"].append(dict(
                seq=int(f[1]), prim=prim, tme=tme, abe=abe, fst=fst, ctxt=ctxt, iip=iip,
                fbp=int(fbp), fbw=int(fbw), fpsm=int(fpsm, 16),
                zbp=int(zbp), zpsm=int(zpsm, 16), zmsk=int(zmsk),
                test=int(f[8], 16), alpha=int(f[9], 16),
                tbp=int(tbp), tpsm=int(tpsm, 16), tw=int(tw), th=int(th), cbp=int(cbp),
                nv=int(f[11]), xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax))
        elif k == "T":
            dbp, dbw, dpsm = f[4].split()
            rrw, rrh = f[7].split()
            fr["xfers"].append(dict(seq=int(f[1]), dbp=int(dbp), dbw=int(dbw),
                                             dpsm=int(dpsm, 16), rrw=int(rrw), rrh=int(rrh),
                                             pixels=int(f[8].split()[1])))
        elif k == "P":
            dfbp, sfbp, w, h, pref = map(int, f[4].split())
            fr["present"].append(dict(dfbp=dfbp, sfbp=sfbp, w=w, h=h, pref=pref))


def infer_offset(frames):
    """GS XYZ coords carry XYOFFSET (OFX/OFY, conventionally 2048). The census does not record
    XYOFFSET, so infer it: the most common xMin across draws is the left edge of the screen for
    the many full-width draws, and PS2 setups use 0 or 2048. Pick whichever candidate puts the
    bulk of the draws inside a plausible 0..640 screen box."""
    xs = [d["xmin"] for fr in frames.values() for d in fr["draws"]]
    if not xs:
        return 0.0
    best, bestscore = 0.0, -1
    for cand in (0.0, 2048.0):
        score = sum(1 for x in xs if -64.0 <= (x - cand) <= 704.0)
        if score > bestscore:
            best, bestscore = cand, score
    print("inferred XYOFFSET origin = %.0f (%d/%d draw xMin land on screen with it)"
          % (best, bestscore, len(xs)))
    return best


def report(frames, want=None, top=14):
    print("frames captured: %d  (%s)" % (len(frames), ", ".join(str(x) for x in list(frames)[:12])))
    ofs = infer_offset(frames)
    for fr in frames.values():
        for d in fr["draws"]:
            d["xmin"] -= ofs; d["xmax"] -= ofs
            d["ymin"] -= ofs; d["ymax"] -= ofs
    # VRAM regions ever written by a Transfer, for the "was this texture uploaded?" question.
    written = set()
    for fr in frames.values():
        for t in fr["xfers"]:
            written.add(t["dbp"])
    print("distinct Transfer dbp (VRAM pages written): %d" % len(written))

    targets = list(frames) if want is None else [f for f in frames if f in want]
    for fi in targets:
        fr = frames[fi]
        d = fr["draws"]
        if not d:
            continue
        print("\n=== frame %d  tick=%d  draws=%d xfers=%d ===" % (fi, fr["tick"], len(d), len(fr["xfers"])))
        for p in fr["present"]:
            print("  PRESENT %dx%d displayFbp=%d sourceFbp=%d preferred=%d"
                  % (p["w"], p["h"], p["dfbp"], p["sfbp"], p["pref"]))

        by_rt = collections.Counter((x["fbp"], x["fbw"], x["fpsm"]) for x in d)
        print("  render targets (fbp,fbw,psm) -> draws:")
        for (fbp, fbw, psm), n in by_rt.most_common():
            print("    fbp=%-5d fbw=%-3d psm=0x%02X   %d" % (fbp, fbw, psm, n))

        print("  prim/tme mix: %s" % dict(collections.Counter(
            "%s%s" % (PRIM.get(x["prim"], "?"), "+tex" if x["tme"] else "") for x in d)))

        untex = [x for x in d if not x["tme"]]
        print("  UNTEXTURED draws: %d" % len(untex))

        # The chartreuse suspect: big-area draws. Rank by bbox area.
        d2 = sorted(d, key=lambda x: (x["xmax"] - x["xmin"]) * (x["ymax"] - x["ymin"]), reverse=True)
        print("  largest %d draws by bbox area:" % top)
        print("    %-8s %-9s %-3s %-4s %-24s %-28s %s"
              % ("seq", "prim", "nv", "tme", "bbox", "tex(tbp/psm/size/cbp)", "rt(fbp/fbw/psm)"))
        for x in d2[:top]:
            area = (x["xmax"] - x["xmin"]) * (x["ymax"] - x["ymin"])
            tex = ("tbp=%d/0x%02X/%dx%d/cbp=%d" % (x["tbp"], x["tpsm"], x["tw"], x["th"], x["cbp"])
                   if x["tme"] else "-")
            up = "" if not x["tme"] else ("" if x["tbp"] in written else "  <-- TBP NEVER UPLOADED")
            print("    %-8d %-9s %-3d %-4d %-24s %-28s fbp=%d/%d/0x%02X  area=%.0f%s"
                  % (x["seq"], PRIM.get(x["prim"], "?"), x["nv"], x["tme"],
                     "%.0f..%.0f,%.0f..%.0f" % (x["xmin"], x["xmax"], x["ymin"], x["ymax"]),
                     tex, x["fbp"], x["fbw"], x["fpsm"], area, up))

        # cont.44 signature: stretched/degenerate triangles.
        big = [x for x in d if (x["xmax"] - x["xmin"]) > 1500 or (x["ymax"] - x["ymin"]) > 1500]
        if big:
            print("  ** %d draws with a >1500px bbox (degenerate-vertex signature) **" % len(big))
            for x in big[:8]:
                print("     seq=%d %s nv=%d bbox=%.0f..%.0f,%.0f..%.0f"
                      % (x["seq"], PRIM.get(x["prim"], "?"), x["nv"],
                         x["xmin"], x["xmax"], x["ymin"], x["ymax"]))

        # Textured draws whose TBP was never the destination of a Transfer => sampling garbage/white.
        bad = collections.Counter(x["tbp"] for x in d if x["tme"] and x["tbp"] not in written)
        if bad:
            print("  ** textured draws sampling a NEVER-UPLOADED tbp: %d draws over %d tbps: %s"
                  % (sum(bad.values()), len(bad), bad.most_common(8)))


if __name__ == "__main__":
    frames = parse(sys.argv[1])
    want = set(int(a) for a in sys.argv[2:]) or None
    report(frames, want)
