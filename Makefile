# NELA toolchain build
# Usage: make          → build ./nelac
#        make clean    → remove build artifacts
#        make test     → build + run Python test suite

CC      ?= cc
CFLAGS  := -O2 -Wall -Wextra
LDFLAGS := -lm

.PHONY: all clean test validate-header check-header fix-header

all: nelac

nelac: src/nelac_runtime.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f nelac

test: nelac
	cd src && python3 nela_runtime.py

# LLM header validation: ensure NELA file indexes are always synchronized
# Use this after any game logic edits to verify the header is up-to-date
validate-header:
	python3 tools/validate_nela_header.py examples/wolf_game.nela

# Alias for CI/CD pipelines
check-header: validate-header

# Regenerate header (CAREFUL: use only after bulk refactors)
fix-header:
	python3 tools/validate_nela_header.py examples/wolf_game.nela --regenerate
