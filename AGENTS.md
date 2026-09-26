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

The C client in `cli/` follows the smol* single-header pattern:

```sh
make -C cli                                            # libc build, -Wall -Wextra clean
make -C cli NOLIBCDIR=<linux>/tools/include/nolibc \
            NOLIBCEXTDIR=<nolibc-extensions> TARWAK=<tarwak>   # + static binary + rootfs tar
```

Cross builds: `CROSS_COMPILE=<prefix>` and, for the static build,
`UAPIDIR=<target kernel UAPI headers>` (nolibc includes `<linux/types.h>`,
the serial code `<asm/termbits.h>`; the host's copies are the wrong
architecture). The ebazfuntime tree builds it this way for an ARM board
that drives a target over its own UART.

`cli/mstarpoker.h` is the whole client (`static inline`, include it in one
translation unit); `cli/mstarpoker.c` is the tool. Public functions are
`mstarpoker_*`, internals `__mstarpoker_*`, errors are negated
`MSTARPOKER_ERR_*`. Under nolibc there is no libc beyond what
`nolibc.h` and `nolibc-extensions` provide (the unix socket comes from the
extensions), so keep to `open/read/write/poll/ioctl/clock_gettime` and
`printf`; guard libc-only includes with `#ifndef NOLIBC` like the existing
code. Test both builds against the stub in QEMU (`-u /tmp/s.ser`) before
committing: at least `ping`, a `load`/`save` round trip, and `go` on a
program that returns and on one that faults (the monitor must come back
with `MPOK1` and `faults` must count it). `make -C cli check` round-trips
the YMODEM sender and the `spl` handoff against `cli/test/xyzmodem_mock.py`,
a mock of the stub plus U-Boot's xyzModem receiver; run it after touching
anything in the YMODEM or console paths. QEMU cannot test YMODEM (its UART
model never delivers host frames to the SPL), so the mock and real silicon
are the only oracles.

The YMODEM sender is shaped by U-Boot's xyzModem, which is not the
textbook receiver: it ACKs a block lazily (just before reading the next
header) so there is no fresh `C` after the header block and block 1 must
simply follow; on EOT it answers ACK, ACK, then `C` for the closing null
header; and it repeats `C` every ~2 s while idle. A `C` inside console
text ("CPUPLL") is not the handshake, which is why `spl` waits for the
`Trying to boot from UART` marker first and `ymodem` only accepts a `C`
with a quiet line behind it.

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
- After `mstarpoker_go()` the stub's replies to anything sent while the
  uploaded code runs arrive late, so `mstarpoker_sync()` drains the link
  after it matches a pong. Keep that: without it the next command reads a
  stale `SB01` as its reply.
- `__mstarpoker_read_some()` hands back what it has when a socket peer
  closes, and only reports `MSTARPOKER_ERR_CLOSED` when there was nothing;
  the console tail relies on that to show the target's last words.
- The port is never shared. `console` and `ymodem` do not sync (the stub
  is gone once the SPL owns the UART); everything else does.
- The option string starts with `+` so glibc's getopt does not permute:
  `go <addr> -1` must reach the command as a positional. nolibc's getopt
  never permutes and ignores the `+`.
- `mstarpoker_console()` does one `read()` per `poll()` and writes it out
  at once; do not turn it back into a fill-the-buffer read, or output
  stalls until 256 bytes have arrived (an SPL waiting for YMODEM prints
  one `C` every 2 s and would look dead).
- Commit messages: plain subject and body, no trailers.

## Layout

| Path | Role |
|------|------|
| `stub.c` | the monitor's command loop (SoC-agnostic) |
| `start.S`, `link.ld` | loader header, entry, vector table, SRAM placement (SoC adapter) |
| `mkipl.py`, `mkflash.py` | IPL header (size + word checksum) and 16 MiB NOR image |
| `mstarpoker.py` | host client: `Link` library + CLI, socket and termios transports |
| `cli/` | the client in C: `mstarpoker.h` single header, `mstarpoker` tool, nolibc + tarwak build |
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
