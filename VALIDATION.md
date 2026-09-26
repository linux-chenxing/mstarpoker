<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# Validating the QEMU model against real SSD202D hardware

The QEMU `miyoomini` model was built largely from reverse engineering, so
many register values are guesses, seeds, or zero-by-omission. `mstarpoker`
runs on the real chip and reads its registers back, so we can compare
real silicon against the model and:

* harvest the **real reset/default register values** and bake them into
  the model (today the readback banks mostly return 0);
* confirm the **RE'd sequences** (DDR bring-up, display, audio) actually
  work on silicon, not just in emulation; and
* answer the model's **open questions** (the boot ROM base, the unknown
  `0x16000000` block, `0x1f00401c` bit 6, ...).

## Method

Every script takes `--json FILE` and dumps a register table; the
"update" scripts dump **before/after** tables. We run each on hardware
(`--serial`) and diff its JSON against the QEMU baseline (`--socket`,
committed under `results/qemu_*.json`). Discrepancies are the work list.

**Apples to apples.** The stub runs right after the mask ROM, *before*
the vendor IPL/u-boot/kernel - in both QEMU and on hardware. So both
snapshots are the same "post-ROM" state (mostly reset values, bar the few
registers the ROM touches; see `bootrom.rst` in the QEMU tree's mstarv7 docs). A register that reads 0 in
QEMU but non-zero on hardware is exactly a real default to capture.

## Setup (once)

```sh
# 1. Build the stub + 16 MiB flash image
make                                     # -> flash.bin

# 2. Write flash.bin to the device's SPI-NOR at offset 0 (external
#    programmer / CH341A clip, or any method that writes the NOR).
#    This overwrites the stock firmware - use a device you can re-flash.

# 3. Connect uart0 (38400 8N1) to the host, power on, and check the stub:
export SER=/dev/ttyUSB0
python3 mstarpoker.py --serial $SER ping           # -> pong
python3 mstarpoker.py --serial $SER rd 0x1f206548  # cpupll loop div (sanity)
```

If `ping` times out the stub is not running: recheck the flash write, the
UART wiring/baud, and that the boot strap selects SPI-NOR. `faults`
reports if a read has aborted.

## Phase 1 - static register snapshots (read-only, safe)

Nothing here changes hardware state. Run all, send back every file.

```sh
# 1a. Boot ROM - dump 16 KiB and again 32 KiB (is the ROM larger / does it
#     alias? answers "where does the ROM live" and the 16-vs-32 KiB question)
python3 scripts/common/dump_bootrom.py --serial $SER --size 0x4000 -o hw_bootrom_16k.bin
python3 scripts/common/dump_bootrom.py --serial $SER --size 0x8000 -o hw_bootrom_32k.bin

# 1b. Chip id + bonded memory
python3 scripts/common/detect_memory.py --serial $SER --json hw_id.json

# 1c. Always-on (PM domain) register defaults
python3 scripts/common/dump_pm_regs.py --serial $SER -o hw_pm.txt --json hw_pm.json

# 1d. A few more key banks' reset defaults (cpupll, clkgen, chiptop, pwm,
#     the DID/strap block). dump N words with the client's dump command:
python3 mstarpoker.py --serial $SER dump 0x1f206400 128 > hw_cpupll.txt   # cpu PLL
python3 mstarpoker.py --serial $SER dump 0x1f207000 128 > hw_clkgen.txt   # clkgen
python3 mstarpoker.py --serial $SER dump 0x1f203c00 128 > hw_chiptop.txt  # chiptop/straps
python3 mstarpoker.py --serial $SER dump 0x1f003400 64  > hw_pwm.txt      # pwm
python3 mstarpoker.py --serial $SER dump 0x1f007000 128 > hw_did.txt      # DID/boot strap
```

Send back: `hw_bootrom_16k.bin`, `hw_bootrom_32k.bin`, `hw_id.json`,
`hw_pm.txt`, `hw_pm.json`, and the `hw_*.txt` bank dumps.

## Phase 2 - functional / sequence validation

These write registers (before/after JSON captures both). They exercise
the RE'd sequences on silicon; note the physical observation for each.

```sh
# 2a. DDR: replay the captured MIU init sequence and test the DRAM.
#     THE key test - does the sequence bring real DDR up?
python3 scripts/ssd20x/dram_test.py --serial $SER --json hw_miu.json | tee hw_dram_test.txt
#     -> report PASS/FAIL of data-bus / address-bus / pattern tests.

# 2b. Display: bring up the pipeline and render the test pattern.
python3 scripts/ssd20x/miyoomini/display_show.py --serial $SER --json hw_disp.json
#     -> does a picture appear on the panel? all of it, right colours,
#        right orientation? photograph it if possible.

# 2c. Audio: play a 1 s tone.
python3 scripts/ssd20x/miyoomini/audio_play.py --serial $SER --secs 1.0 --json hw_bach.json
#     -> does a clean ~1 kHz tone come out of the speaker/headphone?
```

Send back: `hw_miu.json`, `hw_dram_test.txt`, `hw_disp.json`,
`hw_bach.json`, plus a one-line note on each observation (DRAM pass/fail,
picture yes/no + how it looked, audio yes/no).

> Between 2a/2b/2c, power-cycle (or re-flash-boot) the board so each
> starts from a clean post-ROM state - each script does its own DDR init
> and assumes DRAM is fresh.

## What the results tell us / next steps

* **DDR test PASS** confirms the captured sequence is faithful; **FAIL**
  means replaying the QEMU-captured writes is not enough on silicon (the
  read-modify-write concern) - we then capture the sequence differently or
  build it from the IPL disassembly, and iterate against `hw_miu.json`.
* **Display / audio work** confirms the GOP / BACH paths; **nothing shown
  / silent** means the real-hardware hooks (LCD PLL + DSI panel DCS init;
  audiotop codec + clocks) are required - we capture those and add them.
* **Register diffs** (hardware vs `results/qemu_*.json`) become model
  fixes: replace the readback banks' zeros with the real defaults, and
  correct any behaviour that reads differently.

## Result file naming

Prefix hardware results `hw_` and drop them in a directory you send back
(or attach individually). The committed QEMU baselines are
`results/qemu_*.json`; the `hw_*` files are diffed against them and the
differences turned into model changes, updating this plan as we go.

## Log

(Track runs and findings here as we go.)

| Date | Item | Result | Action |
|------|------|--------|--------|
| 2026-07-21 | uart0 baud | HW is 38400 8N1 (ROM sets LCR 0x03, divisor 0x14=20), not the model's nominal 115200 | client/docs default -> 38400; real baudbase ~768000 for the model doc |
| 2026-07-21 | boot ROM (P1) | `hw_bootrom_16k.bin` is byte-identical to the QEMU tree's `pc-bios/ssd202d_bootrom.bin` (sha256 in `results/bootrom.sha256`; the dump itself is not committed); the 32 KiB dump shows 0x4000-0x8000 mirroring 0x0-0x4000 | ROM is 16 KiB, aliased - resolves the "is it larger?" open question; no model change |
| 2026-07-21 | chip version (P1) | `0x1f003d9c` reads `0x100` on HW (bit 8 set); model's 0x1f003xxx region is unmapped and reads 0 | model must return 0x100 at 0x1f003d9c so `socid` reports the right revision |
| 2026-07-21 | bond strap (P1) | `0x1f203d20` = `0x1e` on HW - matches the model (SSD202D / 128 MiB) | confirmed, no change |
| 2026-07-21 | regbank defaults (P1) | readback banks the model resets to 0 have real non-zero defaults on HW: clkgen (0x1f207000), pm_clkgen (0x1f001c00), chiptop (0x1f203c00), pwm (0x1f003400), cpupll (0x1f206400); 123 PM regs differ non-trivially | seed each bank's reset table from the `hw_*` dumps |
| 2026-07-21 | PM GPIO pads (P1) | `0x1f001e00`-`0x1f001f2c` read `0x11`/`0x15` on HW (bit0+bit4), not the model's flat `0x01`; `0x1f001f30`+ read 0 (model wrongly fills them 0x01) | fix `mstar_gpio` PM pad reset defaults |
| 2026-07-21 | DID_KEY (P1) | `0x1f0071c0` = `0x0a20` on HW; model returns just the `did-key` strap `0x20` (extra 0x0a00 strap bits absent) | note; low priority |
| 2026-07-21 | DID chip id (P1) | `0x1f007000/04/08` carry a per-unit OTP id on HW (three non-zero halfwords; redacted in `results/hw_*`); model reads 0 | do NOT hardcode (per-chip); leave 0 or model as random/efuse |
| 2026-07-21 | dynamic regs (P1) | timer/wdt latches `0x1f006050/54`, `0x1f006830/34` differ - free-running counters, expected | exclude from any reset-default baking |
| 2026-07-21 | **FIXED** clkgen/pm_clkgen | seeded reset defaults (55beb5993f); pm_clkgen matches HW exactly, clkgen has 1 documented gap at +0xc4 (0x1108 vs 0x08: read-only strap bits over the ROM's 0x08 mux write) | done |
| 2026-07-21 | **FIXED** chiptop + chip version | seeded chiptop defaults and mapped a chip-version bank returning 0x0100 (b944a4d76c); chiptop matches HW exactly, socid now reports revision U02 | done |
| 2026-07-21 | PM GPIO pads - pending | real defaults are 0x11 (pull-up + OEN) for pads 0-75 and 0x00 for 76-127; the generic `mstar_gpio` resets flat 0x01. bit4 is a constant reset bit the device does not store, and the pad-count split is SoC-specific | needs a per-bank/per-SoC pad-default parameter on `mstar_gpio` (not a hardcoded count in the generic device) - deferred pending design |
| 2026-07-21 | **DDR init (P2)** | replaying the 261 captured MIU writes on real silicon did NOT train DRAM: both completion polls timed out (init-done 0x400 bit15, BIST 0x5c0 bit15) and reading 0x20000000 bus-hung the CPU (no recoverable abort). Confirms the RMW/incomplete-sequence risk. The MIU before/after table (real reset defaults + the replay result) is in `hw_miu.json` | the flattened absolute-write replay is insufficient; rebuild the sequence from the IPL disassembly with its intermediate reads/polls/delays (and any PHY training). Model's MIU stub fakes init-done, hiding this - to be revisited |
| 2026-07-21 | tooling robustness | on the DDR failure the test hung the board; `ddr.init` now returns a trained flag and dram_test/display/audio refuse to touch DRAM unless init-done + BIST asserted. Also fixed a transport overrun (chunk block ops to 1024 words) that was masked by `\|\| true` in the baseline script | done |
| 2026-07-21 | **DDR root cause (IPL disasm)** | disassembled the vendor IPL (Thumb): (1) DDR init is interleaved with ~15 timer-based delays (routine @IPL 0x1cd4 busy-waiting the PM timer 0x1f006050, r0=0x2ee0/0x4b0 ticks) that the write-only capture dropped; (2) more decisively, the ZQ / drive-strength calibration (IPL 0x28dc-0x29f8) is read-modify-write and data-dependent - it reads the analog impedance result (0x1f00402c/0x1f004024) and computes drive-strength deltas, which QEMU skips because those analog bits are never set in the stub. So the captured `INIT_SEQ` is missing the ZQ writes entirely | CONCLUSION: a flattened write list can never train real DRAM. Faithful HW bring-up requires *running* the init on-target |
| 2026-07-21 | **DDR C reimplementation** | reverse-engineered the IPL's DDR logic into clean bare-metal C: `scripts/ssd20x/ddr_c/ddr_init.c` = the HW-validated config-write table + the IPL's timer delays (routine 0x1cd4) + the ZQ/drive-strength calibration (helper 0x1128, delta table @IPL 0x4920). Uploaded to SRAM and run on-target via the stub's `G`, so RMW/ZQ runs against the real PHY at native speed. `ddr.py` now uses it as the real trainer (`init_c`); the old register replay is kept as `init_replay` (QEMU-only). Verified in QEMU: blob runs, returns cleanly, 0 faults, dram_test/display pass | READY for hardware - run `dram_test.py` on the board; it now trains via the C blob. Report whether init-done/BIST assert and the DRAM tests pass |
