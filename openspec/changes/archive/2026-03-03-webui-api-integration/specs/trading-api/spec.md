## MODIFIED Requirements

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

### Requirement: Node status with risk metrics
The system SHALL include risk metrics in the node status response.

#### Scenario: Get node status with risk metrics
- **WHEN** client requests `GET /api/node/status`
- **THEN** system returns node status including risk_metrics object with fields: daily_var, max_drawdown, margin_used, leverage, total_exposure computed from current positions
