#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mock of the mstarpoker stub + U-Boot's xyzModem receiver on a unix socket.

Stub mode answers P/F/r/w/R/W/G like the real stub. G switches to SPL mode:
prints SPL-ish console text (with a decoy 'C' in "CPUPLL"), then runs an
xyzModem-faithful YMODEM receiver: deferred ACKs (sent just before reading
the next header), 'C' every 2 s while idle, EOT -> ACK ACK 'C', final null
header -> ACK, then U-Boot-ish console text. --nak N NAKs block N once.
Exits 0 if the received file matches --expect.
"""
import os, socket, struct, sys, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument("sock"); ap.add_argument("--expect"); ap.add_argument("--nak", type=int, default=-1)
ap.add_argument("--spl-mode", action="store_true", help="start in SPL mode (no stub)")
ap.add_argument("--no-marker", action="store_true")
ap.add_argument("--give-up", type=float, default=30.0, help="seconds to wait for a header before exiting 3")
args = ap.parse_args()

def crc16(d):
    c = 0
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xffff if c & 0x8000 else (c << 1) & 0xffff
    return c

try: os.unlink(args.sock)
except FileNotFoundError: pass
ls = socket.socket(socket.AF_UNIX); ls.bind(args.sock); ls.listen(1)
c, _ = ls.accept(); c.settimeout(None)
mem = {}
def rd(n):
    out = b""
    while len(out) < n:
        d = c.recv(n - len(out))
        if not d: raise EOFError
        out += d
    return out
def rd32(): return struct.unpack("<I", rd(4))[0]

if not args.spl_mode:
    c.sendall(b"\r\nMPOK1\r\n")
    while True:
        op = rd(1)
        if op == b"P": c.sendall(b"SB01")
        elif op == b"F": c.sendall(struct.pack("<I", 0))
        elif op == b"r": a = rd32(); c.sendall(struct.pack("<I", mem.get(a, 0)))
        elif op == b"w": a = rd32(); mem[a] = rd32(); c.sendall(b"w")
        elif op == b"W":
            a = rd32(); n = rd32()
            for i in range(n): mem[a + 4*i] = rd32()
            c.sendall(b"W")
        elif op == b"G": rd32(); c.sendall(b"G"); break

# --- SPL mode: console text then the receiver
if not args.no_marker:
    c.sendall(b"\r\n\r\nU-Boot SPL 2026.07 (Jul 28 2026)\r\ncpuid: 410fc075, mstar chipid: f0\r\nCPUPLL ok\r\n[ssd202d-ddr] DONE - DRAM live @0x20000000\r\nmstar ddr memtest: PASS (128 MiB)\r\n")
    time.sleep(0.3)
    c.sendall(b"Trying to boot from UART\r\n")
    time.sleep(0.2)

c.settimeout(2.0)
tx_ack = False
received = b""; blocks = 0; file_len = None; naked = False
def get_hdr():
    """xyzModem_get_hdr: returns ('blk', seq, payload) / 'eot' / 'timeout'."""
    global tx_ack
    if tx_ack:
        c.sendall(b"\x06"); tx_ack = False
    hdr_chars = 0
    while True:
        try: b = rd(1)
        except socket.timeout: return ("timeout",)
        hdr_chars += 1
        if b in (b"\x01", b"\x02"):
            n = 128 if b == b"\x01" else 1024
            seq, nseq = rd(1)[0], rd(1)[0]
            payload = rd(n); crc = struct.unpack(">H", rd(2))[0]
            if seq + nseq != 0xff or crc != crc16(payload): return ("bad",)
            return ("blk", seq, payload)
        if b == b"\x04" and hdr_chars == 1:
            c.sendall(b"\x06"); return ("eot",)

# stream_open (the real receiver retries forever; this one gives up)
c.sendall(b"C")
t_open = time.time()
while True:
    if time.time() - t_open > args.give_up:
        sys.stderr.write("[mock] no header within %.0fs, giving up\n" % args.give_up); sys.exit(3)
    r = get_hdr()
    if r[0] == "blk" and r[1] == 0:
        name, rest = r[2].split(b"\0", 1); file_len = int(rest.split(b"\0")[0] or 0)
        sys.stderr.write("[mock] header: name=%r len=%d\n" % (name, file_len))
        tx_ack = True; break
    c.sendall(b"C")
# stream_read loop
next_blk = 1
while True:
    r = get_hdr()
    if r[0] == "blk":
        if r[1] == next_blk:
            if r[1] == args.nak and not naked:
                naked = True; sys.stderr.write("[mock] NAKing block %d once\n" % r[1]); c.sendall(b"\x15"); continue
            tx_ack = True; received += r[2]; next_blk = (next_blk + 1) & 0xff; blocks += 1
        elif r[1] == ((next_blk - 1) & 0xff):
            c.sendall(b"\x06")
        else:
            sys.stderr.write("[mock] sequence error\n"); sys.exit(2)
    elif r[0] == "eot":
        c.sendall(b"\x06"); c.sendall(b"C")          # stream_read: ACK, then 'C' for the final header
        r2 = get_hdr(); c.sendall(b"\x06")            # FINAL ACK
        break
    else:
        c.sendall(b"C")
received = received[:file_len]
sys.stderr.write("[mock] got %d blocks, %d bytes\n" % (blocks, len(received)))
c.sendall(b"Loaded %d bytes\r\n\r\nU-Boot 2026.07 (Jul 28 2026 - 14:10:00 +0000)\r\nDRAM:  128 MiB\r\n=> " % len(received))
time.sleep(0.5)
ok = args.expect and received == open(args.expect, "rb").read()
sys.stderr.write("[mock] file %s\n" % ("MATCH" if ok else "MISMATCH"))
c.close(); sys.exit(0 if ok else 1)
