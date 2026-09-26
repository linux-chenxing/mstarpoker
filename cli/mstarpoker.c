/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Drive the mstarpoker serial monitor from the command line.
 *
 *	mstarpoker [-s <serial> [-b baud] | -u <socket>] [-t ms] [-v] <cmd> ...
 *
 *	ping                        is the stub there?
 *	faults                      faults the stub has caught so far
 *	probe <addr>                read a word, reporting a faulting access
 *	rd <addr>                   read a word
 *	wr <addr> <val>             write a word
 *	rd8 <addr>                  read a byte
 *	wr8 <addr> <val>            write a byte
 *	wr16 <addr> <val>           write a halfword (two byte writes)
 *	dump <addr> <nwords>        hex dump of nwords words
 *	save <addr> <nbytes> <file> download memory to a file
 *	load <addr> <file>          upload a file to memory
 *	go <addr> [idle_ms]         call addr, then print its output
 *	console [idle_ms]           print whatever the target sends
 *
 * Numbers take a 0x prefix for hex. One transport is required: -s for a
 * real serial port (38400 8N1 by default, the rate the ROM leaves uart0
 * at) or -u for a QEMU "-serial unix:" socket. -v traces every frame.
 */
#include "mstarpoker.h"

#ifndef NOLIBC
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <getopt.h>
#include <fcntl.h>
#endif

/* Biggest file load/save handles in one go; SRAM is 64 KiB. */
#define FILE_MAX	(1024 * 1024)

static void usage(const char *argv0)
{
	fprintf(stderr,
		"usage: %s [-s <serial> [-b baud] | -u <socket>] [-t ms] [-v] <cmd> [args]\n"
		"  ping | faults | probe <addr> | rd <addr> | wr <addr> <val>\n"
		"  rd8 <addr> | wr8 <addr> <val> | wr16 <addr> <val>\n"
		"  dump <addr> <nwords> | save <addr> <nbytes> <file> | load <addr> <file>\n"
		"  go <addr> [idle_ms] | console [idle_ms]\n",
		argv0);
}

static const char *errstr(int err)
{
	switch (-err) {
	case MSTARPOKER_ERR_OPEN:
		return "cannot open the link";
	case MSTARPOKER_ERR_IO:
		return "i/o error";
	case MSTARPOKER_ERR_TIMEOUT:
		return "timeout waiting for the stub";
	case MSTARPOKER_ERR_PROTO:
		return "unexpected reply from the stub";
	case MSTARPOKER_ERR_NOSTUB:
		return "no response from stub (is it running?)";
	default:
		return "error";
	}
}

static int fail(const char *what, int err)
{
	fprintf(stderr, "%s: %s (%d)\n", what, errstr(err), err);
	return 1;
}

static uint32_t num(const char *s)
{
	return (uint32_t) strtoul(s, NULL, 0);
}

static int read_file(const char *path, uint8_t **out, size_t *len)
{
	uint8_t *buf;
	size_t done = 0;
	int fd;

	fd = open(path, O_RDONLY);
	if (fd < 0)
		return -1;

	buf = malloc(FILE_MAX);
	if (!buf) {
		close(fd);
		return -1;
	}

	for (;;) {
		ssize_t n = read(fd, buf + done, FILE_MAX - done);

		if (n < 0) {
			close(fd);
			free(buf);
			return -1;
		}
		if (n == 0)
			break;
		done += (size_t) n;
		if (done == FILE_MAX)
			break;
	}

	close(fd);
	*out = buf;
	*len = done;
	return 0;
}

static int write_file(const char *path, const uint8_t *buf, size_t len)
{
	size_t done = 0;
	int fd;

	fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (fd < 0)
		return -1;

	while (done < len) {
		ssize_t n = write(fd, buf + done, len - done);

		if (n <= 0) {
			close(fd);
			return -1;
		}
		done += (size_t) n;
	}

	close(fd);
	return 0;
}

static int cmd_dump(struct mstarpoker *m, uint32_t addr, uint32_t nwords)
{
	while (nwords) {
		uint32_t words[MSTARPOKER_CHUNK_WORDS];
		uint32_t n = nwords < MSTARPOKER_CHUNK_WORDS ? nwords :
							      MSTARPOKER_CHUNK_WORDS;
		uint32_t i;
		int ret;

		ret = mstarpoker_read_block(m, addr, words, n);
		if (ret)
			return fail("read", ret);

		for (i = 0; i < n; i++) {
			if (i % 4 == 0)
				printf("0x%08x:", addr + 4 * i);
			printf(" %08x", words[i]);
			if (i % 4 == 3 || i + 1 == n)
				printf("\n");
		}

		addr += 4 * n;
		nwords -= n;
	}

	return 0;
}

int main(int argc, char **argv)
{
	struct mstarpoker m = { .fd = -1 };
	const char *serial = NULL, *sock = NULL, *cmd;
	int baud = MSTARPOKER_BAUD;
	int opt, ret, nargs;
	char **args;

	while ((opt = getopt(argc, argv, "s:u:b:t:v")) != -1) {
		switch (opt) {
		case 's':
			serial = optarg;
			break;
		case 'u':
			sock = optarg;
			break;
		case 'b':
			baud = atoi(optarg);
			break;
		case 't':
			m.timeout_ms = atoi(optarg);
			break;
		case 'v':
			m.verbose = true;
			break;
		default:
			usage(argv[0]);
			return 1;
		}
	}

	if (optind >= argc || (!serial && !sock) || (serial && sock)) {
		usage(argv[0]);
		return 1;
	}
	cmd = argv[optind];
	args = argv + optind + 1;
	nargs = argc - optind - 1;

	ret = serial ? mstarpoker_open_serial(&m, serial, baud) :
		       mstarpoker_open_socket(&m, sock);
	if (ret)
		return fail(serial ? serial : sock, ret);

	/* console just listens; everything else needs a live stub first */
	if (strcmp(cmd, "console")) {
		ret = mstarpoker_sync(&m);
		if (ret)
			return fail("sync", ret);
	}

	if (!strcmp(cmd, "ping") && nargs == 0) {
		ret = mstarpoker_ping(&m);
		printf("%s\n", ret ? "no response" : "pong");
	} else if (!strcmp(cmd, "faults") && nargs == 0) {
		uint32_t count;

		ret = mstarpoker_faults(&m, &count);
		if (!ret)
			printf("fault count: %u\n", count);
	} else if (!strcmp(cmd, "probe") && nargs == 1) {
		uint32_t addr = num(args[0]), val;
		bool faulted;

		ret = mstarpoker_probe(&m, addr, &val, &faulted);
		if (!ret && faulted)
			printf("0x%08x: FAULTED (access aborted, stub still alive)\n",
			       addr);
		else if (!ret)
			printf("0x%08x: 0x%08x\n", addr, val);
	} else if (!strcmp(cmd, "rd") && nargs == 1) {
		uint32_t addr = num(args[0]), val;

		ret = mstarpoker_read32(&m, addr, &val);
		if (!ret)
			printf("0x%08x: 0x%08x\n", addr, val);
	} else if (!strcmp(cmd, "wr") && nargs == 2) {
		uint32_t addr = num(args[0]), val = num(args[1]);

		ret = mstarpoker_write32(&m, addr, val);
		if (!ret)
			printf("wrote 0x%08x = 0x%08x\n", addr, val);
	} else if (!strcmp(cmd, "rd8") && nargs == 1) {
		uint32_t addr = num(args[0]);
		uint8_t val;

		ret = mstarpoker_read8(&m, addr, &val);
		if (!ret)
			printf("0x%08x: 0x%02x\n", addr, val);
	} else if (!strcmp(cmd, "wr8") && nargs == 2) {
		uint32_t addr = num(args[0]), val = num(args[1]);

		ret = mstarpoker_write8(&m, addr, (uint8_t) val);
		if (!ret)
			printf("wrote 0x%08x = 0x%02x\n", addr, val & 0xff);
	} else if (!strcmp(cmd, "wr16") && nargs == 2) {
		uint32_t addr = num(args[0]), val = num(args[1]);

		ret = mstarpoker_write16(&m, addr, (uint16_t) val);
		if (!ret)
			printf("wrote 0x%08x = 0x%04x\n", addr, val & 0xffff);
	} else if (!strcmp(cmd, "dump") && nargs == 2) {
		ret = cmd_dump(&m, num(args[0]), num(args[1]));
		mstarpoker_close(&m);
		return ret;
	} else if (!strcmp(cmd, "save") && nargs == 3) {
		uint32_t addr = num(args[0]);
		size_t len = num(args[1]);
		uint8_t *buf;

		if (len > FILE_MAX) {
			fprintf(stderr, "save: at most %d bytes\n", FILE_MAX);
			return 1;
		}
		buf = malloc(len ? len : 1);
		if (!buf)
			return 1;
		ret = mstarpoker_download(&m, addr, buf, len);
		if (!ret && write_file(args[2], buf, len)) {
			fprintf(stderr, "cannot write %s\n", args[2]);
			return 1;
		}
		if (!ret)
			printf("saved %u bytes from 0x%08x to %s\n",
			       (unsigned int) len, addr, args[2]);
		free(buf);
	} else if (!strcmp(cmd, "load") && nargs == 2) {
		uint32_t addr = num(args[0]);
		uint8_t *buf;
		size_t len;

		if (read_file(args[1], &buf, &len)) {
			fprintf(stderr, "cannot read %s\n", args[1]);
			return 1;
		}
		ret = mstarpoker_upload(&m, addr, buf, len);
		if (!ret)
			printf("loaded %u bytes at 0x%08x\n",
			       (unsigned int) len, addr);
		free(buf);
	} else if (!strcmp(cmd, "go") && (nargs == 1 || nargs == 2)) {
		int idle = nargs == 2 ? atoi(args[1]) : 1000;

		ret = mstarpoker_go(&m, num(args[0]));
		if (!ret) {
			ssize_t n = mstarpoker_console(&m, 1, idle);

			ret = n < 0 ? (int) n : 0;
		}
	} else if (!strcmp(cmd, "console") && nargs <= 1) {
		ssize_t n = mstarpoker_console(&m, 1, nargs ? atoi(args[0]) : -1);

		ret = n < 0 ? (int) n : 0;
	} else {
		usage(argv[0]);
		mstarpoker_close(&m);
		return 1;
	}

	mstarpoker_close(&m);
	return ret ? fail(cmd, ret) : 0;
}
