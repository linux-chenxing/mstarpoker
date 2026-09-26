/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * cpuspeed.c - measure the CPU clock against the PM timer.
 *
 * Uploaded to SRAM and called via the mstarpoker stub's 'G'. It runs a fixed
 * count of a known-length loop and records how many PM-timer ticks it took, so
 * the host can turn that into a CPU rate (the PM timer is a fixed ~12 MHz
 * reference - see timer_test.py). Comparing the rate before and after
 * programming the cpupll shows what the PLL does to the CPU clock.
 *
 * Result, written to SRAM at 0xa0009000 for the host to read:
 *   [0] = PM-timer ticks elapsed during the loop
 *   [1] = loop iteration count
 *   [2] = done marker (0x600dcafe) - lets the host confirm the loop finished
 *         (if the CPU is slow the loop can outlast the host's go() timeout;
 *         the marker distinguishes "still running / desynced" from a real run)
 */

typedef unsigned int   u32;
typedef unsigned short u16;

#define TIMER  0x1f006050u      /* PM free-running counter: +0 low16, +4 high16 */
#define RESULT 0xa0009000u
#define DONE   0x600dcafeu

#define RH(a)  (*(volatile u16 *)(unsigned long)(a))

#define LOOPS  1000000u         /* fixed iteration count (bounded even at a slow clock) */

static u32 timer_now(void)
{
    u32 lo = RH(TIMER);
    u32 hi = RH(TIMER + 4);
    return lo | (hi << 16);
}

__attribute__((section(".text.start"), used))
void cpuspeed(void)
{
    volatile u32 *out = (volatile u32 *)(unsigned long)RESULT;
    u32 c = LOOPS;
    u32 t0, t1;

    t0 = timer_now();
    /* A tight, fully-predicted subs/bne loop - one pass per iteration,
     * running from I-cache, so its length tracks the CPU clock. */
    __asm__ volatile ("1: subs %0, %0, #1\n\t"
                      "   bne 1b\n"
                      : "+r" (c) : : "cc");
    t1 = timer_now();

    out[0] = t1 - t0;
    out[1] = LOOPS;
    out[2] = DONE;
}
