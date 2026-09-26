#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
socid - SoC identification helpers for mstarpoker scripts.

Reads the chip version, the package (bond strap) and, from the package,
the bonded in-package DRAM off a live target. These are pure straps /
ID registers, so they work from the stub before any DDR/MIU init.

Register locations are for MStar infinity2m (e.g. SSD202D); edit the
constants and BOND_TABLE for other parts. Import and reuse:

    from socid import identify
    soc = identify(lk)
    print(soc.part, soc.revision, soc.memory)
"""
import collections

# Chip version: the vendor HAL (_HalGopGetChipVersion) reads RIU register
# 0x1ece - physical 0x1f003d9c (RIU addr << 1) - and takes bit 8. On MStar
# parts chip version N is marketed as revision "U0<N+1>".
CHIP_VER_REG = 0x1f003d9c
CHIP_VER_BIT = 8

# Package "bond" strap in the chiptop block (0x1f203c00 + 0x120).
BOND_REG = 0x1f203d20

# Known bond values -> (part, bonded DRAM). Extend as parts are confirmed.
BOND_TABLE = {
    0x1d: ("SSD201",  "64 MiB DDR3"),
    0x1e: ("SSD202D", "128 MiB DDR3"),
}

SocId = collections.namedtuple(
    "SocId", "part memory bond version revision raw_ver faulted")


def read_bond(lk):
    """(bond byte, faulted)."""
    val, faulted = lk.probe(BOND_REG)
    return (val & 0xff), faulted


def read_chip_version(lk):
    """(version bit, raw register, faulted)."""
    val, faulted = lk.probe(CHIP_VER_REG)
    return ((val >> CHIP_VER_BIT) & 1), val, faulted


def identify(lk):
    """Read the SoC identity into a SocId namedtuple."""
    bond, bf = read_bond(lk)
    ver, raw_ver, vf = read_chip_version(lk)
    part, memory = BOND_TABLE.get(bond, ("unknown", "unknown"))
    return SocId(part=part, memory=memory, bond=bond, version=ver,
                 revision="U%02d" % (ver + 1), raw_ver=raw_ver,
                 faulted=bf or vf)


def format_id(soc):
    """A human-readable one-block summary of a SocId."""
    lines = [
        "part        %s" % soc.part,
        "revision    %s (chip version %d, 0x%08x = 0x%08x)"
        % (soc.revision, soc.version, CHIP_VER_REG, soc.raw_ver),
        "bond strap  0x%08x = 0x%02x" % (BOND_REG, soc.bond),
        "bonded DRAM %s" % soc.memory,
    ]
    if soc.part == "unknown":
        lines.append("            (unknown bond - add 0x%02x to socid.BOND_TABLE)"
                     % soc.bond)
    return "\n".join(lines)
