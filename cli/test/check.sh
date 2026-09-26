#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Self-test for the YMODEM sender and the SPL handoff, against a mock of the
# stub + U-Boot's xyzModem receiver on a unix socket. Exits 0 when every file
# arrived intact.  Usage: check.sh <mstarpoker binary>
set -e
BIN=${1:-./mstarpoker}
here=$(dirname "$0")
sock=$(mktemp -u /tmp/mpkchk.XXXXXX).sock
big=$(mktemp); odd=$(mktemp); spl=$(mktemp)
trap 'rm -f "$big" "$odd" "$spl" "$sock"' EXIT
head -c 300000 /dev/urandom > "$big"        # a u-boot.img sized file
head -c 3001 /dev/urandom > "$odd"          # not a multiple of the block
head -c 4096 /dev/urandom > "$spl"

run() {
	mockargs=$1; shift
	python3 "$here/xyzmodem_mock.py" "$sock" $mockargs 2>/dev/null &
	mock=$!
	sleep 0.5
	"$@" > /dev/null 2>&1 || { wait $mock || true; return 1; }
	wait $mock
}

echo "spl handoff (load + go + marker + ymodem)"
run "--expect $big" "$BIN" -u "$sock" spl "$spl" "$big" 0xa0004000 500
echo "ymodem primitive, odd size, one NAK"
run "--expect $odd --spl-mode --nak 2" "$BIN" -u "$sock" ymodem "$odd" 500
echo "spl refuses to send without the marker"
python3 "$here/xyzmodem_mock.py" "$sock" --no-marker --give-up 8 2>/dev/null &
mock=$!
sleep 0.5
if "$BIN" -w 2000 -u "$sock" spl "$spl" "$odd" 200 > /dev/null 2>&1; then
	echo "expected failure"; kill $mock 2>/dev/null; exit 1
fi
wait $mock || true
echo "ok"
