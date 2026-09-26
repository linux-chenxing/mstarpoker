#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Dump the mask boot ROM over mstarpoker.

The mask ROM stays mapped at low physical addresses after it hands off to
the IPL, so the stub can read it straight back. On the current target (and
the QEMU model) it is at physical 0x0; adjust --base/--size for other
parts. Written to a file for offline analysis (disassembly, diffing).

    dump_bootrom.py --socket /tmp/s.ser -o bootrom.bin
    dump_bootrom.py --serial /dev/ttyUSB0 --size 0x8000
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--base", type=parse_int, default=0x0,
                    help="ROM base address (default 0x0)")
    ap.add_argument("--size", type=parse_int, default=0x4000,
                    help="bytes to read (default 16 KiB)")
    ap.add_argument("-o", "--output", default="bootrom.bin",
                    help="output file (default bootrom.bin)")
    args = ap.parse_args()

    if args.size % 4:
        ap.error("--size must be a multiple of 4")

    lk = open_link(args)
    before = lk.fault_count()

    data = bytearray()
    chunk = 256                                     # words per block read
    while len(data) < args.size:
        n = min(chunk, (args.size - len(data)) // 4)
        words = lk.read_block(args.base + len(data), n)
        data += struct.pack("<%dI" % n, *words)
        sys.stderr.write("\r  read 0x%05x / 0x%05x" % (len(data), args.size))
        sys.stderr.flush()
    sys.stderr.write("\n")

    faults = lk.fault_count() - before
    open(args.output, "wb").write(bytes(data))
    print("wrote %d bytes from 0x%08x to %s" %
          (args.size, args.base, args.output))
    if faults:
        print("warning: %d read(s) faulted - some of the dump may be "
              "invalid (ROM smaller than --size?)" % faults)


if __name__ == "__main__":
    main()
