#!/usr/bin/env python3
"""Minimal VU1 microcode disassembler for PS2Recomp RE (cont.155).

Decode tables mirror ps2xRuntime/src/lib/vu/ps2_vu1_{upper,lower}.cpp exactly.

Usage:
  vu1dis.py <vu1_code.bin> [--start 0xNNN] [--end 0xNNN]          # listing
  vu1dis.py <vu1_code.bin> --writes vf28                          # who writes a reg
  vu1dis.py <vu1_code.bin> --entries                              # E-bit map (program ends)
"""
import argparse
import struct
import sys

DEST_BITS = "xyzw"


def dest_str(d):
    return "".join(DEST_BITS[i] for i in range(4) if d & (8 >> i)) or "-"


UPPER_OPS = {}
for base, name in ((0x00, "ADD"), (0x04, "SUB"), (0x08, "MADD"), (0x0C, "MSUB"),
                   (0x10, "MAX"), (0x14, "MINI"), (0x18, "MUL")):
    for bc in range(4):
        UPPER_OPS[base + bc] = (name + DEST_BITS[bc], "bc")
UPPER_OPS.update({
    0x1C: ("MULq", "q"), 0x1D: ("MAXi", "i"), 0x1E: ("MULi", "i"), 0x1F: ("MINIi", "i"),
    0x20: ("ADDq", "q"), 0x21: ("MADDq", "q"), 0x22: ("ADDi", "i"), 0x23: ("MADDi", "i"),
    0x24: ("SUBq", "q"), 0x25: ("MSUBq", "q"), 0x26: ("SUBi", "i"), 0x27: ("MSUBi", "i"),
    0x28: ("ADD", "r"), 0x29: ("MADD", "r"), 0x2A: ("MUL", "r"), 0x2B: ("MAX", "r"),
    0x2C: ("SUB", "r"), 0x2D: ("MSUB", "r"), 0x2E: ("OPMSUB", "r"), 0x2F: ("MINI", "r"),
})
UPPER_SPECIAL = {}
for base, name in ((0x00, "ADDA"), (0x04, "SUBA"), (0x08, "MADDA"), (0x0C, "MSUBA"),
                   (0x18, "MULA")):
    for bc in range(4):
        UPPER_SPECIAL[base + bc] = (name + DEST_BITS[bc], "abc")
UPPER_SPECIAL.update({
    0x10: ("ITOF0", "cv"), 0x11: ("ITOF4", "cv"), 0x12: ("ITOF12", "cv"), 0x13: ("ITOF15", "cv"),
    0x14: ("FTOI0", "cv"), 0x15: ("FTOI4", "cv"), 0x16: ("FTOI12", "cv"), 0x17: ("FTOI15", "cv"),
    0x1C: ("MULAq", "aq"), 0x1D: ("ABS", "cv"), 0x1E: ("MULAi", "ai"), 0x1F: ("CLIP", "clip"),
    0x20: ("ADDAq", "aq"), 0x21: ("MADDAq", "aq"), 0x22: ("ADDAi", "ai"), 0x23: ("MADDAi", "ai"),
    0x24: ("SUBAq", "aq"), 0x25: ("MSUBAq", "aq"), 0x26: ("SUBAi", "ai"), 0x27: ("MSUBAi", "ai"),
    0x28: ("ADDA", "ar"), 0x29: ("MADDA", "ar"), 0x2A: ("MULA", "ar"),
    0x2C: ("SUBA", "ar"), 0x2D: ("MSUBA", "ar"), 0x2E: ("OPMULA", "ar"),
    0x2F: ("NOP", "nop"), 0x30: ("MOVE?", "cv"), 0x31: ("MR32?", "cv"),
})


def dis_upper(instr):
    flags = "".join(f for bit, f in ((1 << 31, "I"), (1 << 30, "E"), (1 << 29, "M"),
                                     (1 << 28, "D"), (1 << 27, "T")) if instr & bit)
    op = instr & 0x3F
    dest = (instr >> 21) & 0xF
    ft = (instr >> 16) & 0x1F
    fs = (instr >> 11) & 0x1F
    fd = (instr >> 6) & 0x1F
    d = dest_str(dest)
    if (instr & ~0xF9FF07FF) == 0 and op == 0x3F and (instr & 0x7FF) == 0x2FF:
        body = "NOP"
    elif op < 0x30:
        name, kind = UPPER_OPS.get(op, (f"u{op:02x}", "?"))
        if kind == "bc":
            body = f"{name}.{d} vf{fd}, vf{fs}, vf{ft}{DEST_BITS[op & 3]}"
        elif kind in ("q", "i"):
            body = f"{name}.{d} vf{fd}, vf{fs}"
        else:
            body = f"{name}.{d} vf{fd}, vf{fs}, vf{ft}"
    elif 0x3C <= op <= 0x3F:
        sp = (instr & 3) | ((instr >> 4) & 0x7C)
        name, kind = UPPER_SPECIAL.get(sp, (f"sp{sp:02x}", "?"))
        if kind == "abc":
            body = f"{name}.{d} ACC, vf{fs}, vf{ft}{DEST_BITS[sp & 3]}"
        elif kind == "cv":
            body = f"{name}.{d} vf{ft}, vf{fs}"
        elif kind in ("aq", "ai"):
            body = f"{name}.{d} ACC, vf{fs}"
        elif kind == "ar":
            body = f"{name}.{d} ACC, vf{fs}, vf{ft}"
        elif kind == "clip":
            body = f"CLIP vf{fs}, vf{ft}w"
        elif kind == "nop":
            body = "NOP"
        else:
            body = f"{name} vf{fd},vf{fs},vf{ft}"
    else:
        body = f"upper?{op:02x} vf{fd},vf{fs},vf{ft}.{d}"
    return (flags + " " if flags else "") + body


LOWER_TOP = {
    0x00: "LQ", 0x01: "SQ", 0x04: "ILW", 0x05: "ISW", 0x08: "IADDIU", 0x09: "ISUBIU",
    0x10: "FCEQ", 0x11: "FCSET", 0x12: "FCAND", 0x13: "FCOR", 0x14: "FSEQ", 0x15: "FSSET",
    0x16: "FSAND", 0x17: "FSOR", 0x18: "FMEQ", 0x1A: "FMAND", 0x1B: "FMOR", 0x1C: "FCGET",
    0x20: "B", 0x21: "BAL", 0x24: "JR", 0x25: "JALR", 0x28: "IBEQ", 0x29: "IBNE",
    0x2C: "IBLTZ", 0x2D: "IBGTZ", 0x2E: "IBLEZ", 0x2F: "IBGEZ",
}
LOWER_SPECIAL = {
    0x30: "MOVE", 0x31: "MR32", 0x34: "LQI", 0x35: "SQI", 0x36: "LQD", 0x37: "SQD",
    0x38: "DIV", 0x39: "SQRT", 0x3A: "RSQRT", 0x3B: "WAITQ", 0x3C: "MTIR", 0x3D: "MFIR",
    0x3E: "ILWR", 0x3F: "ISWR", 0x40: "RNEXT", 0x41: "RGET", 0x42: "RINIT", 0x43: "RXOR",
    0x64: "MFP", 0x68: "XTOP", 0x69: "XITOP", 0x6C: "XGKICK", 0x70: "ESADD", 0x71: "ERSADD",
    0x72: "ELENG", 0x73: "ERLENG", 0x74: "EATANxy", 0x75: "EATANxz", 0x76: "ESUM",
    0x77: "ERSQRT", 0x78: "ESQRT", 0x79: "ERCPR", 0x7A: "EEXP", 0x7B: "ESIN", 0x7C: "EATAN",
    0x7E: "WAITP",
}


def s11(v):
    return v - 2048 if v & 0x400 else v


def dis_lower(instr, pc, upper_i):
    if upper_i:
        f = struct.unpack("<f", struct.pack("<I", instr))[0]
        return f"LOI 0x{instr:08x} ({f:g})"
    top = (instr >> 25) & 0x7F
    it = (instr >> 16) & 0x1F
    is_ = (instr >> 11) & 0x1F
    id_ = (instr >> 6) & 0x1F
    dest = dest_str((instr >> 21) & 0xF)
    imm11 = s11(instr & 0x7FF)
    imm15 = ((instr >> 21) & 0xF) << 11 | (instr & 0x7FF)
    if instr == 0x8000033C or instr == 0x0000033C:
        return "NOP"
    if top == 0x40:
        direct = instr & 0x3F
        if direct == 0x30:
            return f"IADD vi{id_}, vi{is_}, vi{it}"
        if direct == 0x31:
            return f"ISUB vi{id_}, vi{is_}, vi{it}"
        if direct == 0x32:
            imm5 = (instr >> 6) & 0x1F
            if imm5 & 0x10:
                imm5 -= 32
            return f"IADDI vi{it}, vi{is_}, {imm5}"
        if direct == 0x34:
            return f"IAND vi{id_}, vi{is_}, vi{it}"
        if direct == 0x35:
            return f"IOR vi{id_}, vi{is_}, vi{it}"
        sp = (instr & 3) | ((instr >> 4) & 0x7C)
        name = LOWER_SPECIAL.get(sp, f"low-sp{sp:02x}")
        if name in ("LQI", "LQD"):
            return f"{name}.{dest} vf{it}, (vi{is_})"
        if name in ("SQI", "SQD"):
            return f"{name}.{dest} vf{is_}, (vi{it})"
        if name in ("DIV", "RSQRT"):
            fsf = (instr >> 21) & 3
            ftf = (instr >> 23) & 3
            return f"{name} Q, vf{is_}{DEST_BITS[fsf]}, vf{it}{DEST_BITS[ftf]}"
        if name == "SQRT":
            ftf = (instr >> 23) & 3
            return f"SQRT Q, vf{it}{DEST_BITS[ftf]}"
        if name in ("MTIR",):
            fsf = (instr >> 21) & 3
            return f"MTIR vi{it}, vf{is_}{DEST_BITS[fsf]}"
        if name == "MFIR":
            return f"MFIR.{dest} vf{it}, vi{is_}"
        if name in ("XTOP", "XITOP"):
            return f"{name} vi{it}"
        if name == "XGKICK":
            return f"XGKICK vi{is_}"
        if name in ("MOVE", "MR32"):
            return f"{name}.{dest} vf{it}, vf{is_}"
        if name in ("ILWR", "ISWR"):
            return f"{name}.{dest} vi{it}, (vi{is_})"
        if name == "MFP":
            return f"MFP.{dest} vf{it}, P"
        return f"{name} (vi{it},vi{is_},vi{id_})"
    name = LOWER_TOP.get(top, f"low{top:02x}")
    if name in ("LQ",):
        return f"LQ.{dest} vf{it}, {imm11}(vi{is_})"
    if name in ("SQ",):
        return f"SQ.{dest} vf{is_}, {imm11}(vi{it})"
    if name in ("ILW", "ISW"):
        return f"{name}.{dest} vi{it}, {imm11}(vi{is_})"
    if name in ("IADDIU", "ISUBIU"):
        return f"{name} vi{it}, vi{is_}, 0x{imm15:x}"
    if name in ("B", "BAL"):
        tgt = (pc // 8 + 1 + imm11) * 8
        link = f" vi{it}," if name == "BAL" else ""
        return f"{name}{link} 0x{tgt:x}"
    if name in ("JR",):
        return f"JR vi{is_}"
    if name in ("JALR",):
        return f"JALR vi{it}, vi{is_}"
    if name.startswith("IB"):
        tgt = (pc // 8 + 1 + imm11) * 8
        return f"{name} vi{it}, vi{is_}, 0x{tgt:x}"
    if name.startswith("FC") or name.startswith("FS") or name.startswith("FM"):
        return f"{name} vi{it}, 0x{instr & 0xFFFFFF:x}"
    return f"{name} raw=0x{instr:08x}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binfile")
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--end", type=lambda x: int(x, 0), default=None)
    ap.add_argument("--writes", help="report pairs whose upper/lower writes this reg (e.g. vf28, vi2)")
    ap.add_argument("--entries", action="store_true", help="list E-bit locations (program ends)")
    args = ap.parse_args()

    code = open(args.binfile, "rb").read()
    end = args.end if args.end is not None else len(code)

    for pc in range(args.start, min(end, len(code) - 7), 8):
        lo, hi = struct.unpack_from("<II", code, pc)
        u = dis_upper(hi)
        l = dis_lower(lo, pc, bool(hi & (1 << 31)))
        line = f"{pc:#06x}: {hi:08x} {lo:08x}  {u:<44s} | {l}"
        if args.entries:
            if hi & (1 << 30):
                print(line + "   ; E")
            continue
        if args.writes:
            tgt = args.writes.lower()
            hit = False
            if tgt.startswith("vf"):
                n = int(tgt[2:])
                op = hi & 0x3F
                fd = (hi >> 6) & 0x1F
                ft = (hi >> 16) & 0x1F
                if op < 0x30 and fd == n:
                    hit = True
                if 0x3C <= op <= 0x3F:
                    sp = (hi & 3) | ((hi >> 4) & 0x7C)
                    if sp in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x1D) and ft == n:
                        hit = True
                if f"vf{n}," in l.split(" ", 1)[-1].split(",")[0:1][0] if False else False:
                    pass
                ltxt = l
                for w in ("LQ.", "LQI.", "LQD.", "MFIR.", "MOVE.", "MR32.", "MFP."):
                    if ltxt.startswith(w) and f"vf{n}," in ltxt:
                        hit = True
            elif tgt.startswith("vi"):
                n = int(tgt[2:])
                for w in ("IADD vi", "ISUB vi", "IADDI vi", "IAND vi", "IOR vi",
                          "IADDIU vi", "ISUBIU vi", "ILW", "ILWR", "MTIR vi", "XTOP vi",
                          "XITOP vi", "BAL vi", "JALR vi"):
                    if l.startswith(w.split(" ")[0]) and f"vi{n}" in l.split(",")[0]:
                        hit = True
            if hit:
                print(line)
            continue
        print(line)


if __name__ == "__main__":
    main()
