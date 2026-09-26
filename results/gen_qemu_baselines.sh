#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Regenerate the QEMU-side validation baselines (results/qemu_*) by running
# the same scripts VALIDATION.md runs on hardware, but against the model.
#
#   QEMU=path/to/qemu-system-arm BIOS=path/to/ssd202d_bootrom.bin ./gen_qemu_baselines.sh
#
# BIOS is the mask-ROM dump the miyoomini machine boots (optional if the
# QEMU build finds it in its pc-bios directory by itself).
#
# Needs a built flash.bin (run `make` in the parent dir first).
set -e
here=$(dirname "$0")
top=$here/..
QEMU=${QEMU:-qemu-system-arm}
PY=${PY:-python3}
SK=$(mktemp -u /tmp/mpokbase.XXXX).ser
WAV=$(mktemp -u /tmp/base.XXXX).wav

"$QEMU" -M miyoomini ${BIOS:+-bios "$BIOS"} -drive if=mtd,format=raw,file="$top/flash.bin" \
    -display none -serial unix:"$SK",server,nowait \
    -audiodev wav,id=wav0,path="$WAV" -global mstar-bach.audiodev=wav0 &
qpid=$!
trap 'kill $qpid 2>/dev/null' EXIT
sleep 3

$PY "$top/scripts/common/dump_bootrom.py"  --socket "$SK" --size 0x4000 -o "$here/qemu_bootrom_16k.bin"
$PY "$top/scripts/common/detect_memory.py" --socket "$SK" --json "$here/qemu_id.json"
$PY "$top/scripts/common/dump_pm_regs.py"  --socket "$SK" -o "$here/qemu_pm.txt" --json "$here/qemu_pm.json"
$PY "$top/scripts/ssd20x/dram_test.py"     --socket "$SK" --json "$here/qemu_miu.json" || true
$PY "$top/scripts/ssd20x/miyoomini/display_show.py" --socket "$SK" --json "$here/qemu_disp.json"
$PY "$top/scripts/ssd20x/miyoomini/audio_play.py"   --socket "$SK" --secs 1.0 --json "$here/qemu_bach.json"
for b in "cpupll 0x1f206400 128" "clkgen 0x1f207000 128" "chiptop 0x1f203c00 128" \
         "pwm 0x1f003400 64" "did 0x1f007000 128"; do
    set -- $b
    $PY "$top/mstarpoker.py" --socket "$SK" dump "$2" "$3" > "$here/qemu_$1.txt"
done
echo "regenerated baselines in $here"
