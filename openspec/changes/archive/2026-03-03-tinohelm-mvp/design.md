## Context

TinoHelm is a greenfield single-instance quantitative trading platform wrapping NautilusTrader (NT). The target user is a solo trader using AI agents (OpenClaw) to generate, backtest, and deploy crypto trading strategies on Binance. There is no existing codebase — only a `webui.pen` design file with 16 pages of dark-themed trading UI.

Key constraints from NT:
- One TradingNode per process (global singleton state) — sandbox and live MUST run as separate subprocesses.
- `BacktestEngine` has no singleton constraint — multiple backtests can run in parallel.
- NT provides `ImportableStrategyConfig` for dynamic strategy loading from file paths.
- NT's `BridgeActor` pattern (custom Actor inside TradingNode) is the recommended way to extract events.
- Binance adapter is built-in: `BinanceDataClientConfig`, `BinanceExecClientConfig`.
- NT uses Parquet via `ParquetDataCatalog` for historical data.

## Goals / Non-Goals

**Goals:**
- Single `docker compose up` starts the entire platform (PostgreSQL, Redis, API, Web UI)
- AI agents can create strategies, run backtests, and query results entirely via CLI (`tino`) with JSON output
- Sandbox and live TradingNodes can run simultaneously as isolated subprocesses
- Three-level Kill Switch for live trading emergencies (pause → flatten → kill)
- Web UI matches the `webui.pen` design with real-time data via WebSocket
- Host-mounted volumes for all persistent data (DB, Redis, Parquet, strategies, logs)
- Strategy code is identical across backtest/sandbox/live (NT's core design principle)

**Non-Goals:**
- Multi-instance / distributed deployment (single TinoHelm instance only)
- Authentication / RBAC (single user, no auth for MVP)
- Data lake or advanced data infrastructure (Postgres + Redis + Parquet files only)
- Multiple exchange support (Binance only, but adapter pattern preserved)
- Strategy marketplace or sharing
- Mobile native apps (responsive web only)
- Real-time strategy code editing in browser (scaffold + file system editing only)

## Decisions

### D1: Process Architecture — Subprocess per TradingNode

**Decision**: FastAPI main process spawns TradingNode instances as Python subprocesses via `multiprocessing`. Backtest workers also run as subprocesses in a pool.

**Alternatives considered**:
- *Threads*: NT's TradingNode uses global state, threading is not safe.
- *Separate Docker containers*: Adds complexity (inter-container IPC), overkill for single-instance MVP.
- *Celery workers*: Heavy dependency for what is essentially subprocess management.

**Rationale**: `multiprocessing` is the simplest approach that satisfies NT's one-TradingNode-per-process constraint. The Process Manager in the API server handles lifecycle, health checks, and crash recovery. Future migration to separate containers only requires changing deployment config, not code.

### D2: Event Bridge — Redis PubSub via BridgeActor

**Decision**: A custom NT `Actor` (BridgeActor) runs inside each TradingNode subprocess. It subscribes to NT's internal MessageBus events and publishes them to Redis PubSub channels. The FastAPI server subscribes to these channels and forwards to WebSocket clients.

**Alternatives considered**:
- *Direct IPC (pipes/queues)*: Tighter coupling, harder to debug, no fan-out.
- *gRPC streaming*: Heavy dependency for internal communication.
- *Shared memory*: Complex, error-prone, NT not designed for it.

**Rationale**: Redis is already in the stack (NT uses it for cache). PubSub adds near-zero overhead, naturally decouples processes, supports multiple consumers, and is easy to debug (`redis-cli subscribe`).

Channel naming: `tino:{node_type}:{event_type}` (e.g., `tino:live:fills`, `tino:sandbox:positions`).

### D3: Data Isolation — Redis DB + PG Column

**Decision**: Sandbox uses Redis DB 0, Live uses Redis DB 1. PostgreSQL tables use a `node_type` ENUM column ('backtest', 'sandbox', 'live') for filtering. PubSub channels are prefixed with node type.

**Rationale**: Simplest isolation that prevents data leakage. Redis DB separation is native. PG column approach allows cross-environment queries (dashboard showing both sandbox and live) while keeping data logically separate.

### D4: Strategy Loading — NT's ImportableStrategyConfig

**Decision**: Use NT's built-in `ImportableStrategyConfig` for dynamic strategy loading. TinoHelm scans `strategies/` directory, uses `inspect` to find Strategy/StrategyConfig subclasses, and constructs the importable config.

**Alternatives considered**:
- *Custom importlib loading*: Reinventing what NT already provides.
- *Strategy registry with decorators*: Extra boilerplate for strategy authors.

**Rationale**: NT's mechanism is battle-tested and handles module loading, config instantiation, and factory patterns. TinoHelm only needs to discover files and extract metadata.

### D5: Frontend — React + Next.js Static Export + Tailwind v4

**Decision**: Static SPA built with Next.js (`output: 'export'`), styled with Tailwind v4, served by nginx in Docker. Components generated from `webui.pen` using Pencil's code generation guidelines.

**Rationale**: Pencil's code generation is optimized for React + Tailwind. Static export means no Node.js runtime in production — just nginx serving files. The `webui.pen` design system (Space Grotesk + JetBrains Mono, dark theme with two color variants) maps directly to Tailwind CSS variables.

### D6: CLI Architecture — HTTP Client to API Server

**Decision**: `tino` CLI communicates with the FastAPI server via HTTP. All commands support `--format json` for machine consumption.

**Alternatives considered**:
- *Direct Python execution (bypass API)*: Would duplicate logic, can't control running nodes.
- *Dual mode (API + direct)*: Complexity not justified for MVP.

**Rationale**: Single source of truth (API server). CLI works identically whether run on the host or inside Docker. AI agents get structured JSON output.

### D7: Kill Switch — Three Levels Mapped to NT Primitives

**Decision**:
- Level 1 (PAUSE): `Trader.stop_strategy()` + `RiskEngine.set_trading_state(REDUCING)`
- Level 2 (FLATTEN): `strategy.market_exit()` for all strategies + `set_trading_state(HALTED)`
- Level 3 (KILL): `RiskEngine.shutdown_system()` → graceful shutdown → SIGTERM → SIGKILL

**Rationale**: Maps directly to NT's built-in capabilities. Level 2 uses NT's iterative `market_exit()` which handles cancel-then-close-then-wait automatically. Level 3 includes a 2-second grace period for cancel requests to reach the exchange before process termination.

### D8: Database — PostgreSQL with SQLAlchemy + Alembic

**Decision**: SQLAlchemy ORM with async support (asyncpg), Alembic for migrations. Tables: strategies, strategy_versions, backtest_runs, orders, fills, positions, risk_events, data_catalog, audit_log.

**Rationale**: Standard Python stack. Alembic ensures reproducible schema evolution. Async driver matches FastAPI's async nature.

### D9: Backtest Worker — Subprocess Pool with Redis Job Queue

**Decision**: Backtests run in a `multiprocessing` pool. Jobs are queued in Redis (simple list-based queue, not Celery). Progress is reported via Redis PubSub. Results are stored as JSON in `data/artifacts/` and metadata in PostgreSQL.

**Rationale**: Lightweight job queue without external dependencies. BacktestEngine has no singleton constraint so multiple workers can run in parallel. Redis list + BRPOP is a proven simple queue pattern.

## Risks / Trade-offs

- **[Risk] NT version coupling** → Pin nautilus_trader version in pyproject.toml. NT is under active development; API changes could break TinoHelm. Mitigation: abstract NT interactions behind internal interfaces where practical.

- **[Risk] Subprocess crash leaves orphan orders on exchange** → Level 3 Kill Switch includes grace period for cancel requests. NT's `reconciliation=True` config on restart will detect and handle orphan orders. Document this risk prominently.

- **[Risk] Backtest workers consuming too much CPU affect live trading** → MVP runs all in one container. Mitigation: limit backtest worker pool size (default 2). Future: separate container for workers.

- **[Risk] Redis PubSub message loss (no persistence)** → PubSub is fire-and-forget. Mitigation: critical state (orders, positions) is also persisted to PostgreSQL. WebSocket reconnection re-fetches current state from API, not replay from PubSub.

- **[Risk] Parquet data accumulation on host** → No automatic cleanup. Mitigation: `tino data catalog` shows sizes; `tino data prune` (future) for cleanup. Document disk usage expectations.

- **[Trade-off] No auth for MVP** → Acceptable for single-user self-hosted deployment. API binds to localhost by default. Docker network isolation provides basic protection. Add auth layer before any multi-user or remote access scenario.

- **[Trade-off] Static SPA cannot do SSR** → No SEO needed for a trading platform. All data is fetched client-side via API. Acceptable for the use case.

## Open Questions

- **Q1**: Should the backtest result format include raw trade-by-trade data, or just aggregated statistics? Raw data enables richer analysis in the web UI but increases storage.
- **Q2**: How should the strategy scaffold handle indicator imports? NT has 100+ built-in indicators — should the scaffold include common ones as commented examples?
- **Q3**: What is the desired behavior when both sandbox and live are running and a strategy file is modified on disk? Hot-reload? Require manual restart?
