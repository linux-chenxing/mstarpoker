// SPDX-License-Identifier: GPL-2.0-or-later

#ifndef _MSTARPOKER_H
#define _MSTARPOKER_H

#ifndef NOLIBC
#define _DEFAULT_SOURCE
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#endif

#include <asm/termbits.h>
#include <asm/ioctls.h>

/*
 * A tiny single-header client for the mstarpoker serial monitor.
 *
 *	struct mstarpoker m = { 0 };
 *	mstarpoker_open_serial(&m, "/dev/ttyUSB0", 38400);	// or _open_socket()
 *	mstarpoker_sync(&m);
 *	mstarpoker_read32(&m, 0x1f203d20, &val);
 *	mstarpoker_upload(&m, 0xa0009000, prog, len);
 *	mstarpoker_go(&m, 0xa0009000);
 *	mstarpoker_close(&m);
 *
 * Speaks the protocol in ../PROTOCOL.md over a serial device or a QEMU unix
 * socket. The stub's replies have a fixed shape per command and no framing,
 * so every read is a "read exactly n bytes before the deadline". Unknown
 * opcodes are ignored by the stub, which is what makes ping a safe resync.
 *
 * Everything is static inline; include it in one translation unit. Builds
 * against libc or nolibc + nolibc-extensions (for the unix socket).
 */

/* Error numbers, returned negated */
#define MSTARPOKER_ERR_OPEN	1	/* could not open/configure the link */
#define MSTARPOKER_ERR_IO	2	/* read/write failed */
#define MSTARPOKER_ERR_TIMEOUT	3	/* the stub did not answer in time */
#define MSTARPOKER_ERR_PROTO	4	/* wrong ack / ping reply */
#define MSTARPOKER_ERR_NOSTUB	5	/* sync gave up: no stub on the link */
#define MSTARPOKER_ERR_YMODEM	6	/* receiver never asked / rejected the file */
#define MSTARPOKER_ERR_CLOSED	7	/* the peer closed the link (socket EOF) */

/* The mask ROM leaves uart0 at 38400 8N1 and the stub does not touch it. */
#define MSTARPOKER_BAUD		38400

/* Default per-reply deadline. Block reads scale it by the reply length. */
#define MSTARPOKER_TIMEOUT_MS	2000

/*
 * A block write bigger than a few KiB overruns the stub's UART FIFO (it
 * writes each word as it arrives, but not faster than the link delivers a
 * burst), so uploads are split into this many words per W command.
 */
#define MSTARPOKER_CHUNK_WORDS	256

/* What the stub prints when it (re)starts, and its ping reply. */
#define MSTARPOKER_BANNER	"MPOK1"
#define MSTARPOKER_PONG		"SB01"

/*
 * Where the mstarpoker-flavoured U-Boot SPL links (CONFIG_SPL_TEXT_BASE):
 * above the monitor, inside the free SRAM scratch. What it prints when it
 * is ready for U-Boot over YMODEM.
 */
#define MSTARPOKER_SPL_ADDR	0xa0004000u
#define MSTARPOKER_SPL_MARKER	"Trying to boot from UART"

struct mstarpoker {
	int fd;
	int timeout_ms;		/* 0 = MSTARPOKER_TIMEOUT_MS */
	bool verbose;		/* trace every frame to stderr */
};

#define __mstarpoker_timeout(m) \
	((m)->timeout_ms > 0 ? (m)->timeout_ms : MSTARPOKER_TIMEOUT_MS)

static inline void __mstarpoker_trace(struct mstarpoker *m, const char *dir,
				      const void *buf, size_t len)
{
	const uint8_t *p = buf;
	size_t i;

	if (!m->verbose || !len)
		return;

	fprintf(stderr, "    %s %3u ", dir, (unsigned int) len);
	if (dir[0] == 'T' && p[0] >= 0x20 && p[0] < 0x7f)
		fprintf(stderr, "'%c'  ", p[0]);
	else
		fprintf(stderr, "     ");
	for (i = 0; i < len && i < 32; i++)
		fprintf(stderr, "%02x ", p[i]);
	fprintf(stderr, "%s\n", len > 32 ? "..." : "");
}

static inline int __mstarpoker_write_all(struct mstarpoker *m, const void *buf,
					 size_t len)
{
	const uint8_t *p = buf;
	size_t done = 0;

	__mstarpoker_trace(m, "TX", buf, len);

	while (done < len) {
		ssize_t n = write(m->fd, p + done, len - done);

		if (n > 0)
			done += (size_t) n;
		else if (n < 0 && (errno == EINTR || errno == EAGAIN))
			poll(NULL, 0, 1);
		else
			return -MSTARPOKER_ERR_IO;
	}

	return 0;
}

/*
 * Read up to len bytes, returning as soon as the link has gone quiet for
 * timeout_ms (or len is reached). Returns the byte count, or -errno style.
 * This is the raw primitive: exact-length reads and console streaming are
 * built on it.
 */
static inline ssize_t __mstarpoker_read_some(struct mstarpoker *m, void *buf,
					     size_t len, int timeout_ms)
{
	uint8_t *p = buf;
	size_t done = 0;

	while (done < len) {
		struct pollfd pfd = { .fd = m->fd, .events = POLLIN };
		ssize_t n;
		int ret;

		ret = poll(&pfd, 1, timeout_ms);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			return -MSTARPOKER_ERR_IO;
		}
		if (ret == 0)
			break;			/* quiet: give back what we have */

		n = read(m->fd, p + done, len - done);
		if (n > 0)
			done += (size_t) n;
		else if (n == 0)
			break;			/* peer closed: hand back what we have */
		else if (errno != EINTR && errno != EAGAIN)
			return -MSTARPOKER_ERR_IO;
	}

	if (!done && len) {
		/* nothing at all: tell a closed socket apart from a quiet one */
		struct pollfd pfd = { .fd = m->fd, .events = POLLIN };

		if (poll(&pfd, 1, 0) > 0 && (pfd.revents & (POLLHUP | POLLIN)))
			return -MSTARPOKER_ERR_CLOSED;
	}

	__mstarpoker_trace(m, "RX", buf, done);

	return (ssize_t) done;
}

/* Read exactly len bytes, each within the deadline. */
static inline int __mstarpoker_read_all(struct mstarpoker *m, void *buf,
					size_t len, int timeout_ms)
{
	ssize_t n = __mstarpoker_read_some(m, buf, len, timeout_ms);

	if (n < 0)
		return (int) n;

	return (size_t) n == len ? 0 : -MSTARPOKER_ERR_TIMEOUT;
}

static inline void __mstarpoker_put32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t) v;
	p[1] = (uint8_t) (v >> 8);
	p[2] = (uint8_t) (v >> 16);
	p[3] = (uint8_t) (v >> 24);
}

static inline uint32_t __mstarpoker_get32(const uint8_t *p)
{
	return (uint32_t) p[0] | ((uint32_t) p[1] << 8) |
	       ((uint32_t) p[2] << 16) | ((uint32_t) p[3] << 24);
}

/* Send a command and expect the single ack byte the stub echoes back. */
static inline int __mstarpoker_cmd_ack(struct mstarpoker *m, const uint8_t *cmd,
				       size_t len, uint8_t ack)
{
	uint8_t got;
	int ret;

	ret = __mstarpoker_write_all(m, cmd, len);
	if (ret)
		return ret;

	ret = __mstarpoker_read_all(m, &got, 1, __mstarpoker_timeout(m));
	if (ret)
		return ret;

	return got == ack ? 0 : -MSTARPOKER_ERR_PROTO;
}

/* --- opening the link ---------------------------------------------------- */

/*
 * A real serial device: raw, 8N1, no flow control, at baud (BOTHER, so any
 * rate the UART can do). Pending input is dropped so an old banner or line
 * noise cannot be mistaken for a reply.
 */
static inline int mstarpoker_open_serial(struct mstarpoker *m, const char *dev,
					 int baud)
{
	struct termios2 tio;
	int fd;

	fd = open(dev, O_RDWR | O_NOCTTY);
	if (fd < 0)
		return -MSTARPOKER_ERR_OPEN;

	if (ioctl(fd, TCGETS2, &tio)) {
		close(fd);
		return -MSTARPOKER_ERR_OPEN;
	}

	tio.c_iflag = 0;
	tio.c_oflag = 0;
	tio.c_lflag = 0;
	tio.c_cflag = CREAD | CLOCAL | CS8 | BOTHER;
	tio.c_ispeed = baud > 0 ? baud : MSTARPOKER_BAUD;
	tio.c_ospeed = tio.c_ispeed;
	tio.c_cc[VMIN] = 0;
	tio.c_cc[VTIME] = 0;

	if (ioctl(fd, TCSETS2, &tio)) {
		close(fd);
		return -MSTARPOKER_ERR_OPEN;
	}

	ioctl(fd, TCFLSH, TCIOFLUSH);

	m->fd = fd;
	return 0;
}

/* A QEMU "-serial unix:<path>,server" socket. */
static inline int mstarpoker_open_socket(struct mstarpoker *m, const char *path)
{
	struct sockaddr_un sa;
	size_t len = strlen(path);
	int fd;

	if (len >= sizeof(sa.sun_path))
		return -MSTARPOKER_ERR_OPEN;

	fd = socket(AF_UNIX, SOCK_STREAM, 0);
	if (fd < 0)
		return -MSTARPOKER_ERR_OPEN;

	memset(&sa, 0, sizeof(sa));
	sa.sun_family = AF_UNIX;
	memcpy(sa.sun_path, path, len + 1);

	if (connect(fd, (struct sockaddr *) &sa, sizeof(sa))) {
		close(fd);
		return -MSTARPOKER_ERR_OPEN;
	}

	m->fd = fd;
	return 0;
}

static inline void mstarpoker_close(struct mstarpoker *m)
{
	if (m->fd >= 0)
		close(m->fd);
	m->fd = -1;
}

/* --- protocol commands --------------------------------------------------- */

/* One ping. 0 if the stub answered, -PROTO/-TIMEOUT otherwise. */
static inline int mstarpoker_ping(struct mstarpoker *m)
{
	uint8_t buf[4];
	int ret;

	ret = __mstarpoker_write_all(m, "P", 1);
	if (ret)
		return ret;

	ret = __mstarpoker_read_all(m, buf, 4, __mstarpoker_timeout(m));
	if (ret)
		return ret;

	return memcmp(buf, MSTARPOKER_PONG, 4) ? -MSTARPOKER_ERR_PROTO : 0;
}

/*
 * Ping until the stub answers, tolerating boot-time noise: the reply may
 * arrive glued to the ROM's or the stub's own banner, so anything that comes
 * back is searched for the pong rather than compared whole.
 */
static inline int mstarpoker_sync(struct mstarpoker *m)
{
	uint8_t buf[64];
	int i;

	for (i = 0; i < 50; i++) {
		ssize_t n;
		int ret;

		ret = __mstarpoker_write_all(m, "P", 1);
		if (ret)
			return ret;

		n = __mstarpoker_read_some(m, buf, sizeof(buf), 200);
		if (n < 0)
			return (int) n;
		if (n >= 4) {
			ssize_t j;

			for (j = 0; j + 4 <= n; j++)
				if (!memcmp(buf + j, MSTARPOKER_PONG, 4))
					goto found;
		}
	}

	return -MSTARPOKER_ERR_NOSTUB;

found:
	/*
	 * If the stub was busy (running uploaded code, say) the earlier pings
	 * queued in its UART and it answers every one of them once it is
	 * back, so drain the pongs still in flight: otherwise the next
	 * command reads a stale "SB01" as its reply.
	 */
	while (__mstarpoker_read_some(m, buf, sizeof(buf), 100) > 0)
		;

	return 0;
}

/* Number of faults (bad pokes / crashes) the stub has caught so far. */
static inline int mstarpoker_faults(struct mstarpoker *m, uint32_t *count)
{
	uint8_t buf[4];
	int ret;

	ret = __mstarpoker_write_all(m, "F", 1);
	if (ret)
		return ret;

	ret = __mstarpoker_read_all(m, buf, 4, __mstarpoker_timeout(m));
	if (ret)
		return ret;

	*count = __mstarpoker_get32(buf);
	return 0;
}

static inline int mstarpoker_read32(struct mstarpoker *m, uint32_t addr,
				    uint32_t *val)
{
	uint8_t buf[5] = { 'r' };
	int ret;

	__mstarpoker_put32(buf + 1, addr);
	ret = __mstarpoker_write_all(m, buf, sizeof(buf));
	if (ret)
		return ret;

	ret = __mstarpoker_read_all(m, buf, 4, __mstarpoker_timeout(m));
	if (ret)
		return ret;

	*val = __mstarpoker_get32(buf);
	return 0;
}

static inline int mstarpoker_write32(struct mstarpoker *m, uint32_t addr,
				     uint32_t val)
{
	uint8_t buf[9] = { 'w' };

	__mstarpoker_put32(buf + 1, addr);
	__mstarpoker_put32(buf + 5, val);
	return __mstarpoker_cmd_ack(m, buf, sizeof(buf), 'w');
}

static inline int mstarpoker_read8(struct mstarpoker *m, uint32_t addr,
				   uint8_t *val)
{
	uint8_t buf[5] = { 'b' };
	int ret;

	__mstarpoker_put32(buf + 1, addr);
	ret = __mstarpoker_write_all(m, buf, sizeof(buf));
	if (ret)
		return ret;

	return __mstarpoker_read_all(m, val, 1, __mstarpoker_timeout(m));
}

static inline int mstarpoker_write8(struct mstarpoker *m, uint32_t addr,
				    uint8_t val)
{
	uint8_t buf[6] = { 'B' };

	__mstarpoker_put32(buf + 1, addr);
	buf[5] = val;
	return __mstarpoker_cmd_ack(m, buf, sizeof(buf), 'B');
}

/*
 * The stub has no 16-bit access; RIU registers are 16 bits wide on a 4-byte
 * stride, so read32 returns them in the low half and a halfword write is
 * two byte writes.
 */
static inline int mstarpoker_write16(struct mstarpoker *m, uint32_t addr,
				     uint16_t val)
{
	int ret;

	ret = mstarpoker_write8(m, addr, (uint8_t) val);
	if (ret)
		return ret;

	return mstarpoker_write8(m, addr + 1, (uint8_t) (val >> 8));
}

/* Read nwords consecutive words starting at addr into words[]. */
static inline int mstarpoker_read_block(struct mstarpoker *m, uint32_t addr,
					uint32_t *words, uint32_t nwords)
{
	uint8_t hdr[9] = { 'R' };
	uint32_t i;
	int ret;

	if (!nwords)
		return 0;

	__mstarpoker_put32(hdr + 1, addr);
	__mstarpoker_put32(hdr + 5, nwords);
	ret = __mstarpoker_write_all(m, hdr, sizeof(hdr));
	if (ret)
		return ret;

	/* the stub reads each word from the bus as it sends, so the reply is
	 * one deadline per word on top of the base
	 */
	ret = __mstarpoker_read_all(m, words, 4 * (size_t) nwords,
				    __mstarpoker_timeout(m) + (int) nwords);
	if (ret)
		return ret;

	for (i = 0; i < nwords; i++)
		words[i] = __mstarpoker_get32((uint8_t *) &words[i]);

	return 0;
}

/* Write nwords consecutive words from words[] at addr (one W command). */
static inline int mstarpoker_write_block(struct mstarpoker *m, uint32_t addr,
					 const uint32_t *words, uint32_t nwords)
{
	uint8_t buf[9 + 4 * MSTARPOKER_CHUNK_WORDS];
	uint32_t i;

	if (nwords > MSTARPOKER_CHUNK_WORDS)
		return -MSTARPOKER_ERR_IO;

	buf[0] = 'W';
	__mstarpoker_put32(buf + 1, addr);
	__mstarpoker_put32(buf + 5, nwords);
	for (i = 0; i < nwords; i++)
		__mstarpoker_put32(buf + 9 + 4 * i, words[i]);

	return __mstarpoker_cmd_ack(m, buf, 9 + 4 * (size_t) nwords, 'W');
}

/*
 * Upload raw bytes to addr in chunked block writes. A trailing partial word
 * is zero-padded, as the stub only writes whole words.
 */
static inline int mstarpoker_upload(struct mstarpoker *m, uint32_t addr,
				    const void *data, size_t len)
{
	const uint8_t *p = data;
	size_t done = 0;

	while (done < len) {
		uint32_t words[MSTARPOKER_CHUNK_WORDS];
		uint32_t n = 0;
		int ret;

		while (n < MSTARPOKER_CHUNK_WORDS && done < len) {
			uint8_t w[4] = { 0, 0, 0, 0 };
			size_t k, left = len - done;

			for (k = 0; k < 4 && k < left; k++)
				w[k] = p[done + k];
			words[n++] = __mstarpoker_get32(w);
			done += k;
		}

		ret = mstarpoker_write_block(m, addr, words, n);
		if (ret)
			return ret;
		addr += 4 * n;
	}

	return 0;
}

/* Download len bytes from addr (rounded up to whole words) into data. */
static inline int mstarpoker_download(struct mstarpoker *m, uint32_t addr,
				      void *data, size_t len)
{
	uint8_t *p = data;
	size_t done = 0;

	while (done < len) {
		uint32_t words[MSTARPOKER_CHUNK_WORDS];
		size_t left = len - done;
		uint32_t n = (uint32_t) ((left + 3) / 4);
		uint32_t i;
		int ret;

		if (n > MSTARPOKER_CHUNK_WORDS)
			n = MSTARPOKER_CHUNK_WORDS;

		ret = mstarpoker_read_block(m, addr, words, n);
		if (ret)
			return ret;

		for (i = 0; i < n && done < len; i++) {
			uint8_t w[4];
			size_t k;

			__mstarpoker_put32(w, words[i]);
			for (k = 0; k < 4 && done < len; k++)
				p[done++] = w[k];
		}
		addr += 4 * n;
	}

	return 0;
}

/*
 * Call addr (bit 0 selects Thumb). Returns once the stub has acked the
 * command, i.e. just before it jumps; whatever the code prints, and the
 * "RET" the stub prints if it comes back, is left on the link for
 * mstarpoker_console() to collect.
 */
static inline int mstarpoker_go(struct mstarpoker *m, uint32_t addr)
{
	uint8_t buf[5] = { 'G' };

	__mstarpoker_put32(buf + 1, addr);
	return __mstarpoker_cmd_ack(m, buf, sizeof(buf), 'G');
}

/*
 * Read a word and report whether the access faulted, by bracketing it with
 * the fault counter. The value is meaningless when *faulted is set (the
 * stub skipped the faulting load) but the stub is still alive. This is the
 * safe way to look at a register you know nothing about.
 */
static inline int mstarpoker_probe(struct mstarpoker *m, uint32_t addr,
				   uint32_t *val, bool *faulted)
{
	uint32_t before, after;
	int ret;

	ret = mstarpoker_faults(m, &before);
	if (ret)
		return ret;

	ret = mstarpoker_read32(m, addr, val);
	if (ret)
		return ret;

	ret = mstarpoker_faults(m, &after);
	if (ret)
		return ret;

	*faulted = after != before;
	return 0;
}

/*
 * Stream whatever the target prints to fd_out until the link has been quiet
 * for idle_ms (-1: forever, until the read fails or the process is killed).
 * Returns the number of bytes copied or an error.
 */
static inline ssize_t mstarpoker_console(struct mstarpoker *m, int fd_out,
					 int idle_ms)
{
	ssize_t total = 0;

	for (;;) {
		uint8_t buf[256];
		ssize_t n = __mstarpoker_read_some(m, buf, sizeof(buf), idle_ms);

		if (n == -MSTARPOKER_ERR_CLOSED)
			return total;		/* a socket peer went away: the end */
		if (n < 0)
			return n;
		if (n == 0)
			return total;

		total += n;
		if (write(fd_out, buf, (size_t) n) < 0)
			return -MSTARPOKER_ERR_IO;
	}
}

/* --- console helpers ---------------------------------------------------- */

/* Milliseconds from an arbitrary origin, for deadlines. */
static inline long __mstarpoker_now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

/* Echo one byte of console text: printable, CR and LF only. */
static inline void __mstarpoker_echo(int fd_out, uint8_t b)
{
	if (fd_out >= 0 && (b == '\r' || b == '\n' || (b >= 0x20 && b < 0x7f)))
		if (write(fd_out, &b, 1) < 0)
			return;
}

/*
 * Echo the console to fd_out (-1: discard) until `marker` has been seen or
 * timeout_ms has passed. 0 when found, -TIMEOUT otherwise. Used to spot
 * the SPL reaching its UART boot stage before starting YMODEM, so a 'C' in
 * earlier console text (say "CPUPLL") cannot start the transfer early.
 */
static inline int mstarpoker_wait_marker(struct mstarpoker *m,
					 const char *marker, int timeout_ms,
					 int fd_out)
{
	size_t mlen = strlen(marker), matched = 0;
	long deadline = __mstarpoker_now_ms() + timeout_ms;

	while (__mstarpoker_now_ms() < deadline) {
		uint8_t b;
		ssize_t n = __mstarpoker_read_some(m, &b, 1, 500);

		if (n < 0)
			return (int) n;
		if (n == 0)
			continue;

		__mstarpoker_echo(fd_out, b);

		/* plain prefix match; the marker has no repeated prefix */
		if (b == (uint8_t) marker[matched]) {
			if (++matched == mlen)
				return 0;
		} else {
			matched = b == (uint8_t) marker[0] ? 1 : 0;
		}
	}

	return -MSTARPOKER_ERR_TIMEOUT;
}

/* --- YMODEM sender ------------------------------------------------------ */

/*
 * Send one file to a waiting YMODEM receiver, as the SPL's xyzModem is.
 *
 * Tuned to xyzModem's habits, which differ from the textbook: it ACKs a
 * block lazily, just before it reads the next header, so there is no
 * fresh 'C' after the header block - block 1 must simply follow; on EOT it
 * answers ACK (twice) and then 'C' for the closing null header; and it
 * spams 'C' every ~2 s while waiting, so stray bytes are drained, never
 * treated as errors.
 */
#define MSTARPOKER_YM_SOH	0x01
#define MSTARPOKER_YM_STX	0x02
#define MSTARPOKER_YM_EOT	0x04
#define MSTARPOKER_YM_ACK	0x06
#define MSTARPOKER_YM_NAK	0x15
#define MSTARPOKER_YM_CAN	0x18
#define MSTARPOKER_YM_CRC	0x43	/* 'C': receiver wants CRC-16 mode */
#define MSTARPOKER_YM_PAD	0x1a	/* ^Z pads the last data block */

#define MSTARPOKER_YM_BLOCK	1024
#define MSTARPOKER_YM_RETRIES	20
#define MSTARPOKER_YM_WAIT_C_MS	30000	/* the SPL brings DDR up first */

static inline uint16_t __mstarpoker_crc16(const uint8_t *p, size_t len)
{
	uint16_t crc = 0;
	size_t i;
	int b;

	for (i = 0; i < len; i++) {
		crc ^= (uint16_t) p[i] << 8;
		for (b = 0; b < 8; b++)
			crc = (crc & 0x8000) ? (uint16_t) ((crc << 1) ^ 0x1021) :
					       (uint16_t) (crc << 1);
	}

	return crc;
}

#define __mstarpoker_ym_dbg(m, ...) \
	do { if ((m)->verbose) fprintf(stderr, "[ym] " __VA_ARGS__); } while (0)

/*
 * Read bytes until one of the `wanted` control bytes arrives, ignoring the
 * rest, for up to timeout_ms. Returns the byte or -1 on timeout, -IO on a
 * dead link.
 */
static inline int __mstarpoker_ym_wait(struct mstarpoker *m, const char *wanted,
				       int timeout_ms)
{
	long deadline = __mstarpoker_now_ms() + timeout_ms;

	while (__mstarpoker_now_ms() < deadline) {
		uint8_t b;
		ssize_t n = __mstarpoker_read_some(m, &b, 1, 500);

		if (n < 0)
			return (int) n;
		if (n == 0)
			continue;
		if (memchr(wanted, b, strlen(wanted)))
			return b;
		__mstarpoker_ym_dbg(m, "  rx 0x%02x (waiting for one of %s)\n",
				    b, wanted);
	}

	return -1;
}

/* Discard whatever is pending, e.g. a backlog of 'C' requests. */
static inline void __mstarpoker_ym_drain(struct mstarpoker *m)
{
	uint8_t buf[64];

	while (__mstarpoker_read_some(m, buf, sizeof(buf), 50) > 0)
		;
}

/* One block (128 or 1024 bytes); retransmit until ACKed. 0 / -YMODEM. */
static inline int __mstarpoker_ym_block(struct mstarpoker *m, uint8_t seq,
					const uint8_t *payload, size_t len)
{
	uint8_t frame[3 + MSTARPOKER_YM_BLOCK + 2];
	uint16_t crc = __mstarpoker_crc16(payload, len);
	int attempt;

	frame[0] = len == MSTARPOKER_YM_BLOCK ? MSTARPOKER_YM_STX :
					      MSTARPOKER_YM_SOH;
	frame[1] = seq;
	frame[2] = (uint8_t) (0xff - seq);
	memcpy(frame + 3, payload, len);
	frame[3 + len] = (uint8_t) (crc >> 8);
	frame[4 + len] = (uint8_t) crc;

	for (attempt = 0; attempt < MSTARPOKER_YM_RETRIES; attempt++) {
		int r, ret;

		__mstarpoker_ym_dbg(m, "tx block %u (%u bytes)%s\n", seq,
				    (unsigned int) len,
				    attempt ? " retry" : "");
		ret = __mstarpoker_write_all(m, frame, 5 + len);
		if (ret)
			return ret;

		r = __mstarpoker_ym_wait(m, "\x06\x18", 2000);	/* ACK, CAN */
		if (r == MSTARPOKER_YM_ACK)
			return 0;
		if (r == MSTARPOKER_YM_CAN)
			return -MSTARPOKER_ERR_YMODEM;
		if (r < -1)
			return r;
		/* timeout, or only 'C'/NAK seen: send it again */
	}

	return -MSTARPOKER_ERR_YMODEM;
}

static inline void __mstarpoker_ym_progress(struct mstarpoker *m,
					    const char *name, size_t done,
					    size_t total)
{
	int width = 32, filled, i;

	if (m->verbose || !total)
		return;

	filled = (int) ((unsigned long long) done * width / total);
	fprintf(stderr, "\r  %s [", name);
	for (i = 0; i < width; i++)
		fputc(i < filled ? '#' : '.', stderr);
	fprintf(stderr, "] %3u%%  %u/%u KiB",
		(unsigned int) ((unsigned long long) done * 100 / total),
		(unsigned int) (done / 1024), (unsigned int) (total / 1024));
	if (done >= total)
		fputc('\n', stderr);
}

/*
 * Wait for the receiver's initial 'C' (echoing any console text before it
 * to fd_out), then send `data` as `name`, then close the batch. The link is
 * left as it is afterwards: whatever the target prints next is still there
 * for mstarpoker_console().
 */
static inline int mstarpoker_ymodem_send(struct mstarpoker *m, const char *name,
					 const uint8_t *data, size_t len,
					 int fd_out)
{
	uint8_t hdr[128];
	long deadline = __mstarpoker_now_ms() + MSTARPOKER_YM_WAIT_C_MS;
	size_t off = 0, nlen = strlen(name);
	char sz[16];
	uint8_t seq = 1;
	int ret, r;

	/*
	 * The receiver's 'C' is sent on its own, then the line goes quiet
	 * (it repeats every ~2 s). A 'C' inside console text ("CPUPLL") is
	 * followed straight away by more text, so only a 'C' with nothing
	 * behind it - or another 'C' - counts as the handshake.
	 */
	__mstarpoker_ym_dbg(m, "waiting for the receiver's 'C'\n");
	for (;;) {
		uint8_t b, next;
		ssize_t n;

		if (__mstarpoker_now_ms() >= deadline)
			return -MSTARPOKER_ERR_YMODEM;
		n = __mstarpoker_read_some(m, &b, 1, 500);
		if (n < 0)
			return (int) n;
		if (n == 0)
			continue;
		if (b != MSTARPOKER_YM_CRC) {
			__mstarpoker_echo(fd_out, b);
			continue;
		}
		n = __mstarpoker_read_some(m, &next, 1, 300);
		if (n < 0)
			return (int) n;
		if (n == 0 || next == MSTARPOKER_YM_CRC)
			break;
		__mstarpoker_echo(fd_out, b);
		__mstarpoker_echo(fd_out, next);
	}
	__mstarpoker_ym_drain(m);

	/* block 0: "<name>\0<size>\0", zero padded to 128 bytes */
	memset(hdr, 0, sizeof(hdr));
	if (nlen > 100)
		nlen = 100;
	memcpy(hdr, name, nlen);
	snprintf(sz, sizeof(sz), "%u", (unsigned int) len);
	memcpy(hdr + nlen + 1, sz, strlen(sz));
	ret = __mstarpoker_ym_block(m, 0, hdr, sizeof(hdr));
	if (ret)
		return ret;

	/* no second 'C' is coming (see above); swallow one if it is there */
	__mstarpoker_ym_wait(m, "\x43", 300);

	__mstarpoker_ym_progress(m, name, 0, len);
	while (off < len) {
		uint8_t blk[MSTARPOKER_YM_BLOCK];
		size_t n = len - off < sizeof(blk) ? len - off : sizeof(blk);

		memcpy(blk, data + off, n);
		if (n < sizeof(blk))
			memset(blk + n, MSTARPOKER_YM_PAD, sizeof(blk) - n);
		ret = __mstarpoker_ym_block(m, seq++, blk, sizeof(blk));
		if (ret) {
			if (!m->verbose)
				fputc('\n', stderr);
			return ret;
		}
		off += n;
		__mstarpoker_ym_progress(m, name, off, len);
	}

	/* end: EOT (a textbook receiver NAKs the first, so send it twice) */
	__mstarpoker_ym_dbg(m, "all %u blocks sent; EOT\n", (unsigned int) (seq - 1));
	ret = __mstarpoker_write_all(m, "\x04", 1);
	if (ret)
		return ret;
	r = __mstarpoker_ym_wait(m, "\x06\x15", 3000);		/* ACK, NAK */
	if (r == MSTARPOKER_YM_NAK) {
		ret = __mstarpoker_write_all(m, "\x04", 1);
		if (ret)
			return ret;
		__mstarpoker_ym_wait(m, "\x06", 3000);
	}

	/* the receiver asks for the next file; an empty header ends the batch */
	__mstarpoker_ym_wait(m, "\x43", 3000);
	memset(hdr, 0, sizeof(hdr));
	__mstarpoker_ym_block(m, 0, hdr, sizeof(hdr));
	__mstarpoker_ym_dbg(m, "batch closed\n");

	return 0;
}

/*
 * The whole SPL handoff in one go, so the port is opened once and nothing
 * the SPL prints is lost to a reopen: upload the SPL, check the first word
 * came back, run it, echo its console until it reaches its UART boot stage
 * (or timeout_ms passes), then send U-Boot over YMODEM. Returns 0 once the
 * transfer is done; the console is left for mstarpoker_console().
 */
static inline int mstarpoker_spl(struct mstarpoker *m, uint32_t addr,
				 const uint8_t *spl, size_t spl_len,
				 const char *uboot_name, const uint8_t *uboot,
				 size_t uboot_len, int timeout_ms, int fd_out)
{
	uint32_t first, want;
	int ret;

	ret = mstarpoker_upload(m, addr, spl, spl_len);
	if (ret)
		return ret;

	want = spl_len >= 4 ? __mstarpoker_get32(spl) : 0;
	ret = mstarpoker_read32(m, addr, &first);
	if (ret)
		return ret;
	if (first != want)
		return -MSTARPOKER_ERR_PROTO;

	ret = mstarpoker_go(m, addr);
	if (ret)
		return ret;

	ret = mstarpoker_wait_marker(m, MSTARPOKER_SPL_MARKER, timeout_ms, fd_out);
	if (ret)
		return ret;

	return mstarpoker_ymodem_send(m, uboot_name, uboot, uboot_len, fd_out);
}

#endif /* _MSTARPOKER_H */
