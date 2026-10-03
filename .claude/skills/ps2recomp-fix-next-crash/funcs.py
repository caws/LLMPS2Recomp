#!/usr/bin/env python3
"""
PS2Recomp decomp helper: inspect a game's ELF + functions.csv to diagnose and fix
the recurring "truncated function" / "missing function" problems.

A PS2Recomp CSV often has the majority of its functions declared with only their
first instruction (size 4-8), which makes ps2_recomp emit a single-instruction body
that runs the prologue, leaks stack, and never restores $ra. Correcting the size (or
adding a missing function) and doing a FULL regen build fixes the resulting crash/spin.

Boundary convention (verified against the correct entries): a function spans from its
start to its control-flow end (last `jr ra` + delay slot before the next function
start). For jr/j-first stubs that is start+8.

Usage:
  funcs.py <cmd> [--game <game_dir>] <args>

  funcs.py disasm  <hexaddr> [count]   # disassemble N instrs at an address
  funcs.py bounds  <hexaddr>           # propose correct [start,end,size]
  funcs.py scan                        # list all truncated suspects in the CSV
  funcs.py find    <hexaddr>           # is this addr in the CSV? which entry?

Game resolution (in order): --game/-g <dir>, $PS2RECOMP_GAME, or the current dir if
it contains recomp/config.toml. The ELF path is read from recomp/config.toml `input`;
the CSV is recomp/functions.csv. Load segments are parsed from the ELF (no hardcoding).
"""
import sys, os, re, struct, bisect

REG = ['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7',
       's0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']

def die(msg):
    sys.stderr.write(msg + "\n"); sys.exit(2)

def resolve_game(game_dir):
    if game_dir is None:
        game_dir = os.environ.get('PS2RECOMP_GAME')
    if game_dir is None and os.path.exists('recomp/config.toml'):
        game_dir = '.'
    if game_dir is None:
        die("no game dir: pass --game <dir>, set $PS2RECOMP_GAME, or run from a game repo")
    cfg = os.path.join(game_dir, 'recomp', 'config.toml')
    if not os.path.exists(cfg):
        die(f"not a game repo (missing {cfg})")
    elf_rel = None
    with open(cfg) as f:
        for line in f:
            m = re.match(r'\s*input\s*=\s*"([^"]+)"', line)
            if m:
                elf_rel = m.group(1); break
    if not elf_rel:
        die(f"could not read 'input' from {cfg}")
    elf = os.path.normpath(os.path.join(game_dir, elf_rel))
    csv = os.path.join(game_dir, 'recomp', 'functions.csv')
    return elf, csv

def load_elf(elf):
    with open(elf, 'rb') as f:
        data = f.read()
    if data[:4] != b'\x7fELF':
        die(f"{elf} is not an ELF")
    # ELF32 LE program headers (EE is 32-bit MIPS LE). Collect PT_LOAD segments.
    e_phoff     = struct.unpack_from('<I', data, 0x1c)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x2a)[0]
    e_phnum     = struct.unpack_from('<H', data, 0x2c)[0]
    segs = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        if struct.unpack_from('<I', data, o)[0] != 1:   # PT_LOAD
            continue
        p_offset = struct.unpack_from('<I', data, o + 4)[0]
        p_vaddr  = struct.unpack_from('<I', data, o + 8)[0]
        p_filesz = struct.unpack_from('<I', data, o + 16)[0]
        segs.append((p_vaddr, p_offset, p_filesz))
    if not segs:
        die(f"{elf}: no PT_LOAD segments found")
    return data, segs

def word_at(data, segs, a):
    for va, off, sz in segs:
        if va <= a < va + sz:
            return struct.unpack_from('<I', data, off + (a - va))[0]
    return None

def dis(w):
    if w is None: return '??? (outside code segment)'
    op=(w>>26)&0x3f; rs=(w>>21)&0x1f; rt=(w>>16)&0x1f; rd=(w>>11)&0x1f
    imm=w&0xffff; simm=imm-0x10000 if imm>=0x8000 else imm; sa=(w>>6)&0x1f; func=w&0x3f
    if w==0: return 'nop'
    if op==0:
        if func==0x08: return f'jr {REG[rs]}'
        if func==0x09: return f'jalr {REG[rd]},{REG[rs]}'
        if func==0x21: return f'addu {REG[rd]},{REG[rs]},{REG[rt]}'
        if func==0x2d: return f'daddu {REG[rd]},{REG[rs]},{REG[rt]}'
        if func==0x25: return f'or {REG[rd]},{REG[rs]},{REG[rt]}'
        if func==0x00: return f'sll {REG[rd]},{REG[rt]},{sa}'
        if func==0x10: return f'mfhi {REG[rd]}'
        if func==0x12: return f'mflo {REG[rd]}'
        return f'spec.{func:02x} {REG[rd]},{REG[rs]},{REG[rt]}'
    if op==0x02: return f'j 0x{((w&0x3ffffff)<<2):x}'
    if op==0x03: return f'jal 0x{((w&0x3ffffff)<<2):x}'
    if op==0x01: return f'regimm rt={rt} rs={REG[rs]} off={simm*4:+d}'
    if op==0x04: return f'beq {REG[rs]},{REG[rt]},{simm*4:+d}'
    if op==0x05: return f'bne {REG[rs]},{REG[rt]},{simm*4:+d}'
    if op==0x08: return f'addi {REG[rt]},{REG[rs]},{simm}'
    if op==0x09: return f'addiu {REG[rt]},{REG[rs]},{simm}'
    if op==0x0f: return f'lui {REG[rt]},0x{imm:x}'
    if op==0x23: return f'lw {REG[rt]},{simm}({REG[rs]})'
    if op==0x2b: return f'sw {REG[rt]},{simm}({REG[rs]})'
    return f'op{op:02x} {REG[rt]},{simm}({REG[rs]})'

def is_prologue(w):  # addiu sp,sp,-N
    return w is not None and (w & 0xffff0000)==0x27bd0000 and (w & 0xffff)>=0x8000
def is_return(w):    # jr ra
    return w==0x03e00008
def is_uncond_jump(w):  # j target
    return w is not None and (w>>26)==0x02

def load_csv(csv):
    rows=[]
    with open(csv) as f:
        next(f)
        for line in f:
            p=line.strip().split(',')
            if len(p)<4: continue
            try: rows.append((p[0], int(p[1],16), int(p[2],16), int(p[3])))
            except: pass
    return rows

def next_start(starts, a):
    i=bisect.bisect_right(starts, a)
    return starts[i] if i<len(starts) else None

def cmd_disasm(elf, csv, args):
    data, segs = load_elf(elf)
    a=int(args[0],16); n=int(args[1]) if len(args)>1 else 12
    for i in range(n):
        x=a+i*4; w=word_at(data,segs,x)
        print(f'  {x:08x}: {0 if w is None else w:08x}  {dis(w)}')

def cmd_bounds(elf, csv, args):
    data, segs = load_elf(elf); a=int(args[0],16)
    rows=load_csv(csv); starts=sorted(r[1] for r in rows)
    cap=next_start(starts,a) or (a+0x800)
    w0=word_at(data,segs,a)
    if is_return(w0) or is_uncond_jump(w0):
        end=a+8; kind='jr/j-first stub'
    else:
        last=None
        for x in range(a, min(cap, a+0x2000), 4):
            if is_return(word_at(data,segs,x)): last=x+8
        end=min(last,cap) if last else cap
        kind='control-flow end'
    print(f'addr   0x{a:06X}')
    print(f'first  {w0:08x}  {dis(w0)}')
    print(f'cap    0x{cap:06X} (next CSV start)')
    print(f'-> proposed: 0x{a:06X},0x{end:06X},{end-a}   ({kind})')

def cmd_scan(elf, csv, args):
    data, segs = load_elf(elf); rows=load_csv(csv)
    sus=0
    for name,s,e,sz in rows:
        w0=word_at(data,segs,s)
        if is_prologue(w0) and sz<=12:
            real=None
            for x in range(s,s+0x400,4):
                if is_return(word_at(data,segs,x)): real=x+8; break
            print(f'{name:<18} 0x{s:06X} declSize={sz:<4} realEnd={("0x%06X"%real) if real else "?"}')
            sus+=1
    print(f'\n{sus} truncated suspects (prologue present but size<=12)')

def cmd_find(elf, csv, args):
    a=int(args[0],16); rows=load_csv(csv)
    for name,s,e,sz in rows:
        if s<=a<e:
            print(f'0x{a:06X} is inside {name} (0x{s:06X}-0x{e:06X}, size {sz})')
            if s!=a: print('  NOTE: not the function start -> caller may jump mid-function, or boundary wrong')
            return
    print(f'0x{a:06X} is NOT covered by any CSV entry (missing function)')

CMDS={'disasm':cmd_disasm,'bounds':cmd_bounds,'scan':cmd_scan,'find':cmd_find}

def main():
    argv = sys.argv[1:]
    # pull out --game/-g <dir> from anywhere in argv
    game_dir=None; rest=[]; i=0
    while i < len(argv):
        if argv[i] in ('--game','-g'):
            if i+1>=len(argv): die("--game needs a directory")
            game_dir=argv[i+1]; i+=2; continue
        rest.append(argv[i]); i+=1
    if not rest or rest[0] not in CMDS:
        print(__doc__); sys.exit(1)
    elf, csv = resolve_game(game_dir)
    CMDS[rest[0]](elf, csv, rest[1:])

if __name__=='__main__':
    main()
