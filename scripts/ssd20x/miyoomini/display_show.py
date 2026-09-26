#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bring up the Miyoo Mini display pipeline and render a picture to it.

The Miyoo Mini (SSD202D) drives a 640x480 MIPI-DSI panel. From the
mstarpoker stub (loaded by the mask ROM in place of the vendor IPL),
this:

  1. brings up DDR (the framebuffer lives in DRAM);
  2. initialises the display pipeline - the panel timing generator, the
     GOP scanout plane and the backlight PWM;
  3. uploads a picture into a DRAM framebuffer and points the GOP at it.

The GOP scanout path is what puts pixels on the panel and is exactly what
the QEMU model renders, so this is verifiable end to end in emulation
(the model scans the GOP framebuffer out to its console; screendump it).

On real silicon the pipeline additionally needs the display clocks, the
LCD/MIPI pixel-clock PLL and the DSI panel's DCS init command sequence
(panel-specific). Those are pointed out below and left as a hook to fill
in per panel - they do not affect the QEMU render.

The Miyoo Mini's panel is mounted upside down, so a raw framebuffer
appears rotated 180 degrees (the vendor firmware pre-rotates its own
drawing); the generated test pattern is symmetric enough not to matter.

    display_show.py --socket /tmp/s.ser                 # generated test pattern
    display_show.py --serial /dev/ttyUSB0 --raw565 pic.bin
    display_show.py --socket /tmp/s.ser --no-ddr-init   # DRAM already up
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402
import regdump  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ddr  # noqa: E402

WIDTH, HEIGHT = 640, 480
DRAM_BASE = 0x20000000
FB_ADDR = 0x20100000            # framebuffer, 1 MiB into DRAM

# Panel timing generator (pnl) at 0x1f225200 - the 640x480 timing values
# the vendor firmware programs (captured; see docs display.rst). Needed on
# real hardware so the panel is clocked with valid timing.
PNL_BASE = 0x1f225200
PNL_TIMING = [
    (0x40, 0x0004), (0x44, 0x02e3), (0x48, 0x01f8), (0x50, 0x0003),
    (0x58, 0x0003), (0x5c, 0x0034), (0x60, 0x02b3), (0x64, 0x000f),
    (0x68, 0x01ee), (0x70, 0x0034), (0x74, 0x02b3), (0x78, 0x000f),
    (0x7c, 0x01ee), (0x80, 0x8000),
]

# GOP scanout plane at 0x1f246800 (RGB plane; see docs display.rst).
GOP_BASE = 0x1f246800
GOP_STRETCH_W = 0xc0            # crtc width >> 1
GOP_STRETCH_H = 0xc4            # crtc height
GOP_WIN0 = 0x200               # bit0 enable, bits[7:4] pixel format
GOP_WIN0_ADDRL = 0x204         # fb addr low  (16-byte units, from DRAM base)
GOP_WIN0_ADDRH = 0x208         # fb addr high
GOP_WIN0_PITCH = 0x224         # stride, 16-byte units
GOP_FMT_RGB565 = 0x1

# Backlight PWM at 0x1f003400 (see docs pwm.rst).
PWM_DUTY = 0x1f003408
PWM_PERIOD = 0x1f003410
PWM_ENABLE = 0x1f00341c


# Registers this script writes, snapshotted before/after (all plain config,
# none known to have read side effects).
DISP_REGS = (
    [(PNL_BASE + off, "pnl+0x%02x" % off) for off, _ in PNL_TIMING] +
    [(PWM_PERIOD, "pwm_period"), (PWM_DUTY, "pwm_duty"), (PWM_ENABLE, "pwm_enable"),
     (GOP_BASE + GOP_STRETCH_W, "gop_stretch_w"),
     (GOP_BASE + GOP_STRETCH_H, "gop_stretch_h"),
     (GOP_BASE + GOP_WIN0_ADDRL, "gop_win0_addrl"),
     (GOP_BASE + GOP_WIN0_ADDRH, "gop_win0_addrh"),
     (GOP_BASE + GOP_WIN0_PITCH, "gop_win0_pitch"),
     (GOP_BASE + GOP_WIN0, "gop_win0")]
)
DISP_UNSAFE = ()


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def test_pattern(w, h):
    """A recognisable picture: colour bars over a gradient, white border."""
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (0, 255, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0)]
    buf = bytearray()
    for y in range(h):
        for x in range(w):
            if x == 0 or y == 0 or x == w - 1 or y == h - 1:
                v = 0xffff
            elif y < h // 2:
                r, g, b = bars[x * len(bars) // w]
                v = rgb565(r, g, b)
            else:
                g = int(x * 255 / w)
                v = rgb565(g, g, 255 - g)
            buf += struct.pack("<H", v)
    return bytes(buf)


def upload_fb(lk, addr, data):
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    chunk = 4096
    for i in range(0, len(words), chunk):
        lk.write_block(addr + i * 4, words[i:i + chunk])
        sys.stderr.write("\r  uploading framebuffer %d%%"
                         % (100 * min(i + chunk, len(words)) // len(words)))
        sys.stderr.flush()
    sys.stderr.write("\n")


def display_init(lk):
    """Program the pipeline registers this RE has recovered."""
    print("[disp] panel timing generator")
    for off, val in PNL_TIMING:
        lk.write32(PNL_BASE + off, val)

    # Real-hardware-only, not needed for the QEMU GOP render:
    #  * ungate the display clocks (clkgen 0x1f207000: ge/disp/mop/mipi)
    #  * the LCD/MIPI pixel-clock PLL (0x1f006400/0x1f006600)
    #  * the DSI controller + D-PHY and the panel's DCS init sequence
    #    (0x1f345200 / 0x1f2a5000) - panel-specific; capture it for the
    #    panel with mstarpoker and add it here.

    print("[disp] backlight PWM on")
    lk.write32(PWM_PERIOD, 0x0fff)
    lk.write32(PWM_DUTY, 0x0800)     # ~50%
    lk.write32(PWM_ENABLE, 0x0001)


def show_image(lk, data, w, h):
    print("[disp] uploading %dx%d framebuffer (%d bytes) to 0x%08x"
          % (w, h, len(data), FB_ADDR))
    upload_fb(lk, FB_ADDR, data)

    print("[disp] programming GOP scanout")
    off = (FB_ADDR - DRAM_BASE) >> 4
    lk.write32(GOP_BASE + GOP_STRETCH_W, w >> 1)
    lk.write32(GOP_BASE + GOP_STRETCH_H, h)
    lk.write32(GOP_BASE + GOP_WIN0_ADDRL, off & 0xffff)
    lk.write32(GOP_BASE + GOP_WIN0_ADDRH, (off >> 16) & 0xfff)
    lk.write32(GOP_BASE + GOP_WIN0_PITCH, (w * 2) >> 4)
    lk.write32(GOP_BASE + GOP_WIN0, 0x1 | (GOP_FMT_RGB565 << 4))
    print("[disp] GOP enabled - the picture is now scanned out")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--raw565", help="raw little-endian RGB565 %dx%d image "
                    "(default: a generated test pattern)" % (WIDTH, HEIGHT))
    ap.add_argument("--no-ddr-init", action="store_true",
                    help="skip DDR init (DRAM already up)")
    ap.add_argument("--json",
                    help="write the display before/after register table here")
    args = ap.parse_args()

    if args.raw565:
        data = open(args.raw565, "rb").read()
        need = WIDTH * HEIGHT * 2
        if len(data) != need:
            sys.exit("--raw565 must be %d bytes (%dx%d RGB565), got %d"
                     % (need, WIDTH, HEIGHT, len(data)))
    else:
        data = test_pattern(WIDTH, HEIGHT)

    lk = open_link(args)
    if args.no_ddr_init:
        print("[ddr] skipped (--no-ddr-init)")
    elif not ddr.init(lk):
        sys.exit("[ddr] DDR init did not complete - refusing to touch DRAM "
                 "(the framebuffer lives there; a write would hang the "
                 "target). Fix DDR bring-up first (see dram_test.py).")

    before = regdump.snapshot(lk, DISP_REGS, DISP_UNSAFE)
    display_init(lk)
    show_image(lk, data, WIDTH, HEIGHT)
    after = regdump.snapshot(lk, DISP_REGS, DISP_UNSAFE)
    regdump.print_diff(before, after, "display registers around render")
    if args.json:
        regdump.write_json(args.json, before=before, after=after)
        print("[disp] wrote display register table to %s" % args.json)
    print("[done] picture rendered")


if __name__ == "__main__":
    main()
