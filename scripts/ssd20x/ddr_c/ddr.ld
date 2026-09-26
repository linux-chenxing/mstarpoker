/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Link the DDR-init blob to run from a free slice of on-chip SRAM. The
 * mstarpoker stub uploads it here and calls ddr_init() (at the base) via 'G'. */
ENTRY(ddr_init)

MEMORY {
    SRAM (rwx) : ORIGIN = 0xa0008000, LENGTH = 0x4000
}

SECTIONS {
    .text : {
        *(.text.start)      /* ddr_init first, so the base == entry */
        *(.text*)
        *(.rodata*)
        *(.data*)
    } > SRAM

    .bss (NOLOAD) : {
        *(.bss*)
        *(COMMON)
    } > SRAM

    /DISCARD/ : { *(.ARM.exidx*) *(.comment) *(.note*) }
}
