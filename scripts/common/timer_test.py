#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sanity check of the PM timers the DDR settle delays rely on.

The infinity2m has three timers (0x1f006040, stride 0x40); the DDR-init blob
busy-waits on timer[0]'s free-running counter. If a timer is not running, or
runs at a rate we do not expect, every delay is wrong - so measure them
directly against the host clock:

  1. dump each timer's registers;
  2. confirm its counter advances; and
  3. time a known host-side interval to compute the tick rate, and how
     TIMER_DIVIDE scales it.

The model assumes a 432 MHz source (infinity2m.h); a measured ~12 MHz at
DIVIDE=0 would mean either a fixed prescaler or an unselected clock source -
this shows the truth per timer.

    timer_test.py --serial /dev/ttyUSB0                    # all timers, as-is
    timer_test.py --serial /dev/ttyUSB0 --set-divide 0x23  # apply /36 to each
    timer_test.py --serial /dev/ttyUSB0 --timer 0          # just timer[0]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402

TIMER_BASE = 0x1f006040
TIMER_STRIDE = 0x40
NUM_TIMERS = 3
CTRL, MAX_L, MAX_H, CNT_L, CNT_H, DIV = 0x00, 0x08, 0x0c, 0x10, 0x14, 0x18
CTRL_EN = 1 << 0
CTRL_TRIG = 1 << 1

# The IPL writes 0x1f207004 (clkgen) = 0x30 immediately before setting timer[0]'s
# divider - the timer clock-source select. It switches the timers off the
# always-on 12 MHz onto a faster source (candidate: 432 MHz); with DIVIDE=0x23
# (/36) that lands back at 12 MHz. --clksrc applies it so we can see the jump.
CLKGEN_TIMER_SRC = 0x1f207004
CLKGEN_TIMER_SRC_VAL = 0x30

# The MPLL (main system PLL) enable byte: reads 0x0f out of the ROM, the IPL
# clears it to 0 to enable the MPLL output. See scripts/ssd20x/mpll_test.py.
MPLL_EN = 0x1f206005

# Clock sources we have identified (source-clock Hz -> name). rate*(DIVIDE+1)
# gives the source rate; anything not near one of these is reported as unknown
# together with the clkgen source-select register value.
KNOWN_SOURCES = [
    (12000000, "12 MHz crystal"),
    (432000000, "MPLL (432 MHz)"),
]
SRC_TOLERANCE = 0.15            # +/- 15% (covers the 432-vs-448 spread + jitter)


def classify_source(src_hz):
    """Name the source clock, or None if we do not recognise the rate."""
    for hz, name in KNOWN_SOURCES:
        if abs(src_hz - hz) <= SRC_TOLERANCE * hz:
            return name
    return None


def counter(lk, base):
    """Read the 32-bit counter the way the delay routine does: low16|high16."""
    lo = lk.read32(base + CNT_L) & 0xffff
    hi = lk.read32(base + CNT_H) & 0xffff
    return lo | (hi << 16)


def measure(lk, base, secs):
    c0 = counter(lk, base)
    t0 = time.time()
    time.sleep(secs)
    c1 = counter(lk, base)
    dt = time.time() - t0
    ticks = (c1 - c0) & 0xffffffff        # tolerate 32-bit wrap
    return ticks, dt


def dump_regs(lk, base, tag):
    print("  registers %s:" % tag)
    for name, off in [("CTRL", CTRL), ("MAX_L", MAX_L), ("MAX_H", MAX_H),
                      ("CNT_L", CNT_L), ("CNT_H", CNT_H), ("DIVIDE", DIV)]:
        print("    %-6s 0x%08x = 0x%04x"
              % (name, base + off, lk.read32(base + off) & 0xffff))


def check_timer(lk, idx, secs, set_divide):
    base = TIMER_BASE + idx * TIMER_STRIDE
    print("=== timer[%d] @ 0x%08x ===" % (idx, base))
    dump_regs(lk, base, "before")

    running = counter(lk, base) != counter(lk, base)

    # Build the write list. NB do NOT write the TRIG bit: on real hardware
    # writing CTRL bit1 clears enable and stops the timer (unlike the model).
    # The divider takes effect on a running timer with a bare DIVIDE write.
    w = []
    if set_divide is not None:
        w.append((base + DIV, set_divide))
    if not running:
        # enable a frozen timer the way the ROM runs timer[0]: MAX + EN only
        w += [(base + MAX_L, 0xffff), (base + MAX_H, 0xffff),
              (base + CTRL, CTRL_EN)]
    if w:
        print("  [writing] " + ", ".join("0x%08x <- 0x%x" % (a, v)
                                         for a, v in w))
        for a, v in w:
            lk.write32(a, v)
        dump_regs(lk, base, "after write (did they stick?)")

    c0 = counter(lk, base)
    c1 = counter(lk, base)
    running = c1 != c0
    print("  counter back-to-back: 0x%08x -> 0x%08x  (running: %s)"
          % (c0, c1, "YES" if running else "NO - still frozen (clock gated?)"))
    if not running:
        print()
        return

    rates = []
    for _ in range(3):
        ticks, dt = measure(lk, base, secs)
        rates.append(ticks / dt if dt else 0)
    avg = sum(rates) / len(rates)
    div = lk.read32(base + DIV) & 0xffff
    print("  RATE: %.0f Hz (%.3f MHz)  [DIVIDE=0x%x]" % (avg, avg / 1e6, div))
    if avg > 1000:
        pre = avg * (div + 1)                       # the source-clock rate
        name = classify_source(pre)
        if name:
            print("  source clock (rate*(DIVIDE+1)) = %.3f MHz -> %s"
                  % (pre / 1e6, name))
        else:
            sel = lk.read32(CLKGEN_TIMER_SRC) & 0xffff
            print("  source clock (rate*(DIVIDE+1)) = %.3f MHz -> UNKNOWN "
                  "(clkgen 0x%08x = 0x%04x)" % (pre / 1e6, CLKGEN_TIMER_SRC, sel))
        one = 0x2ee0 / avg
        print("  one 0x2ee0-tick delay on this timer = %.3f ms" % (one * 1e3))
    print()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--set-divide", type=parse_int,
                    help="write TIMER_DIVIDE on each timer before measuring")
    ap.add_argument("--clksrc", nargs="?", type=parse_int, const=CLKGEN_TIMER_SRC_VAL,
                    help="write the IPL's timer clock-source select "
                         "(0x1f207004, default 0x30) before measuring")
    ap.add_argument("--timer", type=int, choices=range(NUM_TIMERS),
                    help="only check this timer (default: all)")
    ap.add_argument("--secs", type=float, default=0.5,
                    help="host-side interval per measurement (default 0.5)")
    args = ap.parse_args()

    lk = open_link(args)

    # MPLL status: 0x1f206005 is the enable byte (0 = output enabled). If any
    # timer below reads the MPLL rate that also confirms it is actually running.
    mpll = lk.read8(MPLL_EN)
    print("MPLL enable 0x%08x = 0x%02x -> %s\n"
          % (MPLL_EN, mpll,
             "output enabled (running if a timer reads ~432 MHz)" if mpll == 0
             else "NOT enabled (ROM default 0x0f); the MPLL is off"))

    if args.clksrc is not None:
        print("[clksrc] WARNING: selecting the fast timer source needs its PLL "
              "up; without it this can hang the SoC (power-cycle to recover).")
        lk.write32(CLKGEN_TIMER_SRC, args.clksrc)
        print("[clksrc] 0x%08x <- 0x%x (read back 0x%04x)\n"
              % (CLKGEN_TIMER_SRC, args.clksrc,
                 lk.read32(CLKGEN_TIMER_SRC) & 0xffff))
    idxs = [args.timer] if args.timer is not None else range(NUM_TIMERS)
    for i in idxs:
        check_timer(lk, i, args.secs, args.set_divide)


if __name__ == "__main__":
    main()
