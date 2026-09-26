# SPDX-License-Identifier: GPL-2.0-or-later
# Build the mstarpoker serial-monitor stub and package it into a 16 MiB
# SPI-NOR image the SSD202D mask ROM can load. See PROTOCOL.md.
#
# Any ARM cross gcc works (the code is freestanding); point CROSS at its
# prefix, e.g. make CROSS=/opt/gcc-arm/bin/arm-linux-gnueabi-

CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
LD      := $(CROSS)ld
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump
PYTHON  ?= python3

CFLAGS  := -mcpu=cortex-a7 -marm -ffreestanding -nostdlib -Os \
           -Wall -Wextra -fno-pic -fno-builtin
LDFLAGS := -T link.ld -nostdlib --no-warn-rwx-segments

OBJS    := start.o stub.o

all: flash.bin

start.o: start.S
	$(CC) $(CFLAGS) -c $< -o $@

stub.o: stub.c
	$(CC) $(CFLAGS) -c $< -o $@

stub.elf: $(OBJS) link.ld
	$(LD) $(LDFLAGS) $(OBJS) -o $@

stub.bin: stub.elf
	$(OBJCOPY) -O binary $< $@

stub.ipl: stub.bin mkipl.py
	$(PYTHON) mkipl.py stub.bin stub.ipl

flash.bin: stub.ipl mkflash.py
	$(PYTHON) mkflash.py stub.ipl flash.bin

# handy: disassemble the built stub
dis: stub.elf
	$(OBJDUMP) -d $<

clean:
	rm -f $(OBJS) stub.elf stub.bin stub.ipl flash.bin

.PHONY: all dis clean
