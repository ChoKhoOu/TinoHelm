## Why

There is no unified platform to manage the full lifecycle of algorithmic trading strategies — from backtesting through sandbox testing to live execution — using NautilusTrader as the core engine. Currently, running NT requires manual Python scripting for each environment, with no web interface, no CLI for AI-driven automation, and no persistent infrastructure for data management or process orchestration. TinoHelm fills this gap as a single-instance quantitative trading platform that wraps NautilusTrader with a FastAPI backend, React web UI, and CLI, enabling both human traders and AI agents (OpenClaw) to create, test, and deploy strategies through a consistent interface.

## What Changes

- **New Python backend**: FastAPI server managing NautilusTrader subprocess lifecycle (backtest workers, sandbox node, live node), with REST API + WebSocket for real-time event streaming.
- **New process orchestration**: Process Manager controlling TradingNode subprocesses with health monitoring, crash recovery, and three-level Kill Switch (pause/flatten/kill).
- **New BridgeActor**: Custom NT Actor running inside TradingNode subprocesses, bridging NT MessageBus events to Redis PubSub for consumption by the API layer.
- **New backtest subsystem**: BacktestEngine wrapper with subprocess worker pool, job queue via Redis, progress tracking, and result persistence.
- **New data pipeline**: Binance historical data fetcher with incremental download, Parquet caching using NT's ParquetDataCatalog format, and catalog management.
- **New strategy management**: File-system based strategy discovery with database metadata tracking, automatic version detection via code hash, and strategy validation.
- **New CLI tool**: `tino` CLI for AI/human interaction — strategy creation (scaffold), data fetching, backtest execution, node control, all with `--format json` for machine consumption.
- **New web UI**: React + Next.js static SPA with dark theme, built from `webui.pen` design — dashboard, backtest analysis, live trading, analytics, portfolio, settings, and more (16 pages designed).
- **New Docker deployment**: docker-compose with PostgreSQL, Redis, API server, and static web server, all data volumes mounted to host for persistence.
- **New strategy scaffold**: NT Strategy API skeleton generator (`tino strategy create`) with lifecycle hooks documented, supporting bar/tick/book data types.

## Capabilities

### New Capabilities
- `process-orchestration`: TradingNode subprocess lifecycle management, health monitoring, crash recovery, three-level Kill Switch, sandbox/live coexistence via separate processes
- `backtest-engine`: BacktestEngine wrapper, subprocess worker pool, job queue, progress tracking, result storage and querying
- `data-pipeline`: Binance historical data fetching, incremental downloads, Parquet caching with NT ParquetDataCatalog, catalog CRUD
- `strategy-management`: File-based strategy discovery, database metadata + version tracking (code hash), validation, scaffold generation
- `event-bridge`: BridgeActor inside TradingNode publishing events to Redis PubSub, FastAPI subscriber forwarding to WebSocket clients
- `trading-api`: FastAPI REST + WebSocket endpoints for all platform operations (backtest, node control, strategies, data, portfolio, analytics, orders, settings)
- `cli-interface`: `tino` CLI with subcommands for strategy/data/backtest/node management, dual output (human-readable + JSON for AI)
- `web-ui`: React/Next.js static SPA from webui.pen design — 16 pages including dashboard, backtest, live trading, analytics, portfolio, settings, mobile views
- `deployment`: Docker Compose orchestration with host-mounted volumes for PostgreSQL, Redis, Parquet data, strategy files, logs

### Modified Capabilities
<!-- No existing capabilities to modify — this is a greenfield project -->

## Impact

- **New codebase**: Entire `src/tinohelm/` Python package, `web/` frontend, `strategies/` directory, `config/` defaults
- **Dependencies**: nautilus_trader, FastAPI, SQLAlchemy, asyncpg, redis-py, typer/click, pydantic-settings (Python); React, Next.js, Tailwind v4 (frontend)
- **Infrastructure**: PostgreSQL 16, Redis 7, Docker + Docker Compose
- **External APIs**: Binance Spot/Futures API (data + execution), Binance Testnet for sandbox
- **File system**: Host-mounted volumes at `data/`, `strategies/`, `logs/` for persistence across container restarts
