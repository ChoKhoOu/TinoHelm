# Web UI

## Purpose

Provides the React + Next.js static SPA serving as the primary user interface for TinoHelm, including dashboard, backtest analysis, live trading, analytics, portfolio, settings, and mobile-responsive views matching the webui.pen design file.

## Requirements

### Requirement: Static SPA built from webui.pen design
The system SHALL provide a React + Next.js static SPA matching the `webui.pen` design file, built with Tailwind v4 and serving as the primary user interface.

#### Scenario: Build produces static files
- **WHEN** `next build` is executed with `output: 'export'` config
- **THEN** system produces a static `out/` directory containing all HTML/JS/CSS files servable by nginx

#### Scenario: Design system matches webui.pen
- **WHEN** the SPA is rendered
- **THEN** UI uses Space Grotesk for headings, JetBrains Mono for code/data, dark theme colors from webui.pen CSS variables, and follows the component library defined in the design

### Requirement: Dashboard page
The system SHALL display a dashboard overview with equity curve, key metrics, and active strategy list fetched from the backend API.

#### Scenario: Dashboard loads
- **WHEN** user navigates to the dashboard
- **THEN** page displays total equity, daily PnL, active positions count, Sharpe ratio, an equity curve chart, and a list of active strategies with their status

#### Scenario: Dashboard loads with API data
- **WHEN** user navigates to the dashboard and API is available
- **THEN** page fetches `GET /api/dashboard/summary` and displays total equity, daily PnL, active positions count, Sharpe ratio from the response; formats numeric values for display

#### Scenario: Dashboard loading state
- **WHEN** dashboard is fetching data
- **THEN** page displays a loading indicator

#### Scenario: Dashboard API unavailable
- **WHEN** API request fails
- **THEN** page displays an error message instead of mock data

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

### Requirement: Live trading page
The system SHALL display real-time positions, open orders, and risk metrics all fetched from backend API and WebSocket.

#### Scenario: Risk metrics from API
- **WHEN** user is on the live trading page
- **THEN** risk metrics panel displays data from `GET /api/node/status` risk_metrics field

#### Scenario: Live data streaming
- **WHEN** user is on the live trading page and a node is running
- **THEN** page displays real-time positions, open orders updated via WebSocket

#### Scenario: Kill Switch UI
- **WHEN** user views the live trading page
- **THEN** page displays three Kill Switch buttons: Pause (green, instant click), Flatten (orange, confirmation dialog), Kill (red, 3-second long press with progress indicator)

### Requirement: Environment switching
The system SHALL allow users to switch between sandbox and live views in the web panel.

#### Scenario: Switch environment
- **WHEN** user toggles between sandbox and live in the UI
- **THEN** all data displays, WebSocket subscriptions, and controls switch to the selected environment

### Requirement: Analytics page
The system SHALL display analytics charts with data fetched from backend analytics API endpoints.

#### Scenario: Analytics loads
- **WHEN** user navigates to analytics
- **THEN** page displays monthly returns heatmap, drawdown over time chart, returns distribution histogram, and rolling Sharpe ratio chart

#### Scenario: Analytics loads with API data
- **WHEN** user navigates to analytics
- **THEN** page fetches all four analytics endpoints and renders charts with real data

#### Scenario: Analytics with no data
- **WHEN** analytics API returns empty data arrays
- **THEN** page displays empty state messages in each chart area

### Requirement: Portfolio page
The system SHALL display portfolio allocation and venue exposure fetched from `GET /api/portfolio/allocation`.

#### Scenario: Portfolio loads
- **WHEN** user navigates to portfolio
- **THEN** page displays position allocation breakdown chart and venue exposure table

#### Scenario: Portfolio loads with API data
- **WHEN** user navigates to portfolio
- **THEN** page fetches allocation data and renders position breakdown and venue exposure

#### Scenario: Portfolio empty state
- **WHEN** no positions exist
- **THEN** page displays empty state message

### Requirement: Settings page
The system SHALL display and allow editing of platform settings fetched from the backend API.

#### Scenario: View and edit settings
- **WHEN** user navigates to settings
- **THEN** page displays Binance API key configuration, risk limit inputs, notification toggles, and NautilusTrader/Python/Redis version info

#### Scenario: Settings loads with API data
- **WHEN** user navigates to settings
- **THEN** page fetches `GET /api/health` for system info and `GET /api/settings` for risk limits; displays real values

#### Scenario: Update risk limits
- **WHEN** user modifies a risk limit value
- **THEN** page sends `PUT /api/settings/risk-limits` and shows success/error feedback

### Requirement: Additional pages
The system SHALL provide order history, watchlist, data catalog, and strategy list pages fetched from backend APIs.

#### Scenario: Order history
- **WHEN** user navigates to order history
- **THEN** page displays filterable, paginated order table

#### Scenario: Order history from API
- **WHEN** user navigates to order history
- **THEN** page fetches `GET /api/orders` with filter parameters and displays real order data

#### Scenario: Watchlist
- **WHEN** user navigates to watchlist
- **THEN** page displays real-time price cards for watched instruments

#### Scenario: Watchlist from API
- **WHEN** user navigates to watchlist
- **THEN** page fetches `GET /api/watchlist` and displays saved instruments

#### Scenario: Add instrument to watchlist
- **WHEN** user clicks "Add Instrument" and submits the form
- **THEN** page sends `POST /api/watchlist` and refreshes the list

#### Scenario: Data catalog
- **WHEN** user navigates to data catalog
- **THEN** page displays available data sets with stats and a data table

#### Scenario: Data catalog from API
- **WHEN** user navigates to data catalog
- **THEN** page fetches `GET /api/data/catalog` and displays real dataset information

#### Scenario: Strategies list from API
- **WHEN** user navigates to strategies page
- **THEN** page fetches `GET /api/strategies` and displays discovered strategies with their metadata

### Requirement: Mobile responsive views
The system SHALL provide mobile-optimized dashboard and live trading views with bottom navigation.

#### Scenario: Mobile dashboard
- **WHEN** user accesses dashboard on a mobile device (viewport < 768px)
- **THEN** page renders mobile layout with compact metrics, chart, and strategy list with bottom tab navigation

#### Scenario: Mobile live trading with Kill Switch
- **WHEN** user accesses live trading on mobile
- **THEN** page renders mobile layout with Kill Switch button prominently displayed in the header

### Requirement: Motion design specification
The system SHALL implement animations following the webui.pen motion design spec with three timing tiers.

#### Scenario: Micro interactions
- **WHEN** user interacts with buttons, toggles, or hover states
- **THEN** animations use 150ms duration with ease-out timing

#### Scenario: Panel transitions
- **WHEN** panels, modals, or page sections animate
- **THEN** animations use 250ms duration with ease-in-out timing

#### Scenario: Data loading transitions
- **WHEN** charts, tables, or data-heavy content loads
- **THEN** animations use 400ms duration with ease-in-out timing
