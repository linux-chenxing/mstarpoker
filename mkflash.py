#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
mkflash.py - build a 16 MiB SPI-NOR image with an IPL at offset 0.

The mask ROM, strapped for SPI NOR, checks the IPL magic through the XIP
window (flash offset 4) and loads the IPL from flash offset 0. So the IPL
goes at the start of the image; the rest is padded to 16 MiB with 0xff
(the erased state of NOR flash).

    qemu-system-arm -M miyoomini -drive if=mtd,format=raw,file=flash.bin ...
"""
import sys

FLASH_SIZE = 16 * 1024 * 1024
PAD = 0xff


def build(ipl: bytes, size: int = FLASH_SIZE) -> bytes:
    if len(ipl) > size:
        raise SystemExit("mkflash: IPL (%d bytes) larger than the image (%d)"
                         % (len(ipl), size))
    return ipl + bytes([PAD]) * (size - len(ipl))


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: mkflash.py <input.ipl> <output.flash>")
    ipl = open(argv[1], "rb").read()
    open(argv[2], "wb").write(build(ipl))
    print("mkflash: %s (0x%x bytes) -> %s (0x%x, rest 0xff)" %
          (argv[1], len(ipl), argv[2], FLASH_SIZE))


if __name__ == "__main__":
    main(sys.argv)
