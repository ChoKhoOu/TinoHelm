## MODIFIED Requirements

### Requirement: Backtest analysis page
The system SHALL display backtest configuration, execution, rich metrics, equity curve, and trade log with all data fetched from the backend API.

#### Scenario: Strategy list from API
- **WHEN** user opens the backtest page
- **THEN** strategy select dropdown is populated from `GET /api/strategies` response

#### Scenario: Submit new backtest
- **WHEN** user fills in backtest configuration (strategy, symbol, interval, date range, initial capital, leverage) and clicks "Run Backtest"
- **THEN** system POSTs to `/api/backtest/run` with `{strategy, symbol, interval, start_date, end_date, initial_capital, leverage}` and begins polling status

#### Scenario: Backtest progress display
- **WHEN** a backtest is running
- **THEN** page displays a progress indicator with percentage from status polling or WebSocket events

#### Scenario: View backtest result with rich metrics
- **WHEN** backtest completes and result is available
- **THEN** page displays metrics cards for: Total Return (%), Sharpe Ratio, Max Drawdown (%), Win Rate (%), Profit Factor, Total Trades, Sortino Ratio, Calmar Ratio, Annual Return (%), Expectancy, Avg Win/Loss Ratio, Long/Short distribution

#### Scenario: Equity curve chart from real data
- **WHEN** backtest completes and result contains equity_curve data
- **THEN** page renders a cumulative returns line chart using the equity_curve time series from the result, with timestamp on x-axis and equity value on y-axis, plus a drawdown area chart below

#### Scenario: Trade log table
- **WHEN** backtest completes and result contains trade_log data
- **THEN** page displays a table with columns: Time, Instrument, Side, Quantity, Entry Price, Exit Price, PnL, Duration

#### Scenario: Cancel running backtest
- **WHEN** user clicks "Cancel" button while a backtest is running or queued
- **THEN** system POSTs to `/api/backtest/{run_id}/cancel` and updates UI to show cancelled state

#### Scenario: View past backtest runs
- **WHEN** user opens the backtest page
- **THEN** page fetches `GET /api/backtest/runs` and displays a list/table of recent runs with status, strategy, date range, and key metrics; clicking a run loads its full result

## ADDED Requirements

### Requirement: Parameter optimization UI
The system SHALL provide a UI for configuring and running Optuna-based parameter optimization.

#### Scenario: Configure optimization
- **WHEN** user selects a strategy and clicks "Optimize"
- **THEN** page displays an optimization form with fields: n_trials (default 100), fitness objective dropdown (Sharpe/Calmar/Sortino/Net Profit), train/test split percentage slider (default 85%), plus the standard backtest config fields (symbol, interval, dates, capital, leverage)

#### Scenario: Run optimization
- **WHEN** user submits the optimization form
- **THEN** system POSTs to `/api/backtest/optimize`, displays a progress view showing trials completed / total, current best value, and a chart of objective values across trials

#### Scenario: View optimization results
- **WHEN** optimization completes
- **THEN** page displays: best parameters found, best fitness value, train vs test metrics comparison, a table of all trials sorted by objective value, and a button to "Run Backtest with Best Params" that pre-fills the backtest form
