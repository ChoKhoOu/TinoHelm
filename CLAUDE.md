# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader. It provides backtesting, paper trading (sandbox), and live trading via a FastAPI backend, Redis job queue, PostgreSQL persistence, and a Next.js frontend.

## Commands

```bash
# Docker (primary way to run)
docker compose up -d              # Start all services
docker compose up -d --build api  # Rebuild API after src/ code changes
docker compose logs api --tail 50 # Check API logs

# Local dev (Python)
python -m venv .venv && source .venv/bin/activate
pip install -e "."                # Install package (includes plotly for tearsheets)
pip install -e ".[optimize]"      # With Optuna support

# Tests (must use venv python, system python has PEP 668 restrictions)
.venv/bin/pip install pytest      # Install pytest in venv
.venv/bin/python -m pytest tests/ -x -q            # Run all tests
.venv/bin/python -m pytest tests/portfolio/ -x -q   # Run one test dir
.venv/bin/python -m pytest tests/actors/test_risk_guard.py::test_daily_loss_breach -x -v  # Single test

# Database migrations
alembic upgrade head

# Rust CLI/TUI (recommended — fast, zero dependencies)
cd cli && cargo build --release    # Build (~2.5 MB binary)
cli/target/release/tino --help     # CLI mode
cli/target/release/tino            # TUI mode (interactive dashboard)
cli/target/release/tino backtest list
cli/target/release/tino strategy list
cli/target/release/tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01

# Python CLI (legacy — thin HTTP client, needs API server running)
.venv/bin/tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01
.venv/bin/tino backtest list
.venv/bin/tino strategy list
.venv/bin/tino strategy rescan     # Hot-reload strategies without restart
.venv/bin/tino data fetch BTCUSDT-PERP 1m 2024-01-01 2025-01-01

# Frontend
cd web && npm ci && npm run dev    # Dev server on :3000
cd web && npm run build            # Static export to web/out/

# E2E verification scripts
./scripts/e2e_backtest.sh
./scripts/verify_docker.sh
```

## Architecture

```
                    ┌─────────┐
                    │  nginx  │ :3000 (static SPA + /api proxy)
                    └────┬────┘
                         │
┌────────────────────────┼────────────────────────┐
│  API Container         │                        │
│  ┌─────────────────────┴──────────────────────┐ │
│  │  FastAPI (uvicorn :8000)                   │ │
│  │  Routes: /api/backtest, /strategies, /data │ │
│  │  WebSocket: /ws/events, /ws/equity         │ │
│  └──────┬──────────────┬──────────────────────┘ │
│         │              │                        │
│  ┌──────┴──────┐  ┌────┴─────────────┐          │
│  │ Backtest    │  │ TradingNode      │          │
│  │ Workers     │  │ (sandbox/live)   │          │
│  │ (subprocess)│  │ (subprocess)     │          │
│  └──────┬──────┘  └────┴─────────────┘          │
└─────────┼──────────────┼────────────────────────┘
          │              │
    ┌─────┴─────┐   ┌────┴────┐
    │  Redis    │   │ Postgres│
    │  :6379    │   │  :5432  │
    └───────────┘   └─────────┘
```

**Data flow for backtests**: CLI → POST /api/backtest/run → Redis queue `tino:backtest:queue` → Worker subprocess dequeues → BacktestRunner (NT BacktestEngine) → Result extraction → DB + Redis progress → CLI polls status

**Data pipeline** (instrument + bars): `data/instruments.py` fetches real instrument definitions from Binance `/fapi/v1/exchangeInfo` API (24h file cache), `data/providers/binance.py` fetches klines from `/fapi/v1/klines`, `data/catalog.py` wraps both into NT-native Parquet format. Instruments are always built from real exchange parameters — never hardcoded.

**Data aggregation**: Only 1-minute data needs to be stored. Runner auto-detects if requested interval data exists; if not, loads 1m data and uses NT composite aggregation (`INTERNAL@1-MINUTE-EXTERNAL`).

### Portfolio Architecture

Everything is a portfolio. Single `.py` strategies are auto-wrapped as implicit portfolios with 1 strategy, 1 symbol, 0 actors:

```
~/.tino/strategies/
├── btc_multi_factor.py              # Single file → implicit portfolio
└── crypto_momentum/                 # Portfolio folder
    ├── portfolio.yaml               # Config: symbols, interval, actors, params
    ├── strategy.py                  # Strategy class
    └── factors.py                   # Extracted indicators/constants
```

**Strategy/Actor loading** is unified in `portfolio/loader.py` — the single entry point shared by BacktestRunner, Sandbox node, and Live node. It creates N strategy instances (one per symbol) with injected `instrument_id`/`bar_type`/`order_id_tag`/`manage_stop`, plus optional Actor instances.

**RiskGuardActor** (`actors/risk_guard.py`) is a cross-strategy portfolio risk overlay. It subscribes to all bar types via `self.cache.bar_types()` in `on_start()`, and communicates via NT msgbus (`self.msgbus.publish("risk.guard.state", action)`). Strategies subscribe to these topics to honor risk signals.

**BridgeActor** (`node/bridge_actor.py`) bridges NT internal msgbus events to Redis PubSub for cross-process communication. Used by both sandbox and live nodes.

**DB design**: The `strategies` table is ephemeral (rebuilt on `tino strategy rescan`). `backtest_runs` uses `strategy_name` (string column) instead of FK for decoupling.

## Key Conventions

### Symbol Naming
- User input: `BTCUSDT-PERP` (includes instrument type suffix)
- NT internal: `BTCUSDT-PERP.BINANCE` (auto-appended by `_normalize_symbol()`)
- Binance API: `BTCUSDT` (use `strip_to_binance_api_symbol()` from `data/instruments.py` — handles all suffixes: `-PERP`, `-SWAP`, `-SPOT`, `-LINEAR`, `.BINANCE`)
- Jesse format: `BTC-USDT` (used in `SYMBOL_PROFILES` keys, converted by `_nt_symbol_to_jesse()`)
- **Do NOT auto-append `-PERP`** — user must explicitly specify instrument type

### Bar Type Format
```
BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL        # Pre-built bars
BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL  # Aggregated from 1m
```

### NT Config System
NautilusTrader uses **msgspec Structs**, not Pydantic. Strategy configs have `__struct_fields__` not `model_fields`. Use `strategy/utils.py` shared helpers (`get_config_fields`, `get_config_field_names`) which handle both systems.

### Data Paths (Docker volumes)
All user data lives under `~/.tino/` on the host, mounted into the container:
- `~/.tino/strategies/` — Strategy Python files and portfolio folders
- `~/.tino/actors/` — Global shared Actor `.py` files
- `~/.tino/data/catalog/` — NT ParquetDataCatalog (Parquet files)
- `~/.tino/data/artifacts/` — Backtest result JSON/CSV
- `~/.tino/config/` — default.yaml, user.yaml
- `~/.tino/data/instruments_cache.json` — Cached Binance exchangeInfo (24h TTL, auto-refreshed)

### Config
Priority: ENV vars (`TINO_` prefix, `__` nested delimiter) > `config/user.yaml` > `config/default.yaml`

### Redis Key Patterns
- `tino:backtest:queue` — Job queue (LPUSH/BRPOP)
- `tino:backtest:cancel:{run_id}` — Cancel flag
- `tino:backtest:progress:{run_id}` — Progress percentage
- `tino:heartbeat:{node_type}` — Node heartbeat (15s TTL)

## 用户规则
- **MUST**: 涉及 NT API 的任何开发，必须先浏览 https://nautilustrader.io/docs/latest/ 对应文档页面，确认 API 签名和行为后再写代码。不要凭记忆或猜测调用 NT API。
- NT 的文档在这  https://nautilustrader.io/docs/latest/api_reference/backtest/
- 策略保存在 ~/.tino/strategies

## NautilusTrader Quick Reference

This section documents NT APIs and patterns used (or planned) in TinoHelm. **Always consult** https://nautilustrader.io/docs/latest/ for the source of truth.

### Strategy Class

Strategy inherits from Actor and adds order management. Same source code works for backtest and live.

**StrategyConfig key fields** (base class, always available):
- `order_id_tag: str` — **REQUIRED** for multi-instance. Strategy ID = `{ClassName}-{tag}`. Duplicates raise `RuntimeError`. Our loader auto-generates `"000"`, `"001"`, etc.
- `manage_stop: bool = False` — When `True`, calling `stop()` auto-triggers `market_exit()` (cancel orders + close positions). Our loader sets `True`.
- `manage_gtd_expiry: bool = False` — Auto-cancel GTD orders on expiry if venue doesn't support.
- `oms_type` — `OmsType.HEDGING` (multiple positions per instrument) or `OmsType.NETTING` (one position per instrument).

**Lifecycle methods:**
```
on_start()     → Subscribe to data, initialize state
on_stop()      → Cleanup (if manage_stop=True, market_exit() runs first)
on_save()      → Return dict of state for persistence
on_load(state) → Restore state from dict
on_reset()     → Reset between backtest runs
on_resume() / on_degrade() / on_fault() / on_dispose()
```

**Data event handlers:**
```
on_bar(bar)           ← subscribe_bars(bar_type)
on_quote_tick(tick)   ← subscribe_quote_ticks(instrument_id)
on_trade_tick(tick)   ← subscribe_trade_ticks(instrument_id)
on_order_book(book)   ← subscribe_order_book(instrument_id)
on_data(data)         ← subscribe_data(DataType)
on_signal(signal)     ← subscribe_signal(name)
on_historical_data(data) ← request_bars(bar_type)
```

**Order event handlers** (specific → generic cascade):
```
on_order_accepted(event) / on_order_rejected(event) / on_order_filled(event)
on_order_canceled(event) / on_order_expired(event) / on_order_updated(event)
on_order_denied(event) / on_order_submitted(event) / on_order_triggered(event)
on_order_event(event)  ← catches ALL order events
on_event(event)        ← universal fallback
```

**Position event handlers:**
```
on_position_opened(event) / on_position_changed(event) / on_position_closed(event)
on_position_event(event)  ← catches ALL position events
```

**Order management:**
```python
self.submit_order(order)                    # Submit to venue
self.submit_order(order, position_id=pid)   # Submit linked to position (HEDGING)
self.cancel_order(order)                    # Cancel single
self.cancel_all_orders(instrument_id)       # Cancel all for instrument
self.modify_order(order, quantity=...)      # Modify open order
self.close_all_positions(instrument_id)     # Close with market orders
self.market_exit()                          # Graceful full exit (cancel + close + poll)
```

**Order creation** (via `self.order_factory`):
```python
order = self.order_factory.market(instrument_id, OrderSide.BUY, Quantity.from_str("1.0"))
order = self.order_factory.limit(instrument_id, OrderSide.SELL, Quantity.from_str("1.0"), Price.from_str("50000.0"))
order = self.order_factory.stop_market(instrument_id, OrderSide.SELL, Quantity.from_str("1.0"), Price.from_str("48000.0"))
```

**Order types:** `MARKET`, `LIMIT`, `STOP_MARKET`, `STOP_LIMIT`, `MARKET_TO_LIMIT`, `MARKET_IF_TOUCHED`, `LIMIT_IF_TOUCHED`, `TRAILING_STOP_MARKET`, `TRAILING_STOP_LIMIT`

**Time-in-force:** `GTC` (default), `IOC`, `FOK`, `GTD`, `DAY`

### Actor Class

Actor is the base for non-trading components. Strategy inherits from Actor.

**Key differences from Strategy:** No order management methods. Used for data processing, monitoring, risk overlays.

**Data subscription** (must be called in `on_start()` for callbacks to fire):
```python
self.subscribe_bars(bar_type)                   # → on_bar()
self.subscribe_quote_ticks(instrument_id)       # → on_quote_tick()
self.subscribe_trade_ticks(instrument_id)       # → on_trade_tick()
self.subscribe_data(DataType(MyData))           # → on_data()
self.subscribe_signal("signal_name")            # → on_signal()
self.subscribe_order_fills(instrument_id)       # → on_order_filled()
```

**Publishing** (three approaches, from low-level to high-level):
```python
# 1. Direct msgbus — any Python object, custom topic names
self.msgbus.publish("my.topic", any_python_object)
self.msgbus.subscribe("my.topic", handler_fn)

# 2. Structured data — requires Data subclass, integrates with catalog
self.publish_data(DataType(GreeksData), data_obj)
self.subscribe_data(DataType(GreeksData))  # → on_data()

# 3. Lightweight signals — primitive values only (int, float, str)
self.publish_signal(name="alert", value="high", ts_event=bar.ts_event)
self.subscribe_signal("alert")  # → on_signal()
```

**IMPORTANT:** Actor does NOT have a plain `self.publish(topic, msg)` method. Use `self.msgbus.publish()` for custom topics.

### Cache API

Accessed via `self.cache` in any Actor/Strategy. Available in `on_start()` with engine data already loaded.

```python
# Instruments
self.cache.instrument(instrument_id)         # → Instrument | None
self.cache.instruments()                     # → list[Instrument]
self.cache.instruments(venue=venue)          # Filter by venue
self.cache.instrument_ids()                  # → list[InstrumentId]
self.cache.instrument_ids(venue=venue)       # Filter by venue

# Bars
self.cache.bar_types()                       # → list[BarType] (ALL loaded bar types)
self.cache.bar_types(instrument_id=id)       # Filter by instrument
self.cache.bars(bar_type)                    # → list[Bar]
self.cache.bar(bar_type, index=0)            # Most recent bar (0=latest)
self.cache.bar_count(bar_type)               # → int
self.cache.has_bars(bar_type)                # → bool

# Ticks
self.cache.quote_tick(instrument_id)         # → QuoteTick | None (most recent)
self.cache.trade_tick(instrument_id)         # → TradeTick | None

# Orders
self.cache.orders_open()                     # → list[Order]
self.cache.orders_open(instrument_id=id)     # Filter
self.cache.orders_closed()                   # → list[Order]
self.cache.order(client_order_id)            # → Order | None

# Positions
self.cache.positions_open()                  # → list[Position]
self.cache.positions_closed()                # → list[Position]
self.cache.position(position_id)             # → Position | None
self.cache.positions(instrument_id=id)       # Filter
```

### Portfolio API

Accessed via `self.portfolio` in any Actor/Strategy. The Portfolio is NT's central hub for cross-strategy position/account aggregation — distinct from our `PortfolioConfig` (strategy composition config).

```python
# Account
self.portfolio.account(venue)                # → Account | None
account.balance_total(currency)              # → Money
account.balance_free(currency)               # → Money

# PnL (supports target_currency for cross-currency conversion)
self.portfolio.unrealized_pnl(instrument_id) # → Money | None
self.portfolio.realized_pnl(instrument_id)   # → Money | None
self.portfolio.total_pnl(instrument_id)      # → Money | None
self.portfolio.net_exposure(instrument_id)   # → Money | None
self.portfolio.net_exposures(venue)          # → dict[Currency, Money] (all instruments)

# Position queries
self.portfolio.is_net_long(instrument_id)    # → bool
self.portfolio.is_net_short(instrument_id)   # → bool
self.portfolio.is_flat(instrument_id)        # → bool
self.portfolio.is_completely_flat()          # → bool
self.portfolio.positions_open()              # → list[Position]

# Analyzer (available on engine.portfolio.analyzer)
engine.portfolio.analyzer.register_statistic(stat)    # Register custom statistic
engine.portfolio.analyzer.returns()                   # → pd.Series (daily returns)
engine.portfolio.analyzer.get_performance_stats_pnls()     # PnL stats dict
engine.portfolio.analyzer.get_performance_stats_returns()  # Returns stats dict
engine.portfolio.analyzer.get_performance_stats_general()  # General stats dict
```

**Currency conversion**: All PnL/exposure methods accept optional `target_currency` parameter. For single-currency (USDT) setups this is not needed. The Portfolio uses BID prices for long exposure, ASK for short, MID for mixed when converting.

**Prefer NT Portfolio API over manual calculation**: Use `net_exposures(venue)` instead of manually looping `positions_open()` to calculate exposure. NT handles currency conversion and edge cases properly.

### BacktestEngine (Low-Level API)

```python
engine = BacktestEngine(config=BacktestEngineConfig())

# Add venue
engine.add_venue(
    venue=Venue("BINANCE"),
    oms_type=OmsType.HEDGING,           # or NETTING
    account_type=AccountType.MARGIN,     # or CASH
    starting_balances=[Money(10_000.0, USDT)],
)

# Add data (defer sort for performance with multi-instrument)
engine.add_data(bars_instrument1, sort=False)
engine.add_data(bars_instrument2, sort=False)
engine.sort_data()  # Single efficient sort

# Add components
engine.add_instrument(instrument)
engine.add_strategy(strategy_instance)
engine.add_actor(actor_instance)

# Run
engine.run()

# Reset for next run (preserves data, removes strategies)
engine.reset()
engine.add_strategy(new_strategy)
engine.run()
```

### Position Model

**HEDGING (our default):** Multiple simultaneous positions per instrument. Each `submit_order(..., position_id=pid)` creates/modifies an independent position. Closed positions don't reopen.

**NETTING:** One position per instrument. All fills net against each other. Position can flip long↔short.

**Key Position attributes:**
```python
p.instrument_id      # InstrumentId
p.entry              # OrderSide enum (use .name for "BUY"/"SELL")
p.side               # PositionSide (LONG/SHORT/FLAT)
p.quantity            # Quantity (current)
p.peak_qty            # Quantity (max exposure reached)
p.avg_px_open         # float
p.avg_px_close        # float
p.realized_pnl        # Money (use .as_double() for float, str has currency suffix)
p.unrealized_pnl(price)  # Money
p.ts_opened           # int (nanoseconds) — NOT opened_ts
p.ts_closed           # int (nanoseconds) — NOT closed_ts
p.duration_ns         # int (nanoseconds) — NOT duration
p.commissions()       # dict[Currency, Money]
p.events              # list of fill events
```

### Data Types

**Built-in:** `Bar`, `QuoteTick`, `TradeTick`, `OrderBookDelta`, `OrderBookDepth10`, `MarkPriceUpdate`, `IndexPriceUpdate`, `InstrumentStatus`

**Bar aggregation methods:** `MINUTE`, `HOUR`, `DAY` (time-based), `TICK`, `VOLUME`, `VALUE`, `RENKO` (threshold-based), `TICK_IMBALANCE`, `TICK_RUNS`, `VOLUME_IMBALANCE`, `VOLUME_RUNS` (information-driven)

**Custom data:** Subclass `Data` with `ts_event`/`ts_init` properties, or use `@customdataclass` decorator for auto-serialization.

**Bar timestamps:** `ts_init` must be the bar **closing time** for correct backtest execution (prevents look-ahead bias).

### Instrument Precision

NT enforces precision strictly — mismatches cause `RuntimeError` at instrument creation, RiskEngine order check, and MatchingEngine data validation.

**Always use factory methods for order parameters:**
```python
instrument = self.cache.instrument(instrument_id)
price = instrument.make_price(50000.123)  # Rounds to instrument's price_precision
qty = instrument.make_qty(1.5)            # Rounds to instrument's size_precision
```

**Never** create `Price`/`Quantity` directly without checking instrument precision. The RiskEngine will deny orders with excess precision.

**Instrument definitions** for backtesting come from Binance `exchangeInfo` API (fetched and cached by `data/instruments.py`). Live/Sandbox use NT's `InstrumentProviderConfig(load_all=True)` which fetches real definitions from the exchange.

### Execution Flow

```
Strategy.submit_order()
  → [OrderEmulator] (if emulation_trigger set)
  → [ExecAlgorithm] (if exec_algorithm_id set)
  → RiskEngine (precision, notional, qty checks)
  → ExecutionEngine
  → ExecutionClient (venue)
```

**OMS type mismatch:** When strategy uses HEDGING but venue uses NETTING, the ExecutionEngine creates "virtual positions" tracked locally but netted at the venue.

### RiskEngine & TradingState

The RiskEngine runs pre-trade checks on every order:
- Price/quantity precision matches instrument
- Prices are positive
- Within min/max quantity and max notional limits
- `reduce_only` orders only reduce positions

If any check fails, order gets `OrderDenied` event (terminal, not sent to exchange).

**TradingState** controls system-wide order flow:
- `ACTIVE` — normal operation
- `HALTED` — rejects all new orders until state changes
- `REDUCING` — only allows cancels and position-reducing orders

These map to our RiskGuardActor breach actions (`reduce_only` → REDUCING, `halt_new` → HALTED). Currently we use msgbus publish; a future enhancement could directly set TradingState for system-level enforcement.

### Order Lifecycle

**Status flow**: `INITIALIZED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED`

**Terminal states**: `DENIED` (Nautilus rejected), `REJECTED` (exchange rejected), `CANCELED`, `EXPIRED`, `FILLED`

**In-flight states** (awaiting venue response): `SUBMITTED`, `PENDING_UPDATE`, `PENDING_CANCEL`

**Key order features**:
- `reduce_only=True` — only reduces positions, auto-canceled when flat
- `post_only=True` — maker-only, ensures maker fee tier
- `tags=["ENTRY"]` — arbitrary string tags for tracking/filtering
- Contingent orders: OTO (One-Triggers-Other), OCO (One-Cancels-Other), OUO (One-Updates-Other)

**TWAP execution algorithm** is built-in via `exec_algorithm_id` parameter — no custom implementation needed for order splitting.

### Clock & Timers

```python
self.clock.utc_now()        # → pd.Timestamp (tz-aware)
self.clock.timestamp_ns()   # → int (nanoseconds since epoch)

# One-time alert
self.clock.set_time_alert("my_alert", alert_time)

# Recurring timer → on_event(TimeEvent)
self.clock.set_timer("my_timer", interval=pd.Timedelta(minutes=1))
```

### Visualization (Tearsheet)

NT provides interactive HTML tearsheets via Plotly. `plotly>=6.3.1` is a base dependency of TinoHelm. Docs: https://nautilustrader.io/docs/latest/concepts/visualization/

```python
from nautilus_trader.analysis import create_tearsheet, TearsheetConfig

# High-level: generate full tearsheet from engine
create_tearsheet(engine=engine, output_path="tearsheet.html")

# Low-level: from precomputed stats (no engine needed)
from nautilus_trader.analysis.tearsheet import create_tearsheet_from_stats
create_tearsheet_from_stats(stats_pnls=..., stats_returns=..., returns=...)
```

**Built-in charts**: `run_info`, `stats_table`, `equity` (supports benchmark overlay), `drawdown`, `monthly_returns` (heatmap), `distribution`, `rolling_sharpe`, `yearly_returns`, `bars_with_fills` (K-line + order fill markers).

**Themes**: `plotly_white` (default), `plotly_dark`, `nautilus`, `nautilus_dark`. Custom themes via `register_theme()`.

Our `BacktestRunner._generate_tearsheet()` generates a full tearsheet as a backtest artifact at `{artifacts_dir}/tearsheet.html` with all charts + `bars_with_fills` per bar type.

### Backtest Artifacts

Each backtest run produces artifacts at `~/.tino/data/artifacts/{run_id}/`:
- `results.json` — Full result dict (statistics, equity_curve, trade_log, per_instrument, etc.)
- `fills_report.csv`, `orders_report.csv`, `order_fills_report.csv`, `positions_report.csv`, `account_report.csv` — NT raw reports
- `tearsheet.html` — Interactive Plotly tearsheet (always generated)

### Live Trading Configuration

**Critical rules**:
- One `TradingNode` per process (global singleton)
- Never block the event loop in callbacks (`on_bar`, `on_order_filled`, etc.) — offload heavy work to executor
- Never use Jupyter for live trading

**LiveExecEngineConfig** recommended production settings:
```python
LiveExecEngineConfig(
    reconciliation=True,                       # Align state with exchange on startup
    reconciliation_lookback_mins=1440,         # 24h lookback (None = max available)
    allow_overfills=True,                      # Prevent position drift from duplicate fills
    inflight_check_interval_ms=2000,           # Check unconfirmed orders every 2s
    open_check_interval_secs=10.0,             # Poll open orders every 10s
    open_check_lookback_mins=60,               # NEVER below 60 per NT docs
    reconciliation_startup_delay_secs=10.0,    # NEVER below 10 per NT docs
    purge_closed_orders_interval_mins=15,      # Memory management for long sessions
    purge_closed_orders_buffer_mins=60,        # Keep 60min before purge
    purge_closed_positions_interval_mins=15,
    purge_closed_positions_buffer_mins=60,
)
```

**`allow_overfills=True`** is essential — WebSocket reconnection can replay fill events, causing duplicate fills that would be rejected by default, leading to position drift.

Our `live.py` and `sandbox.py` nodes use these settings. Factory passes `reconciliation` and `reconciliation_lookback_mins` from the config dict.

## Strategy Development

- NT uses `OmsType.HEDGING` for independent position management (each order = separate position with its own position_id).
- Strategy config `instrument_id`, `bar_type`, `order_id_tag`, and `manage_stop` are injected by the portfolio loader — strategies should not hardcode them.
- `--param key=value` on CLI auto-infers types (bool/int/float/None/str). Parameters are filtered to only those matching the strategy's config fields.
- Strategies can define module-level `SYMBOL_PROFILES` dict (Jesse format keys like `"BTC-USDT"`) for per-symbol parameter profiles. The loader validates symbols against this and warns on unrecognized entries.
- Implement `on_save()` / `on_load()` for live restart state persistence.
- Implement `on_order_rejected()` to handle venue rejections.
- **Constructor (`__init__`)**: Do NOT access `self.clock` or `self.log` here — system hasn't initialized them yet. Only set instance attributes.

## Rust CLI/TUI Development

### Architecture
```
cli/src/
├── main.rs          # Entry: CLI (clap) vs TUI (no args) dispatch
├── api.rs           # HTTP client — all API calls to FastAPI backend
├── tui/
│   ├── mod.rs       # Event loop, key handling, render dispatch, DataCmd channel
│   ├── app.rs       # App state, adaptive tick rate, animation flags
│   ├── theme.rs     # Color palette & style presets (semantic names only)
│   ├── chrome.rs    # Top bar (workspace tabs, WS dot, clock) & bottom bar (key hints)
│   ├── widgets.rs   # Shared primitives: spinner, pulse_color, header_cell, kv_line
│   ├── ws.rs        # WebSocket listener for real-time events
│   ├── workspaces/  # One module per F-key workspace
│   │   ├── dashboard.rs  # F1 — overview
│   │   ├── backtest.rs   # F2 — master-detail with rich stats
│   │   ├── strategy.rs   # F3 — strategy list + detail
│   │   ├── nodes.rs      # F4 — sandbox/live node cards
│   │   └── data.rs       # F5 — data catalog
│   └── views/       # (legacy) older view implementations, being migrated to workspaces/
```

### Theme Color Conventions
Color constants use **semantic names** describing purpose, NEVER literal color names:
- `FG_IDENTIFIER` (soft blue) — names, tickers, identifiers
- `FG_TAG` (purple) — categories, types, labels
- `FG_HIGHLIGHT` (warm gold) — key values
- `FG_HINT` (cyan) — keyboard shortcut hints
- `FG_RUNNING` (cyan) — status: in-progress
- `FG_POSITIVE` / `FG_NEGATIVE` — semantic only (profit/loss, online/offline)
- `FG_AMBER` — structural elements only (headers, titles, brand)

**Rule**: Green/Red are NEVER decorative — always semantic (positive/negative). When adding a new color, name it by purpose (`FG_IDENTIFIER`), not appearance (`FG_BLUE`).

### Table Design Principle
All list/table views MUST have a `─` divider line between the header row and data rows. Two implementation patterns:
- **Table widget** (backtest, strategy): Use 2-line `header_cell()` helper — line 1 is amber header text, line 2 is `─`.repeat(50) in `FG_BORDER`. Set `.height(2)` on the header Row. The `─` auto-truncates to column width.
- **Paragraph** (data catalog): Manual `format!` header line + full-width `─` divider as a separate Line.

Never render a bare Table header without a divider — it violates the design language.

### Ratatui Version Pinning
Project uses **ratatui 0.30** + **crossterm 0.28** + **ratatui-macros 0.7**. Use `line![]` / `span!()` macros from `ratatui-macros` to construct styled text instead of verbose `Line::from(vec![Span::styled(...)])` patterns. See `widgets.rs` and workspace files for examples.

### Adaptive Tick Rate & Animation System
The TUI uses an adaptive refresh rate (`app.tick_rate_ms()`) to balance animation smoothness vs CPU usage:
- **100ms (10 FPS)**: boot animation, loading spinners, pulse animations, WS connected/connecting
- **250ms (4 FPS)**: running backtests (progress monitoring)
- **500ms (2 FPS)**: fully idle

**Key rule**: Any visual element with animation (spinner, pulse, etc.) MUST be covered by `has_active_animations()` in `app.rs`, otherwise the tick rate drops to 500ms and the animation looks choppy/broken.

Animation primitives in `widgets.rs`:
- `spinner(frame)` — Braille character spinner (`⠋⠙⠹…`), cycles every 2 frames. Used in title bars during loading.
- `pulse_color(bright, dim, frame)` — Smooth sine-wave color interpolation between two `Color::Rgb` values, 20-frame cycle (~1.3s). Used for status indicator dots (WS connection, node heartbeat).

Both take `app.frame_count` (incremented every tick) as the `frame` parameter.

### Non-Blocking Data Loading
All API calls use a `DataCmd` channel pattern to avoid blocking the event loop:
- `fire_load_*(&client, &mut app, &data_tx)` — spawns a `tokio::spawn` task that sends results back via `mpsc::unbounded_channel`
- `handle_data_cmd(&mut app, cmd)` — receives results in the main loop and updates `App` state
- Loading state flags (`backtest_loading`, `strategy_loading`, etc.) control spinner display AND tick rate

### Navigation Pattern
- **Tab / Shift+Tab**: cycle workspaces (replaces F1-F5 as primary nav)
- **←/→**: switch panel focus (left list / right detail)
- **j/k or ↑/↓**: context-sensitive — navigates list when left panel focused, scrolls detail when right panel focused
- **Enter**: explicit action (load detail, submit form)
- Auto-load: moving cursor in backtest list auto-fetches detail results

### Worker Pool (Python backend)
- Keep-alive worker (`idle_timeout=0`): always running, never auto-exits
- Ephemeral workers (`idle_timeout=60`): auto-spawned on queue demand, self-terminate after idle
- `ProcessManager.ensure_capacity()`: called by Watchdog every 10s — prunes dead workers, maintains min count, scales up based on `LLEN tino:backtest:queue`

## Pitfalls & Lessons Learned

### msgspec Struct Internals (Critical)
- `__struct_fields__` is a **tuple** of field names, NOT a dict.
- `__struct_defaults__` is a **tuple** of default values corresponding to the **last N** fields that have defaults. Example: fields `(a, b, c, d)` with defaults `(10, 20)` means `c=10, d=20`. **Never** call `.get()` or treat it as a dict.
- Always use `strategy/utils.py` helpers instead of accessing these directly.

### Alembic Migrations
- The `revision` value is an **arbitrary string ID** (e.g., `"add_watchlist"`), NOT the filename. When writing `down_revision`, use the actual `revision` string from the parent migration, not the filename.
- Existing migration chain: `None → "add_watchlist" → "002"`.
- DB `DateTime` columns are `TIMESTAMP WITHOUT TIME ZONE` (naive). **Never** assign `datetime.now(timezone.utc)` (aware) to them — this causes `asyncpg.DataError`. Use `datetime.utcnow()` or rely on column-level `server_default`/`onupdate=func.now()`.

### NautilusTrader API Mismatches
- **CRITICAL: Always read NT docs before writing any NT API call.** Many NT APIs have non-obvious signatures, and guessing from memory has caused multiple production bugs in this project.
- `BacktestEngine.add_venue()` takes `venue: Venue` (object), NOT `venue_name: str`. Also `starting_balances` must be `list[Money]`, not `list[Decimal]`.
- `BacktestEngine.add_strategy()` takes an instantiated `Strategy` object, NOT `ImportableStrategyConfig`. Must manually import and instantiate.
- `TriggerType.LAST_TRADE` does not exist — use `TriggerType.LAST_PRICE`.
- `Order.is_filled` does not exist — use `order.status == OrderStatus.FILLED`.
- `nautilus_trader.analysis.statistics` module may not exist in all versions — wrap in try/except.
- NT `Actor` and `Strategy` are **Cython extension classes** — cannot be instantiated with `object.__new__()` in tests. Use a plain Python stub class that replicates the logic (see `tests/actors/test_risk_guard.py` `_RiskGuardStub` pattern).
- Actor does **NOT** have `self.publish(topic, msg)` — use `self.msgbus.publish(topic, msg)` for custom topics. This was a real bug: code compiled fine but silently failed at runtime because MagicMock accepted the call in tests.
- `on_bar()` will **never fire** unless `self.subscribe_bars(bar_type)` is called in `on_start()`. Same for all other data callbacks. Use `self.cache.bar_types()` to discover all loaded bar types without needing config fields.
- Position attributes: `ts_opened`/`ts_closed`/`duration_ns` — **NOT** `opened_ts`/`closed_ts`/`duration`.
- `OrderSide` enum: `entry.name` gives `"BUY"`/`"SELL"`, `str(entry)` gives the integer value `"1"`/`"2"`.
- `Money` objects stringify with currency suffix (e.g., `"114.60 USDT"`). Use `.as_double()` for float value, or split string and take first part.
- **Prefer NT built-in Portfolio methods** (e.g., `net_exposures(venue)`) over manually looping `positions_open()` for exposure/PnL calculations. NT handles currency conversion, edge cases, and mixed long/short pricing correctly.
- NT `Venue` and `Currency` objects should be resolved once in `on_start()` and cached as instance attributes — do NOT import/create them on every bar event.
- `instrument.make_price(value)` and `instrument.make_qty(value)` — **always use** these when creating order parameters. They round to the instrument's declared precision. Creating `Price`/`Quantity` directly risks RiskEngine denial.
- Instrument `price_precision` and `size_precision` must exactly match the `price_increment` and `size_increment`. E.g., `price_precision=2` requires `price_increment=Price(0.01, 2)`. Mismatch raises error at creation.
- Backtest instrument definitions are sourced from Binance `exchangeInfo` API via `data/instruments.py`, cached to `~/.tino/data/instruments_cache.json` (24h TTL). This replaces the old hardcoded 5-symbol precision table.
- `OrderDenied` vs `OrderRejected`: DENIED = Nautilus RiskEngine rejected (precision, notional, qty). REJECTED = exchange rejected. Both terminal but different causes — handle separately in `on_order_denied()` and `on_order_rejected()`.
- `TradingState` (ACTIVE/HALTED/REDUCING) is system-wide on the RiskEngine. Currently not directly settable from Actor (Actor lacks RiskEngine reference). Our RiskGuardActor uses msgbus publish instead.
- `Quantity` is unsigned — subtracting a larger quantity from a smaller one raises `ValueError`. Always check before arithmetic.
- `Money` arithmetic requires matching currencies — `USD + EUR` raises `ValueError`.
- Value types (`Price`, `Quantity`, `Money`) are **immutable** and use fixed-point integer storage internally. Arithmetic returns new instances. Mixed operations with `float` return `float`; with `int`/`Decimal` return `Decimal`.

### Python String Methods
- `str.lstrip("./")` removes individual **characters** `'.'` and `'/'`, NOT the prefix `"./"`. Use `str.removeprefix("./")` for prefix removal. This was a path traversal vulnerability.
- When handling relative paths (e.g., `./module:ClassName`), always `resolve()` the path and verify it stays within the expected boundary directory.

### Data & Serialization
- NaN/Infinity values from NT analysis will crash PostgreSQL JSON columns. Always sanitize with `math.isnan()`/`math.isinf()` before DB writes.
- NT report columns like `commission` and `realized_pnl` contain strings with currency suffix (e.g., `"2.04 USDT"`). Must strip the currency part before `float()` conversion. Use `Money.as_double()` when the object is available.
- ParquetDataCatalog writes data under `{catalog_path}/data/bar/{bar_type_str}/` — do NOT check `{catalog_path}/` directly for parquet files.
- Bar `ts_init` must be the **closing time** of the bar for correct backtest execution.

### Docker & Dependencies
- Container only has `asyncpg` by default — backtest workers need sync DB access via `psycopg2-binary` (must be in Dockerfile).
- Strategy files are volume-mounted (`~/.tino/strategies/`), but source code is baked into the image. Code changes in `src/` require `docker compose up -d --build api`, but strategy changes are hot-reloadable via `tino strategy rescan`.
- The `scan_strategies()` function does `importlib.import_module()` which requires the strategies dir on `sys.path`. Files starting with `_` are skipped. Non-NT files (e.g., Jesse strategies) fail silently with a log warning.

### Dynamic Module Loading
- `_load_module_from_file()` adds the module's parent dir to `sys.path`. Always clean up with `try/finally` to avoid path pollution in long-running processes.
- Use unique module names (e.g., `_portfolio_load_{stem}`) and delete from `sys.modules` before loading to ensure fresh imports.

### Exchange-Specific Parameters
- **Never hardcode** tick sizes, lot sizes, margin requirements, or other exchange-specific parameters. These vary per symbol and change over time. Always fetch from the exchange API and cache.
- `data/instruments.py` is the canonical source for instrument definitions. It fetches from Binance `exchangeInfo` API, caches to `~/.tino/data/instruments_cache.json` (24h TTL), and falls back to hardcoded defaults only on API failure.
- Symbol stripping must be consistent across the codebase. Use `strip_to_binance_api_symbol()` — do NOT do ad-hoc `.replace("-PERP", "")` in individual files.
