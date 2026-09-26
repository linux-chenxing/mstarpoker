#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bring up the SSD202D USB UTMI PHYs from the mstarpoker stub and verify.

The stock IPL powers up and calibrates the three USB UTMI PHYs in its SRAM
phase, before Linux. This test reproduces that sequence (see mstar_usbphy)
on real silicon and dumps the UTMI/USBC registers before and after, so the
power-up/calibration can be confirmed and diffed against the QEMU baseline.

No DRAM is needed (the PHYs are not in DRAM), so this runs standalone right
after the mask ROM - no DDR bring-up required.

    usb_phy_test.py --socket /tmp/s.ser              # QEMU
    usb_phy_test.py --serial /dev/ttyUSB0            # hardware
    usb_phy_test.py --serial /dev/ttyUSB0 --json usb.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
import regdump  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mstar_usbphy as usbphy  # noqa: E402
from mstarpoker import add_transport_args, open_link  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--json",
                    help="write the before/after UTMI register table here")
    args = ap.parse_args()

    lk = open_link(args)
    faults0 = lk.fault_count()

    table = usbphy.regs()
    before = regdump.snapshot(lk, table)
    usbphy.usb_phy_init(lk)
    after = regdump.snapshot(lk, table)

    regdump.print_diff(before, after,
                       "USB UTMI PHY registers (before -> after)")
    faults = lk.fault_count() - faults0
    print("[usbphy] %d register faults during bring-up" % faults)
    if args.json:
        regdump.write_json(args.json, before=before, after=after)
        print("[usbphy] wrote register table to %s" % args.json)
    print("[done] USB PHY bring-up complete" if lk.alive()
          else "[warn] stub not responding after bring-up")


if __name__ == "__main__":
    main()
