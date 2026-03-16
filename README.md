# TinoHelm

[中文文档](README_CN.md)

Quantitative trading platform powered by [NautilusTrader](https://nautilustrader.io). Supports backtesting, paper trading (sandbox), and live trading via a FastAPI backend, Redis job queue, PostgreSQL persistence, and a Rust TUI.

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
