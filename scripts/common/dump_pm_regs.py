#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Dump the always-on (PM-domain) register banks over mstarpoker.

The PM ("always on") domain keeps power in standby; these banks hold
power-on defaults that the main domain's reset does not touch. This reads
them back as the boot ROM left them, so the values can be compared with a
model or across parts. It reads register-by-register through the
fault-safe path and skips any register listed in PM_UNSAFE (clear-on-read
status, FIFOs, ...) so the dump does not perturb the state it reports.

Best-effort bank list for the current target (MStar infinity2m / SSD202D);
edit PM_BANKS / PM_UNSAFE for other parts. Each entry is one 128-register
RIU bank.

    dump_pm_regs.py --socket /tmp/s.ser
    dump_pm_regs.py --serial /dev/ttyUSB0 -o pm_regs.txt --json pm_regs.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mstarpoker import add_transport_args, open_link  # noqa: E402
import regdump  # noqa: E402

# (base, name). One RIU bank = 128 16-bit regs on a 4-byte stride = 0x200.
PM_BANKS = [
    (0x1f001c00, "pm_clkgen  always-on clock gates (pwm/spi/ir/rtc/sar/pm_sleep)"),
    (0x1f001e00, "pm_gpio    always-on GPIO pads"),
    (0x1f002400, "rtc        second RTC (sstar,infinity-rtc)"),
    (0x1f006000, "wdt        watchdog"),
    (0x1f006800, "rtcpwc     RTC + power controller"),
    (0x1f007000, "pm_misc    chip id / boot-media strap (DID_KEY at +0x1c0)"),
]

# Absolute addresses of PM registers that must not be read (read side
# effects). None identified yet on this target; add them as they are found.
PM_UNSAFE = set()

BANK_WORDS = 0x80   # 128 registers


def _cell(e):
    if e["value"] is not None:
        return "%08x" % e["value"]
    return "--w/o---" if e["note"] and "write-only" in e["note"] else "-FAULT--"


def dump_bank(lk, base, name):
    regs = [(base + i * 4, "") for i in range(BANK_WORDS)]
    snap = regdump.snapshot(lk, regs, PM_UNSAFE)
    faults = sum(1 for e in snap if e["note"] == "fault")
    lines = ["\n== 0x%08x  %s ==%s" %
             (base, name, "   (%d faulted)" % faults if faults else "")]
    for i in range(0, BANK_WORDS, 4):
        lines.append("  0x%08x: %s" % (base + i * 4,
                     " ".join(_cell(e) for e in snap[i:i + 4])))
    return "\n".join(lines) + "\n", snap


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("-o", "--output", help="also write the text dump here")
    ap.add_argument("--json", help="write the banks as JSON here")
    args = ap.parse_args()

    lk = open_link(args)
    fh = open(args.output, "w") if args.output else None
    sections = {}
    for base, name in PM_BANKS:
        text, snap = dump_bank(lk, base, name)
        sys.stdout.write(text)
        if fh:
            fh.write(text)
        sections[name.split()[0]] = snap
    if fh:
        fh.close()
    if args.json:
        regdump.write_json(args.json, **sections)
        print("\nwrote JSON to %s" % args.json)


if __name__ == "__main__":
    main()
