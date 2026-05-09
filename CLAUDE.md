# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader. It provides backtesting, paper trading (sandbox), and live trading via a FastAPI backend, Redis job queue, PostgreSQL persistence, and a Next.js frontend.

# Database migrations
alembic upgrade head

# Rust CLI (LLM-first, no TUI)
make                         # Build release binary, install tino, and fail if PATH cannot resolve the new binary
make verify-install          # Verify /usr/local/bin or fallback BINDIR is the tino resolved by a fresh shell
make BINDIR=/path/on/PATH    # Optional alternate install dir
make build                   # Build only: cli/target/release/tino
make package                 # Package dist/tino-<target>.tar.gz
tino --help
tino -f llm api get /api/node/status
tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01

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

**Data flow for live/sandbox trading**: TradingNode subprocess → 5 specialized actors handle bridging: **SnapshotActor** publishes NT position/order/bar events to Redis PubSub (`tino:{node_type}:positions`, `tino:{node_type}:fills`), **DbWriterActor** persists to PostgreSQL (`positions` table via UPSERT, `fills` table via INSERT ON CONFLICT DO NOTHING), **CommandActor** receives external lifecycle commands via Redis SUBSCRIBE, **HealthActor** sends heartbeats + monitors strategy files, **MetricsActor** captures equity snapshots → EventBridge relays to WebSocket clients. CLI consumers use HTTP one-shot calls by default and can still inspect the same persisted positions/fills/summary endpoints. Dedup keys: `position_id` for positions, `trade_id` for fills.

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

**DB design**: The `strategies` table is ephemeral (rebuilt on `tino strategy rescan`). `backtest_runs` uses `strategy_name` (string column) instead of FK. `positions` stores live/sandbox snapshots (upserted by `position_id`). `fills` stores immutable fill records (deduped by `trade_id`). `data_catalog` tracks Parquet/JSON storage metadata with `record_count` and `source_type` columns. `data_fetch_jobs` tracks persistent data ingestion jobs with status/progress/error (survives API restarts).

**Data fetch jobs**: `POST /api/data/fetch-batch` → creates `DataFetchJob` DB records → pushes to Redis queue `tino:data:queue` → async worker in API process dequeues (BRPOP) → runs `BinanceVisionPipeline.ingest()` → updates DB progress. On API restart, interrupted jobs (status=running) are reset to queued and re-enqueued. Downloads run with `asyncio.Semaphore(5)` concurrency + `run_in_executor` for ZIP extraction.

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
- `tino:data:queue` — Data fetch job queue (LPUSH/BRPOP)
- `tino:data:progress:{job_id}` — Data fetch progress (PubSub)
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

## Rust CLI Development

### Architecture
```
cli/src/
├── main.rs          # clap entrypoint; no no-arg TUI fallback
├── api.rs           # Auth-aware HTTP client + generic request_json()
├── output.rs        # text/json/llm output modes and LLM envelope
├── config.rs        # API URL + X-API-Key discovery
├── cli/
│   ├── api.rs       # generic `tino api call/get/post/routes`; covers every FastAPI route
│   ├── auth.rs      # `tino auth status|login|logout`
│   ├── backtest.rs  # typed human-friendly backtest shortcuts
│   ├── data.rs      # data fetch/list/info/compact/validate/scan shortcuts
│   ├── factor.rs    # typed factor research shortcuts
│   ├── node.rs      # sandbox/live node lifecycle + node strategy controls
│   ├── signal.rs    # typed signal research/export shortcuts
│   ├── strategy.rs  # strategy CRUD/validate/rescan/params shortcuts
│   ├── universe.rs  # universe sync/get helpers
│   └── style.rs     # terminal text styling only, not TUI
└── types.rs         # serde structs for typed shortcuts
```

### Output Contract
- `-f text`: human terminal output.
- `-f json`: raw API JSON or typed-command JSON.
- `-f llm`: stable envelope `{ok,data,error,meta}` for autonomous callers. API errors should still be parseable JSON on stdout and exit non-zero.

### API Coverage Rule
Do not wait for typed Rust structs before exposing new backend operations. Add or use `tino api call METHOD /path --body-file req.json` first; typed subcommands are convenience wrappers for common workflows.

### Auth Rule
Backend auth uses `X-API-Key`. Client priority is `--api-key` > `TINO_API_KEY` > `~/.tino/credentials/api_key` > `~/.tino/config/user.yaml`. Never print secret values; only print source labels such as `env`, `credentials_file`, or `none`. `tino auth login --api-key ...` persists the key under `~/.tino/credentials/api_key` with a private credentials directory/file; agent/CI usage should prefer ephemeral env/flag credentials unless the workspace is private.

### Worker Pool (Python backend)
- Keep-alive worker (`idle_timeout=0`): always running
- Ephemeral workers (`idle_timeout=60`): auto-spawned on demand, self-terminate after idle
- `ProcessManager.ensure_capacity()`: called by Watchdog every 10s

## Frontend (Next.js / src/web)

> **Detailed frontend guide**: See `src/web/CLAUDE.md` for component architecture, Tailwind class mapping table, chart theme API, and shadcn gotchas.

### Stack
- **Next.js 16** (App Router, static export) + **React 19** + **Tailwind CSS v4** + **shadcn/ui v4** (`@base-ui/react` primitives)
- **Recharts** for charts, **framer-motion** for animations, **lightweight-charts** for candlestick
- **Font**: Inter (`font-sans`) for UI, JetBrains Mono (`font-mono`) for data values; loaded via `next/font/google` (self-hosted) with Inter OpenType features `cv11`/`ss01`/`ss03` enabled on `body`. Legacy QDS aliases `var(--font-u)`/`var(--font-d)` alias to the new tokens.

### QDS Warm Design System
**MUST**: All frontend development MUST strictly follow the design references in `.claude/skills/TinoHelmDS/`. These are the single source of truth for UI/UX, layout, spacing, color, typography, and animation. Pixel-perfect replication is expected — do not simplify, approximate, or deviate from the design mockups. The `docs/` UI directory referenced in older docs does not exist — all design references live in `.claude/skills/TinoHelmDS/`.

**Design reference files** (`.claude/skills/TinoHelmDS/`):
| File | Scope |
|------|-------|
| `Web UI Kit.html` | Master design system: full dashboard frame, tokens, components |
| `Charts Spec.html` | Recharts chart theme, tooltip/grid/legend/label patterns |
| `QDS Pitch Deck.html` | Design principles and token architecture overview |
| `preview/component-row.html` | Row layout with 3px accent stripe (backtest list) |
| `preview/component-kpi.html` | KPI stat card patterns |
| `preview/component-badges.html` | 7-color semantic badges |
| `preview/component-tabs.html` | Filter tabs, TabNav patterns |
| `preview/component-progress.html` | Coverage bar, shimmer progress |
| `preview/component-buttons.html` | Button variants |
| `preview/color-semantic.html` | Semantic color usage rules |
| `preview/type-section-label.html` | Section label typography |
| `preview/type-data.html` | Data value mono typography |
| `colors_and_type.css` | Token reference CSS |

**Workflow**: Before implementing any frontend page/component, ALWAYS read the corresponding preview card or `Web UI Kit.html` reference first. Replicate its structure, spacing, colors, and animations exactly.

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

**QDS business components** (`components/qds/`): `StatCard`, `ShimmerBar`, `StatusBadge`, `HelpTip`, `PageHeader`, `SectionLabel`, `InlineError`.

**Chart theme** (`lib/chartTheme.ts`): Use `CHART_TOOLTIP_PROPS` spread on Recharts tooltips, `CHART_GRID_STYLE` for grids. Recharts props use `var()` directly (correct — don't convert to Tailwind).

**QDS CSS classes** (`qds-input`, `qds-card`, `qds-table` etc.) exist in globals.css for QDS business components. **`bt-*/dc-*/cg/ca/cr/ci/dim/mono` and factor-research primitives (`.sc/.sc-l/.fl/.fi/.fsel/.ctbl/...`) have been deleted from globals.css as of 2026-04-19 DS standardization.** New code must use Tailwind semantic classes and `components/qds/` components — see `src/web/CLAUDE.md` §标准化后的约束 for the full prohibition list and migration table.

### 4-Layer Notification System
Spec: `.claude/skills/TinoHelmDS/` (see `src/web/CLAUDE.md` for detailed 4-layer spec). Implementation:
- **Layer 1 (Silent/Ticker)**: High-frequency WS events (fill, order, position, progress) → data flows into UI components. `FillTicker` in StatusBar shows latest fill with fade transition.
- **Layer 2 (Inline)**: User-triggered API calls → `useAction` hook (`hooks/use-action.ts`) manages button state (idle→loading→success/error). `InlineError` component for error display. **API errors NEVER use toast.**
- **Layer 3 (Toast)**: Async background events (backtest complete, data fetch complete, connection degraded) → `NotificationListener` component routes WS events through `lib/notification-router.ts` to Sonner toast. Dedupe by event ID, max 3 on screen, 5s auto-dismiss.
- **Layer 4 (Modal)**: Critical risk events (daily limit, max drawdown, liquidation) → blocking modal (future).

Event routing table: `lib/notification-router.ts` maps event types to channels with dedupe config.

## Pitfalls & Lessons Learned

### msgspec Struct Internals (Critical)
- `__struct_fields__` is a **tuple** of field names, NOT a dict.
- `__struct_defaults__` is a **tuple** of default values for the **last N** fields with defaults. **Never** call `.get()` or treat as a dict.
- Always use `strategy/utils.py` helpers instead of accessing these directly.

### Alembic Migrations
- The `revision` value is an **arbitrary string ID**, NOT the filename. Use the actual `revision` string from the parent migration for `down_revision`.
- Migration chain: `None → "001" → "002" → "003" → "004" → "005" → "006" → "007"` (006 adds `record_count` + `source_type` to `data_catalog`; 007 adds `data_fetch_jobs` table).
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
- **Multi-stage Dockerfile**: `deps` stage collects pre-built wheels (nautilus_trader has manylinux wheels for aarch64+x86_64, no Rust toolchain needed), `runtime` stage installs from wheels via `--mount=type=bind`. Build-essential never enters the final image (~1.37GB vs old 3.47GB).
- **Shared image**: `api`, `node-sandbox`, `node-live` all use `tinohelm-api:latest`. Only `api` service builds; the others reference the pre-built image.
- **Layer caching**: Dependencies are installed before source code is copied. Editing `src/` only invalidates the lightweight `pip install --no-deps .` layer (~30s rebuild vs full ~8min).
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

## Agent skills

### Issue tracker

GitHub Issues on `ChoKhoOu/TinoHelm` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical 5-role vocabulary (`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`); `wontfix` already exists in the repo, the other four will be created lazily on first use. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root (both are lazily created by `/grill-with-docs` when needed). See `docs/agents/domain.md`.
