# Root developer entry points for the Rust CLI.
# Default `make` builds and installs `tino` into a PATH-style bin directory.

BIN := tino
CLI_DIR := cli
RUSTUP_CARGO := $(HOME)/.cargo/bin/cargo
CARGO ?= $(shell if [ -x "$(RUSTUP_CARGO)" ]; then printf '%s' "$(RUSTUP_CARGO)"; else printf 'cargo'; fi)
INSTALL ?= install
RM ?= rm -f
GLOBAL_BINDIR := /usr/local/bin
USER_BINDIR := $(HOME)/.local/bin
BINDIR ?= $(shell if [ -w "$(GLOBAL_BINDIR)" ]; then printf '%s' "$(GLOBAL_BINDIR)"; else printf '%s' "$(USER_BINDIR)"; fi)
HOST_TRIPLE := $(shell $(CARGO) -vV 2>/dev/null | awk '/^host:/ {print $$2}')
TARGET ?= $(HOST_TRIPLE)
DIST_DIR ?= dist
ARTIFACT ?= $(BIN)-$(TARGET)
PACKAGE ?= $(DIST_DIR)/$(ARTIFACT).tar.gz
TARGET_BIN := $(CLI_DIR)/target/$(TARGET)/release/$(BIN)

.DEFAULT_GOAL := install

.PHONY: install build package check fmt test uninstall clean dist-clean help

install: build
	@mkdir -p "$(BINDIR)"
	$(INSTALL) -m 0755 "$(CLI_DIR)/target/release/$(BIN)" "$(BINDIR)/$(BIN)"
	@"$(BINDIR)/$(BIN)" version
	@case ":$$PATH:" in \
		*":$(BINDIR):"*) \
			printf 'installed: %s\n' "$(BINDIR)/$(BIN)"; \
			;; \
		*) \
			printf 'installed: %s\n' "$(BINDIR)/$(BIN)"; \
			printf 'warning: %s is not in PATH. Add this to your shell rc:\n' "$(BINDIR)"; \
			printf '  export PATH="%s:$$PATH"\n' "$(BINDIR)"; \
			;; \
	esac

build:
	$(CARGO) build --manifest-path "$(CLI_DIR)/Cargo.toml" --release --locked

package:
	@test -n "$(TARGET)" || { printf 'TARGET is empty; install Rust/Cargo or pass TARGET=<triple>\n' >&2; exit 1; }
	$(CARGO) build --manifest-path "$(CLI_DIR)/Cargo.toml" --release --locked --target "$(TARGET)"
	@mkdir -p "$(DIST_DIR)"
	@tmpdir=$$(mktemp -d); \
		$(INSTALL) -m 0755 "$(TARGET_BIN)" "$$tmpdir/$(BIN)"; \
		tar -C "$$tmpdir" -czf "$(PACKAGE)" "$(BIN)"; \
		$(RM) -r "$$tmpdir"; \
		printf 'packaged: %s\n' "$(PACKAGE)"

check: fmt
	$(CARGO) check --manifest-path "$(CLI_DIR)/Cargo.toml" --locked
	$(CARGO) test --manifest-path "$(CLI_DIR)/Cargo.toml" --locked
	$(CARGO) clippy --manifest-path "$(CLI_DIR)/Cargo.toml" --all-targets -- -D warnings

fmt:
	$(CARGO) fmt --manifest-path "$(CLI_DIR)/Cargo.toml" --check

test:
	$(CARGO) test --manifest-path "$(CLI_DIR)/Cargo.toml" --locked

uninstall:
	$(RM) "$(BINDIR)/$(BIN)"

clean:
	$(CARGO) clean --manifest-path "$(CLI_DIR)/Cargo.toml"

dist-clean:
	$(RM) -r "$(DIST_DIR)"

help:
	@printf 'make                  Build release CLI and install tino to $(BINDIR)\n'
	@printf 'make install          Same as make; default BINDIR is /usr/local/bin when writable, else ~/.local/bin\n'
	@printf 'make build            Build release CLI only\n'
	@printf 'make package          Build release CLI and write $(PACKAGE)\n'
	@printf 'make package TARGET=x86_64-unknown-linux-gnu ARTIFACT=tino-linux-x86_64\n'
	@printf 'make check            Run fmt/check/test/clippy for the CLI\n'
	@printf 'make test             Run CLI tests\n'
	@printf 'make uninstall        Remove $(BINDIR)/$(BIN)\n'
	@printf 'make dist-clean       Remove $(DIST_DIR)\n'
	@printf 'make BINDIR=/path     Override install directory, e.g. /usr/local/bin\n'
