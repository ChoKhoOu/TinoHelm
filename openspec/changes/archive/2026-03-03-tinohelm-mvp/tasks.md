## 1. Project Skeleton & Configuration

- [x] 1.1 Create Python project structure: `src/tinohelm/` package with subpackages (api, core, backtest, node, data, strategy, db, cli), `pyproject.toml` with all dependencies (nautilus_trader, fastapi, uvicorn, sqlalchemy, asyncpg, redis, typer, pydantic-settings, alembic)
- [x] 1.2 Create configuration system: `src/tinohelm/core/config.py` with Pydantic Settings class loading from `config/default.yaml` + `.env`, covering server, database, redis, binance, and paths settings
- [x] 1.3 Create `config/default.yaml` with sensible defaults (testnet=true, worker pool size=2, ports, paths)
- [x] 1.4 Create `.env.example` with placeholder values for BINANCE_API_KEY, BINANCE_API_SECRET, POSTGRES_PASSWORD

## 2. Database Schema & Migrations

- [x] 2.1 Create SQLAlchemy async engine and session management in `src/tinohelm/db/session.py`
- [x] 2.2 Create SQLAlchemy models in `src/tinohelm/db/models.py`: strategies (id, name, file_path, created_at, updated_at), strategy_versions (id, strategy_id, version, code_hash, created_at), backtest_runs (id, run_id, strategy_id, strategy_version_id, symbol, interval, start_date, end_date, params_json, status, result_summary_json, error, created_at, completed_at), orders (id, node_type, order_id, instrument_id, side, type, quantity, price, status, strategy_id, created_at), fills (id, node_type, order_id, instrument_id, side, quantity, price, commission, created_at), positions (id, node_type, instrument_id, side, quantity, avg_price, unrealized_pnl, realized_pnl, strategy_id, updated_at), risk_events (id, node_type, event_type, details_json, created_at), data_catalog (id, symbol, data_type, interval, start_date, end_date, file_path, size_bytes, created_at), audit_log (id, action, details_json, created_at)
- [x] 2.3 Set up Alembic with async support: `alembic.ini`, `src/tinohelm/db/migrations/env.py`, initial migration generating all tables
- [x] 2.4 Create migration auto-run on API startup in app lifespan handler

## 3. Process Manager & Node Control

- [x] 3.1 Create `src/tinohelm/core/process_manager.py`: ProcessManager class managing subprocess lifecycle for sandbox and live TradingNodes using `multiprocessing.Process`, tracking PID, status, restart count
- [x] 3.2 Implement node start logic: construct TradingNodeConfig with appropriate trader_id, instance_id, Redis DB (sandbox=0, live=1), Binance adapter configs from settings, BridgeActor inclusion
- [x] 3.3 Implement graceful stop: send stop command via Redis, wait for timeout_post_stop, SIGTERM, then SIGKILL fallback
- [x] 3.4 Implement three-level Kill Switch: Level 1 (publish pause command to Redis for target strategy), Level 2 (publish flatten-all command), Level 3 (shutdown_system + SIGTERM + SIGKILL sequence with 2s grace)
- [x] 3.5 Implement watchdog loop: async background task checking Redis heartbeat keys every 10 seconds, detecting crashes, triggering auto-restart (max 3 retries), notifying WebSocket clients on crash
- [x] 3.6 Create `src/tinohelm/node/sandbox.py` and `src/tinohelm/node/live.py`: subprocess entry points that build and run TradingNode with BridgeActor, Binance adapters, and strategy loading via ImportableStrategyConfig
- [x] 3.7 Create `src/tinohelm/node/factory.py`: TradingNodeConfig builder that assembles configs from settings (Binance keys, account type, testnet flag, reconciliation settings, cache config with Redis DB isolation)

## 4. BridgeActor (Event Bridge)

- [x] 4.1 Create `src/tinohelm/node/bridge_actor.py`: NT Actor subclass that subscribes to MessageBus events (OrderFilled, PositionOpened/Changed/Closed, OrderAccepted/Rejected/Canceled, Bar) and publishes serialized JSON to Redis PubSub channels `tino:{node_type}:{event_type}`
- [x] 4.2 Implement heartbeat timer: 5-second interval timer publishing to Redis key `tino:heartbeat:{node_type}` with 15-second TTL, including trading_state, strategy count, position count
- [x] 4.3 Implement command listener: subscribe to Redis channel `tino:{node_type}:commands` for receiving Kill Switch commands from API (pause, flatten, kill) and executing corresponding NT API calls
- [x] 4.4 Create `src/tinohelm/core/bridge.py`: FastAPI-side Redis PubSub subscriber that listens to `tino:*` channels and dispatches events to WebSocket hub

## 5. Backtest Engine

- [x] 5.1 Create `src/tinohelm/backtest/runner.py`: BacktestRunner wrapping NT BacktestEngine — accepts strategy module path, symbol, interval, date range, params; constructs BacktestRunConfig with BacktestVenueConfig (BINANCE, USDT_FUTURES, NETTING), BacktestDataConfig pointing to ParquetDataCatalog, and ImportableStrategyConfig
- [x] 5.2 Create `src/tinohelm/backtest/worker.py`: subprocess worker function that dequeues jobs from Redis list `tino:backtest:queue` via BRPOP, runs BacktestRunner, publishes progress to `tino:backtest:progress:{run_id}`, writes result JSON to `data/artifacts/{run_id}.json`, updates database record
- [x] 5.3 Create `src/tinohelm/backtest/result.py`: result extraction from BacktestEngine — total return, Sharpe ratio, max drawdown, win rate, profit factor, trade count, average trade duration, trade-by-trade log serialization
- [x] 5.4 Integrate worker pool into ProcessManager: start N worker processes on API startup, monitor their health, restart on crash

## 6. Data Pipeline

- [x] 6.1 Create `src/tinohelm/data/providers/binance.py`: Binance historical bar fetcher using REST API (`/fapi/v1/klines` for futures), handling pagination, rate limiting (1200 weight/min), and converting to NT Bar objects
- [x] 6.2 Create `src/tinohelm/data/catalog.py`: ParquetDataCatalog manager — write bars to Parquet files organized by `data/catalog/bars/{instrument_id}/{year-month}.parquet`, update data_catalog database records with covered date ranges
- [x] 6.3 Create `src/tinohelm/data/fetcher.py`: DataFetcher orchestrator — checks data_catalog for existing ranges, computes missing segments, calls Binance provider for gaps, merges into catalog, runs as background task with progress reporting via Redis PubSub

## 7. Strategy Management

- [x] 7.1 Create `src/tinohelm/strategy/registry.py`: StrategyRegistry that scans `strategies/` directory, imports modules, uses `inspect` to find Strategy/StrategyConfig subclasses, extracts config JSON schema via Pydantic, computes file SHA-256 hash, upserts to database
- [x] 7.2 Create `src/tinohelm/strategy/validator.py`: validate a strategy file — importability, has StrategyConfig subclass, has Strategy subclass, Config has instrument_id field, returns validation report with config params and implemented hooks
- [x] 7.3 Create `src/tinohelm/strategy/loader.py`: build ImportableStrategyConfig from strategy name + param overrides, resolving module path and class names from registry
- [x] 7.4 Create strategy scaffold generator in `src/tinohelm/strategy/scaffold.py`: generates `strategies/<name>.py` with skeleton StrategyConfig + Strategy class, all lifecycle hooks with type annotations and docstring comments, common API usage examples as comments; supports `--type bar|tick|book`
- [x] 7.5 Create `strategies/templates/` with one working example strategy (EMA cross on bars) that can be used for end-to-end testing

## 8. FastAPI Application & Routes

- [x] 8.1 Create `src/tinohelm/api/app.py`: FastAPI app with lifespan handler (startup: init DB, run migrations, start ProcessManager, start backtest workers, scan strategies; shutdown: stop all nodes, stop workers)
- [x] 8.2 Create `src/tinohelm/api/deps.py`: dependency injection for DB session, Redis client, ProcessManager, StrategyRegistry
- [x] 8.3 Create `src/tinohelm/api/routes/backtest.py`: POST /api/backtest/run, GET /api/backtest/runs, GET /api/backtest/{run_id}/status, GET /api/backtest/{run_id}/result
- [x] 8.4 Create `src/tinohelm/api/routes/node.py`: POST /api/node/start, POST /api/node/stop, POST /api/node/kill, GET /api/node/status
- [x] 8.5 Create `src/tinohelm/api/routes/strategy.py`: GET /api/strategies, GET /api/strategies/{name}, POST /api/strategies/create, POST /api/strategies/{name}/validate
- [x] 8.6 Create `src/tinohelm/api/routes/data.py`: GET /api/data/catalog, POST /api/data/fetch
- [x] 8.7 Create `src/tinohelm/api/routes/dashboard.py`: GET /api/dashboard/summary, GET /api/portfolio/allocation, GET /api/orders, GET /api/analytics/* endpoints
- [x] 8.8 Create `src/tinohelm/api/routes/settings.py`: GET /api/settings, PUT /api/settings/risk-limits; GET /api/health endpoint
- [x] 8.9 Create `src/tinohelm/api/ws/hub.py`: WebSocket endpoint at /ws/events with subscription filter support, Redis PubSub relay to connected clients, cleanup on disconnect
- [x] 8.10 Create `src/tinohelm/core/audit.py`: audit logging helper that writes action + details to audit_log table

## 9. CLI Tool

- [x] 9.1 Create `src/tinohelm/cli/main.py`: Typer app with subcommand groups (strategy, data, backtest, sandbox, live, node, server), `--format` global option (text/json), `--api-url` option (default http://localhost:8000)
- [x] 9.2 Create `src/tinohelm/cli/strategy.py`: commands for create, list, validate, info — HTTP calls to API with formatted output
- [x] 9.3 Create `src/tinohelm/cli/data.py`: commands for fetch, catalog — HTTP calls with progress display
- [x] 9.4 Create `src/tinohelm/cli/backtest.py`: commands for run, status, wait, result, list — HTTP calls with polling for wait command
- [x] 9.5 Create `src/tinohelm/cli/node.py`: commands for sandbox start/stop, live start/stop/kill, node status
- [x] 9.6 Register CLI entry point in pyproject.toml: `[project.scripts] tino = "tinohelm.cli.main:app"`

## 10. Docker & Deployment

- [x] 10.1 Create `Dockerfile`: multi-stage build — Python 3.11 base, install system deps + nautilus_trader (Rust build), copy source, install package, expose 8000
- [x] 10.2 Create `Dockerfile.web`: Node.js build stage (npm install + next build + next export), nginx production stage serving static files on port 3000
- [x] 10.3 Create `docker-compose.yml`: services for postgres (16-alpine, port 5432, volume ./data/postgres), redis (7-alpine, port 6379, volume ./data/redis, appendonly yes), api (build from Dockerfile, port 8000, volumes for strategies/data/logs/config, depends_on postgres+redis, env_file .env), web (build from Dockerfile.web, port 3000, depends_on api); all with healthchecks
- [x] 10.4 Create `nginx.conf` for web container: serve static files, proxy /api/* and /ws/* to api:8000
- [x] 10.5 Verify end-to-end: `docker compose up --build`, confirm all services start, API health check passes, web UI loads

## 11. Web UI Foundation

- [x] 11.1 Initialize Next.js project in `web/`: `npx create-next-app@latest`, configure `next.config.js` with `output: 'export'`, install Tailwind v4
- [x] 11.2 Create `web/src/app/globals.css`: CSS variables from webui.pen design tokens (all color variables), font utility classes for Space Grotesk and JetBrains Mono in `@layer base`
- [x] 11.3 Create layout component with sidebar navigation matching webui.pen Sidebar structure (Dashboard, Strategies, Backtest, Live Trading, Portfolio, Orders, Watchlist, Analytics, Data Catalog, Settings)
- [x] 11.4 Create reusable components from webui.pen Component Library: buttons (primary/secondary/danger/ghost), badges (status variants), inputs, toggles, cards, metric cards
- [x] 11.5 Create API client in `web/src/lib/api.ts`: fetch wrapper for REST endpoints, WebSocket connection manager with auto-reconnect
- [x] 11.6 Create WebSocket hook `web/src/hooks/useWebSocket.ts`: connect to /ws/events, subscribe to event types, parse incoming JSON, expose typed event streams

## 12. Web UI Pages

- [x] 12.1 Dashboard page: equity curve chart (recharts/lightweight-charts), metric cards (total equity, daily PnL, positions, Sharpe), active strategies table
- [x] 12.2 Backtest Analysis page: config form (strategy select, symbol, date range), run button, progress bar, cumulative returns chart, stats cards, trade log table
- [x] 12.3 Live Trading page: real-time positions table, open orders table, risk metrics panel, three Kill Switch buttons (Pause/Flatten/Kill) with appropriate confirmation UX
- [x] 12.4 Strategy Detail page: K-line chart area, strategy parameters panel, performance metrics
- [x] 12.5 Analytics page: monthly returns heatmap, drawdown chart, returns distribution histogram, rolling Sharpe chart
- [x] 12.6 Portfolio page: allocation donut/bar chart, venue exposure table
- [x] 12.7 Settings page: API key inputs with mask/reveal, risk limit sliders/inputs, notification toggles, system version display
- [x] 12.8 Order History page: filterable order table with status badges, pagination
- [x] 12.9 Watchlist page: instrument price cards grid with real-time updates
- [x] 12.10 Data Catalog page: data set stats cards, data table with symbol/interval/range/size
- [x] 12.11 Strategy Editor page: read-only code viewer (Monaco or CodeMirror) displaying strategy file content
- [ ] 12.12 Mobile responsive views: mobile dashboard layout, mobile live trading with Kill Switch in header, bottom tab navigation

## 13. End-to-End Integration Test

- [x] 13.1 Create example strategy `strategies/ema_cross_demo.py`: simple EMA crossover on BTCUSDT-PERP 1h bars, fully working with proper Config and lifecycle hooks
- [x] 13.2 End-to-end demo flow: start platform via docker compose → fetch sample data via CLI (`tino data fetch`) → submit backtest via CLI (`tino backtest run ema_cross_demo`) → verify result via CLI (`tino backtest result`) → verify result appears in Web UI backtest page → verify dashboard shows backtest stats
- [x] 13.3 Sandbox demo flow: start sandbox node via CLI (`tino sandbox start --strategy ema_cross_demo`) → verify WebSocket events appear in Web UI live trading page → stop sandbox via CLI
