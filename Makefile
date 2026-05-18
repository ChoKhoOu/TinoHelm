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

.PHONY: install verify-install build package check fmt test uninstall clean dist-clean deploy cli-deploy help

install: build
	@mkdir -p "$(BINDIR)"
	$(INSTALL) -m 0755 "$(CLI_DIR)/target/release/$(BIN)" "$(BINDIR)/$(BIN)"
	@resolved=$$(command -v "$(BIN)" 2>/dev/null || true); \
	if [ "$$resolved" = "$(BINDIR)/$(BIN)" ] || [ "$(origin BINDIR)" = "file" ]; then \
		$(MAKE) --no-print-directory verify-install BINDIR="$(BINDIR)"; \
	else \
		"$(BINDIR)/$(BIN)" version; \
		printf 'installed: %s\n' "$(BINDIR)/$(BIN)"; \
		printf 'note: explicit BINDIR is not first on PATH; PATH verification skipped for install.\n'; \
		printf '      verify later with: PATH="%s:$$PATH" make verify-install BINDIR="%s"\n' "$(BINDIR)" "$(BINDIR)"; \
	fi

verify-install:
	@test -x "$(BINDIR)/$(BIN)" || { \
		printf 'missing executable: %s\n' "$(BINDIR)/$(BIN)" >&2; \
		exit 1; \
	}
	@"$(BINDIR)/$(BIN)" version
	@resolved=$$(command -v "$(BIN)" 2>/dev/null || true); \
	if [ -z "$$resolved" ]; then \
		printf 'installed: %s\n' "$(BINDIR)/$(BIN)" >&2; \
		printf 'error: %s is not on PATH; cannot call `%s` from arbitrary directories.\n' "$(BINDIR)" "$(BIN)" >&2; \
		printf 'fix: rerun `make BINDIR=/path/on/PATH` or add this to your shell rc:\n' >&2; \
		printf '  export PATH="%s:$$PATH"\n' "$(BINDIR)" >&2; \
		exit 1; \
	fi; \
	if [ "$$resolved" != "$(BINDIR)/$(BIN)" ]; then \
		printf 'installed: %s\n' "$(BINDIR)/$(BIN)" >&2; \
		printf 'error: PATH resolves `%s` to %s, not the newly installed binary.\n' "$(BIN)" "$$resolved" >&2; \
		printf 'fix: move %s earlier in PATH or install into the earlier PATH directory.\n' "$(BINDIR)" >&2; \
		exit 1; \
	fi; \
	fresh=$$(bash -lc 'command -v $(BIN)' 2>/dev/null || true); \
	if [ "$$fresh" != "$(BINDIR)/$(BIN)" ]; then \
		printf 'installed: %s\n' "$(BINDIR)/$(BIN)" >&2; \
		printf 'error: a fresh login shell resolves `%s` to `%s`; expected `%s`.\n' "$(BIN)" "$${fresh:-<missing>}" "$(BINDIR)/$(BIN)" >&2; \
		exit 1; \
	fi; \
	bash -lc '$(BIN) version >/dev/null'; \
	printf 'installed and PATH-resolved: %s\n' "$$resolved"

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

deploy:
	docker compose -f docker-compose.yml --profile sandbox pull api web
	docker compose -f docker-compose.yml --profile sandbox up -d --no-build
	docker image prune -f
	docker builder prune -f

cli-deploy:
	./scripts/install-tino.sh --nightly

help:
	@printf 'make                  Build release CLI, install tino, and fail if PATH cannot resolve it\n'
	@printf 'make install          Same as make; default BINDIR is /usr/local/bin when writable, else ~/.local/bin\n'
	@printf 'make verify-install  Check that the installed tino is executable and first on PATH\n'
	@printf 'make build            Build release CLI only\n'
	@printf 'make package          Build release CLI and write $(PACKAGE)\n'
	@printf 'make package TARGET=x86_64-unknown-linux-gnu ARTIFACT=tino-linux-x86_64\n'
	@printf 'make check            Run fmt/check/test/clippy for the CLI\n'
	@printf 'make test             Run CLI tests\n'
	@printf 'make uninstall        Remove $(BINDIR)/$(BIN)\n'
	@printf 'make dist-clean       Remove $(DIST_DIR)\n'
	@printf 'make deploy           Pull latest remote images and recreate sandbox stack\n'
	@printf 'make cli-deploy       Install latest nightly tino CLI from remote release\n'
	@printf 'make BINDIR=/path     Override install directory, e.g. /usr/local/bin\n'
