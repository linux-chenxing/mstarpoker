<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# mstarpoker — a bare-metal serial boot monitor

A tiny stub that a SoC's boot ROM loads and runs, giving a host machine a
serial protocol to **read and write registers and memory, upload code and
run it** on real silicon — to confirm hardware reset/default register
values (e.g. against a QEMU model) and probe unknown hardware.

The monitor and the host client are **SoC-agnostic**. Only a small
"target adapter" is chip-specific: the loader header the boot ROM expects
(`start.S` + `mkipl.py`) and the console-UART address (a config block in
`stub.c`). Retargeting to another chip is normally just those.

The current target is an **MStar/SigmaStar infinity2m** SoC (e.g. the
SSD202D on the Miyoo Mini). The whole chain is validated end-to-end in
the QEMU `miyoomini` machine, which runs the *real* mask-ROM dump — so if
the ROM accepts and runs the stub in emulation, it behaves the same on
silicon.

```
  host (mstarpoker.py) <==serial==> UART <-- stub in SRAM <-- boot ROM <-- flash (flash.bin)
```

## 1. Build

Needs an ARM cross toolchain (`gcc-arm-none-eabi` +
`binutils-arm-none-eabi`, or any `arm-*-gcc` - the code is freestanding)
and Python 3.

```
make                                   # -> stub.elf, stub.bin, stub.ipl, flash.bin
make CROSS=/opt/gcc/bin/arm-linux-gnueabi-   # a differently-named toolchain
make dis                               # disassemble the stub
make clean
```

`flash.bin` is a 16 MiB SPI-NOR image with the stub as its IPL. Write it
to the device's SPI-NOR at offset 0 (external programmer, or any method
that can write the flash), or pass it to QEMU:

```
qemu-system-arm -M miyoomini -bios ssd202d_bootrom.bin \
    -drive if=mtd,format=raw,file=flash.bin \
    -display none -serial unix:/tmp/s.ser,server,nowait
```

The `miyoomini` machine and the SSD202D mask-ROM dump it boots from live
in the MStar QEMU tree (the ROM is `pc-bios/ssd202d_bootrom.bin` there).

> Writing `flash.bin` overwrites the stock firmware; use a device you are
> happy to re-flash. The stub does not write the flash itself.

## 2. Protocol

Host-driven request/response over the serial link (below). Each command
is one ASCII opcode byte followed by fixed little-endian arguments; the
stub replies with exactly the bytes implied by the command (no length
framing — the host knows the shape of each reply). Addresses and values
are 32-bit little-endian. **Unknown opcode bytes are ignored**, which
makes `P` a safe resync.

| Opcode     | Args                  | Reply                                     | Meaning                                  |
|:----------:|-----------------------|-------------------------------------------|------------------------------------------|
| `P` (0x50) | —                     | `"SB01"` (4 B)                            | ping / sync / liveness check             |
| `F` (0x46) | —                     | count(4)                                  | number of faults caught so far (see §4)  |
| `r` (0x72) | addr(4)               | value(4)                                  | read one 32-bit word                     |
| `w` (0x77) | addr(4) val(4)        | `'w'` (1 B)                               | write one 32-bit word                    |
| `R` (0x52) | addr(4) n(4)          | n×value(4)                                | read a block of n words (addr += 4 each) |
| `W` (0x57) | addr(4) n(4) n×val(4) | `'W'` (1 B)                               | write a block of n words                 |
| `b` (0x62) | addr(4)               | value(1)                                  | read one byte                            |
| `B` (0x42) | addr(4) val(1)        | `'B'` (1 B)                               | write one byte                           |
| `G` (0x47) | addr(4)               | `'G'`, then if it returns `"\r\nRET\r\n"` | call addr (bit 0 selects ARM/Thumb)      |

Word accesses use 32-bit `ldr`/`str`. RIU-style 16-bit registers on a
4-byte stride read back in the low 16 bits; use `b`/`B` for byte-wide
registers.

`G` calls the address like a function (via `blx`, so bit 0 picks
ARM/Thumb). If the called code does `bx lr` it returns to the monitor —
so *upload and run* is: `W` a program into SRAM, then `G` its address.

On entry (and after a recovery, §4) the stub prints `\r\nMPOK1\r\n` so a
human/host can see it is alive, then enters the command loop.

## 3. Serial link

* Console **UART**, a 16550. Register 0 (RBR/THR) is the data register and
  register 5 is the LSR (bit 0 = RX ready, bit 5 = TX empty). The stride
  between registers is the SoC's regshift. On the current target this is
  uart0 at `0x1f221000` with an 8-byte stride (regshift 3) — the UART the
  mask ROM itself prints on.
* **38400 8N1**, no flow control. The mask ROM programs the UART with LCR
  `0x03` (8N1) and divisor `0x14` (20) - i.e. 38400 baud - so the stub
  inherits that and does not re-init it; connect at the same baud the
  ROM's own messages appear at. (Note: the QEMU model's nominal
  BAUDBASE says 115200; that is cosmetic over a socket, but 38400 is the
  real rate.)

## 4. Liveness and fault recovery

**Liveness:** send `P`; a live stub replies `"SB01"`. If it does not reply
within your timeout, the stub is dead or wedged. `mstarpoker.py`'s
`alive()` / `sync()` do exactly this.

**Fault recovery:** poking unknown registers can fault the CPU. The stub
installs its own ARM vector table (via VBAR) so it survives:

* a **data abort** (a bad register read/write) is counted and the faulting
  access is skipped — the monitor keeps running;
* an **undefined instruction / prefetch abort** (e.g. a crashed uploaded
  program) is counted and drops back into a fresh monitor (you will see a
  new `MPOK1` banner).

The `F` command returns the running fault count, so you can tell whether
an access aborted. `mstarpoker.py`'s `probe(addr)` reads `F`, does the
read, reads `F` again, and reports `(value, faulted)` — a *safe* read
that never wedges the target. (Note: a true bus hang, where the access
never returns an abort, cannot be recovered by software; only a reset
clears that. The ping still detects it as "dead".)

## 5. Image formats (target adapter)

### Loader header (16 bytes, `mkipl.py`)

The MStar mask ROM, strapped for SPI NOR, checks the IPL magic through the
XIP window at flash offset 4, copies the image to SRAM at `0xa0000000`,
verifies it and jumps there. The header (reverse-engineered from the
stock IPL and the ROM disassembly):

| Offset | Size | Field                                                                |
|-------:|:----:|----------------------------------------------------------------------|
| `0x00` | u32  | ARM `b` branch over the header (to `_start`)                         |
| `0x04` | u32  | magic `"IPL_"` (`0x5f4c5049`, little-endian)                         |
| `0x08` | u32  | image size in bytes (header + body)                                  |
| `0x0c` | u32  | checksum: sum of the 32-bit LE words in `[0x10 : size]`, mod 2³²     |
| `0x10` |  …   | body (the linked stub; `_start` may be aligned a little past `0x10`) |

There is no "signed image" flag (the byte the ROM tests for `0xfa`), so
the ROM skips SHA/RSA authentication and jumps straight in.

### Flash image (16 MiB, `mkflash.py`)

IPL at offset 0, the rest padded to 16 MiB with `0xff` (erased NOR).

## 6. Host client — `mstarpoker.py`

Standard-library only (no pyserial); works over a QEMU unix socket or a
real serial device.

```
mstarpoker.py --socket /tmp/s.ser ping
mstarpoker.py --serial /dev/ttyUSB0 rd    0x1f206548
mstarpoker.py --serial /dev/ttyUSB0 dump  0x1f206400 128
mstarpoker.py --serial /dev/ttyUSB0 probe 0x1f224400      # safe: reports faults
mstarpoker.py --serial /dev/ttyUSB0 faults
mstarpoker.py --serial /dev/ttyUSB0 wr    0x1f003408 0x400
mstarpoker.py --serial /dev/ttyUSB0 load  0xa0009000 prog.bin
mstarpoker.py --serial /dev/ttyUSB0 go    0xa0009000
```

As a library:

```python
from mstarpoker import Link
lk = Link.open_serial("/dev/ttyUSB0")     # or Link.open_socket("/tmp/s.ser")
lk.sync()
print(hex(lk.read32(0x1f206548)))          # cpupll loop divider
val, faulted = lk.probe(0x1f224400)        # safe read
lk.dump(0x1f206400, 0x80)                  # a whole RIU bank
lk.upload(0xa0009000, open("p.bin","rb").read()); lk.go(0xa0009000)
```

### C client — `cli/mstarpoker`

The same client as a single C header (`cli/mstarpoker.h`, all `static
inline`) with a command-line tool in front of it, for hosts without
Python: it builds against libc or as a static nolibc binary. Same
commands, plus `rd8`/`wr8`/`wr16`, `save` (download memory to a file) and
`console` (stream what the target prints). See `cli/` and the README.

## 7. Runtime environment and caveats

* The stub runs from **on-chip SRAM** (IMI, `0xa0000000`, 64 KiB). Its
  stacks are near the top (`0xa000f000`, abort `0xa000e800`) and the code
  is ~0.8 KiB, so roughly `0xa0000800`..`0xa000e000` is free scratch for
  uploads.
* **DRAM is not initialised** — the stock IPL is what normally trains the
  MIU. So `0x20000000`+ is unusable until you upload and run a DDR-init
  routine. SRAM and all MMIO are available.
* Values read are **as the boot ROM left them**, not pristine reset
  values, for the few registers the ROM (and this stub's UART use) touch
  before the monitor starts. On the current target those are, from the ROM
  disassembly: uart0 (`0x1f221xxx`) and its pad-mux (`0x1f2070c4`,
  `0x1f203d4c`), the timer/watchdog (`0x1f006010`-`0x1f006058`), the boot
  progress scratch (`0x1f200800`/`0x808`), `0x1f203d40` bit 15,
  `0x1f001c24`, `0x16001000`/`0x04`, and the SPI-NOR load path (FSP
  `0x1f002d..`, BDMA `0x1f200404..`). Every other block is pristine.
* Interrupts stay masked; the stub polls the UART. MMU and D-cache are off
  (as the ROM left them), I-cache on.

## 8. Retargeting to another SoC

1. `stub.c` — set the UART config block (`UART_BASE`, `UART_STRIDE`).
2. `start.S` + `link.ld` — set the SRAM load address / stacks, and the
   loader header the new ROM expects.
3. `mkipl.py` — the header layout / checksum for the new ROM.
4. `mkflash.py` — the boot-medium image layout/size.

The protocol (`stub.c`'s command loop) and `mstarpoker.py` are unchanged.

## 9. Files

| File            | Role                                                     |
|-----------------|----------------------------------------------------------|
| `stub.c`        | the serial monitor (SoC-agnostic)                        |
| `start.S`       | loader header, entry, vector table / fault handlers      |
| `link.ld`       | link the stub to run from on-chip SRAM                   |
| `Makefile`      | build stub → `stub.bin` → `stub.ipl` → `flash.bin`       |
| `mkipl.py`      | wrap a raw binary in the loader header (size + checksum) |
| `mkflash.py`    | pad an IPL into a 16 MiB SPI-NOR image                   |
| `mstarpoker.py` | host-side protocol client (CLI + `Link` library)         |
| `cli/`          | the client in C: single header + command-line tool       |
| `socid.py`      | SoC identification helpers (chip version, bond strap)    |
| `regdump.py`    | register snapshot / before-after tables for the scripts  |
| `scripts/`      | probe and bring-up scripts (`common/` any target, `ssd20x/` SoC-specific) |
| `results/`      | QEMU baselines and real-hardware register dumps          |
| `PROTOCOL.md`   | this document                                            |
| `VALIDATION.md` | the hardware validation plan and its log                 |
