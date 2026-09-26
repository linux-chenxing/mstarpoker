#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
regdump - register snapshots and before/after tables for mstarpoker scripts.

snapshot(lk, regs, unsafe) reads a set of registers into an ordered list
of entries, for dumping and before/after comparison around updates. Reads
go through the fault-safe probe(), so a read that aborts is recorded
(value=None, note "fault") rather than wedging the target.

IMPORTANT - side effects: registers that cannot be read without side
effects (clear-on-read status, FIFOs that pop on read, or reads that
trigger hardware) must be listed in ``unsafe``. They are never read; they
are recorded as write-only. When in doubt whether a read is safe, list
the register as unsafe - a snapshot must not perturb the thing it dumps.
"""
import json
import sys


def snapshot(lk, regs, unsafe=()):
    """regs: iterable of addr or (addr, name). Returns a list of entries.

    Each entry is {addr, name, value, note}; value is None when the
    register was not read (unsafe) or the read faulted."""
    unsafe = set(unsafe)
    out = []
    for entry in regs:
        addr, name = entry if isinstance(entry, tuple) else (entry, "")
        if addr in unsafe:
            out.append({"addr": addr, "name": name, "value": None,
                        "note": "write-only (unsafe to read)"})
            continue
        value, faulted = lk.probe(addr)
        out.append({"addr": addr, "name": name,
                    "value": None if faulted else value,
                    "note": "fault" if faulted else None})
    return out


def _v(e):
    if e["value"] is not None:
        return "0x%08x" % e["value"]
    return "  (w/o)  " if e["note"] and "write-only" in e["note"] else "  FAULT "


def print_snapshot(snap, title=None, out=sys.stdout):
    if title:
        out.write("\n== %s ==\n" % title)
    for e in snap:
        out.write("  0x%08x = %s%s\n" % (e["addr"], _v(e),
                  ("  " + e["name"]) if e["name"] else ""))


def print_diff(before, after, title="register changes (before -> after)",
               out=sys.stdout, changed_only=False):
    out.write("\n== %s ==\n" % title)
    for b, a in zip(before, after):
        changed = b["value"] != a["value"]
        if changed_only and not changed:
            continue
        out.write("  0x%08x = %s -> %s%s%s\n" %
                  (b["addr"], _v(b), _v(a),
                   ("  " + b["name"]) if b["name"] else "",
                   "  *" if changed else ""))


def _jsonify(snap):
    return [{"addr": "0x%08x" % e["addr"], "name": e["name"],
             "value": None if e["value"] is None else "0x%08x" % e["value"],
             "note": e["note"]} for e in snap]


def write_json(path, **sections):
    """write_json('x.json', before=snap, after=snap) - hex-string values."""
    data = {k: _jsonify(v) for k, v in sections.items()}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path
