/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(cpuspeed)
MEMORY { SRAM (rwx) : ORIGIN = 0xa0008000, LENGTH = 0x2000 }
SECTIONS {
    .text : { *(.text.start) *(.text*) *(.rodata*) *(.data*) } > SRAM
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > SRAM
    /DISCARD/ : { *(.ARM.exidx*) *(.comment) *(.note*) }
}
