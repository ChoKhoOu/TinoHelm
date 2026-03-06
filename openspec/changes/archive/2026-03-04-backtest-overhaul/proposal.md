## Why

The current backtest system is non-functional end-to-end: the frontend sends field names (`strategy`, `venue`, `date_range`) that don't match the backend schema (`strategy_name`, `symbol`, `interval`, `start_date`, `end_date`), the status polling response doesn't include result data, and the list endpoint returns a different shape than expected. Beyond these blocking bugs, `result.py` extracts only 13 basic metrics while NautilusTrader's `portfolio.analyzer` already computes Sharpe, Sortino, Calmar, max drawdown, equity curve, and more — we're just not reading them. This change fixes the broken flow, unlocks NautilusTrader's full analytics, and adds parameter optimization via Optuna.

## What Changes

### Layer 1 — Fix broken backtest flow
- Fix frontend-backend field mapping for POST `/api/backtest/run`, GET `/api/backtest/runs`, and status polling
- Fix `total_return_pct` hardcoded divisor (currently `/10000` regardless of actual `starting_balance`)
- Make frontend call `/api/backtest/{run_id}/result` after completion instead of expecting result in status response

### Layer 2 — Rich metrics from NautilusTrader
- Extract 25+ metrics from `engine.portfolio.analyzer`: Sharpe Ratio, Sortino Ratio, Calmar Ratio, Omega Ratio, Max Drawdown, Annual Return, Expectancy, Avg Win/Loss ratio, Long/Short distribution, Avg Holding Time, Largest Win/Loss, Win/Loss Streak, Total Fees Paid, Serenity Index
- Generate equity curve time series data from portfolio analyzer returns
- Populate `strategy_version_id` on `BacktestRun` records
- Update frontend metrics cards and charts to display real computed values

### Layer 3 — Advanced capabilities
- **Parameter optimization**: Optuna-based hyperparameter search with configurable fitness objectives (Sharpe, Calmar, Sortino, net profit), train/test split for overfitting prevention
- **Backtest cancellation**: Cancel endpoint + worker SIGTERM handling + cancelled status flow
- **WebSocket progress**: Real-time progress streaming via existing EventBridge instead of HTTP polling
- **Intermediate progress**: Hook into NautilusTrader engine callbacks for progress updates beyond 0%/100%

## Capabilities

### New Capabilities
- `backtest-optimization`: Optuna-based hyperparameter optimization with genetic/TPE search, configurable fitness objectives, train/test data splitting, and optimization result storage

### Modified Capabilities
- `backtest-engine`: Overhaul result extraction to use NautilusTrader's portfolio analyzer for 25+ metrics + equity curve; add cancellation support; add intermediate progress reporting; fix starting_balance in return calculation
- `trading-api`: Fix backtest endpoint request/response schemas to match frontend contract; add cancel endpoint; add optimization endpoints; add WebSocket progress channel
- `web-ui`: Fix backtest page data flow to match corrected API; display rich metrics; render equity curve from real data; add optimization UI; add cancel button

## Impact

- **Backend**: `backtest/result.py` (major rewrite), `backtest/runner.py` (cancel hook, progress callback), `backtest/worker.py` (cancel handling, progress publishing), `api/routes/backtest.py` (schema fixes + new endpoints), new `backtest/optimizer.py`
- **Frontend**: `web/src/app/backtest/page.tsx` (field mapping, metrics display, equity chart, cancel, optimization UI)
- **Database**: `backtest_runs` table gains `strategy_version_id` population; new `optimization_runs` table for Optuna studies
- **Dependencies**: Add `optuna` Python package
- **API contract**: **BREAKING** — backtest request/response schemas change to match what frontend actually sends
