#!/usr/bin/env python3
"""Registration invariant checker for a game's override control plane.

Dispatch is address-based and the LAST registerFunction for an address WINS
(PS2Runtime::registerFunction -> replaceFunction overwrites the slot). So the meaning of
register_overrides.cpp is the ORDERED LIST of registrations, together with the guards they sit in.
Two things silently change behaviour while looking like "just moving code":

  1. MOVING A REGISTRATION LINE. Many registrations live inside a conditional
     (`if (getenv(...)) { runtime.registerFunction(...); }`). Relocating one — or relocating a call
     that installs a group — can put it under a different guard, or reorder who wins a slot.

  2. A `#define` THAT DRIFTS AWAY FROM ITS `#if`. If `#define LOTR_X 1` ends up in a different
     translation unit from `#if LOTR_X`, the macro quietly becomes 0, the guarded registration
     disappears, and a DIFFERENT hook wins that address. Any check that masks the preprocessor is
     blind to this — so this tool audits macros explicitly.

The list spans TWO files once a game has mods: applyLOTROverrides() in src/register_overrides.cpp
calls registerMods() in mods/register_mods.cpp as its last statement, and the tail is spliced in at
that call site so the audit still sees ONE ordered list.

Usage (from the ENGINE repo):
    check_registrations.py --game <game_dir> [--ref HEAD]      # diff working tree vs a git ref
    check_registrations.py --game <game_dir> --list            # dump the registration table
    check_registrations.py --game <game_dir> --dead            # registrations that can never win

Exit code 1 if the diff shows a change (so it can gate a refactor).
"""
import argparse, collections, glob, hashlib, os, re, subprocess, sys

CONTROL = "src/register_overrides.cpp"
APPLY = "applyLOTROverrides"

# The MODS TAIL. The control plane's list continues in a second TU: applyLOTROverrides() calls
# registerMods() as its last statement, and mods register there so that adding one touches no file
# in src/ (docs/overrides.md). The list did not become two lists — so this tool splices the tail in
# AT THE CALL SITE and audits one ordered list. Without that it would simply stop seeing mods:
# a mod clobbering a base hook would vanish from the diff and from --dead, which is the exact class
# of invisible-registration bug this checker exists to catch.
MODS = "mods/register_mods.cpp"
MODS_APPLY = "registerMods"
MODS_CALL = re.compile(r'(?:lotr::)?mods::' + MODS_APPLY + r'\s*\(')


# ---------- a C++-aware scanner: a naive paren/brace match walks straight through a format
# ---------- string like "[glyph] (%s)" and eats the rest of the file.
def code_mask(t):
    m = [True] * len(t)
    i, n = 0, len(t)
    while i < n:
        c = t[i]
        if c == '/' and i + 1 < n and t[i + 1] == '/':
            while i < n and t[i] != '\n':
                m[i] = False
                i += 1
        elif c == '/' and i + 1 < n and t[i + 1] == '*':
            m[i] = m[i + 1] = False
            i += 2
            while i < n and not (t[i] == '*' and i + 1 < n and t[i + 1] == '/'):
                m[i] = False
                i += 1
            if i < n - 1:
                m[i] = m[i + 1] = False
                i += 2
        elif c in '"\'':
            q = c
            m[i] = False
            i += 1
            while i < n:
                if t[i] == '\\':
                    m[i] = False
                    if i + 1 < n:
                        m[i + 1] = False
                    i += 2
                    continue
                if t[i] == q:
                    m[i] = False
                    i += 1
                    break
                m[i] = False
                i += 1
        else:
            i += 1
    return m


def match_block(t, mask, i, open_c, close_c):
    d, j = 0, i
    while j < len(t):
        if mask[j]:
            if t[j] == open_c:
                d += 1
            elif t[j] == close_c:
                d -= 1
                if d == 0:
                    return j
        j += 1
    return len(t) - 1


def func_span(t, name):
    mask = code_mask(t)
    m = re.search(r'\b' + re.escape(name) + r'\s*\([^)]*\)\s*\{', t)
    if not m:
        return None
    i = t.index('{', m.start())
    return i, match_block(t, mask, i, '{', '}')


def norm(t):
    t = re.sub(r'//.*', '', t)
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    # compile-time switches are audited separately; a `#define LOTR_x` that used to sit inside a
    # hook body is not a behavioural difference as long as its VALUE is unchanged.
    t = re.sub(r'^[ \t]*#define LOTR_\w+[^\n]*\n', '', t, flags=re.M)
    return re.sub(r'\s+', '', t)


def hook_bodies(files):
    """name -> body text, for every `void hook_*(...) { ... }` in the modules."""
    out = {}
    for path, t in files.items():
        mask = code_mask(t)
        for m in re.finditer(r'\nvoid (hook_\w+)\s*\(', t):
            i = t.index('{', m.end())
            out[m.group(1)] = t[i:match_block(t, mask, i, '{', '}') + 1]
    return out


def scan_function(control, fname, bodies, origin, where):
    """ordered [(addr, guard_depth, in_if, target, body_hash)] for one registrar function."""
    mask = code_mask(control)
    span = func_span(control, fname)
    if not span:
        sys.exit(f"error: {fname}() not found in {origin}")
    fi, fj = span
    lines = control.split('\n')
    in_if, depth = set(), 0
    for n, l in enumerate(lines):
        if re.match(r'\s*#if', l):
            depth += 1
        elif re.match(r'\s*#endif', l):
            depth = max(0, depth - 1)
        elif depth:
            in_if.add(n)

    out = []
    for m in re.finditer(r'runtime\.registerFunction\s*\(', control):
        s = m.start()
        if not (fi <= s <= fj) or not mask[s]:
            continue
        i = control.index('(', s)
        e = match_block(control, mask, i, '(', ')')
        call = control[s:e + 1]
        addr = re.search(r'\(\s*(0x[0-9A-Fa-f]+)', call).group(1).lower()
        d = 0
        for k in range(fi, s):
            if not mask[k]:
                continue
            if control[k] == '{':
                d += 1
            elif control[k] == '}':
                d -= 1
        ln = control[:s].count('\n')
        # any qualified name, however deeply nested: a mod's body is lotr::mods::<mod>::hook_*,
        # which the old two-segment pattern missed -- it then fell through to the lambda branch
        # and crashed looking for a brace that a named target does not have.
        ref = re.search(r',\s*((?:\w+::)*\w+)\s*\)$', call)
        target = ref.group(1) if ref else '<lambda>'
        fn = target.split('::')[-1]
        body = bodies.get(fn)
        if body is None and target == '<lambda>':      # still an inline lambda
            j = control.index('{', control.index(',', s))
            body = control[j:match_block(control, mask, j, '{', '}') + 1]
        elif body is None:
            # A NAMED target whose body is in a file we did not load -- say so. Assuming a lambda
            # here used to crash with `ValueError: substring not found`, which reads like a bug in
            # the control plane rather than a gap in what the tool can see.
            print(f"!! {origin}:L{ln + 1} {addr}: body of {target}() not found in the loaded sources",
                  file=sys.stderr)
            body = f"<missing:{target}>"
        out.append({'addr': addr, 'depth': d, 'if': ln in in_if, 'line': ln + 1, 'pos': s,
                    'where': where,
                    'target': target, 'hash': hashlib.sha1(norm(body).encode()).hexdigest()[:10]})
    return out


def mods_call_site(control):
    """(offset, brace depth, inside #if) of the registerMods() call in applyLOTROverrides, or None.

    The call's own guard applies to every mod: a conditional here would gate all of them at once,
    so the tail's rows inherit this depth and #if state rather than reporting their own in isolation.
    """
    mask = code_mask(control)
    span = func_span(control, APPLY)
    if not span:
        return None
    fi, fj = span
    lines = control.split('\n')
    in_if, depth = set(), 0
    for n, l in enumerate(lines):
        if re.match(r'\s*#if', l):
            depth += 1
        elif re.match(r'\s*#endif', l):
            depth = max(0, depth - 1)
        elif depth:
            in_if.add(n)
    hits = [m.start() for m in MODS_CALL.finditer(control) if fi <= m.start() <= fj and mask[m.start()]]
    if not hits:
        return None
    if len(hits) > 1:
        print(f"!! {MODS_APPLY}() is called {len(hits)} times in {APPLY}() — the tail must be installed once")
    s = hits[0]
    d = 0
    for k in range(fi, s):
        if not mask[k]:
            continue
        if control[k] == '{':
            d += 1
        elif control[k] == '}':
            d -= 1
    return s, d, control[:s].count('\n') in in_if


def registrations(files, bodies):
    """The ONE ordered list: the control plane, with the mods tail spliced in at its call site."""
    control = files[CONTROL]
    out = scan_function(control, APPLY, bodies, CONTROL, 'base')

    call = mods_call_site(control)
    if call is None:
        return out                      # no mods tail in this tree (or in this git ref)
    pos, call_depth, call_if = call
    if MODS not in files:
        # Loud, not silent: the list continues somewhere this tool cannot read, so every mod
        # registration is invisible to the audit.
        print(f"!! {APPLY}() calls {MODS_APPLY}() but {MODS} was not found — mods are NOT being checked")
        return out
    tail = scan_function(files[MODS], MODS_APPLY, bodies, MODS, 'mods')
    for r in tail:
        r['depth'] = call_depth + (r['depth'] - 1)   # a guarded call deepens every mod under it
        r['if'] = r['if'] or call_if
    at = sum(1 for r in out if r['pos'] < pos)
    return out[:at] + tail + out[at:]


def macros(files):
    out = {}
    for t in files.values():
        for m in re.finditer(r'^[ \t]*#define (LOTR_\w+)\s+(\d+)', t, re.M):
            out.setdefault(m.group(1), m.group(2))
    return out


def load(game, ref=None):
    """{relpath: text} for the control plane + every module source, from the tree or a git ref.

    Both override trees are read: src/ (the faithful base game) and mods/ (one subfolder per mod,
    optional). A mod's hook BODY lives in mods/<mod>/, while its registerFunction call stays in the
    control plane like every other -- so a checker that only read src/ would report a mod's body as
    a bare lambda and its every edit as "BODY changed"."""
    files = {}
    if ref:
        # `git ls-tree` tolerates a path that does not exist in that ref (empty output, exit 0),
        # so this still works against a commit from before mods/ existed.
        listing = subprocess.run(["git", "-C", game, "ls-tree", "-r", "--name-only", ref,
                                  "src/", "mods/"],
                                 capture_output=True, text=True).stdout.split()
        for p in listing:
            if p.endswith(('.cpp', '.h')):
                files[p] = subprocess.run(["git", "-C", game, "show", f"{ref}:{p}"],
                                          capture_output=True, text=True).stdout
    else:
        # both levels: mods/register_mods.cpp (the tail) sits at the root, the bodies in mods/<mod>/
        mods = sorted(glob.glob(f"{game}/mods/*.cpp") + glob.glob(f"{game}/mods/*.h")
                      + glob.glob(f"{game}/mods/*/*.cpp") + glob.glob(f"{game}/mods/*/*.h"))
        for p in [CONTROL] + sorted(glob.glob(f"{game}/src/*/*.cpp")
                                    + glob.glob(f"{game}/src/*/*.h")) + mods:
            rel = os.path.relpath(p, game) if p.startswith(game) else p
            files[rel] = open(os.path.join(game, rel)).read()
    return files


def dead_list(regs):
    """A registration can never win if a LATER one is unconditional (depth 1, no #if)."""
    by = collections.defaultdict(list)
    for i, r in enumerate(regs):
        by[r['addr']].append(i)
    dead = []
    for addr, idxs in by.items():
        for k, i in enumerate(idxs):
            if any(regs[j]['depth'] == 1 and not regs[j]['if'] for j in idxs[k + 1:]):
                dead.append(regs[i])
    # ordered by their place in the merged list: 'line' alone would interleave two files.
    return sorted(dead, key=lambda r: regs.index(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=os.environ.get("PS2RECOMP_GAME"), required=False)
    ap.add_argument("--ref", default="HEAD", help="git ref to compare the working tree against")
    ap.add_argument("--list", action="store_true", help="dump the registration table")
    ap.add_argument("--dead", action="store_true", help="show registrations that can never win")
    a = ap.parse_args()
    if not a.game:
        sys.exit("error: pass --game <game_dir> (or set PS2RECOMP_GAME)")

    now_files = load(a.game)
    now = registrations(now_files, hook_bodies(now_files))

    if a.list:
        print(f"{'#':>4} {'where':>5} {'line':>6} {'addr':>9} {'depth':>5} {'#if':>4}  target")
        for i, r in enumerate(now):
            print(f"{i:>4} {r['where']:>5} {r['line']:>6} {r['addr']:>9} {r['depth']:>5} "
                  f"{'yes' if r['if'] else '':>4}  {r['target']}")
        return
    if a.dead:
        d = dead_list(now)
        print(f"{len(d)} registration(s) can NEVER win — a later UNCONDITIONAL one always overwrites them:")
        for r in d:
            print(f"  {r['where']}:L{r['line']:<6} {r['addr']}  {r['target']}"
                  f"   (depth {r['depth']}{', inside #if' if r['if'] else ''})")
        return

    old_files = load(a.game, a.ref)
    old = registrations(old_files, hook_bodies(old_files))
    ok = True

    # --- preprocessor: same value, and every #if can see its macro
    om, nm = macros(old_files), macros(now_files)
    for k in sorted(set(om) | set(nm)):
        if om.get(k, '(undef)') != nm.get(k, '(undef)'):
            print(f"!! macro {k}: {a.ref}={om.get(k,'(undef)')} now={nm.get(k,'(undef)')}")
            ok = False
    for rel, t in now_files.items():
        if not rel.endswith('.cpp'):
            continue
        for k in set(re.findall(r'#if (LOTR_\w+)', t)):
            if '#include "lotr_switches.h"' not in t and f'#define {k}' not in t:
                print(f"!! {rel}: `#if {k}` but the macro is not visible in this TU (it would be 0)")
                ok = False

    # --- registrations: same sequence of (address, guard depth, body)
    if len(old) != len(now):
        print(f"!! registration COUNT {len(old)} -> {len(now)}")
        ok = False
    for i, (o, n) in enumerate(zip(old, now)):
        if o['addr'] != n['addr']:
            print(f"!! #{i}: address {o['addr']} -> {n['addr']} ({n['where']}:L{n['line']})")
            ok = False
        elif o['depth'] != n['depth'] or o['if'] != n['if']:
            print(f"!! #{i} {o['addr']}: GUARD changed (depth {o['depth']}{'/#if' if o['if'] else ''}"
                  f" -> {n['depth']}{'/#if' if n['if'] else ''}) — it may now register under a different condition")
            ok = False
        elif o['hash'] != n['hash']:
            print(f"!! #{i} {o['addr']} ({n['target']}): BODY changed")
            ok = False

    print(f"\n{'✓' if ok else '!!'} {len(now)} registrations vs {a.ref}: "
          + ("same order, same guards, same bodies; switches unchanged"
             if ok else "DIVERGENCE (see above)"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
