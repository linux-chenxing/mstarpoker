#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Measure the CPU clock the cpupll drives, the same way timer_test measures the
PM timer.

The PM timer is a fixed ~12 MHz reference (see timer_test.py). This uploads a
tiny blob (cpuspeed_c/cpuspeed.bin) that runs a fixed-length loop and reports
how many PM-timer ticks it took; ticks-per-loop is inversely proportional to
the CPU clock. Measure once as-is, then optionally program the cpupll (the
vendor IPL's cpupll-init sequence) and measure again to see what the PLL does.

    cpupll_test.py --serial /dev/ttyUSB0                 # measure current CPU clock
    cpupll_test.py --serial /dev/ttyUSB0 --set-cpupll    # program cpupll, re-measure
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link  # noqa: E402

BLOB = os.path.join(os.path.dirname(__file__), "cpuspeed_c", "cpuspeed.bin")
BLOB_ADDR = 0xa0008000
RESULT_ADDR = 0xa0009000

TIMER_HZ = 12000000                      # PM timer reference (measured)
TIMER0_DIVIDE = 0x1f006058               # so the cpupll lock delay is real

# The vendor IPL's cpupll-init writes (IPL 0x2c40-0x2c84), byte/halfword.
# (addr, value, width_in_bytes)
CPUPLL_SEQ = [
    (0x1f206448, 0x88, 1),
    (0x1f206449, 0x00, 1),
    (0x1f206445, 0x01, 1),
    (0x1f206584, 0x37, 1),
    (0x1f206581, 0x4b, 1),
    (0x1f206580, 0xc7, 1),
    (0x1f206540, 0x4bc7, 2),
    (0x1f206544, 0x0037, 2),
    (0x1f206588, 0x01, 1),
    (0x1f206445, 0x00, 1),
]


def write_reg(lk, addr, val, width):
    """Byte or halfword write (the stub has no 16-bit op, so split)."""
    if width == 1:
        lk.write8(addr, val)
    else:
        lk.write8(addr, val & 0xff)
        lk.write8(addr + 1, (val >> 8) & 0xff)


DONE = 0x600dcafe


def run_measure(lk):
    """Run the cpuspeed blob once; return (ticks, loops) or None if it did not
    complete (loop outran the timeout / the link desynced)."""
    lk.write32(RESULT_ADDR + 8, 0)      # clear the done marker
    lk.write32(RESULT_ADDR, 0)
    lk.go(BLOB_ADDR, timeout=15.0)
    if not lk.alive():                  # a very slow loop can overrun the FIFO
        lk.sync()
    if lk.read32(RESULT_ADDR + 8) != DONE:
        return None
    ticks = lk.read32(RESULT_ADDR)
    loops = lk.read32(RESULT_ADDR + 4)
    return ticks, loops


def report(tag, res):
    if res is None:
        print("  %-8s did NOT complete (loop outran the timeout or the link "
              "desynced) - CPU may be very slow" % tag)
        return None
    ticks, loops = res
    secs = ticks / TIMER_HZ
    ips = loops / secs if secs else 0
    # the subs/bne loop is ~1-2 cycles/iter on Cortex-A7, so the CPU clock is
    # (1-2)x the loop rate - i.e. loops/s is a lower bound on MHz. The
    # before/after ratio is exact regardless of the per-loop cycle count.
    print("  %-8s %d loops in %d ticks = %.3f ms -> %.2f M loops/s "
          "(CPU >= ~%.0f MHz)"
          % (tag, loops, ticks, secs * 1e3, ips / 1e6, ips / 1e6))
    return ips


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--set-cpupll", action="store_true",
                    help="program the IPL's cpupll init sequence between measurements")
    args = ap.parse_args()

    lk = open_link(args)
    with open(BLOB, "rb") as f:
        blob = f.read()
    lk.upload(BLOB_ADDR, blob)
    print("cpuspeed blob: %d bytes at 0x%08x\n" % (len(blob), BLOB_ADDR))

    print("CPU clock (loop timed against the 12 MHz PM timer):")
    base = report("before", run_measure(lk))

    if args.set_cpupll:
        print("\n[set] programming the cpupll init sequence...")
        for addr, val, width in CPUPLL_SEQ:
            write_reg(lk, addr, val, width)
        # the IPL waits ~3.6 ms (0x4b0 ticks at /36) for lock; be generous
        time.sleep(0.05)
        print("[set] done, re-measuring:")
        after = report("after", run_measure(lk))
        if base and after:
            print("\n=> cpupll changed the CPU clock by %.2fx" % (after / base))
    else:
        print("\n(run with --set-cpupll to program the PLL and see the change)")


if __name__ == "__main__":
    main()
