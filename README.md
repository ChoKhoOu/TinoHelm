# TinoHelm

Single-instance quantitative trading platform powered by [NautilusTrader](https://nautilustrader.io). Supports backtesting, paper trading (sandbox), and live trading via a FastAPI backend, Redis job queue, PostgreSQL persistence, and a Rust TUI.

## Quick Start

### 1. Start Backend Services

```bash
docker compose up -d
```

This starts **PostgreSQL**, **Redis**, and the **API server** (FastAPI on port 8000).

Rebuild after source code changes:

```bash
docker compose up -d --build api
```

### 2. Build the CLI / TUI

The Rust CLI is the primary interface — fast, zero-dependency single binary.

```bash
cd cli && cargo build --release
```

The binary is at `cli/target/release/tino` (~2.5 MB).

### 3. Usage

**TUI mode** (interactive dashboard):

```bash
./cli/target/release/tino
```

**CLI mode** (one-shot commands):

```bash
tino backtest list
tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01
tino strategy list
```

## Project Structure

```
cli/              Rust CLI + TUI (clap + ratatui)
src/tinohelm/     Python backend (FastAPI + NautilusTrader)
src/web/          Next.js frontend (optional)
scripts/          Utility scripts
docker-compose.yml
Dockerfile        API container
Dockerfile.web    Web frontend container (optional)
```

## Configuration

Strategy files live in `~/.tino/strategies/`. All data under `~/.tino/data/`.

See [CLAUDE.md](CLAUDE.md) for detailed architecture, conventions, and API reference.
