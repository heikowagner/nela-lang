# NELA toolchain build
# Usage: make          → build ./nelac
#        make clean    → remove build artifacts
#        make test     → build + run Python test suite

CC      ?= cc
CFLAGS  := -O2 -Wall -Wextra
LDFLAGS := -lm

.PHONY: all clean test

all: nelac

nelac: src/nelac_runtime.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f nelac

test: nelac
	cd src && python3 nela_runtime.py
