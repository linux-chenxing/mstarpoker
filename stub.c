/*
 * mstarpoker stub - a tiny serial monitor for bare-metal SoC bring-up.
 *
 * Runs from on-chip SRAM after the boot ROM loads it, and speaks a small
 * binary protocol on a 16550 UART so a host can read/write registers and
 * memory, upload code and run it - to confirm reset/default register
 * values and probe hardware. The protocol is documented in PROTOCOL.md.
 *
 * The monitor is SoC-agnostic; only the small "target configuration"
 * block below (the UART) is chip-specific. Fault handling lives in
 * start.S: a bad access is caught, counted (see the 'F' command) and
 * skipped, so poking around does not wedge the target.
 *
 * Freestanding: no libc.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

typedef unsigned char  u8;
typedef unsigned int   u32;

/* ---- target configuration --------------------------------------------
 * The console UART, a 16550. UART_STRIDE is the byte spacing between its
 * registers (regshift): the data register (RBR/THR) is register 0 and the
 * line status register (LSR) is register 5. Current values target an
 * MStar/SigmaStar infinity2m SoC's uart0 (regshift 3 -> 8-byte stride);
 * the ROM has it configured at 38400 8N1, so we do not re-init it.
 */
#define UART_BASE   0x1f221000u
#define UART_STRIDE 8u
#define UART_DATA   (UART_BASE + 0u * UART_STRIDE)
#define UART_LSR    (UART_BASE + 5u * UART_STRIDE)
#define LSR_RX_RDY  (1u << 0)   /* receive data ready */
#define LSR_TX_RDY  (1u << 5)   /* transmit holding register empty */
/* ---------------------------------------------------------------------- */

/* Count of faults caught by the handlers in start.S (bad pokes / crashes). */
u32 g_fault;

static inline u32  rd32(u32 a)          { return *(volatile u32 *)a; }
static inline void wr32(u32 a, u32 v)   { *(volatile u32 *)a = v; }
static inline u8   rd8(u32 a)           { return *(volatile u8 *)a; }
static inline void wr8(u32 a, u8 v)     { *(volatile u8 *)a = v; }

static u8 uart_getc(void)
{
    while (!(rd32(UART_LSR) & LSR_RX_RDY)) {
    }
    return (u8)rd32(UART_DATA);
}

static void uart_putc(u8 c)
{
    while (!(rd32(UART_LSR) & LSR_TX_RDY)) {
    }
    wr32(UART_DATA, c);
}

/* Little-endian 32-bit word off the wire, LSB first. */
static u32 get32(void)
{
    u32 v = uart_getc();
    v |= (u32)uart_getc() << 8;
    v |= (u32)uart_getc() << 16;
    v |= (u32)uart_getc() << 24;
    return v;
}

static void put32(u32 v)
{
    uart_putc(v & 0xff);
    uart_putc((v >> 8) & 0xff);
    uart_putc((v >> 16) & 0xff);
    uart_putc((v >> 24) & 0xff);
}

static void puts_(const char *s)
{
    while (*s) {
        uart_putc(*s++);
    }
}

void monitor_main(void)
{
    /* Human-readable banner so the host can spot a running stub. */
    puts_("\r\nMPOK1\r\n");

    for (;;) {
        u8 cmd = uart_getc();

        switch (cmd) {
        case 'P':               /* ping -> "SB01" */
            put32(0x31304253u); /* 'S' 'B' '0' '1' little-endian */
            break;

        case 'F':               /* fault count (bad pokes caught so far) */
            put32(g_fault);
            break;

        case 'r': {             /* read one 32-bit word */
            u32 a = get32();
            put32(rd32(a));
            break;
        }
        case 'w': {             /* write one 32-bit word */
            u32 a = get32();
            u32 v = get32();
            wr32(a, v);
            uart_putc('w');     /* ack */
            break;
        }
        case 'R': {             /* read a block of N words */
            u32 a = get32();
            u32 n = get32();
            while (n--) {
                put32(rd32(a));
                a += 4;
            }
            break;
        }
        case 'W': {             /* write a block of N words */
            u32 a = get32();
            u32 n = get32();
            while (n--) {
                wr32(a, get32());
                a += 4;
            }
            uart_putc('W');     /* ack */
            break;
        }
        case 'b': {             /* read one byte */
            u32 a = get32();
            uart_putc(rd8(a));
            break;
        }
        case 'B': {             /* write one byte */
            u32 a = get32();
            u8 v = uart_getc();
            wr8(a, v);
            uart_putc('B');     /* ack */
            break;
        }
        case 'G': {             /* call address (bit0 selects ARM/Thumb) */
            u32 a = get32();
            uart_putc('G');     /* ack before we leave */
            ((void (*)(void))a)();
            puts_("\r\nRET\r\n"); /* if it returns, back to the monitor */
            break;
        }
        default:                /* ignore stray bytes (line noise, sync) */
            break;
        }
    }
}
