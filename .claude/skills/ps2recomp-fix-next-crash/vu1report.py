#!/usr/bin/env python3
"""One-shot RE report over a VU1 snapshot (cont.155).

For each given program start pc (masked to 0x3FF8): disassemble the first N pairs,
collect absolute LQ/ILW reads (base vi0), then dump those VU-data qwords.

Usage: vu1report.py <snapdir> 0x1e8 0x180 0x10 ...
"""
import re
import struct
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    snapdir = sys.argv[1]
    pcs = [int(x, 0) & 0x3FF8 for x in sys.argv[2:]]
    code = os.path.join(snapdir, "vu1_code.bin")
    data = open(os.path.join(snapdir, "vu1_data.bin"), "rb").read()

    abs_reads = set()
    for pc in pcs:
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "vu1dis.py"), code,
             "--start", hex(pc), "--end", hex(pc + 0x140)],
            capture_output=True, text=True).stdout
        print(f"===== program @{pc:#x} =====")
        print(out)
        for m in re.finditer(r"(?:LQ|ILW)\.\w+ (?:vf|vi)\d+, (-?\d+)\(vi0\)", out):
            addr = int(m.group(1))
            if 0 <= addr < 1024:
                abs_reads.add(addr)

    if abs_reads:
        print("===== absolute VU-data reads (vi0-based) =====")
        for qw in sorted(abs_reads):
            off = qw * 16
            w = struct.unpack_from("<4I", data, off)
            f = struct.unpack_from("<4f", data, off)
            print(f"  qw{qw:#05x}: " + " ".join(f"{x:08x}" for x in w) +
                  "   " + " ".join(f"{x:.5g}" for x in f))


if __name__ == "__main__":
    main()
