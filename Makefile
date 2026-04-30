# Root developer entry points for the Rust CLI.
# Default `make` builds and installs `tino` into a PATH-style bin directory.

BIN := tino
CLI_DIR := cli
CARGO ?= cargo
INSTALL ?= install
RM ?= rm -f
BINDIR ?= $(HOME)/.cargo/bin

.DEFAULT_GOAL := install

.PHONY: install build check fmt test uninstall clean help

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

check: fmt
	$(CARGO) check --manifest-path "$(CLI_DIR)/Cargo.toml" --locked
	$(CARGO) test --manifest-path "$(CLI_DIR)/Cargo.toml" --locked

fmt:
	$(CARGO) fmt --manifest-path "$(CLI_DIR)/Cargo.toml" --check

test:
	$(CARGO) test --manifest-path "$(CLI_DIR)/Cargo.toml" --locked

uninstall:
	$(RM) "$(BINDIR)/$(BIN)"

clean:
	$(CARGO) clean --manifest-path "$(CLI_DIR)/Cargo.toml"

help:
	@printf 'make                  Build release CLI and install tino to $(BINDIR)\n'
	@printf 'make build            Build release CLI only\n'
	@printf 'make check            Run fmt/check/test for the CLI\n'
	@printf 'make test             Run CLI tests\n'
	@printf 'make uninstall        Remove $(BINDIR)/$(BIN)\n'
	@printf 'make BINDIR=/path     Override install directory, e.g. /usr/local/bin\n'
