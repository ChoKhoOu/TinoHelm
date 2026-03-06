## ADDED Requirements

### Requirement: Backtest API endpoints
The system SHALL provide REST endpoints for backtest lifecycle management.

#### Scenario: Submit backtest
- **WHEN** client POSTs to `POST /api/backtest/run` with `{strategy, symbol, interval, start, end, params?}`
- **THEN** system validates inputs, enqueues job, and returns `{run_id, status: "queued"}`

#### Scenario: Get backtest status
- **WHEN** client requests `GET /api/backtest/{run_id}/status`
- **THEN** system returns `{run_id, status, progress_pct?, error?}`

#### Scenario: Get backtest result
- **WHEN** client requests `GET /api/backtest/{run_id}/result`
- **THEN** system returns full result JSON with statistics and trade log

#### Scenario: List backtest runs
- **WHEN** client requests `GET /api/backtest/runs`
- **THEN** system returns paginated list of runs with filters (strategy, status, date range)

### Requirement: Node control API endpoints
The system SHALL provide REST endpoints for starting, stopping, and querying TradingNode status.

#### Scenario: Start node
- **WHEN** client POSTs to `POST /api/node/start` with `{mode: "sandbox"|"live", strategies: [...]}`
- **THEN** system starts the appropriate TradingNode subprocess and returns status

#### Scenario: Stop node
- **WHEN** client POSTs to `POST /api/node/stop` with `{mode: "sandbox"|"live"}`
- **THEN** system initiates graceful shutdown and returns status

#### Scenario: Kill switch
- **WHEN** client POSTs to `POST /api/node/kill` with `{level: 1|2|3, strategy_id?}`
- **THEN** system executes the corresponding Kill Switch level and returns confirmation

#### Scenario: Get node status
- **WHEN** client requests `GET /api/node/status`
- **THEN** system returns status of all nodes (sandbox, live) including running state, strategies, positions, heartbeat info

### Requirement: Strategy API endpoints
The system SHALL provide REST endpoints for strategy listing and metadata.

#### Scenario: List strategies
- **WHEN** client requests `GET /api/strategies`
- **THEN** system returns list of discovered strategies with metadata (name, version, params, hooks)

#### Scenario: Get strategy detail
- **WHEN** client requests `GET /api/strategies/{name}`
- **THEN** system returns strategy detail including config schema, version history, and associated backtest runs

### Requirement: Data API endpoints
The system SHALL provide REST endpoints for data catalog queries and data fetch triggers.

#### Scenario: List data catalog
- **WHEN** client requests `GET /api/data/catalog`
- **THEN** system returns available data with symbols, intervals, and date ranges

#### Scenario: Trigger data fetch
- **WHEN** client POSTs to `POST /api/data/fetch` with `{symbol, interval, start, end}`
- **THEN** system initiates background data download and returns job status

### Requirement: Dashboard and analytics API endpoints
The system SHALL provide REST endpoints for dashboard summary, portfolio, analytics, orders, and watchlist.

#### Scenario: Dashboard summary
- **WHEN** client requests `GET /api/dashboard/summary`
- **THEN** system returns total equity, daily PnL, active strategy count, Sharpe ratio, and open position count

#### Scenario: Portfolio allocation
- **WHEN** client requests `GET /api/portfolio/allocation`
- **THEN** system returns current position allocation breakdown by instrument

#### Scenario: Order history
- **WHEN** client requests `GET /api/orders` with optional filters (status, instrument, date range)
- **THEN** system returns paginated order list

### Requirement: Settings API endpoints
The system SHALL provide REST endpoints for reading and updating platform settings.

#### Scenario: Get settings
- **WHEN** client requests `GET /api/settings`
- **THEN** system returns current settings including Binance connection status, risk limits, notification preferences

#### Scenario: Update risk limits
- **WHEN** client PUTs to `PUT /api/settings/risk-limits` with new values
- **THEN** system updates the configuration and logs the change to audit log

### Requirement: Health check endpoint
The system SHALL provide a health check endpoint for Docker and monitoring.

#### Scenario: All services healthy
- **WHEN** client requests `GET /api/health`
- **THEN** system returns `{status: "healthy", api: "ok", postgres: "ok", redis: "ok", sandbox_node: "running"|"stopped", live_node: "running"|"stopped", uptime_seconds: N}`

### Requirement: WebSocket event streaming
The system SHALL provide WebSocket endpoints for real-time event streaming.

#### Scenario: Subscribe to events
- **WHEN** client connects to `WS /ws/events` with subscription filters
- **THEN** server streams matching events (fills, positions, orders, bars, ticker, risk, backtest progress) from Redis PubSub in real-time

#### Scenario: Equity curve streaming
- **WHEN** client connects to `WS /ws/equity`
- **THEN** server streams periodic equity snapshots for real-time chart updates
