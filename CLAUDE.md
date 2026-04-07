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
cli/target/release/tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01

# Frontend
cd src/web && npm ci && npm run dev    # Dev server on :3000
cd src/web && npm run build            # Static export to src/web/out/

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
│  │  Routes: /api/backtest, /strategies, /data,│ │
│  │          /api/trading (positions, fills)   │ │
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

**Data flow for live/sandbox trading**: TradingNode subprocess → 5 specialized actors handle bridging: **SnapshotActor** publishes NT position/order/bar events to Redis PubSub (`tino:{node_type}:positions`, `tino:{node_type}:fills`), **DbWriterActor** persists to PostgreSQL (`positions` table via UPSERT, `fills` table via INSERT ON CONFLICT DO NOTHING), **CommandActor** receives external lifecycle commands via Redis SUBSCRIBE, **HealthActor** sends heartbeats + monitors strategy files, **MetricsActor** captures equity snapshots → EventBridge relays to WebSocket clients → TUI receives `position.update`/`fill.new` events and refreshes display. Dedup keys: `position_id` for positions, `trade_id` for fills. TUI boots by GET-loading historical data, then switches to WS real-time stream.

**Data pipeline** (instrument + bars): `data/instruments.py` fetches real instrument definitions from Binance `/fapi/v1/exchangeInfo` API (24h file cache), `data/catalog.py` wraps into NT-native Parquet format. BinanceVisionPipeline (`data/pipeline.py`) orchestrates Download → Convert → Store for 12 data types. Vision types map to DB categories via `_WRITE_CATEGORY` (e.g., klines→bar, aggTrades→trade_tick, fundingRate→funding_rate).

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

**Strategy/Actor loading** uses a unified module loader (`strategy/module_loader.py`) for safe importlib operations with sys.path cleanup and boundary protection. The high-level entry point is `strategy/loader.py` (shared by BacktestRunner, Sandbox node, and Live node), which creates N strategy instances (one per symbol) with injected `instrument_id`/`bar_type`/`order_id_tag`/`manage_stop`, plus optional Actor instances. Strategy bundle config is loaded via `portfolio/config.py`.

**RiskGuardActor** (`actors/risk_guard.py`) is a cross-strategy portfolio risk overlay. It subscribes to all bar types via `self.cache.bar_types()` in `on_start()`, and communicates via NT msgbus (`self.msgbus.publish("risk.guard.state", action)`). Strategies subscribe to these topics to honor risk signals.

**5 Node Actors** (`node/actors/`) — decomposed single-responsibility actors:
- **SnapshotActor** — NT events → Redis PubSub (positions, orders, bars, risk metrics)
- **CommandActor** — Redis SUBSCRIBE → LifecycleController dispatch (daemon thread + deque + NT timer)
- **DbWriterActor** — trade events → PostgreSQL (batched 1s flush via `queue_for_executor`)
- **HealthActor** — heartbeat (5s) + strategy file monitoring (10s poll) + auto-resume on restart
- **MetricsActor** — equity snapshots (60s timer) → Redis + PostgreSQL

All 5 actors are created in `_common.py:load_components()` and wired into the TradingNode.

### Strategy Lifecycle

Runtime strategy management is layered across three components in `node/`:

**StrategyRegistry** (`node/strategy_registry.py`) — pure Python, no NT deps. Tracks strategy state machine:
```
available → starting → running → paused → available
                         ↓                    ↑
                      flattening ─────────────┘
```
- Scans `~/.tino/strategies/` for `portfolio.yaml` folders
- Allocates globally unique `order_id_tag` prefixes (2-char hex: `"00"`, `"01"`, …) to avoid strategy ID collisions

**LifecycleController** (`node/lifecycle_controller.py`) — 4-level control:
- **L1 Soft Pause**: publishes `lifecycle.pause.{strategy_id}` on msgbus
- **L2 Flatten**: `trader.market_exit_strategy(strategy_id)` — cancel orders + close positions
- **L3 Halt**: `risk_engine.set_trading_state(TradingState.HALTED)` — system-wide order block
- **L4 Shutdown**: `os.kill(SIGTERM)` — graceful process termination

**Topics** (`node/topics.py`) — msgbus topic constants: `LIFECYCLE_PAUSE`, `LIFECYCLE_RESUME`, `LIFECYCLE_FLATTEN`, `RISK_GUARD_STATE`.

**API**: `GET /api/node/strategies`, `POST /api/node/strategy/{start,pause,resume,flatten-stop}`. **CLI**: `tino node strategy list|start|pause|resume|flatten-stop --mode sandbox|live`.

**DB design**: The `strategies` table is ephemeral (rebuilt on `tino strategy rescan`). `backtest_runs` uses `strategy_name` (string column) instead of FK. `positions` stores live/sandbox snapshots (upserted by `position_id`). `fills` stores immutable fill records (deduped by `trade_id`). `data_catalog` tracks Parquet/JSON storage metadata with `record_count` and `source_type` columns.

## Key Conventions

### Symbol Naming
- User input: `BTCUSDT-PERP` (includes instrument type suffix)
- NT internal: `BTCUSDT-PERP.BINANCE` (auto-appended by `_normalize_symbol()`)
- Binance API: `BTCUSDT` (use `strip_to_binance_api_symbol()` from `data/instruments.py`)
- Jesse format: `BTC-USDT` (used in `SYMBOL_PROFILES` keys)
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
- `~/.tino/data/instruments_cache.json` — Cached Binance exchangeInfo (24h TTL)

### Config
Priority: ENV vars (`TINO_` prefix, `__` nested delimiter) > `config/user.yaml` > `config/default.yaml`

### Redis Key Patterns
- `tino:backtest:queue` — Job queue (LPUSH/BRPOP)
- `tino:backtest:cancel:{run_id}` — Cancel flag
- `tino:backtest:progress:{run_id}` — Progress percentage
- `tino:heartbeat:{node_type}` — Node heartbeat (15s TTL)
- `tino:{node_type}:positions` — Position update events (PubSub)
- `tino:{node_type}:fills` — Fill/trade events (PubSub)
- `tino:{node_type}:commands_ack` — Lifecycle command acknowledgments (PubSub)
- `tino:{node_type}:lifecycle_state` — Node lifecycle state snapshot (key)
- `tino:{node_type}:strategy_registry` — Strategy registry JSON snapshot (key, 30s TTL)

### Data Pipeline Type Mapping
Vision data types map to DB categories via `_WRITE_CATEGORY` in `pipeline.py`:

| Vision Type | DB `data_type` | DB `interval` | Storage |
|---|---|---|---|
| klines, markPriceKlines, indexPriceKlines, premiumIndexKlines | `bar` | user-selected | Parquet `data/bar/` |
| aggTrades, trades | `trade_tick` | `tick` | Parquet `data/trade_tick/` |
| bookTicker | `quote_tick` | `tick` | (no writer yet) |
| fundingRate | `funding_rate` | `8h` | JSON `~/.tino/data/funding_rates/` |

Frontend `/api/data/types` returns `db_category` for each Vision type. Frontend filter tabs use DB categories; Fetch dialog uses Vision types.

## 用户规则
- **MUST**: 涉及 NT API 的任何开发，必须先浏览 https://nautilustrader.io/docs/latest/ 对应文档页面，确认 API 签名和行为后再写代码。不要凭记忆或猜测调用 NT API。
- NT API 参考文档: https://nautilustrader.io/docs/latest/api_reference/backtest/
- 项目内 NT 参考指南: `docs/guide/nautilustrader_complete_guide.md` (中文, 1290 行)
- 策略保存在 ~/.tino/strategies

## Strategy Development

- NT uses `OmsType.HEDGING` for independent position management (each order = separate position with its own position_id).
- Strategy config `instrument_id`, `bar_type`, `order_id_tag`, and `manage_stop` are injected by the portfolio loader — strategies should not hardcode them.
- `--param key=value` on CLI auto-infers types (bool/int/float/None/str). Parameters are filtered to only those matching the strategy's config fields.
- Strategies can define module-level `SYMBOL_PROFILES` dict (Jesse format keys like `"BTC-USDT"`) for per-symbol parameter profiles.
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
│   └── views/       # (legacy) older view implementations, being migrated to workspaces/
```

### Theme Color Conventions
Color constants use **semantic names** describing purpose, NEVER literal color names:
- `FG_IDENTIFIER` (soft blue) — names, tickers, identifiers
- `FG_TAG` (purple) — categories, types, labels
- `FG_HIGHLIGHT` (warm gold) — key values
- `FG_HINT` (cyan) — keyboard shortcut hints
- `FG_POSITIVE` / `FG_NEGATIVE` — semantic only (profit/loss, online/offline)
- `FG_AMBER` — structural elements only (headers, titles, brand)

**Rule**: Green/Red are NEVER decorative — always semantic. Name colors by purpose (`FG_IDENTIFIER`), not appearance (`FG_BLUE`).

### Table Design Principle
All list/table views MUST have a `─` divider line between header row and data rows. Two patterns:
- **Table widget**: Use 2-line `header_cell()` helper — line 1 is amber text, line 2 is `─`.repeat(50) in `FG_BORDER`. Set `.height(2)` on the header Row.
- **Paragraph**: Manual `format!` header line + full-width `─` divider as a separate Line.

### Ratatui Version Pinning
**ratatui 0.30** + **crossterm 0.28** + **ratatui-macros 0.7**. Use `line![]` / `span!()` macros from `ratatui-macros`.

### Adaptive Tick Rate & Animation System
- **100ms (10 FPS)**: boot animation, loading spinners, pulse animations, WS connected/connecting
- **250ms (4 FPS)**: running backtests (progress monitoring)
- **500ms (2 FPS)**: fully idle

**Key rule**: Any visual element with animation MUST be covered by `has_active_animations()` in `app.rs`, otherwise the tick rate drops to 500ms.

### Non-Blocking Data Loading
All API calls use a `DataCmd` channel pattern: `fire_load_*()` spawns `tokio::spawn`, sends results via `mpsc::unbounded_channel`, `handle_data_cmd()` updates `App` state in the main loop.

### Worker Pool (Python backend)
- Keep-alive worker (`idle_timeout=0`): always running
- Ephemeral workers (`idle_timeout=60`): auto-spawned on demand, self-terminate after idle
- `ProcessManager.ensure_capacity()`: called by Watchdog every 10s

## Frontend (Next.js / src/web)

> **Detailed frontend guide**: See `src/web/CLAUDE.md` for component architecture, Tailwind class mapping table, chart theme API, and shadcn gotchas.

### Stack
- **Next.js 16** (App Router, static export) + **React 19** + **Tailwind CSS v4** + **shadcn/ui v4** (`@base-ui/react` primitives)
- **Recharts** for charts, **framer-motion** for animations, **lightweight-charts** for candlestick
- **Font**: IBM Plex Sans (`font-sans`) for UI, IBM Plex Mono (`font-mono`) for data

### QDS Warm Design System
All pages MUST follow QDS Warm. Reference: `docs/ui/qds-*.html` + `docs/ui/qds-warm-theme.css`.

**Token architecture** (two layers in `globals.css`):
1. QDS short tokens in `:root` (`--bg-p`, `--acc`, `--suc`, etc.) — source of truth
2. shadcn oklch variables (`--background`, `--primary`, etc.) + QDS extensions in `@theme inline` — consumed by Tailwind

**Prefer Tailwind classes** over inline `var()` or arbitrary values:
```tsx
// ✅ Correct
<div className="rounded-lg border bg-card text-qds-success">

// ❌ Avoid (legacy pattern)
<div className="rounded-[var(--r)] border-[var(--bd)] bg-[var(--bg-p)]" style={{ color: "var(--suc)" }}>
```

**Key Tailwind mappings**: `bg-card` = cards, `bg-background` = body, `bg-secondary` = hover, `bg-input` = form inputs, `text-qds-success/danger/info/warning` = semantic colors, `text-muted-foreground` = tertiary text.

**Color semantic rules**: Green/Red are NEVER decorative — always semantic (profit/loss, success/fail).

**QDS business components** (`components/qds/`): `StatCard`, `ShimmerBar`, `StatusBadge`, `HelpTip`, `PageHeader`, `SectionLabel`.

**Chart theme** (`lib/chartTheme.ts`): Use `CHART_TOOLTIP_PROPS` spread on Recharts tooltips, `CHART_GRID_STYLE` for grids. Recharts props use `var()` directly (correct — don't convert to Tailwind).

**QDS CSS classes** (`qds-input`, `qds-card`, `qds-table`, `bt-*`) exist in globals.css with `!important` for pixel-perfect replication of design mockups. New code should prefer Tailwind classes and QDS components.

## Pitfalls & Lessons Learned

### msgspec Struct Internals (Critical)
- `__struct_fields__` is a **tuple** of field names, NOT a dict.
- `__struct_defaults__` is a **tuple** of default values for the **last N** fields with defaults. **Never** call `.get()` or treat as a dict.
- Always use `strategy/utils.py` helpers instead of accessing these directly.

### Alembic Migrations
- The `revision` value is an **arbitrary string ID**, NOT the filename. Use the actual `revision` string from the parent migration for `down_revision`.
- Migration chain: `None → "001" → "002" → "003" → "004" → "005" → "006"` (006 adds `record_count` + `source_type` to `data_catalog`).
- DB `DateTime` columns are `TIMESTAMP WITHOUT TIME ZONE` (naive). **Never** assign `datetime.now(timezone.utc)` (aware) — causes `asyncpg.DataError`. Use `datetime.utcnow()` or `server_default`/`func.now()`.

### NautilusTrader API Gotchas
> **CRITICAL**: Always read NT docs before writing any NT API call. See `docs/guide/nautilustrader_complete_guide.md` for comprehensive reference.

Key gotchas that have caused bugs in this project:
- Actor does **NOT** have `self.publish(topic, msg)` — use `self.msgbus.publish(topic, msg)`.
- `on_bar()` will **never fire** unless `self.subscribe_bars(bar_type)` is called in `on_start()`.
- Position attributes: `ts_opened`/`ts_closed`/`duration_ns` — **NOT** `opened_ts`/`closed_ts`/`duration`.
- `Money` objects stringify with currency suffix (e.g., `"114.60 USDT"`). Use `.as_double()` for float.
- **Always** use `instrument.make_price()` / `instrument.make_qty()` for order parameters. Direct `Price`/`Quantity` creation risks RiskEngine denial.
- NT `Actor`/`Strategy` are Cython extension classes — cannot use `object.__new__()` in tests. Use stub pattern (see `tests/actors/test_risk_guard.py`).
- `Quantity` is unsigned — subtracting a larger qty from smaller raises `ValueError`.

### Data & Serialization
- NaN/Infinity values from NT analysis will crash PostgreSQL JSON columns. Always sanitize before DB writes.
- ParquetDataCatalog writes data under `{catalog_path}/data/bar/{bar_type_str}/` — do NOT check `{catalog_path}/` directly.
- Bar `ts_init` must be the **closing time** of the bar for correct backtest execution.
- FundingRate data is stored as JSON (`~/.tino/data/funding_rates/{symbol.lower()}.json`), not Parquet.

### Docker & Dependencies
- Container only has `asyncpg` by default — backtest workers need sync DB via `psycopg2-binary` (in Dockerfile).
- Strategy files are volume-mounted (`~/.tino/strategies/`), but source code is baked into the image. Code changes in `src/` require `docker compose up -d --build api`, but strategy changes are hot-reloadable via `tino strategy rescan`.

### Dynamic Module Loading
- `_load_module_from_file()` adds the module's parent dir to `sys.path`. Always clean up with `try/finally` to avoid path pollution.
- Use unique module names and delete from `sys.modules` before loading to ensure fresh imports.

### Exchange-Specific Parameters
- **Never hardcode** tick sizes, lot sizes, margin requirements. Always fetch from exchange API and cache.
- `data/instruments.py` is the canonical source. Fetches from Binance `exchangeInfo` API, caches to `~/.tino/data/instruments_cache.json` (24h TTL).
- Use `strip_to_binance_api_symbol()` consistently — do NOT do ad-hoc `.replace("-PERP", "")`.

### Python String Methods
- `str.lstrip("./")` removes individual **characters**, NOT the prefix `"./"`. Use `str.removeprefix("./")`.
- When handling relative paths, always `resolve()` and verify within expected boundary directory.
