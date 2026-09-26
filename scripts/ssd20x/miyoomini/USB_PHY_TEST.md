<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# SSD202D USB UTMI PHY bring-up test

Reproduces the stock IPL's USB UTMI PHY power-up/calibration on real silicon
via mstarpoker, so the sequence can be confirmed before it is wired into a
bootloader. The three USB UTMI PHYs (0x284200 / 0x284a00 / 0x285200) were
previously mistaken for display "scalers"; they are USB, and the vendor IPL
brings them up in its SRAM phase (before Linux).

- Driver:  `scripts/ssd20x/mstar_usbphy.py`  (the "proper driver": PLL +
  per-port UTMI power-up/calibration + eye trims, from the firmware trace)
- Test:    `scripts/ssd20x/miyoomini/usb_phy_test.py`  (snapshot -> init ->
  snapshot, prints a before/after diff, optional `--json`)

No DDR is required (the PHYs are not in DRAM); this runs standalone right
after the mask ROM.

## Run in QEMU (smoke test)

```sh
qemu-system-arm -M miyoomini -bios ssd202d_bootrom.bin \
    -drive if=mtd,format=raw,file=flash.bin \
    -display none -serial unix:/tmp/s.ser,server,nowait &
python3 scripts/ssd20x/miyoomini/usb_phy_test.py --socket /tmp/s.ser
```

Expect `0 register faults` and `USB PHY bring-up complete`. (QEMU's readback
bank only round-trips one byte of each 16-bit register, so the *values* in the
diff are cosmetic in emulation; on hardware `read32` returns the full 16-bit
register.)

## Run on hardware

Flash `flash.bin` to the device's SPI-NOR at offset 0 (overwrites the stock
firmware - use a device you can re-flash), connect uart0 (38400 8N1), then:

```sh
python3 scripts/ssd20x/miyoomini/usb_phy_test.py \
    --serial /dev/ttyUSB0 --json usb_hw.json
```

The before/after diff (and `usb_hw.json`) is the ground-truth PHY register
state after bring-up - compare it against the values the driver writes and,
if you capture one, against a stock-firmware register dump.
