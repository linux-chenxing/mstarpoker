<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# mstarpoker

A bare-metal serial boot monitor for poking at a SoC before anything else
runs on it, plus the host tooling and bring-up scripts written around it.

The stub replaces the vendor first-stage loader: the mask ROM loads it
into on-chip SRAM, and from then on a host machine can read and write
registers and memory, upload code and run it over the console UART. It
was written to bring up the MStar/SigmaStar SSD202D in the Miyoo Mini and
to check a QEMU model of that chip against real silicon, but the monitor
and client are SoC-agnostic; see "Retargeting" in `PROTOCOL.md`.

```
  host (mstarpoker.py) <==serial==> UART <-- stub in SRAM <-- boot ROM <-- flash (flash.bin)
```

## Quick start

```sh
make                                  # stub -> stub.ipl -> flash.bin (needs an ARM cross gcc)
# write flash.bin to the SPI-NOR at offset 0, or run it in QEMU:
qemu-system-arm -M miyoomini -bios ssd202d_bootrom.bin \
    -drive if=mtd,format=raw,file=flash.bin \
    -display none -serial unix:/tmp/s.ser,server,nowait &
python3 mstarpoker.py --socket /tmp/s.ser ping          # -> pong
python3 mstarpoker.py --serial /dev/ttyUSB0 rd 0x1f203d20
```

Writing `flash.bin` to a device overwrites its stock firmware; the stub
never writes the flash itself.

## C client

`cli/` has the same client as a single C header plus a command-line tool,
in the style of the smol* tools: no dependencies, builds against libc or
as a fully static nolibc binary, and packs into a tarwak rootfs so it can
run on a small Linux box wired to the target.

```sh
make -C cli                                    # -> cli/mstarpoker
cli/mstarpoker -u /tmp/s.ser ping              # QEMU socket
cli/mstarpoker -s /dev/ttyUSB0 probe 0x1f224400
cli/mstarpoker -s /dev/ttyUSB0 load 0xa0009000 prog.bin
cli/mstarpoker -s /dev/ttyUSB0 go 0xa0009000   # ...then prints what it says
cli/mstarpoker -s /dev/ttyUSB0 spl u-boot-spl.bin u-boot.img   # the U-Boot handoff
```

Commands: `ping`, `faults`, `probe`, `rd`, `wr`, `rd8`, `wr8`, `wr16`,
`dump`, `save`, `load`, `go`, `console`, `ymodem`, `spl`. `-v` traces
every frame and every YMODEM block.

`spl` is the whole U-Boot bring-up in one invocation, so the port is
opened once: it uploads a mstarpoker-flavoured SPL to 0xa0004000, checks
it, runs it, echoes the SPL's console until it prints
`Trying to boot from UART`, then sends U-Boot over YMODEM and keeps
echoing the console. The sender is tuned to U-Boot's xyzModem receiver
(lazy ACKs, no second `C` after the header block, ACK-ACK-`C` on EOT).
`ymodem <file>` is the bare primitive for scripting.

Cross builds take `CROSS_COMPILE=<prefix>` and, for a static nolibc
build, `UAPIDIR=<the target's kernel UAPI headers>`, because nolibc
includes `<linux/types.h>` and the serial code needs `<asm/termbits.h>`
for the target architecture.

To use it from C, include `cli/mstarpoker.h` in one translation unit:

```c
struct mstarpoker m = { 0 };
mstarpoker_open_serial(&m, "/dev/ttyUSB0", 38400);
mstarpoker_sync(&m);
mstarpoker_read32(&m, 0x1f203d20, &val);
```

`mstarpoker_open_serial()` is worth knowing about on its own: it is the
termios2 `TCGETS2`/`TCSETS2` + `BOTHER` incantation that sets a raw 8N1
port to an arbitrary baud rate without the `Bxxx` constants, which is
poorly documented anywhere outside the kernel. If you only want that,
copy those twenty lines.

**Do not share the port.** The protocol has no framing and no checksum,
so a second reader on the tty makes replies come up short and the client
time out, which is the loud, safe failure. The write direction is not
safe: `w`, `W` and `B` take their arguments from whatever bytes follow,
so anything typing into a shared console can write arbitrary target
memory. One process owns the port at a time.

## Layout

| Path            | What                                                       |
|-----------------|------------------------------------------------------------|
| `stub.c`, `start.S`, `link.ld`, `Makefile` | the monitor, built to run from SRAM |
| `mkipl.py`, `mkflash.py` | wrap the stub in the ROM's loader header and a NOR image |
| `mstarpoker.py` | host client: CLI and the `Link` library                    |
| `cli/`          | the same client in C: `mstarpoker.h` single header + `mstarpoker` tool, nolibc/tarwak build |
| `socid.py`, `regdump.py` | helpers shared by the scripts                     |
| `scripts/common/` | probes for any target: boot ROM dump, PM registers, timers, PLLs |
| `scripts/ssd20x/` | SSD20x bring-up: DDR/MIU init and DRAM test, USB PHY, Miyoo Mini display and audio |
| `results/`      | QEMU baselines and real-hardware register dumps            |
| `PROTOCOL.md`   | the wire protocol, image formats, runtime caveats, retargeting |
| `VALIDATION.md` | the hardware-vs-model validation plan and its findings log |

Everything is GPL-2.0-or-later; see the SPDX tags.
