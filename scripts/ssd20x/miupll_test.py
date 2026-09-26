#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Configure the miupll (the DDR PLL) and hunt for its lock bit.

The miupll clocks the MIU / DDR controller. It is block 0x1f206200 - a separate
PLL from the MPLL (0x1f206000) and the cpupll (0x1f206400); do not confuse them.
Unlike the MPLL we cannot route the miupll onto a timer to measure it (no known
mux value), so the only thing we can observe on the live chip is whether it
locks. This test therefore:

  1. dumps the miupll block registers;
  2. applies the IPL's miupll config sequence (lifted from the DDR-init table,
     scripts/ssd20x/ddr_c/ddr_table.inc);
  3. dumps the block again and reports every bit that changed - a bit that goes
     0 -> 1 after the config settles, in a register we did NOT write, is a
     lock-bit candidate.

The config is all byte writes, block-relative: 0x05 mirrors the MPLL's
output-enable at 0x1f206005 (= 0), 0x0c/0x0d looks like the frequency word and
0x10/0x11 a divider.

Once a lock bit is found, re-run with --lock ADDR:BIT to time how long it takes
to set after the config, e.g. --lock 0x1f206218:0.

This only writes the PLL block - nothing is clocked against the miupll here - so
it should be safe; power-cycle if the board wedges anyway.

    miupll_test.py --serial /dev/ttyUSB0
    miupll_test.py --serial /dev/ttyUSB0 --span 0x80 --settle 0.1
    miupll_test.py --serial /dev/ttyUSB0 --lock 0x1f206218:0
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402

MIUPLL_BASE = 0x1f206200

# The IPL's miupll config, from the DDR-init table. All byte writes, offsets
# relative to MIUPLL_BASE.
MIUPLL_SEQ = [
    (0x05, 0x00),   # output-enable (cf. MPLL's 0x1f206005 = 0)
    (0x08, 0x00),
    (0x09, 0x00),
    (0x0c, 0x1e),   # frequency word, low byte
    (0x0d, 0x01),   # frequency word, high byte
    (0x10, 0x10),   # divider
    (0x11, 0x00),
]
SEQ_OFFSETS = set(off for off, _ in MIUPLL_SEQ)


def dump(lk, span):
    """Read `span` bytes of the miupll block."""
    return [lk.read8(MIUPLL_BASE + o) & 0xff for o in range(span)]


def show(tag, b):
    print("  %s:" % tag)
    for row in range(0, len(b), 16):
        cells = " ".join("%02x" % b[row + i]
                         for i in range(16) if row + i < len(b))
        print("    +0x%02x: %s" % (row, cells))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--span", type=parse_int, default=0x40,
                    help="bytes of the miupll block to scan (default 0x40)")
    ap.add_argument("--settle", type=float, default=0.05,
                    help="seconds to wait for lock after config (default 0.05)")
    ap.add_argument("--lock",
                    help="poll one bit as the lock bit, ADDR:BIT (e.g. "
                         "0x1f206218:0), and time how long until it sets")
    args = ap.parse_args()

    lk = open_link(args)

    before = dump(lk, args.span)
    print("miupll block 0x%08x, scanning %d bytes\n" % (MIUPLL_BASE, args.span))
    show("before config", before)

    print("\n[config] applying the IPL's miupll sequence:")
    for off, val in MIUPLL_SEQ:
        lk.write8(MIUPLL_BASE + off, val)
        print("    0x%08x <- 0x%02x" % (MIUPLL_BASE + off, val))

    # If we were told where the lock bit is, time it right after the writes;
    # otherwise just wait a fixed settle before re-dumping.
    if args.lock:
        addr_s, bit_s = args.lock.split(":")
        addr, bit = parse_int(addr_s), int(bit_s)
        print("\n[poll] waiting for 0x%08x bit %d to set..." % (addr, bit))
        t0 = time.time()
        for _ in range(1000):
            if lk.read8(addr) & (1 << bit):
                print("    LOCKED after %.1f ms" % ((time.time() - t0) * 1e3))
                break
            time.sleep(0.001)
        else:
            print("    never set within ~1 s - not the lock bit, or it did not "
                  "lock")
    else:
        time.sleep(args.settle)

    after = dump(lk, args.span)
    print()
    show("after config + settle", after)

    print("\n[diff] bytes that changed:")
    found = candidate = False
    for o in range(args.span):
        if before[o] == after[o]:
            continue
        found = True
        ours = o in SEQ_OFFSETS
        newly_set = after[o] & ~before[o]
        note = " (we wrote this)" if ours else ""
        if newly_set and not ours:
            note = "  <== bits 0x%02x went 0->1: LOCK-BIT CANDIDATE" % newly_set
            candidate = True
        print("    +0x%02x (0x%08x): 0x%02x -> 0x%02x%s"
              % (o, MIUPLL_BASE + o, before[o], after[o], note))
    if not found:
        print("    (nothing changed at all - writes may not be sticking)")
    elif not candidate:
        print("\n  no 0->1 bit outside our own writes in this span; try a larger"
              " --span or a longer --settle, the lock bit may be elsewhere.")


if __name__ == "__main__":
    main()
