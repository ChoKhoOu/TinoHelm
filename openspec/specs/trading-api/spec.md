# Trading API

## Purpose

Provides the FastAPI REST and WebSocket API layer for TinoHelm, exposing endpoints for backtest management, node control, strategy metadata, data catalog, dashboard analytics, settings, health checks, and real-time event streaming.

## Requirements

### Requirement: Backtest API endpoints
The system SHALL provide REST endpoints for backtest lifecycle management.

#### Scenario: Submit backtest
- **WHEN** client POSTs to `POST /api/backtest/run` with `{strategy, symbol, interval, start_date, end_date, initial_capital?, leverage?, params?}`
- **THEN** system validates inputs, looks up strategy by name, resolves current strategy_version_id, enqueues job in Redis with starting_balance from initial_capital (default 10000) and leverage (default 1), creates BacktestRun database record, and returns `{run_id, status: "queued"}`

#### Scenario: Get backtest status
- **WHEN** client requests `GET /api/backtest/{run_id}/status`
- **THEN** system returns `{run_id, status, progress_pct?, error?}` and when status is "completed" also includes a `result` field with the full statistics and equity_curve from the artifact file

#### Scenario: Get backtest result
- **WHEN** client requests `GET /api/backtest/{run_id}/result`
- **THEN** system returns full result JSON with all 25+ statistics, equity_curve time series, and trade log

#### Scenario: List backtest runs
- **WHEN** client requests `GET /api/backtest/runs` with optional filters (strategy, status, date range, limit, offset)
- **THEN** system returns `{runs: [...], total: N}` where runs is a paginated list of backtest run summaries

#### Scenario: Cancel backtest
- **WHEN** client POSTs to `POST /api/backtest/{run_id}/cancel`
- **THEN** system sets Redis cancel key, returns `{run_id, status: "cancelling"}`, and the worker transitions the run to "cancelled"

### Requirement: Backtest optimization API endpoints
The system SHALL provide REST endpoints for Optuna-based parameter optimization.

#### Scenario: Start optimization
- **WHEN** client POSTs to `POST /api/backtest/optimize` with `{strategy, symbol, interval, start_date, end_date, initial_capital?, leverage?, n_trials, fitness_objective, train_pct?}`
- **THEN** system validates inputs, creates an optimization_runs database record, launches an Optuna study in a background process, and returns `{optimization_id, status: "running"}`

#### Scenario: Get optimization status
- **WHEN** client requests `GET /api/backtest/optimize/{optimization_id}/status`
- **THEN** system returns `{optimization_id, status, trials_completed, total_trials, best_params?, best_value?, current_trial?}`

#### Scenario: Get optimization results
- **WHEN** client requests `GET /api/backtest/optimize/{optimization_id}/result`
- **THEN** system returns `{optimization_id, status, best_params, best_value, fitness_objective, all_trials: [{trial_number, params, value, state}], train_metrics?, test_metrics?}`

#### Scenario: List optimization runs
- **WHEN** client requests `GET /api/backtest/optimize/runs`
- **THEN** system returns paginated list of optimization runs with id, strategy, status, best_value, trials_completed, created_at

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

### Requirement: Node status with risk metrics
The system SHALL include risk metrics in the node status response.

#### Scenario: Get node status with risk metrics
- **WHEN** client requests `GET /api/node/status`
- **THEN** system returns node status including risk_metrics object with fields: daily_var, max_drawdown, margin_used, leverage, total_exposure computed from current positions

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
The system SHALL provide REST endpoints for dashboard summary, portfolio, analytics, orders, and watchlist with real computed data from the database.

#### Scenario: Dashboard summary
- **WHEN** client requests `GET /api/dashboard/summary`
- **THEN** system returns JSON with numeric fields: total_equity, daily_pnl, open_positions (count from positions table), active_strategy_count (count from strategies table), sharpe_ratio, total_orders_today (count from orders table), win_rate

#### Scenario: Portfolio allocation
- **WHEN** client requests `GET /api/portfolio/allocation`
- **THEN** system returns current positions from the positions table grouped by instrument

#### Scenario: Order history
- **WHEN** client requests `GET /api/orders` with optional filters (status, instrument, date range)
- **THEN** system returns paginated order list from the orders table

#### Scenario: Analytics returns heatmap
- **WHEN** client requests `GET /api/analytics/returns-heatmap`
- **THEN** system returns monthly return percentages aggregated from completed backtest_runs, grouped by year and month

#### Scenario: Analytics drawdown
- **WHEN** client requests `GET /api/analytics/drawdown`
- **THEN** system returns drawdown time series computed from completed backtest_runs max_drawdown values

#### Scenario: Analytics returns distribution
- **WHEN** client requests `GET /api/analytics/distribution`
- **THEN** system returns histogram bins of total_return values from completed backtest_runs

#### Scenario: Analytics rolling Sharpe
- **WHEN** client requests `GET /api/analytics/rolling-sharpe`
- **THEN** system returns rolling Sharpe ratio values computed from completed backtest_runs ordered by completion date

#### Scenario: Analytics with no data
- **WHEN** client requests any analytics endpoint and no backtest_runs exist
- **THEN** system returns `{data: [], message: "No backtest data available"}`

### Requirement: Settings API endpoints
The system SHALL provide REST endpoints for reading and updating platform settings including real-time system information.

#### Scenario: Get settings
- **WHEN** client requests `GET /api/settings`
- **THEN** system returns current settings including risk limits and notification preferences

#### Scenario: Get system info
- **WHEN** client requests `GET /api/health`
- **THEN** system returns status with additional fields: nautilus_version, python_version, redis_version, uptime_seconds, platform_version

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
