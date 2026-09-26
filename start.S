/*
 * mstarpoker stub - entry, loader header and fault handling.
 *
 * A tiny bare-metal ARM serial monitor. The monitor itself (stub.c) is
 * generic; this file is the SoC "adapter": the loader header the target's
 * boot ROM expects, and the low-level entry. Retargeting to another chip
 * is normally just this file (plus mkipl.py's header) and the config
 * block in stub.c.
 *
 * Current target: an MStar/SigmaStar infinity2m SoC (e.g. SSD202D). Its
 * mask ROM loads an "IPL" into IMI SRAM and jumps to it; the first word
 * must branch over a 16-byte header carrying the "IPL_" magic, size and a
 * word-sum checksum (mkipl.py fills the last two in). See PROTOCOL.md.
 *
 * We install our own ARM vector table (via VBAR) so that poking a bad
 * register cannot wedge the chip: a data abort is counted and skipped,
 * and an undefined-instruction / prefetch abort (e.g. from a crashed
 * uploaded program) drops back into the monitor, whose ping still answers.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

/* ---- SoC configuration ------------------------------------------------
 * Where the boot ROM places the image and runs it. Must match ORIGIN in
 * link.ld. Stacks live near the top of the on-chip SRAM.
 */
    .equ STACK_TOP,       0xa000f000
    .equ ABORT_STACK_TOP, 0xa000e800

/* ---- loader header (SoC-specific) ------------------------------------- */
    .section .header, "ax"
    .global _header
_header:
    b       _start              /* 0x00: branch over the header */
    .word   0x5f4c5049          /* 0x04: "IPL_" magic (little-endian) */
    .word   0                   /* 0x08: image size   (mkipl fills in) */
    .word   0                   /* 0x0c: word checksum (mkipl fills in) */

    .text
    .global _start
_start:
    cpsid   if                  /* keep IRQ/FIQ masked (ROM already did) */

    /* zero .bss (g_fault lives here) */
    ldr     r0, =_bss_start
    ldr     r1, =_bss_end
    mov     r2, #0
1:  cmp     r0, r1
    strlo   r2, [r0], #4
    blo     1b

    /* give Abort mode a small stack (the data-abort handler uses it) */
    cpsid   if, #0x17           /* Abort mode */
    ldr     sp, =ABORT_STACK_TOP
    cpsid   if, #0x13           /* back to Supervisor mode */

    /* point the CPU's vector base (VBAR) at our table */
    ldr     r0, =_vectors
    mcr     p15, 0, r0, c12, c0, 0

    ldr     sp, =STACK_TOP      /* Supervisor stack */
    bl      monitor_main
1:  b       1b                  /* should never return */

/*
 * Vector table. Must be 32-byte aligned. Only the fault vectors matter;
 * IRQ/FIQ stay masked and reset/svc are unused.
 */
    .align  5
_vectors:
    b       .                   /* 0x00 reset (unused) */
    b       _recover            /* 0x04 undefined instruction */
    b       .                   /* 0x08 supervisor call (unused) */
    b       _recover            /* 0x0c prefetch abort */
    b       _dabt               /* 0x10 data abort */
    b       .                   /* 0x14 (reserved) */
    b       .                   /* 0x18 IRQ (masked) */
    b       .                   /* 0x1c FIQ (masked) */

/*
 * Data abort: count it and return to the instruction *after* the faulting
 * one, so a bad register access does not wedge the monitor. With the MMU
 * off, device accesses are strongly-ordered, so the abort is precise and
 * lr points at faulting_instr + 8.
 */
_dabt:
    sub     lr, lr, #4          /* skip the faulting instruction on return */
    push    {r0, r1}
    ldr     r0, =g_fault
    ldr     r1, [r0]
    add     r1, r1, #1
    str     r1, [r0]
    pop     {r0, r1}
    movs    pc, lr              /* return, restoring CPSR from SPSR */

/*
 * Undefined instruction / prefetch abort: a crashed uploaded program.
 * Count it and re-enter the monitor from scratch (fresh Supervisor stack).
 */
_recover:
    cpsid   if, #0x13           /* Supervisor mode, IRQ/FIQ masked */
    ldr     r0, =g_fault
    ldr     r1, [r0]
    add     r1, r1, #1
    str     r1, [r0]
    ldr     sp, =STACK_TOP
    bl      monitor_main
1:  b       1b
