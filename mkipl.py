#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
mkipl.py - wrap a raw ARM binary in an SSD202D IPL header.

The SSD202D mask ROM loads an "IPL" from the boot medium into IMI SRAM at
0xa0000000 and jumps to it. It accepts the image only if the header is
valid. The header, reverse-engineered from the stock IPL and the ROM
disassembly (see PROTOCOL.md), is 16 bytes at the start of the image:

    offset 0x00  u32  ARM `b 0x10` branch over the header (0xea000002)
    offset 0x04  u32  magic "IPL_" (0x5f4c5049, little-endian)
    offset 0x08  u32  image size in bytes (header + body)
    offset 0x0c  u32  checksum: sum of the 32-bit little-endian words in
                      [0x10 : size], modulo 2**32

The unsigned Miyoo IPL sets no "signed" flag, so the ROM skips the
SHA/RSA authentication and jumps straight to 0x10.

Two modes, chosen automatically:
  * if the input already starts with the header (magic at 0x04), the size
    and checksum fields are filled in in place (this is what the linked
    stub uses - its branch is computed by the linker);
  * otherwise a fresh 16-byte header is prepended to the input.
"""
import struct
import sys

MAGIC = 0x5f4c5049          # "IPL_"
BRANCH = 0xea000002         # ARM: b (pc + 8 + 8) == b 0x10
HDR = 16


def build(body_with_or_without_header: bytes) -> bytes:
    d = bytearray(body_with_or_without_header)

    have_header = (len(d) >= 8 and
                   struct.unpack_from("<I", d, 4)[0] == MAGIC)
    if not have_header:
        d = bytearray(struct.pack("<II", BRANCH, MAGIC)) + b"\0" * 8 + d

    # pad the whole image to a multiple of 4 for the word checksum
    while len(d) % 4:
        d.append(0)

    size = len(d)
    struct.pack_into("<I", d, 8, size)

    # checksum: sum of 32-bit LE words from 0x10 to end
    csum = 0
    for off in range(HDR, size, 4):
        csum = (csum + struct.unpack_from("<I", d, off)[0]) & 0xffffffff
    struct.pack_into("<I", d, 12, csum)

    # sanity: the entry word must be an unconditional ARM branch (b) over
    # the header. The linker chooses the exact target (>= 0x10, wherever
    # _start landed after alignment), so only check the opcode/condition.
    entry = struct.unpack_from("<I", d, 0)[0]
    if (entry >> 24) != 0xea:
        raise SystemExit("mkipl: entry word 0x%08x is not an ARM `b` branch; "
                         "is the input a linked stub with a header?" % entry)
    return bytes(d)


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: mkipl.py <input.bin> <output.ipl>")
    body = open(argv[1], "rb").read()
    out = build(body)
    open(argv[2], "wb").write(out)
    size, csum = struct.unpack_from("<I", out, 8)[0], struct.unpack_from("<I", out, 12)[0]
    print("mkipl: %s -> %s  size=0x%x  checksum=0x%08x" %
          (argv[1], argv[2], size, csum))


if __name__ == "__main__":
    main(sys.argv)
