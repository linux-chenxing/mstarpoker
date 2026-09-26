#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
DDR bring-up for ssd20x targets - reusable by any script that needs DRAM.

Two methods:

* init(lk) / init_c(lk) - THE real trainer. Uploads the bare-metal DDR-init
  blob (ddr_c/ddr_init.bin, a clean C reimplementation of the vendor IPL's DDR
  logic) into SRAM and runs it on-target via the stub's 'G'. Because it runs on
  the chip, its read-modify-write ZQ / drive-strength calibration works against
  the real PHY and its settle delays use the real timer - so it actually trains
  real DRAM. See ddr_c/ddr_init.c.

* init_replay(lk) - replays the captured MIU register writes
  (ddr_seq_ssd202d) over the serial link. This does NOT train real DRAM (it
  drops the delays and skips the read-dependent ZQ calibration); it only
  exercises the flow under QEMU, whose MIU is a stub. Kept for reference and
  as a QEMU-only fallback. See VALIDATION.md for why replay is insufficient.

init(lk) returns True only if DRAM genuinely trained (MIU init-done + BIST
asserted). A False return means callers must not touch 0x20000000+, because a
read of untrained DRAM bus-hangs the CPU with no recoverable abort.
"""
import hashlib
import os

import ddr_seq_ssd202d as seq

DRAM_BASE = 0x20000000

# The DDR-init blob and where it is uploaded/run (matches ddr_c/ddr.ld).
DDR_BLOB = os.path.join(os.path.dirname(__file__), "ddr_c", "ddr_init.bin")
BLOB_ADDR = 0xa0008000

# The blob's DRAM self-test verdict, written to this SRAM word (ddr_init.c).
# init-done is not a reliable "trained" signal on real silicon (the IPL never
# polls it; the model fakes it), so we rely on the blob actually touching DRAM.
RESULT_ADDR = 0xa0009000
RESULT_PROBING = 0xd1900001      # reached the DRAM probe (a hang stops here)
RESULT_PASS = 0xd1900a00         # DRAM read back correctly
RESULT_FAIL = 0xd1900bad         # DRAM responded but data was wrong
RESULT_NOTIMER = 0xd1900d1e      # the delay timer would not run - blob bailed


def _poll(lk, off, mask, tries=2000):
    for _ in range(tries):
        if lk.read32(seq.MIU_BASE + off) & mask:
            return True
    return False


def init_c(lk, verbose=True, blob=DDR_BLOB):
    """Upload and run the on-target DDR-init blob. Returns the trained flag,
    decided by the blob's own DRAM self-test (see RESULT_ADDR)."""
    with open(blob, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()[:16]
    if verbose:
        # Print the blob's hash so a pasted-back log can be matched to the
        # exact blob that produced it (we iterate on this fast).
        print("[ddr] DDR-init blob: %d bytes, sha256:%s" % (len(data), sha))
        print("[ddr] uploading to 0x%08x and running on-target ddr_init()..."
              % BLOB_ADDR)
    lk.write32(RESULT_ADDR, 0)          # clear the verdict word first
    lk.upload(BLOB_ADDR, data)
    # The blob busy-waits through the DDR settle delays before returning. It
    # also arms the watchdog, so a DRAM bus-hang resets the SoC (~3s) instead
    # of wedging forever; the mask ROM then reloads the stub.
    lk.go(BLOB_ADDR, timeout=6.0)
    if not lk.alive():
        # Maybe the self-test hung and the watchdog reset the SoC - the stub
        # reloads from flash and comes back. Give it time and re-sync.
        try:
            lk.sync(tries=200)
            if verbose:
                print("[ddr] the DRAM self-test bus-hung; the watchdog reset "
                      "the SoC and the stub reloaded - DRAM is NOT trained "
                      "(no power-cycle needed)")
        except IOError:
            if verbose:
                print("[ddr] target did not come back (no watchdog reset?) - "
                      "power-cycle before retrying")
        return False

    verdict = lk.read32(RESULT_ADDR)
    if verdict == RESULT_PASS:
        if verbose:
            print("[ddr] ddr_init done - DRAM self-test PASSED (DRAM trained)")
        return True
    if verbose:
        msg = {RESULT_FAIL: "DRAM responded but data was wrong (marginal)",
               RESULT_PROBING: "stopped at the DRAM probe",
               RESULT_NOTIMER: "the delay timer would not run - check the timer "
                               "source (a prior test may have left it on the "
                               "MPLL); DDR sequence was not run",
               0: "blob did not reach the self-test"}.get(
                   verdict, "unexpected verdict 0x%08x" % verdict)
        print("[ddr] ddr_init done - DRAM self-test did NOT pass: %s" % msg)
    return False


def init_replay(lk, verbose=True):
    """Replay the captured MIU writes (QEMU-only; does not train real DRAM)."""
    if verbose:
        print("[ddr] replaying %d MIU writes (QEMU flow only)..."
              % len(seq.INIT_SEQ))
    ok = True
    for off, val in seq.INIT_SEQ:
        lk.write32(seq.MIU_BASE + off, val)
        poll = seq.POLL_AFTER.get((off, val))
        if poll:
            po, mask = poll
            if not _poll(lk, po, mask):
                ok = False
                if verbose:
                    print("[ddr] WARNING: poll 0x%03x & 0x%04x timed out"
                          % (po, mask))
    if verbose:
        print("[ddr] replay complete%s"
              % ("" if ok else " (poll timeouts - DRAM NOT trained)"))
    return ok


def init(lk, verbose=True, method="c"):
    """Bring up DDR. method='c' runs the on-target blob (real trainer);
    method='replay' uses the QEMU-only register replay. Falls back to replay
    if the blob is missing. Returns the trained flag."""
    if method == "c" and os.path.exists(DDR_BLOB):
        return init_c(lk, verbose=verbose)
    if method == "c" and verbose:
        print("[ddr] %s not built (run 'make' in ddr_c/); using replay"
              % os.path.basename(DDR_BLOB))
    return init_replay(lk, verbose=verbose)


def main():
    import argparse
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from mstarpoker import add_transport_args, open_link  # noqa: E402

    ap = argparse.ArgumentParser(
        description="Bring up / train the SSD202D DDR over the mstarpoker stub.")
    add_transport_args(ap)
    ap.add_argument("--method", choices=("c", "replay"), default="c",
                    help="'c' = on-target blob (real trainer, default); "
                         "'replay' = QEMU-only register replay")
    args = ap.parse_args()

    lk = open_link(args)
    trained = init(lk, method=args.method)
    sys.exit(0 if trained else 1)


if __name__ == "__main__":
    main()
