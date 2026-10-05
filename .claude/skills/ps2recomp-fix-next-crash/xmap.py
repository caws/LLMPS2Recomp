#!/usr/bin/env python3
"""xmap.py -- map guest addresses between two builds of the same game (e.g. EUR <-> USA).

Two regional ELFs share most code, but every relocated field differs (jal targets, lui/addiu
address halves, gp-relative offsets), and game code is shifted by inserted/removed functions.
This matches code by a RELOCATION-INSENSITIVE signature: each instruction is normalized by
masking the fields a relink changes, then a window of normalized words is looked up in the
other ELF.

  xmap.py map  --src <game_dir> --dst <game_dir> HEX [HEX ...]   # map individual addresses
  xmap.py csv  --src <game_dir> --dst <game_dir> [--prefix gapfix] [--emit]
        # map every --src CSV row whose name starts with a prefix (default: gapfix,boundfix)
        # into --dst; prints status per row, --emit prints dst-ready CSV rows (CRLF) for the
        # rows that mapped UNIQUELY and are absent (gapfix) / mis-bounded (boundfix) in dst.
  xmap.py xlate --src <game_dir> --dst <game_dir> HEX [HEX ...]
        # translate ANY guest address (code OR data: globals, tables, gp-relative vars). Data pairs
        # come from the relocated operands (lui+lo, gp-relative, jal targets) of every function whose
        # normalized body matches exactly in both ELFs; 'exact' = seen as an operand, 'interp' = between
        # two anchors with the SAME delta, 'unknown' = anchors disagree (inspect by hand).

Matching: an address A in src is mapped by taking the normalized window
[A - BACK, A + FWD) (default 6 words back, 18 forward; shrunk at segment edges), finding every
position in dst with the same normalized window, and accepting it only if UNIQUE. A non-unique
or absent window is reported, never guessed. Verify load-bearing mappings with funcs.py disasm.
"""
import argparse, os, re, struct, sys

def load_cfg(game):
    cfg = open(os.path.join(game, 'recomp', 'config.toml')).read()
    m = re.search(r'^\s*input\s*=\s*"([^"]+)"', cfg, re.M)
    elf = os.path.normpath(os.path.join(game, m.group(1)))
    return elf, os.path.join(game, 'recomp', 'functions.csv')

def load_code(elf):
    d = open(elf, 'rb').read()
    phoff = struct.unpack_from('<I', d, 0x1c)[0]; phnum = struct.unpack_from('<H', d, 0x2c)[0]
    best = None
    for i in range(phnum):
        t, off, va, pa, fs, ms, fl, al = struct.unpack_from('<8I', d, phoff + i * 32)
        if t == 1 and fs and (best is None or fs > len(best[1])):
            best = (va, d[off:off + fs])
    va, b = best
    words = list(struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4]))
    return va, words

def norm(w):
    op = w >> 26
    if op in (2, 3):            # j / jal: target is relocated
        return op << 26
    if op == 0 or op == 0x1c:   # SPECIAL / MMI: register-only, keep exact
        return w
    if op in (0x10, 0x11, 0x12, 0x13):  # COP0/1/2: keep exact (no relocs)
        return w
    # I-type (lui, addiu, loads/stores, branches): immediate may be relocated/shifted.
    # Branch offsets are position-relative and usually survive, but a branch whose span
    # contains inserted code changes too -- mask them all for robustness. EXCEPT immediates
    # a relink cannot change: constants off $zero (addiu v1,zero,131 = a syscall number) and
    # stack-frame offsets off $sp. lui (rs is always zero) carries an address half -> masked.
    rs = (w >> 21) & 31
    if op != 0x0f and op not in (4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17) and rs in (0, 29):
        return w
    return w & 0xFFFF0000

class Img:
    def __init__(self, game):
        self.elf, self.csv = load_cfg(game)
        self.base, words = load_code(self.elf)
        self.raw = words
        self.n = [norm(w) for w in words]
        self.rows = []
        for line in open(self.csv, newline=''):
            p = line.strip().split(',')
            if len(p) < 4 or not p[1].startswith('0x'): continue
            self.rows.append((p[0], int(p[1], 16), int(p[2], 16)))
        self.starts = {s: (nm, e) for nm, s, e in self.rows}
        self._idx = {}

    def idx(self, a): return (a - self.base) // 4

    def in_function(self, a):
        import bisect
        if not hasattr(self, '_sr'):
            self._sr = sorted((s, e) for nm, s, e in self.rows); self._ss = [s for s, e in self._sr]
        i = bisect.bisect_right(self._ss, a) - 1
        return i >= 0 and self._sr[i][0] <= a < self._sr[i][1]

    def index(self, k):
        if k not in self._idx:
            t = {}
            n = self.n
            for i in range(len(n) - k + 1):
                t.setdefault(tuple(n[i:i + k]), []).append(i)
            self._idx[k] = t
        return self._idx[k]

def map_addr(src, dst, a, back=6, fwd=18):
    return _map_window(src, dst, a, back, fwd, anchor=True)

def _map_window(src, dst, a, back=6, fwd=18, anchor=False):
    i = src.idx(a)
    if i < 0 or i >= len(src.n): return None, 'out-of-range'
    b = min(back, i); f = min(fwd, len(src.n) - i)
    key = tuple(src.n[i - b:i + f])
    k = b + f
    hits = dst.index(k).get(key, [])
    if len(hits) == 1:
        return dst.base + (hits[0] + b) * 4, 'ok'
    # retry: forward-only (the bytes BEFORE a function may be another function that changed)
    # fallbacks: forward-only windows (the code BEFORE a function may be a function that
    # changed), then shorter ones. Each accepted only if UNIQUE.
    worst = len(hits)
    for ff in (f, 12, 8):
        ff = min(ff, f)
        if ff < 8 and ff != f: continue
        key2 = tuple(src.n[i:i + ff]); hits2 = dst.index(ff).get(key2, [])
        if len(hits2) == 1: return dst.base + hits2[0] * 4, 'ok-fwd%d' % ff
        worst = max(worst, len(hits2))
    # neighbour anchor: a tiny/duplicated function keeps its offset from the nearest function
    # before (or after) it that DOES map uniquely; accept a+delta only if the normalized words
    # there match. Resolves identical copies (stubs, thunks) by position.
    if worst and anchor:
        import bisect
        if not hasattr(src, '_ss2'):
            src._ss2 = sorted(s for nm, s, e in src.rows)
        k = bisect.bisect_right(src._ss2, a) - 1
        for step in (-1, 1):
            j = k if step == -1 else k + 1
            for _ in range(6):
                if j < 0 or j >= len(src._ss2): break
                p = src._ss2[j]
                if p != a and abs(p - a) < 0x800:
                    mp, stp = _map_window(src, dst, p, back, fwd)
                    if mp:
                        cand = mp + (a - p)
                        ci = dst.idx(cand)
                        if 0 <= ci and dst.n[ci:ci + f] == src.n[i:i + f]:
                            return cand, 'ok-anchor'
                        break
                j += step
    return None, ('absent' if not worst else 'ambiguous(%d)' % worst)

LOADSTORE = {0x09, 0x0d, 0x19, 0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29,
             0x2a, 0x2b, 0x2c, 0x2d, 0x2e, 0x31, 0x35, 0x36, 0x37, 0x39, 0x3d, 0x3e, 0x3f}

def find_gp(img):
    """gp as crt0 sets it: a tiny constant tracker over the first instructions (lui/addiu/ori into a
    register, then either those ops on gp directly or a register move `daddu gp,rX,zero`)."""
    val = {}
    for w in img.raw[:96]:
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        imm = w & 0xFFFF
        if op == 0x0f: val[rt] = imm << 16
        elif op in (0x09, 0x0d) and rs in val:
            val[rt] = (val[rs] + ((imm - 0x10000) if (op == 0x09 and imm & 0x8000) else imm)) & 0xFFFFFFFF
        elif op == 0 and (w & 0x3F) in (0x21, 0x2d, 0x25) and ((w >> 16) & 31) == 0 and rs in val:
            val[(w >> 11) & 31] = val[rs]
        if 28 in val and op != 0x0f: return val[28]
    return val.get(28)

def operand_addrs(words, gp):
    """yield (word index, absolute address) for each lui-paired / gp-relative operand."""
    hi = {}
    for k, w in enumerate(words):
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 0x0f:
            hi[rt] = (w & 0xFFFF) << 16; continue
        if op in LOADSTORE:
            imm = w & 0xFFFF
            simm = imm - 0x10000 if (imm & 0x8000 and op != 0x0d) else imm
            if rs in hi: yield k, (hi[rs] + simm) & 0xFFFFFFFF
            elif rs == 28 and gp is not None: yield k, (gp + simm) & 0xFFFFFFFF
            if op in (0x09, 0x0d) and rt in hi and rt != rs: del hi[rt]   # rt overwritten
        elif op == 0 and (w & 0x3F) in (0x21, 0x2d, 0x25):              # addu/daddu/or rd: clobber
            hi.pop((w >> 11) & 31, None)

def build_maps(src, dst):
    """code map from matched rows + jal targets; data map from paired relocated operands."""
    from collections import Counter, defaultdict
    gs, gd = find_gp(src), find_gp(dst)
    votes = defaultdict(Counter); code = {}
    for nm, s, e in src.rows:
        ms, st = map_addr(src, dst, s)
        if not ms: continue
        i, j, n = src.idx(s), dst.idx(ms), (e - s) // 4
        if src.n[i:i + n] != dst.n[j:j + n]: continue          # body must match exactly (normalized)
        code[s] = ms
        ws, wd = src.raw[i:i + n], dst.raw[j:j + n]
        for a, b in zip(ws, wd):
            if a >> 26 == 3: votes[(a & 0x3FFFFFF) << 2][(b & 0x3FFFFFF) << 2] += 1
        da = dict(operand_addrs(ws, gs)); db = dict(operand_addrs(wd, gd))
        for k, va in da.items():
            if k in db: votes[va][db[k]] += 1
    m = {a: c.most_common(1)[0][0] for a, c in votes.items()}
    return code, m, (gs, gd)

def xlate(src, dst, maps, a):
    code, m, _ = maps
    if a in m: return m[a], 'exact'
    if src.in_function(a):                      # code: only inside a CSV function (never data)
        r, st = map_addr(src, dst, a)
        if r: return r, 'code-' + st
    keys = sorted(m)
    import bisect
    i = bisect.bisect_right(keys, a)
    lo = keys[i - 1] if i else None; hi = keys[i] if i < len(keys) else None
    dl = m[lo] - lo if lo is not None else None; dh = m[hi] - hi if hi is not None else None
    if dl is not None and dl == dh: return a + dl, 'interp(0x%x..0x%x)' % (lo, hi)
    return None, 'unknown (below 0x%x d=%s, above 0x%x d=%s)' % (lo or 0, hex(dl) if dl is not None else '-', hi or 0, hex(dh) if dh is not None else '-')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['map', 'csv', 'xlate'])
    ap.add_argument('--src', required=True); ap.add_argument('--dst', required=True)
    ap.add_argument('--prefix', default='gapfix,boundfix')
    ap.add_argument('--emit', action='store_true')
    ap.add_argument('addrs', nargs='*')
    o = ap.parse_args()
    src, dst = Img(o.src), Img(o.dst)
    if o.cmd == 'map':
        for h in o.addrs:
            a = int(h, 16); m, st = map_addr(src, dst, a)
            print('0x%06x -> %s  %s' % (a, '0x%06x' % m if m else '-', st))
        return
    if o.cmd == 'xlate':
        maps = build_maps(src, dst)
        print('# gp src=0x%x dst=0x%x; %d data/code operand pairs' % (maps[2][0], maps[2][1], len(maps[1])), file=sys.stderr)
        for h in o.addrs:
            a = int(h, 16); r, st = xlate(src, dst, maps, a)
            print('0x%06x -> %s  %s' % (a, '0x%06x' % r if r else '-', st))
        return
    pre = tuple(o.prefix.split(','))
    stats = {}
    emit = []
    for nm, s, e in src.rows:
        if not nm.startswith(pre): continue
        ms, st = map_addr(src, dst, s)
        me = None
        if ms:
            # map END as start+size; confirm the body maps contiguously (last word maps to ms+size-4)
            size = e - s
            last, st2 = map_addr(src, dst, e - 4)
            if last != ms + size - 4: st = 'size-mismatch'; ms = None
            else: me = ms + size
        if ms:
            cur = dst.starts.get(ms)
            if cur is None: st = 'ADD'
            elif cur[1] == me: st = 'present-same'
            else: st = 'REBOUND(dst end 0x%06x)' % cur[1]
        stats[st.split('(')[0]] = stats.get(st.split('(')[0], 0) + 1
        if not o.emit:
            print('%-22s 0x%06x-0x%06x -> %s  %s' % (nm, s, e, '0x%06x-0x%06x' % (ms, me) if ms else '-', st))
        elif st in ('ADD',) or st.startswith('REBOUND'):
            tag = nm.split('_')[0].rstrip('0123456789abcdef') or nm
            emit.append((ms, me, st))
    if o.emit:
        for ms, me, st in sorted(emit):
            kind = 'gapfix' if st == 'ADD' else 'boundfix'
            sys.stdout.write('%s_%x,0x%08X,0x%08X,%d\r\n' % (kind, ms, ms, me, me - ms))
    print('summary:', stats, file=sys.stderr)

if __name__ == '__main__':
    main()
