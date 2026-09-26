# SPDX-License-Identifier: GPL-2.0-or-later
# Build the bare-metal DDR-init blob (uploaded and run via the mstarpoker stub).
CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
LD      := $(CROSS)ld

CFLAGS  := -mcpu=cortex-a7 -marm -ffreestanding -nostdlib -Os -Wall -Wextra \
           -fno-pic -fno-builtin -fno-stack-protector
LOAD    := 0xa0008000

all: ddr_init.bin

ddr_init.o: ddr_init.c ddr_table.inc
	$(CC) $(CFLAGS) -c ddr_init.c -o ddr_init.o

ddr_init.elf: ddr_init.o ddr.ld
	$(LD) -T ddr.ld --no-warn-rwx-segments ddr_init.o -o ddr_init.elf

ddr_init.bin: ddr_init.elf
	$(OBJCOPY) -O binary ddr_init.elf ddr_init.bin
	@printf 'ddr_init.bin: %s bytes, load 0x%08x\n' "$$(stat -c%s ddr_init.bin)" $(LOAD)

dis: ddr_init.elf
	$(CROSS)objdump -d ddr_init.elf

clean:
	rm -f ddr_init.o ddr_init.elf ddr_init.bin

.PHONY: all dis clean
