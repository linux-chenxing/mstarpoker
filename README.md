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

## Layout

| Path            | What                                                       |
|-----------------|------------------------------------------------------------|
| `stub.c`, `start.S`, `link.ld`, `Makefile` | the monitor, built to run from SRAM |
| `mkipl.py`, `mkflash.py` | wrap the stub in the ROM's loader header and a NOR image |
| `mstarpoker.py` | host client: CLI and the `Link` library                    |
| `socid.py`, `regdump.py` | helpers shared by the scripts                     |
| `scripts/common/` | probes for any target: boot ROM dump, PM registers, timers, PLLs |
| `scripts/ssd20x/` | SSD20x bring-up: DDR/MIU init and DRAM test, USB PHY, Miyoo Mini display and audio |
| `results/`      | QEMU baselines and real-hardware register dumps            |
| `PROTOCOL.md`   | the wire protocol, image formats, runtime caveats, retargeting |
| `VALIDATION.md` | the hardware-vs-model validation plan and its findings log |

Everything is GPL-2.0-or-later; see the SPDX tags.
