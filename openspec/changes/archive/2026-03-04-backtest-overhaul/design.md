## Context

TinoHelm's backtest system wraps NautilusTrader's `BacktestEngine` via a Redis-queue worker pool. The current implementation has three severity levels of issues:

1. **Broken** — Frontend and backend use incompatible request/response schemas; the backtest flow cannot complete end-to-end
2. **Incomplete** — `result.py` manually computes 13 basic metrics while NautilusTrader's `portfolio.analyzer` already computes 25+ metrics including Sharpe, drawdown, equity curve — all unused
3. **Missing** — No parameter optimization, no cancel mechanism, no real-time progress

The frontend (`backtest/page.tsx`) sends `{strategy, venue, date_range, initial_capital}` but the API expects `{strategy_name, symbol, interval, start_date, end_date, params}`. The runs list endpoint returns `list[BacktestRunItem]` but the frontend expects `{runs: [...]}`. The status endpoint only returns `{status, progress_pct}` but the frontend expects `result` data inline.

## Goals / Non-Goals

**Goals:**
- Fix all frontend-backend contract mismatches so the backtest flow works end-to-end
- Extract 25+ metrics from NautilusTrader's built-in `portfolio.analyzer` and position/order reports
- Generate equity curve time series from the engine's portfolio returns
- Add Optuna-based parameter optimization with train/test split
- Add backtest cancellation (API + worker + frontend)
- Add WebSocket progress streaming via EventBridge

**Non-Goals:**
- Multi-venue backtests (only BINANCE for now)
- Multi-symbol or multi-timeframe in a single backtest run (NautilusTrader supports this but it's complex; defer to future)
- Walk-forward analysis (Layer 3+ — defer)
- Custom fee models or slippage simulation beyond NautilusTrader defaults
- Backtest CLI commands (existing spec mentions them but they're not in scope here)

## Decisions

### D1: Extract metrics from NautilusTrader's portfolio.analyzer instead of manual calculation

**Choice**: Use `engine.portfolio.analyzer` for all risk-adjusted metrics.

**Rationale**: NautilusTrader already computes Sharpe, Sortino, max drawdown, returns series etc. in its Rust/Cython core. Re-implementing these in Python is both slower and error-prone. The current `result.py` manually iterates positions to compute PnL — this should only supplement what the analyzer doesn't provide (e.g., trade-level stats like win/loss streak).

**Alternatives considered**:
- Manual Python computation (current approach) — duplicates work, error-prone (`total_return_pct` bug proves this)
- Third-party library (e.g., quantstats) — adds dependency for what NautilusTrader already provides

### D2: Pass starting_balance through the extraction pipeline

**Choice**: `BacktestRunner` passes `starting_balance` to `extract_backtest_results()` as a parameter.

**Rationale**: The current code hardcodes `total_pnl / 10000` for return percentage. The actual starting balance is available in `strategy_params` and should flow through. The portfolio analyzer also has this information via account balances.

### D3: Fix API contract by updating backend to match frontend expectations

**Choice**: Update the backend API schemas to accept what the frontend sends, rather than changing the frontend.

**Rationale**: The frontend was built from the design spec and represents the intended UX. The backend schemas were written independently and diverged. Aligning backend to frontend is less disruptive since:
- Frontend field names are more user-friendly (`strategy` vs `strategy_name`)
- The frontend already handles the response shapes it expects
- Changing frontend field names would require updating i18n keys, form state, etc.

Specifically:
- POST `/api/backtest/run`: Accept `{strategy, symbol, interval, start_date, end_date, initial_capital, leverage, params}`
- GET `/api/backtest/runs`: Return `{runs: [...], total: N}` wrapper
- GET `/api/backtest/{run_id}/status`: Include `result` field when status is "completed"

### D4: Optuna optimizer as a separate module with dedicated endpoints

**Choice**: New `backtest/optimizer.py` module + new API endpoints under `/api/backtest/optimize`.

**Rationale**: Optimization is a long-running process (runs many backtests). It needs:
- Its own Redis queue or async execution model
- A database table to store study results
- Separate API endpoints (start optimization, get progress, get results)
- Train/test date split logic

Embedding this in the existing backtest worker would over-complicate the single-run flow. A dedicated optimizer runs Optuna's `study.optimize()` which internally calls `BacktestRunner.run()` per trial.

**Alternatives considered**:
- Reuse backtest worker pool — optimizer needs to run N sequential trials internally, doesn't fit the one-job-per-dequeue model
- Client-side optimization (frontend submits N runs) — wasteful, no intelligent search

### D5: Cancellation via Redis signal key + worker polling

**Choice**: API sets a Redis key `tino:backtest:cancel:{run_id}`, worker checks it between engine setup and run, and periodically if possible.

**Rationale**: NautilusTrader's `BacktestEngine.run()` is a blocking call — we can't interrupt it mid-execution without process termination. The cancel mechanism works at two levels:
- **Pre-run**: Worker checks cancel key before starting the engine → immediate skip
- **Mid-run**: Worker process receives SIGTERM → catches it, marks as cancelled
- **Queue**: Queued jobs check cancel key when dequeued → skip if cancelled

### D6: WebSocket progress via existing EventBridge

**Choice**: Publish progress events to EventBridge Redis channel, let the existing `/ws/events` endpoint forward them.

**Rationale**: The EventBridge + WebSocket hub already exist (`core/bridge.py` + `api/ws/hub.py`). We just need the worker to publish progress events in the format the bridge expects, and the frontend to subscribe. No new WebSocket endpoint needed.

## Risks / Trade-offs

- **[NautilusTrader API instability]** → The `portfolio.analyzer` API may change between NT versions. Mitigation: Pin NT version in requirements; wrap analyzer access in a try/except fallback to manual calculation.
- **[Optuna adds ~50MB dependency]** → Acceptable for the value it provides. Mitigation: Make it an optional dependency (`pip install tinohelm[optimize]`).
- **[Cancel can't interrupt mid-backtest]** → `BacktestEngine.run()` is blocking. Mitigation: Document that cancel is best-effort for running backtests; guaranteed for queued ones. Process SIGTERM handles the worst case.
- **[Breaking API changes]** → POST `/api/backtest/run` schema changes. Mitigation: No external consumers exist; this is an internal API between our frontend and backend.
- **[Optimizer resource consumption]** → Hundreds of backtest runs consume CPU. Mitigation: Configurable `n_trials` limit; optimizer runs as a single process (not in worker pool) to avoid starving regular backtests.
