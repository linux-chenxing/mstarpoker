# SPDX-License-Identifier: GPL-2.0-or-later
CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
LD      := $(CROSS)ld
CFLAGS  := -mcpu=cortex-a7 -marm -ffreestanding -nostdlib -O2 -Wall -Wextra \
           -fno-pic -fno-builtin
all: cpuspeed.bin
cpuspeed.bin: cpuspeed.c cpuspeed.ld
	$(CC) $(CFLAGS) -c cpuspeed.c -o cpuspeed.o
	$(LD) -T cpuspeed.ld --no-warn-rwx-segments cpuspeed.o -o cpuspeed.elf
	$(OBJCOPY) -O binary cpuspeed.elf cpuspeed.bin
	@printf 'cpuspeed.bin: %s bytes, load 0xa0008000\n' "$$(stat -c%s cpuspeed.bin)"
clean:
	rm -f cpuspeed.o cpuspeed.elf cpuspeed.bin
.PHONY: all clean
