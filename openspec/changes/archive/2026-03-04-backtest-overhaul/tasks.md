## 1. Fix Backtest Result Extraction (Layer 1 + 2)

- [x] 1.1 Rewrite `backtest/result.py` to extract 25+ metrics from `engine.portfolio.analyzer` (Sharpe, Sortino, Calmar, Omega, max drawdown, annual return, expectancy, avg win/loss, win/loss streak, largest win/loss, long/short pct, avg holding times, total fees) plus existing trade-level stats
- [x] 1.2 Add equity curve time series extraction from portfolio analyzer returns (array of `{timestamp, equity, returns_pct, drawdown_pct}`)
- [x] 1.3 Fix `total_return_pct` to use actual `starting_balance` parameter instead of hardcoded 10000
- [x] 1.4 Pass `starting_balance` from `BacktestRunner` through to `extract_backtest_results()`

## 2. Fix Backend API Schemas (Layer 1)

- [x] 2.1 Update POST `/api/backtest/run` request schema to accept `{strategy, symbol, interval, start_date, end_date, initial_capital?, leverage?, params?}` — rename `strategy_name` to `strategy`, add `initial_capital` and `leverage` fields
- [x] 2.2 Update GET `/api/backtest/runs` response to return `{runs: [...], total: N}` wrapper instead of flat list
- [x] 2.3 Update GET `/api/backtest/{run_id}/status` to include `result` field with full statistics + equity_curve when status is "completed"
- [x] 2.4 Populate `strategy_version_id` on BacktestRun record when creating a new run

## 3. Fix Frontend Backtest Page (Layer 1 + 2)

- [x] 3.1 Update backtest form to submit correct field names matching the fixed API schema (`strategy`, `symbol`, `interval`, `start_date`, `end_date`, `initial_capital`, `leverage`)
- [x] 3.2 Update runs list fetching to handle `{runs: [...], total: N}` response shape
- [x] 3.3 Update status polling to read `result` from status response when completed, or fall back to calling `/api/backtest/{run_id}/result`
- [x] 3.4 Display all rich metrics in cards: Total Return, Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor, Total Trades, Annual Return, Expectancy, Avg Win/Loss Ratio, Long/Short %
- [x] 3.5 Render equity curve chart from real `equity_curve` time series data with drawdown area chart below
- [x] 3.6 Update trade log table columns to include Entry Price, Exit Price, Duration
- [x] 3.7 Add i18n keys for all new metrics and labels

## 4. Backtest Cancellation (Layer 3)

- [x] 4.1 Add POST `/api/backtest/{run_id}/cancel` endpoint that sets Redis cancel key and returns cancelling status
- [x] 4.2 Update `backtest/worker.py` to check Redis cancel key before starting engine run, and handle SIGTERM to mark run as "cancelled"
- [x] 4.3 Add "Cancel" button to frontend backtest page that calls cancel endpoint and updates UI state

## 5. WebSocket Progress Streaming (Layer 3)

- [x] 5.1 Update `backtest/worker.py` to publish progress events to EventBridge Redis channel in format `{type: "backtest.progress", run_id, progress_pct, status}`
- [x] 5.2 Update frontend to optionally subscribe to WebSocket progress events instead of HTTP polling when WebSocket connection is available

## 6. Database Schema for Optimization

- [x] 6.1 Add `OptimizationRun` model to `db/models.py` with fields: id, strategy_id, symbol, interval, start_date, end_date, n_trials, fitness_objective, train_pct, status, best_params_json, best_value, trials_completed, created_at, completed_at
- [x] 6.2 Ensure table is created via `Base.metadata.create_all` in app lifespan

## 7. Optimizer Engine

- [x] 7.1 Create `backtest/optimizer.py` with `BacktestOptimizer` class that: extracts parameter ranges from strategy config schema, creates Optuna study with TPE sampler, runs N trials calling `BacktestRunner.run()` per trial, reports progress via Redis pub/sub
- [x] 7.2 Implement train/test date split logic — split the date range by train_pct, run optimization on training period, validate best params on test period
- [x] 7.3 Implement fitness objective mapping (sharpe → statistics.sharpe_ratio, calmar → statistics.calmar_ratio, sortino → statistics.sortino_ratio, profit → statistics.total_pnl)
- [x] 7.4 Add `optuna` to project dependencies (optional: `pip install tinohelm[optimize]`)

## 8. Optimization API Endpoints

- [x] 8.1 Add POST `/api/backtest/optimize` endpoint that validates inputs, creates OptimizationRun record, launches optimizer in background process
- [x] 8.2 Add GET `/api/backtest/optimize/{optimization_id}/status` endpoint returning progress, best params so far
- [x] 8.3 Add GET `/api/backtest/optimize/{optimization_id}/result` endpoint returning full results with all trials and train/test metrics
- [x] 8.4 Add GET `/api/backtest/optimize/runs` endpoint for listing optimization runs

## 9. Optimization Frontend

- [x] 9.1 Add "Optimize" button and optimization configuration form (n_trials, fitness objective dropdown, train/test split slider)
- [x] 9.2 Add optimization progress view showing trials completed, current best value, objective convergence chart
- [x] 9.3 Add optimization results view with best params, train vs test comparison, all trials table, "Run Backtest with Best Params" button
- [x] 9.4 Add i18n keys for all optimization UI labels

## 10. Verification

- [ ] 10.1 Verify end-to-end backtest flow: submit → poll → result with all 25+ metrics displayed
- [ ] 10.2 Verify equity curve renders from real data
- [ ] 10.3 Verify cancel flow works for queued and running backtests
- [ ] 10.4 Verify optimization flow: configure → run → view results → run backtest with best params
- [x] 10.5 Build check: Python backend has no import errors, Next.js builds all pages successfully
