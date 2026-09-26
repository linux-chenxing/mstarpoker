#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Play audio on the Miyoo Mini through the BACH controller.

The BACH audio block DMAs PCM out of DRAM to the codec. Its reader
(playback) sub-channel is a DRAM ring: the host fills the ring and pulses
the EN register's TRIGGER bit to queue a period; the DMA drains it to the
codec. From the mstarpoker stub this:

  1. brings up DDR (the PCM buffer lives in DRAM);
  2. programs the reader DMA (buffer address / size / trigger) and starts
     playback; and
  3. streams a tone (or a raw PCM file) to it.

Audio is 44100 Hz, signed-16-bit, stereo (the format the vendor firmware
uses and the QEMU BACH model assumes). The model plays the DMA buffer out
to QEMU's audio backend, so this is verifiable in emulation:

    qemu-system-arm -M miyoomini ... \
        -audiodev wav,id=wav0,path=out.wav -global mstar-bach.audiodev=wav0
    audio_play.py --socket /tmp/s.ser        # then check out.wav

On real silicon the audio codec (audiotop at 0x1f206800), its clocks and
the reader-channel reset are also needed; those are noted as the
real-hardware hook below and do not affect the QEMU render.

    audio_play.py --socket /tmp/s.ser --freq 440 --secs 1.5
    audio_play.py --serial /dev/ttyUSB0 --raw sound.s16   # raw S16LE stereo 44100
"""
import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from mstarpoker import add_transport_args, open_link, parse_int  # noqa: E402
import regdump  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ddr  # noqa: E402

FREQ = 44100                    # sample rate (fixed in the model / firmware)
BYTES_PER_FRAME = 4             # S16 stereo
DRAM_BASE = 0x20000000
BUF_ADDR = 0x20200000          # PCM buffer, 2 MiB into DRAM

# BACH reader (playback) sub-channel, base 0x1f2a0400. Offsets and the DMA
# address encoding are from the model / docs bach.rst.
BACH_BASE = 0x1f2a0400
RD_CTRL0 = 0x100               # channel control / interrupt flags
RD_EN = 0x104                  # addr_lo[11:0], trigger[13], init[14], en[15]
RD_ADDR = 0x108                # addr_hi[14:0]
RD_SIZE = 0x10c                # ring size, in MIU units (bytes >> 3)
RD_TRIGGER = 0x110             # bytes queued per trigger, in MIU units
RD_UNDERRUN = 0x118            # underrun threshold, in MIU units
RD_LEVEL = 0x11c               # live queued level (read-only, dynamic)
ADDR_SHIFT = 3
EN_TRIGGER = 1 << 13
EN_INIT = 1 << 14
EN_EN = 1 << 15

# The DMA address is a DRAM-bus offset (from DRAM base) in MIU units.
MAX_BYTES = 0xffff << ADDR_SHIFT   # RD_SIZE is 16 bits -> ~512 KiB, ~2.97 s

# Registers this script writes, snapshotted before/after. RD_LEVEL is a
# live counter (not a config write) so it is left out. None are known to
# have read side effects.
BACH_REGS = [
    (BACH_BASE + RD_CTRL0, "rd_ctrl0"), (BACH_BASE + RD_EN, "rd_en"),
    (BACH_BASE + RD_ADDR, "rd_addr"), (BACH_BASE + RD_SIZE, "rd_size"),
    (BACH_BASE + RD_TRIGGER, "rd_trigger"), (BACH_BASE + RD_UNDERRUN, "rd_underrun"),
]
BACH_UNSAFE = ()


def tone(freq_hz, secs, amp=0.3):
    n = int(FREQ * secs)
    n -= n % 2
    buf = bytearray()
    for i in range(n):
        s = int(amp * 32767 * math.sin(2 * math.pi * freq_hz * i / FREQ))
        buf += struct.pack("<hh", s, s)
    return bytes(buf)


def audio_init(lk):
    """Reset the reader channel. Real-hardware-only extras (not needed for
    the QEMU render): ungate the audio clock, and initialise the audiotop
    codec at 0x1f206800 (analog path, sample rate) - capture that for the
    board and add it here."""
    lk.write32(BACH_BASE + RD_CTRL0, 0)


def play(lk, pcm):
    while len(pcm) % 8:
        pcm += b"\0"
    if len(pcm) > MAX_BYTES:
        print("[audio] clamping to %d bytes (~%.1fs); single-shot buffer limit"
              % (MAX_BYTES, MAX_BYTES / (FREQ * BYTES_PER_FRAME)))
        pcm = pcm[:MAX_BYTES]

    print("[audio] uploading %d bytes of PCM to 0x%08x" % (len(pcm), BUF_ADDR))
    words = list(struct.unpack("<%dI" % (len(pcm) // 4), pcm))
    chunk = 4096
    for i in range(0, len(words), chunk):
        lk.write_block(BUF_ADDR + i * 4, words[i:i + chunk])

    miu = (BUF_ADDR - DRAM_BASE) >> ADDR_SHIFT
    size_mu = len(pcm) >> ADDR_SHIFT
    print("[audio] programming reader DMA and triggering playback")
    lk.write32(BACH_BASE + RD_ADDR, (miu >> 12) & 0x7fff)
    lk.write32(BACH_BASE + RD_SIZE, size_mu)
    lk.write32(BACH_BASE + RD_TRIGGER, size_mu)          # queue the whole buffer
    lk.write32(BACH_BASE + RD_UNDERRUN, 8)
    lk.write32(BACH_BASE + RD_EN,
               (miu & 0xfff) | EN_EN | EN_INIT | EN_TRIGGER)
    print("[audio] playing (%.2f s)" % (len(pcm) / (FREQ * BYTES_PER_FRAME)))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_transport_args(ap)
    ap.add_argument("--freq", type=float, default=1000.0,
                    help="tone frequency in Hz (default 1000)")
    ap.add_argument("--secs", type=float, default=1.0,
                    help="tone length in seconds (default 1.0)")
    ap.add_argument("--raw", help="raw little-endian S16 stereo 44100 file "
                    "to play instead of a tone")
    ap.add_argument("--no-ddr-init", action="store_true",
                    help="skip DDR init (DRAM already up)")
    ap.add_argument("--json", help="write the BACH before/after table here")
    args = ap.parse_args()

    pcm = open(args.raw, "rb").read() if args.raw else tone(args.freq, args.secs)

    lk = open_link(args)
    if args.no_ddr_init:
        print("[ddr] skipped (--no-ddr-init)")
    elif not ddr.init(lk):
        sys.exit("[ddr] DDR init did not complete - refusing to touch DRAM "
                 "(the PCM buffer lives there; a read/write would hang the "
                 "target). Fix DDR bring-up first (see dram_test.py).")

    before = regdump.snapshot(lk, BACH_REGS, BACH_UNSAFE)
    audio_init(lk)
    play(lk, pcm)
    after = regdump.snapshot(lk, BACH_REGS, BACH_UNSAFE)
    regdump.print_diff(before, after, "BACH registers around playback")
    if args.json:
        regdump.write_json(args.json, before=before, after=after)
        print("[audio] wrote BACH register table to %s" % args.json)
    print("[done] audio played")


if __name__ == "__main__":
    main()
