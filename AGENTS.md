<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# Working on mstarpoker

Notes for people and agents picking this repository up cold. Read
`PROTOCOL.md` first; it is the contract everything else is built on.

## What this is

A bare-metal serial monitor (`stub.c` + `start.S`) that the SoC's mask ROM
loads into on-chip SRAM instead of the vendor first-stage loader, a host
client (`mstarpoker.py`) that drives it, and scripts that use the two to
probe and bring up an MStar/SigmaStar SSD202D (the Miyoo Mini) and to check
a QEMU model of that chip against real silicon. Nothing here writes flash
and nothing here needs DRAM until a script explicitly trains it.

## Build

```sh
make CROSS=<prefix>          # stub.bin -> stub.ipl -> flash.bin, plus the on-target blobs
make dis                     # disassemble the stub
make clean
```

`CROSS` defaults to `arm-none-eabi-`. Any ARM cross gcc works because every
target-side source is freestanding (`-ffreestanding -nostdlib`); a
kernel.org "nolibc" `arm-linux-gnueabi-` toolchain has been used and
produces a byte-identical `stub.bin`. `make` also builds the two blobs the
scripts upload (`scripts/ssd20x/ddr_c/ddr_init.bin`,
`scripts/common/cpuspeed_c/cpuspeed.bin`); `ddr.py` falls back to the
QEMU-only register replay if `ddr_init.bin` is missing, so build before
testing on hardware.

Python is 3, standard library only. Keep it that way: the client has to run
on whatever laptop the board is wired to.

## Run it in QEMU

The `miyoomini` machine and the SSD202D mask-ROM dump live in the MStar
QEMU tree, not here. The ROM must be passed with `-bios`; the model then
boots exactly like the silicon does and loads the IPL (our stub) from the
mtd drive.

```sh
qemu-system-arm -M miyoomini -m 128M -bios ssd202d_bootrom.bin \
    -drive if=mtd,format=raw,file=flash.bin \
    -display none -serial unix:/tmp/s.ser,server,nowait -audio none &
python3 mstarpoker.py --socket /tmp/s.ser ping                      # pong
python3 mstarpoker.py --socket /tmp/s.ser faults                    # 0
python3 scripts/ssd20x/miyoomini/usb_phy_test.py --socket /tmp/s.ser # "0 register faults"
```

`-audio none` (or an `-audiodev` wired to the bach device) is needed because
the machine instantiates the audio block. Some model builds do not seed the
bond strap, in which case `socid` reports an unknown part and
`dram_test.py` refuses to size the DRAM; that is the model, not the stub.

## Run it on hardware

1. Write `flash.bin` to the SPI-NOR at offset 0 with an external programmer.
   This replaces the stock firmware; only do it on a unit that can be
   re-flashed. The stub never writes the flash.
2. Connect uart0 at **38400 8N1** (the rate the ROM leaves it at, not
   115200) and `python3 mstarpoker.py --serial /dev/ttyUSB0 ping`.
3. Follow `VALIDATION.md`: phase 1 is read-only, phase 2 writes registers
   and trains DRAM. Power-cycle between phase 2 scripts; each assumes a
   fresh post-ROM state.

If `ping` times out, try `--verbose` (`-v`) to see the frames: a dead stub
shows as no RX at all, a wrong baud as garbage.

## Conventions

- Every file carries an SPDX `GPL-2.0-or-later` tag.
- Scripts import the client with the `sys.path.insert(...)` idiom relative
  to their own location (`scripts/common/` and `scripts/ssd20x/` are two
  levels down, `scripts/ssd20x/miyoomini/` three). Use
  `add_transport_args()` / `open_link()` for the CLI so every script takes
  the same `--socket/--serial/--baud/-v`.
- Read unknown registers with `lk.probe()` (fault-safe), never bare
  `read32()`. Take snapshots with `regdump.snapshot()` and list
  clear-on-read or FIFO registers in its `unsafe` set; a dump must not
  perturb what it dumps.
- Scripts that write registers take `--json FILE` and record before/after
  tables, so hardware runs can be diffed against `results/qemu_*.json`
  (regenerate those with `results/gen_qemu_baselines.sh`).
- Anything that touches DRAM must check `ddr.init()`'s trained flag first.
  A read of untrained DRAM bus-hangs the CPU past what the fault handler
  can catch; only a reset recovers it.
- The stub has no 16-bit access. RIU registers are 16-bit on a 4-byte
  stride: `read32` returns them in the low half, and a halfword write is
  two `write8` calls (see `write_reg` in `cpupll_test.py`).
- Hardware facts that were learned the hard way and must not be "fixed"
  back: PM timer is a fixed 12 MHz reference; writing a timer's TRIG
  disables it on silicon; MPLL is 432 MHz; replaying captured DDR writes
  cannot train real DRAM (the ZQ calibration is read-modify-write on
  analog results). The `VALIDATION.md` log has the evidence for each.
- Commit messages: plain subject and body, no trailers.

## Layout

| Path | Role |
|------|------|
| `stub.c` | the monitor's command loop (SoC-agnostic) |
| `start.S`, `link.ld` | loader header, entry, vector table, SRAM placement (SoC adapter) |
| `mkipl.py`, `mkflash.py` | IPL header (size + word checksum) and 16 MiB NOR image |
| `mstarpoker.py` | host client: `Link` library + CLI, socket and termios transports |
| `socid.py`, `regdump.py` | chip identification; register snapshots / diffs / JSON |
| `scripts/common/` | target-independent probes: boot ROM dump, PM regs, memory detect, timer / cpupll / cpuspeed blob |
| `scripts/ssd20x/` | SSD20x: DDR sequence + C trainer (`ddr_c/`), `ddr.py`, `dram_test.py`, MIU/MPLL PLL tests, USB PHY driver |
| `scripts/ssd20x/miyoomini/` | board-level: display test pattern, audio tone, USB PHY test |
| `results/` | QEMU baselines (`qemu_*`) and hardware dumps (`hw_*`) from the validation runs |

## Retargeting

Only four things are chip-specific: the UART block at the top of `stub.c`,
the SRAM addresses and loader header in `start.S` / `link.ld`, the header
format in `mkipl.py`, and the boot-medium layout in `mkflash.py`. The
protocol and the client do not change. Details in `PROTOCOL.md` §8.

## Related trees

- The QEMU `miyoomini` machine, its mask-ROM image and the mstarv7 docs
  (`bootrom.rst`, `ipl.rst`, `display.rst`, `bach.rst`) referenced from the
  comments live in the MStar QEMU tree; this repository started life as
  `contrib/mstarpoker` there.
- The U-Boot mstar port carries `splrun.py`, a consumer of `mstarpoker.py`
  that uploads a whole U-Boot SPL through the monitor and then YMODEMs
  U-Boot proper in, so a board comes all the way up over one UART with
  nothing flashed. If the `Link` API changes, that script needs updating.
