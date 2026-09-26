#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Test the MPLL (main system PLL) by doing the IPL's MPLL setup, routing timer[0]
onto the MPLL clock, and checking it runs at ~448 MHz.

The MPLL is the main PLL (block 0x1f206000); it feeds mpll_216m / mpll_144m /...
in the vendor clock tree and the fast timer/bus clocks. The ROM does the bulk of
its setup; the IPL's only MPLL-block write is 0x1f206005 = 0 (an output-enable /
reset-clear), done immediately before it selects the MPLL as timer[0]'s source
(clkgen 0x1f207004 = 0x30) and divides by 36. An earlier attempt that flipped
the timer source WITHOUT that 0x206005 = 0 write hung the SoC - the point of
this test is that the write is what makes the MPLL source usable.

  --divide 0    -> full MPLL rate on the timer (expect ~448 MHz)
  --divide 0x23 -> the IPL's /36 (expect ~12 MHz)

WARNING: if the MPLL is still not up after the setup, selecting it as the timer
source hangs the SoC. Power-cycle to recover.

    mpll_test.py --serial /dev/ttyUSB0
    mpll_test.py --serial /dev/ttyUSB0 --divide 0x23
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402

MPLL_EN = 0x1f206005                 # IPL writes 0 here before using the MPLL
CLKGEN_TIMER_SRC = 0x1f207004        # timer clock-source select (clkgen)
TIMER_SRC_MPLL = 0x30                # the IPL's value: select the MPLL
TIMER0 = 0x1f006040
CNT_L, CNT_H, DIV = 0x10, 0x14, 0x18


def counter(lk):
    lo = lk.read32(TIMER0 + CNT_L) & 0xffff
    hi = lk.read32(TIMER0 + CNT_H) & 0xffff
    return lo | (hi << 16)


def measure(lk, secs):
    c0 = counter(lk)
    t0 = time.time()
    time.sleep(secs)
    c1 = counter(lk)
    dt = time.time() - t0
    return (c1 - c0) & 0xffffffff, dt


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--divide", type=parse_int, default=0,
                    help="timer[0] DIVIDE (0 = full MPLL rate; 0x23 = IPL's /36)")
    ap.add_argument("--secs", type=float, default=0.3)
    args = ap.parse_args()

    lk = open_link(args)

    print("MPLL enable reg 0x%08x = 0x%02x (before)" % (MPLL_EN, lk.read8(MPLL_EN)))
    print("[setup] MPLL: 0x%08x <- 0x00 (the IPL's MPLL write), then settle"
          % MPLL_EN)
    lk.write8(MPLL_EN, 0)
    time.sleep(0.05)
    print("        0x%08x = 0x%02x (after)" % (MPLL_EN, lk.read8(MPLL_EN)))

    lk.write32(TIMER0 + DIV, args.divide)
    print("\n[route] timer[0] source <- MPLL: 0x%08x <- 0x%x  (DIVIDE=0x%x)"
          % (CLKGEN_TIMER_SRC, TIMER_SRC_MPLL, args.divide))
    print("        WARNING: hangs the SoC if the MPLL is not actually up.")
    lk.write32(CLKGEN_TIMER_SRC, TIMER_SRC_MPLL)

    c0 = counter(lk)
    c1 = counter(lk)
    print("        counter: 0x%08x -> 0x%08x  (running: %s)"
          % (c0, c1, "YES" if c1 != c0 else "NO"))
    if c1 == c0:
        print("\n=> timer frozen after routing to the MPLL - the MPLL clock is "
              "not reaching it (MPLL not up).")
        return

    ticks, dt = measure(lk, args.secs)
    rate = ticks / dt if dt else 0
    src = rate * (args.divide + 1)
    print("\n=> timer[0] RATE: %.0f Hz (%.3f MHz)" % (rate, rate / 1e6))
    print("=> MPLL clock (rate*(DIVIDE+1)) = %.3f MHz  (expect ~448 MHz)"
          % (src / 1e6))


if __name__ == "__main__":
    main()
