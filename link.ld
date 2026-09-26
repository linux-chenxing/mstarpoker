/*
 * mstarpoker stub linker script.
 *
 * The boot ROM copies the image to on-chip SRAM at ORIGIN and jumps there.
 * The loader header (.header) must sit first, so _start lands at
 * ORIGIN+0x10 and the header's branch reaches it. start.S zeroes .bss.
 *
 * ORIGIN/LENGTH are the SoC's SRAM (IMI) where the ROM runs the image;
 * change them together with the addresses in start.S when retargeting.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0xa0000000, LENGTH = 64K
}

SECTIONS {
    .header : { KEEP(*(.header)) } > RAM
    .text   : { *(.text*) }        > RAM
    .rodata : { *(.rodata*) }      > RAM
    .data   : { *(.data*) }        > RAM
    . = ALIGN(4);

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > RAM

    /DISCARD/ : { *(.ARM.*) *(.comment) *(.note*) }
}
