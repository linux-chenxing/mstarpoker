#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
mstarpoker.py - host-side client for the mstarpoker serial monitor.

Talks the small binary protocol served by the stub (see PROTOCOL.md) over
either a QEMU unix socket (``--socket``) or a real serial port
(``--serial``, 38400 8N1). Uses only the standard library, so it runs on
the target host without pyserial.

Library use:

    from mstarpoker import Link
    lk = Link.open_serial("/dev/ttyUSB0")
    lk.sync()
    print(hex(lk.read32(0x1f206548)))
    lk.dump(0x1f206400, 0x80)

CLI:

    mstarpoker.py --socket /tmp/x.ser ping
    mstarpoker.py --serial /dev/ttyUSB0 rd 0x1f206548
    mstarpoker.py --serial /dev/ttyUSB0 dump 0x1f206400 128
    mstarpoker.py --serial /dev/ttyUSB0 wr 0x1f003408 0x400
    mstarpoker.py --serial /dev/ttyUSB0 load 0xa0009000 prog.bin
    mstarpoker.py --serial /dev/ttyUSB0 go 0xa0009000
"""
import argparse
import os
import socket
import struct
import sys
import time


class Transport:
    """A byte pipe: send(bytes), recv(n) with a deadline, close()."""
    verbose = False

    def send(self, data):
        raise NotImplementedError

    def recv(self, n, timeout=1.0):
        raise NotImplementedError

    def close(self):
        pass

    def _trace(self, arrow, data):
        """Dump a frame to stderr when --verbose is on."""
        if not self.verbose or not data:
            return
        hx = " ".join("%02x" % b for b in data)
        # the first byte of a TX frame is the command letter
        ann = " '%c'" % data[0] if arrow == "TX" and 32 <= data[0] < 127 else ""
        sys.stderr.write("    %s %3d%-4s %s\n" % (arrow, len(data), ann, hx))


class SocketTransport(Transport):
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.s.settimeout(0.2)

    def send(self, data):
        self._trace("TX", data)
        self.s.sendall(data)

    def recv(self, n, timeout=1.0):
        out = b""
        end = time.time() + timeout
        while len(out) < n and time.time() < end:
            try:
                d = self.s.recv(n - len(out))
                if d:
                    out += d
            except OSError:
                pass
        self._trace("RX", out)
        return out

    def close(self):
        self.s.close()


class SerialTransport(Transport):
    """Raw serial via termios - no pyserial dependency."""
    def __init__(self, dev, baud=38400):
        import termios
        self.fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attr = termios.tcgetattr(self.fd)
        # raw 8N1
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attr
        speed = getattr(termios, "B%d" % baud)
        cflag = termios.CS8 | termios.CLOCAL | termios.CREAD
        iflag = 0
        oflag = 0
        lflag = 0
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def send(self, data):
        self._trace("TX", data)
        n = 0
        while n < len(data):
            try:
                n += os.write(self.fd, data[n:])
            except BlockingIOError:
                time.sleep(0.001)

    def recv(self, n, timeout=1.0):
        out = b""
        end = time.time() + timeout
        while len(out) < n and time.time() < end:
            try:
                d = os.read(self.fd, n - len(out))
                if d:
                    out += d
                else:
                    time.sleep(0.001)
            except (BlockingIOError, OSError):
                time.sleep(0.001)
        self._trace("RX", out)
        return out

    def close(self):
        os.close(self.fd)


class Link:
    """The mstarpoker monitor protocol."""
    def __init__(self, transport):
        self.t = transport

    @classmethod
    def open_socket(cls, path):
        return cls(SocketTransport(path))

    @classmethod
    def open_serial(cls, dev, baud=38400):
        return cls(SerialTransport(dev, baud))

    # --- framing helpers -------------------------------------------------
    def _need(self, n, timeout=2.0):
        d = self.t.recv(n, timeout)
        if len(d) != n:
            raise IOError("short read: wanted %d, got %d (%r)" % (n, len(d), d))
        return d

    # --- protocol commands ----------------------------------------------
    def sync(self, tries=50):
        """Ping until the stub answers 'SB01', tolerating boot-time noise."""
        for _ in range(tries):
            self.t.send(b"P")
            r = self.t.recv(4, 0.2)
            if r == b"SB01":
                return True
            # the reply may be mixed with the ROM banner / partial - resync
            if b"SB01" in self.t.recv(64, 0.1) + r:
                return True
        raise IOError("no response from stub (is it running?)")

    def ping(self):
        self.t.send(b"P")
        return self._need(4) == b"SB01"

    def alive(self, timeout=0.5):
        """Non-raising liveness check: True if the stub answers the ping."""
        try:
            self.t.send(b"P")
            return self.t.recv(4, timeout) == b"SB01"
        except OSError:
            return False

    def fault_count(self):
        """Number of faults (bad pokes / crashes) the stub has caught."""
        self.t.send(b"F")
        return struct.unpack("<I", self._need(4))[0]

    def probe(self, addr):
        """Read a word and report whether the access faulted.

        Returns (value, faulted). The value is meaningless when faulted is
        True (the faulting load was skipped), but the stub stays alive."""
        before = self.fault_count()
        val = self.read32(addr)
        return val, self.fault_count() != before

    def read32(self, addr):
        self.t.send(b"r" + struct.pack("<I", addr))
        return struct.unpack("<I", self._need(4))[0]

    def write32(self, addr, val):
        self.t.send(b"w" + struct.pack("<II", addr, val & 0xffffffff))
        return self._need(1) == b"w"

    def read_block(self, addr, nwords):
        self.t.send(b"R" + struct.pack("<II", addr, nwords))
        data = self._need(4 * nwords, timeout=2.0 + nwords * 0.001)
        return list(struct.unpack("<%dI" % nwords, data))

    def write_block(self, addr, words):
        payload = struct.pack("<II", addr, len(words))
        payload += b"".join(struct.pack("<I", w & 0xffffffff) for w in words)
        self.t.send(b"W" + payload)
        return self._need(1) == b"W"

    def read8(self, addr):
        self.t.send(b"b" + struct.pack("<I", addr))
        return self._need(1)[0]

    def write8(self, addr, val):
        self.t.send(b"B" + struct.pack("<I", addr) + bytes([val & 0xff]))
        return self._need(1) == b"B"

    # A single block write of more than a few KiB overruns the stub's / the
    # transport's buffers, so split large transfers into this many words.
    CHUNK_WORDS = 256

    def upload(self, addr, data):
        """Upload raw bytes (padded to a word) via chunked block writes."""
        if len(data) % 4:
            data = data + b"\0" * (4 - len(data) % 4)
        words = list(struct.unpack("<%dI" % (len(data) // 4), data))
        for i in range(0, len(words), self.CHUNK_WORDS):
            if not self.write_block(addr + i * 4, words[i:i + self.CHUNK_WORDS]):
                return False
        return True

    def go(self, addr, timeout=1.0):
        """Call addr (bit0 selects Thumb). Returns any text until it settles.

        Uploaded code that busy-waits (e.g. DDR settle delays) can take a while
        to return; raise timeout for those."""
        self.t.send(b"G" + struct.pack("<I", addr))
        return self.t.recv(64, timeout)

    # --- convenience -----------------------------------------------------
    def dump(self, addr, nwords, out=sys.stdout):
        vals = self.read_block(addr, nwords)
        for i in range(0, nwords, 4):
            row = vals[i:i + 4]
            out.write("0x%08x: %s\n" % (addr + i * 4,
                      " ".join("%08x" % v for v in row)))


def parse_int(s):
    return int(s, 0)


def add_transport_args(ap):
    """Add --socket/--serial/--baud to a parser (shared by the scripts)."""
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--socket", help="QEMU unix serial socket")
    g.add_argument("--serial", help="serial device, e.g. /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="dump every frame exchanged with the stub to stderr")


def open_link(args):
    """Open and sync a Link from parsed add_transport_args() options."""
    if args.socket:
        lk = Link.open_socket(args.socket)
    else:
        lk = Link.open_serial(args.serial, args.baud)
    # set before sync() so the handshake itself is traced - that is exactly
    # where a dead/absent stub shows up ("no response from stub").
    lk.t.verbose = getattr(args, "verbose", False)
    lk.sync()
    return lk


def main():
    ap = argparse.ArgumentParser(description="mstarpoker serial monitor client")
    add_transport_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("faults")
    p = sub.add_parser("probe"); p.add_argument("addr", type=parse_int)
    p = sub.add_parser("rd"); p.add_argument("addr", type=parse_int)
    p = sub.add_parser("wr"); p.add_argument("addr", type=parse_int); p.add_argument("val", type=parse_int)
    p = sub.add_parser("dump"); p.add_argument("addr", type=parse_int); p.add_argument("nwords", type=parse_int)
    p = sub.add_parser("load"); p.add_argument("addr", type=parse_int); p.add_argument("file")
    p = sub.add_parser("go"); p.add_argument("addr", type=parse_int)
    args = ap.parse_args()

    lk = open_link(args)

    if args.cmd == "ping":
        print("pong" if lk.ping() else "no response")
    elif args.cmd == "faults":
        print("fault count: %d" % lk.fault_count())
    elif args.cmd == "probe":
        val, faulted = lk.probe(args.addr)
        if faulted:
            print("0x%08x: FAULTED (access aborted, stub still alive)" % args.addr)
        else:
            print("0x%08x: 0x%08x" % (args.addr, val))
    elif args.cmd == "rd":
        print("0x%08x: 0x%08x" % (args.addr, lk.read32(args.addr)))
    elif args.cmd == "wr":
        lk.write32(args.addr, args.val)
        print("wrote 0x%08x = 0x%08x" % (args.addr, args.val))
    elif args.cmd == "dump":
        lk.dump(args.addr, args.nwords)
    elif args.cmd == "load":
        data = open(args.file, "rb").read()
        lk.upload(args.addr, data)
        print("loaded %d bytes at 0x%08x" % (len(data), args.addr))
    elif args.cmd == "go":
        print(lk.go(args.addr).decode("latin1"), end="")
    lk.t.close()


if __name__ == "__main__":
    main()
