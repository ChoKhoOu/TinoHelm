## ADDED Requirements

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
- **THEN** worker publishes progress percentage and current simulated timestamp to the PubSub channel at regular intervals

#### Scenario: WebSocket progress streaming
- **WHEN** a client subscribes to backtest progress via WebSocket
- **THEN** API server forwards PubSub messages to the WebSocket client in real-time

### Requirement: Backtest result storage
The system SHALL store backtest results as JSON in `data/artifacts/{run_id}.json` and update the database record with summary statistics.

#### Scenario: Backtest completes successfully
- **WHEN** BacktestEngine finishes execution
- **THEN** worker extracts statistics (total return, Sharpe ratio, max drawdown, win rate, trade count, etc.), writes full results to artifact file, updates database record with status "completed" and summary stats

#### Scenario: Backtest fails
- **WHEN** BacktestEngine encounters an error during execution
- **THEN** worker updates database record with status "failed" and error message, publishes failure event to PubSub

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
