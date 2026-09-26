#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
SSD20xd (infinity2m) USB UTMI PHY bring-up driver.

Reverse-engineered from the stock Miyoo Mini firmware. The vendor IPL powers
up and calibrates the three USB UTMI PHYs in its SRAM phase (right after DRAM
init), long before Linux runs - which is why the mainline USB PHY driver never
has to perform this analog power-up/calibration itself. This module reproduces
that exact sequence so it can be validated on real silicon with mstarpoker
before it is wired into a bootloader.

The register blocks were previously mislabelled as display "scalers"
(0x284200/0x284a00/0x285200); they are in fact the USB UTMI PHYs, matching the
Linux DT (utmi@284a00, usbc@284e00).

Blocks (RIU: 16-bit registers on a 4-byte stride, byte addressable):

  0x1f283e00  UPLL         USB PLL (shared by all ports)
  0x1f284000  USB clk gate

  port  UTMI PHY     USBC (companion controller)
  0     0x1f284200   0x1f284600
  1     0x1f284a00   0x1f284e00
  2     0x1f285200   0x1f286200

UTMI register offsets (see u-boot include/linux/usb/mstar_utmi.h):
  0x00 pwrctrl   0x04 config   0x10 clkctrl
  0x40 pll_test[15:0]   0x44 pll_test[31:16]   0xa4 calibration strobe
"""

# --- register map ---------------------------------------------------------
UPLL_BASE   = 0x1f283e00
USB_CLKGATE = 0x1f284000

UTMI_PWRCTRL   = 0x00
UTMI_CONFIG    = 0x04
UTMI_CLKCTRL   = 0x10
UTMI_PLLTEST_L = 0x40
UTMI_PLLTEST_H = 0x44
UTMI_CAL       = 0xa4   # vendor calibration strobe (pulsed with 0x3f)

# pwrctrl values from the vendor power-up walk (0x7f05 start -> 0x7f03 normal)
PWRCTRL_START  = 0x7f05
PWRCTRL_NORMAL = 0x7f03

PORTS = [
    {"name": "usb0", "utmi": 0x1f284200, "usbc": 0x1f284600,
     "eye": [(0x50, 0x0000), (0x5c, 0x0002), (0x58, 0x8000)]},
    {"name": "usb1", "utmi": 0x1f284a00, "usbc": 0x1f284e00,
     "eye": [(0x50, 0x00c0), (0x5c, 0x0005), (0x58, 0x8000)]},
    {"name": "usb2", "utmi": 0x1f285200, "usbc": 0x1f286200,
     "eye": [(0x50, 0x00c0), (0x5c, 0x0005), (0x58, 0x8000)]},
]


# --- low level ------------------------------------------------------------
def _w8(lk, addr, val):
    lk.write8(addr, val)


def _w16(lk, addr, val):
    # RIU 16-bit registers are byte addressable (low byte at addr, high byte at
    # addr+1). The monitor protocol has no 16-bit store, and a 32-bit store
    # would also clobber the padding half-word at addr+2, so write the two
    # bytes explicitly - exactly how the vendor IPL programs these registers.
    lk.write8(addr, val & 0xff)
    lk.write8(addr + 1, (val >> 8) & 0xff)


# --- driver ---------------------------------------------------------------
def upll_init(lk):
    """Bring up the shared USB PLL and ungate the USB clock."""
    _w8(lk, UPLL_BASE + 0x00, 0xc0)
    _w8(lk, UPLL_BASE + 0x1c, 0x11)
    _w8(lk, USB_CLKGATE,      0xb0)
    _w8(lk, UPLL_BASE + 0x08, 0x10)
    _w8(lk, UPLL_BASE + 0x09, 0x01)


def utmi_port_init(lk, port):
    """Power up and calibrate one UTMI PHY port, matching the vendor IPL."""
    utmi = port["utmi"]
    usbc = port["usbc"]

    _w8(lk,  utmi + 0x10, 0x2f)          # clkctrl low byte
    _w8(lk,  utmi + 0x11, 0x0c)          # clkctrl high byte
    _w16(lk, utmi + UTMI_CLKCTRL, 0x040f)
    _w16(lk, utmi + UTMI_PWRCTRL, PWRCTRL_START)
    _w8(lk,  utmi + UTMI_CAL, 0x00)

    _w8(lk,  usbc + 0x00, 0x0a)          # USBC reset control
    _w8(lk,  usbc + 0x00, 0x28)

    _w16(lk, utmi + UTMI_PLLTEST_H, 0x2088)
    _w16(lk, utmi + UTMI_PLLTEST_L, 0x8051)
    _w16(lk, utmi + UTMI_CONFIG,    0x2084)
    _w16(lk, utmi + UTMI_CLKCTRL,   0x0426)

    # calibration walk: pulse the strobe (0x3f) across the pwrctrl states
    _w16(lk, utmi + UTMI_PWRCTRL, 0x6bc3)
    _w8(lk,  utmi + UTMI_CAL, 0x3f)
    _w16(lk, utmi + UTMI_PWRCTRL, 0x69c3)
    _w8(lk,  utmi + UTMI_CAL, 0x3f)
    _w16(lk, utmi + UTMI_PWRCTRL, 0x0001)
    _w8(lk,  utmi + UTMI_CAL, 0x00)
    _w16(lk, utmi + UTMI_PWRCTRL, PWRCTRL_NORMAL)


def utmi_eye_init(lk, port):
    """Per-port eye-diagram / drive-strength trims (applied after power-up)."""
    for off, val in port["eye"]:
        _w16(lk, port["utmi"] + off, val)


def usb_phy_init(lk, ports=PORTS):
    """Full USB PHY bring-up: PLL, then each UTMI port, then the eye trims.

    Order matches the vendor IPL: all ports are powered up/calibrated first,
    then the eye trims are applied to each.
    """
    upll_init(lk)
    for p in ports:
        utmi_port_init(lk, p)
    for p in ports:
        utmi_eye_init(lk, p)


# --- register table for snapshots ----------------------------------------
def regs(ports=PORTS):
    """(addr, name) list covering the PLL + each UTMI/USBC port, for dumps."""
    out = [(UPLL_BASE, "upll"), (USB_CLKGATE, "usb_clkgate")]
    for p in ports:
        u = p["utmi"]
        out += [
            (u + UTMI_PWRCTRL,   p["name"] + ".pwrctrl"),
            (u + UTMI_CONFIG,    p["name"] + ".config"),
            (u + UTMI_CLKCTRL,   p["name"] + ".clkctrl"),
            (u + UTMI_PLLTEST_L, p["name"] + ".plltest_l"),
            (u + UTMI_PLLTEST_H, p["name"] + ".plltest_h"),
            (p["usbc"] + 0x00,   p["name"] + ".usbc_rst"),
        ]
    return out
