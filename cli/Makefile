COPTS = -ggdb -std=c99 -Os

# Cross builds: CROSS_COMPILE=<prefix> picks the compiler; UAPIDIR=<dir>
# (one or more, space separated) adds the target's kernel UAPI headers,
# which a static nolibc build needs (nolibc's std.h includes
# <linux/types.h>, and the serial code wants <asm/termbits.h> for
# termios2) - the host's headers are the wrong architecture.
ifdef CROSS_COMPILE
CC := $(CROSS_COMPILE)gcc
endif

# -Wall and -Wextra only on the libc build: nolibc-extensions defines a lot
# it does not use, so a static build buries anything of ours in
# -Wunused-function.

HDR   := mstarpoker.h
PROGS := mstarpoker

all: $(PROGS)

# The usual libc build.
$(PROGS): %: %.c $(HDR)
	$(CC) $(COPTS) -Wall -Wextra -o $@ $<

# Fully static nolibc build: pass NOLIBCDIR (your nolibc, e.g.
# tools/include/nolibc in the linux source) and NOLIBCEXTDIR (a checkout of
# nolibc-extensions, which carries the unix socket nolibc lacks).
ifdef NOLIBCDIR
ifndef NOLIBCEXTDIR
$(warning Please also pass NOLIBCEXTDIR with the path to your nolibc-extensions checkout for static targets)
else
NOLIBC_PROGS := $(addsuffix _nolibc,$(PROGS))
all: $(NOLIBC_PROGS)

NOLIBC_INC = $(addprefix -I,$(UAPIDIR)) \
	     -include $(NOLIBCDIR)/nolibc.h \
	     -include $(NOLIBCEXTDIR)/include/nolibc-extensions.h

$(NOLIBC_PROGS): %_nolibc: %.c $(HDR)
	$(CC) -nostdlib $(NOLIBC_INC) $(COPTS) -static -o $@ $< -lgcc

# A rootfs tarball of the static binary (installed as /bin/mstarpoker),
# built with tarwak. Pass TARWAK=<path to the tarwak binary>.
ifdef TARWAK
all: mstarpoker.tar

mstarpoker.tar: rootfs.tarwak.json $(NOLIBC_PROGS)
	$(TARWAK) -i rootfs.tarwak.json -o $@ -b ./ -p "%s_nolibc"
else
$(warning Pass TARWAK=<path to tarwak> to also build the mstarpoker.tar rootfs)
endif
endif
else
$(warning Pass NOLIBCDIR and NOLIBCEXTDIR to also build static nolibc binaries)
endif

.PHONY: clean
clean:
	rm -f $(PROGS) $(addsuffix _nolibc,$(PROGS)) mstarpoker.tar
