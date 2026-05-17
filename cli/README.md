# TinoHelm CLI

Rust CLI for TinoHelm. One-shot typed subcommands — every operation has a dedicated command with structured flags. Default output is JSON; use `-f text` for human-friendly tables.

## Install

For private GitHub Releases, authenticate before running the installer:

```bash
gh auth login
```

From the repository root, install or update a prebuilt CLI without building Rust:

```bash
./scripts/install-tino.sh
```

The installer supports Linux and Apple Silicon macOS, installs the moving `nightly` release by default, and does not provide Windows or Intel macOS binaries. Use an explicit tag when stable releases exist:

```bash
./scripts/install-tino.sh --version <tag>
```

Build from source only when you explicitly want a local Rust toolchain:

```bash
make
make build                       # build only: cli/target/release/tino
make package                     # package dist/tino-<target>.tar.gz
make verify-install              # verify installed binary and PATH resolution
make BINDIR=/path/on/PATH        # explicit alternate install directory
make uninstall                   # remove the installed binary
```

Binary after a build-only run: `cli/target/release/tino`.

## Output modes

```bash
tino backtest list                         # default: JSON to stdout
tino -f text backtest list                 # human-friendly tables with colors
```

Errors are JSON on stderr with a non-zero exit code. Scripts check `$?`; no envelope parsing needed.

## Auth

The backend expects `X-API-Key` when `TINO_API_KEY` is configured server-side.

Priority for client auth:

1. `--api-key`
2. `TINO_API_KEY`
3. `~/.tino/credentials/api_key` (created by `tino auth login`, mode `0600`)
4. `~/.tino/config/user.yaml` (`api.key`)

```bash
tino auth status
tino auth login --stdin < .secret/tino_api_key
tino auth logout
```

Config URL priority:

1. `--api-url`
2. `TINO_API_URL`
3. `~/.tino/config/user.yaml` (`api.url`)
4. `http://localhost:8000`

Example `~/.tino/config/user.yaml`:

```yaml
api:
  url: http://localhost:8000
  key_file: ~/.tino/credentials/api_key
```

## Command reference

Top-level commands: `auth`, `backtest`, `strategy`, `data`, `factor`, `signal`, `node`, `universe`, `trading`, `version`.

Use `tino --help` or `tino <command> --help` for full flag documentation.

## Examples

```bash
# Backtest
tino backtest run btc_multi_factor \
  --symbol BTCUSDT-PERP \
  --interval 5m \
  --start 2025-02-01 \
  --end 2025-03-01

tino backtest wait <run_id> --timeout 300
tino backtest result <run_id>
tino backtest compare <id1> <id2>
tino backtest artifacts list <run_id>

# Factor research
tino factor list
tino factor explore --factor RSI --universe top50 --start 2025-01-01 --end 2025-03-01
tino factor explore --body-file factor_explore.json    # complex requests
tino factor run --body-file factor_run.json
tino factor runs --limit 20
tino factor report <run_id>

# Signal research/export
tino signal list
tino signal run --body-file signal_run.json
tino signal report <run_id>
tino signal export <run_id>

# Trading
tino trading positions list
tino trading orders list
tino trading orders cancel <client_order_id>
tino trading analytics drawdown
tino trading watchlist list

# Node management
tino node status
tino node health
tino node lifecycle state
tino node risk-limits

# Data management
tino data symbols
tino data coverage BTCUSDT-PERP
tino data jobs list
tino data consolidate --symbol BTCUSDT-PERP --interval 1m

# Universe helpers
tino universe list
tino universe sync ~/.tino/research/universes/top10_perp.csv
tino universe get <universe_id>

# Version (all components)
tino version
```

## Design philosophy

- **Typed subcommands only** — no generic API caller. `tino --help` is the single source of truth for all available operations (see [ADR 0004](../docs/adr/0004-cli-typed-subcommands-only.md)).
- **JSON by default** — machine-parseable without extra flags; humans opt in with `-f text`.
- **Flags + body-file** — common parameters use typed flags; complex requests accept `--body-file`/`--stdin` (mutually exclusive with flags).

## Requirements

- TinoHelm API server for API-backed operations: `docker compose pull && docker compose up -d`
- Rust toolchain for building from source
