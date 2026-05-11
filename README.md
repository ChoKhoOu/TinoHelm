# TinoHelm

[中文文档](README_CN.md)

Quantitative trading platform powered by [NautilusTrader](https://nautilustrader.io). Supports backtesting, paper trading (sandbox), and live trading via a FastAPI backend, Redis job queue, PostgreSQL persistence, and a Rust LLM-first CLI.

## Quick Start

For private GHCR images or private GitHub Releases, authenticate before pulling images or running the installer:

```bash
gh auth login
gh auth token | docker login ghcr.io -u "$(gh api user --jq .login)" --password-stdin
```

### 1. Start Backend Services

```bash
docker compose pull
docker compose up -d
```

This pulls the published API/web images from GHCR and starts **PostgreSQL**, **Redis**, and the **API server** (FastAPI on port 8000).

Opt into local image builds only when you actually want them:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### 2. Sync the Python environment

```bash
uv sync --extra test --extra optimize --extra ops
```

Use `uv run ...` for Python entry points so local commands, CI, and Docker all consume the same locked dependency set from `uv.lock`.

### 3. Install the CLI

The Rust CLI is the primary interface: one-shot commands, raw JSON, and stable `llm` envelopes for autonomous callers.

```bash
./scripts/install-tino.sh
```

The installer supports Linux and Apple Silicon macOS, installs the moving `nightly` release by default, and does not provide Windows or Intel macOS binaries. Use an explicit tag when stable releases exist:

```bash
./scripts/install-tino.sh --version <tag>
```

Build from source only when you explicitly want a local Rust build:

```bash
make
make build                       # build only: cli/target/release/tino
make package                     # package dist/tino-<target>.tar.gz
make verify-install              # verify installed binary and PATH resolution
make BINDIR=/path/on/PATH        # explicit alternate install directory
make uninstall                   # remove the installed binary
```

### 4. Usage

```bash
tino --help
tino -f llm api get /api/node/status
tino backtest list
tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01
tino factor list
tino signal list
```

## Getting Started — Create Your First Strategy

### Scaffold a new strategy

```bash
tino strategy create my_strategy            # Bar-based (default)
tino strategy create my_hft_strategy -t tick  # Tick-based
```

This generates a ready-to-edit template at `~/.tino/strategies/<name>.py`.

### Strategy types

| Type | Trigger | Use case |
|------|---------|----------|
| `bar` | `on_bar()` — fires on each candlestick close | Momentum, mean-reversion, multi-factor — most strategies |
| `tick` | `on_quote_tick()` / `on_trade_tick()` — fires on every market update | Market-making, HFT, spread trading |

### Bar strategy template

```python
class MyStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId          # e.g. "BTCUSDT-PERP.BINANCE"
    bar_type: BarType                    # e.g. "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"
    trade_size: Decimal = Decimal("0.01")

class MyStrategy(Strategy):
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        setup_pause_support(self)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if is_paused(self):
            return
        # Your trading logic here
        # bar.open, bar.high, bar.low, bar.close, bar.volume

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        self.close_all_positions(self.instrument_id)
```

### Tick strategy template

```python
class MyHftStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId
    trade_size: Decimal = Decimal("0.01")

class MyHftStrategy(Strategy):
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        # tick.bid_price, tick.ask_price, tick.bid_size, tick.ask_size
        pass

    def on_trade_tick(self, tick: TradeTick) -> None:
        # tick.price, tick.size, tick.aggressor_side
        pass
```

## Project Structure

```
cli/              Rust CLI (clap, LLM-first API caller)
src/tinohelm/     Python backend (FastAPI + NautilusTrader)
src/web/          Next.js frontend (optional)
scripts/          Utility scripts
docker-compose.yml
Dockerfile        API container
Dockerfile.web    Web frontend container (optional)
```

## Python development workflow

```bash
uv run alembic upgrade head
uv run pytest -q --tb=short -m "not integration and not performance"
uv run --extra ops python scripts/emergency_flatten.py --help
uv run python scripts/migrate_funding_json_to_parquet.py --dry-run
```

## Configuration

Strategy files live in `~/.tino/strategies/`. All data under `~/.tino/data/`.

See [CLAUDE.md](CLAUDE.md) for detailed architecture, conventions, and API reference.
