# TinoHelm CLI & TUI

Fast, native CLI and interactive TUI dashboard for TinoHelm, built in Rust.

## Build

```bash
# Requires Rust toolchain (https://rustup.rs)
cd cli
cargo build --release
```

Binary: `cli/target/release/tino` (~2.5 MB)

## Usage

### CLI Mode (one-shot commands)

```bash
tino backtest list                    # List backtest runs
tino backtest run <strategy> \
  --symbol BTCUSDT-PERP \
  --interval 5m \
  --start 2025-02-01 \
  --end 2025-03-01                    # Run a backtest
tino backtest result <run_id>         # View result (supports short ID prefix)
tino strategy list                    # List strategies
tino strategy rescan                  # Hot-reload strategies
tino data fetch BTCUSDT-PERP 1m ...  # Fetch market data
tino node status                      # Node status
```

### TUI Mode (interactive dashboard)

```bash
tino        # Launch TUI (no args)
tino ui     # Explicit TUI launch
```

Keybindings:
- `1/2/3` — Switch tabs (Backtests, Strategies, Nodes)
- `j/k` or arrows — Navigate lists
- `Enter` — Open detail view
- `n` — New backtest (from backtest list)
- `r` — Refresh current view
- `Esc` — Go back
- `q` — Quit

### Global Options

```bash
tino --api-url http://host:8000 backtest list   # Custom API URL
tino -f json backtest list                       # JSON output
```

## Configuration

Reads from (highest priority first):
1. `--api-url` flag
2. `TINO_API_URL` environment variable
3. `~/.tino/config/user.yaml` (`api_url` field)
4. Default: `http://localhost:8000`

## Requirements

- TinoHelm API server must be running (`docker compose up -d`)
- Rust toolchain for building from source
