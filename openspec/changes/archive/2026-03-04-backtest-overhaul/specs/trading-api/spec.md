## MODIFIED Requirements

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

## ADDED Requirements

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
