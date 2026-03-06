# Backtest Engine

## Purpose

Provides backtest job submission, execution via worker pool, progress reporting, result storage, and result querying capabilities for the TinoHelm trading platform.

## Requirements

### Requirement: Backtest job submission
The system SHALL accept backtest job submissions specifying strategy name, instrument, time range, and optional parameter overrides, returning a unique run_id.

#### Scenario: Submit backtest via API
- **WHEN** user POSTs to `/api/backtest/run` with strategy name, symbol, start date, end date, and optional params
- **THEN** system validates inputs, enqueues the job in Redis, creates a `backtest_runs` database record with status "queued", and returns the run_id

#### Scenario: Submit backtest via CLI
- **WHEN** user runs `tino backtest run <strategy> --symbol <sym> --start <date> --end <date>`
- **THEN** CLI sends the request to the API and displays the run_id

### Requirement: Backtest worker pool
The system SHALL execute backtests in a subprocess worker pool using NT's BacktestEngine, with configurable pool size (default 2).

#### Scenario: Worker picks up job
- **WHEN** a backtest job is in the Redis queue AND a worker is available
- **THEN** worker dequeues the job, constructs BacktestRunConfig with ImportableStrategyConfig pointing to the strategy file, and runs BacktestEngine

#### Scenario: Multiple concurrent backtests
- **WHEN** multiple backtest jobs are submitted
- **THEN** up to `max_workers` (default 2) jobs run simultaneously, remaining jobs wait in queue

### Requirement: Backtest progress reporting
The system SHALL report backtest progress via Redis PubSub channel `tino:backtest:progress:{run_id}`.

#### Scenario: Progress updates during backtest
- **WHEN** a backtest is running
- **THEN** worker publishes progress percentage (0 at start, 100 at completion) and status transitions to the PubSub channel

#### Scenario: WebSocket progress streaming
- **WHEN** a client subscribes to backtest progress via the existing `/ws/events` WebSocket endpoint
- **THEN** EventBridge forwards progress events from Redis PubSub to the WebSocket client in real-time, with event type `backtest.progress` containing `{run_id, progress_pct, status}`

### Requirement: Backtest result storage
The system SHALL store backtest results as JSON in `data/artifacts/{run_id}.json` and update the database record with summary statistics.

#### Scenario: Backtest completes successfully
- **WHEN** BacktestEngine finishes execution
- **THEN** worker extracts statistics from `engine.portfolio.analyzer` including: total_pnl, total_return_pct (using actual starting_balance), sharpe_ratio, sortino_ratio, calmar_ratio, omega_ratio, max_drawdown, annual_return, expectancy, avg_win, avg_loss, avg_win_loss_ratio, win_rate, profit_factor, total_trades, winning_trades, losing_trades, winning_streak, losing_streak, largest_win, largest_loss, long_pct, short_pct, avg_holding_time, avg_winning_holding_time, avg_losing_holding_time, total_fees, gross_profit, gross_loss, open_positions, total_orders, filled_orders, final_balance; writes full results to artifact file; updates database record with status "completed" and summary stats

#### Scenario: Equity curve data included in results
- **WHEN** BacktestEngine finishes execution
- **THEN** worker extracts the portfolio returns time series from `engine.portfolio.analyzer` and includes an `equity_curve` array in the result JSON, where each entry contains `{timestamp, equity, returns_pct, drawdown_pct}`

#### Scenario: Strategy version linked to backtest run
- **WHEN** a backtest run is created
- **THEN** system looks up the current `StrategyVersion` for the strategy and populates `strategy_version_id` on the `BacktestRun` record

#### Scenario: Backtest fails
- **WHEN** BacktestEngine encounters an error during execution
- **THEN** worker updates database record with status "failed" and error message, publishes failure event to PubSub

### Requirement: Backtest cancellation
The system SHALL support cancelling queued and running backtest jobs.

#### Scenario: Cancel a queued backtest
- **WHEN** client POSTs to `POST /api/backtest/{run_id}/cancel` and the backtest status is "queued"
- **THEN** system sets a Redis cancel key `tino:backtest:cancel:{run_id}`, and when the worker dequeues the job it checks the cancel key, skips execution, and updates status to "cancelled"

#### Scenario: Cancel a running backtest
- **WHEN** client POSTs to `POST /api/backtest/{run_id}/cancel` and the backtest status is "running"
- **THEN** system sets the Redis cancel key and sends SIGTERM to the worker process; worker catches the signal, marks the run as "cancelled" in the database, and publishes cancellation event

#### Scenario: Cancel an already completed backtest
- **WHEN** client POSTs to `POST /api/backtest/{run_id}/cancel` and the backtest status is "completed" or "failed"
- **THEN** system returns 409 Conflict with message indicating the backtest is already in a terminal state

### Requirement: Backtest result querying
The system SHALL provide API endpoints to list backtest runs, retrieve results, and get trade-level detail.

#### Scenario: List backtest runs
- **WHEN** user requests `GET /api/backtest/runs` or `tino backtest list`
- **THEN** system returns paginated list of backtest runs with run_id, strategy, status, and summary stats

#### Scenario: Get backtest result
- **WHEN** user requests `GET /api/backtest/{run_id}/result` or `tino backtest result <run_id>`
- **THEN** system returns full result JSON including all statistics and trade log

#### Scenario: CLI wait for completion
- **WHEN** user runs `tino backtest wait <run_id>`
- **THEN** CLI polls status until completed/failed, then outputs the result
