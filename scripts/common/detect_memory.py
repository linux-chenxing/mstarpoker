#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Detect the chip and its bonded in-package memory over mstarpoker.

MStar infinity2m parts bond the DDR die into the package; the chiptop
"bond" strap identifies the package (and with it the DRAM size), and the
chip-version register gives the revision. Both are pure ID reads - no
DRAM/MIU init needed - so they work straight from the stub.

Identification lives in the reusable socid library.

    detect_memory.py --socket /tmp/s.ser
    detect_memory.py --serial /dev/ttyUSB0
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link  # noqa: E402
import socid  # noqa: E402
import regdump  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--json", help="write the ID-register table to this file")
    args = ap.parse_args()

    lk = open_link(args)
    soc = socid.identify(lk)
    if soc.faulted:
        sys.exit("an ID register faulted - wrong register base for this part?")
    print(socid.format_id(soc))

    # ID registers are read-only straps (no updates, no read side effects).
    snap = regdump.snapshot(lk, [(socid.BOND_REG, "bond strap"),
                                 (socid.CHIP_VER_REG, "chip version")])
    regdump.print_snapshot(snap, "ID registers")
    if args.json:
        regdump.write_json(args.json, registers=snap)
        print("wrote ID-register table to %s" % args.json)


if __name__ == "__main__":
    main()
