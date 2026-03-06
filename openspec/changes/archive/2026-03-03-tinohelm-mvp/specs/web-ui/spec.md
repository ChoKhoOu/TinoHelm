## ADDED Requirements

### Requirement: Static SPA built from webui.pen design
The system SHALL provide a React + Next.js static SPA matching the `webui.pen` design file, built with Tailwind v4 and serving as the primary user interface.

#### Scenario: Build produces static files
- **WHEN** `next build` is executed with `output: 'export'` config
- **THEN** system produces a static `out/` directory containing all HTML/JS/CSS files servable by nginx

#### Scenario: Design system matches webui.pen
- **WHEN** the SPA is rendered
- **THEN** UI uses Space Grotesk for headings, JetBrains Mono for code/data, dark theme colors from webui.pen CSS variables, and follows the component library defined in the design

### Requirement: Dashboard page
The system SHALL display a dashboard overview with equity curve, key metrics, and active strategy list.

#### Scenario: Dashboard loads
- **WHEN** user navigates to the dashboard
- **THEN** page displays total equity, daily PnL, active positions count, Sharpe ratio, an equity curve chart, and a list of active strategies with their status

### Requirement: Backtest analysis page
The system SHALL display backtest configuration, results, cumulative returns chart, and trade log.

#### Scenario: View backtest result
- **WHEN** user navigates to backtest analysis with a run_id
- **THEN** page displays strategy name, date range, cumulative returns chart, key statistics (Sharpe, max drawdown, win rate, total return), and a paginated trade log table

#### Scenario: Submit new backtest
- **WHEN** user fills in backtest configuration form and clicks "Run Backtest"
- **THEN** system submits the backtest and shows real-time progress via WebSocket

### Requirement: Live trading page
The system SHALL display real-time positions, open orders, risk metrics, and Kill Switch controls.

#### Scenario: Live data streaming
- **WHEN** user is on the live trading page and a live node is running
- **THEN** page displays real-time positions, open orders, and risk metrics updated via WebSocket

#### Scenario: Kill Switch UI
- **WHEN** user views the live trading page
- **THEN** page displays three Kill Switch buttons: Pause (green, instant click), Flatten (orange, confirmation dialog), Kill (red, 3-second long press with progress indicator)

### Requirement: Environment switching
The system SHALL allow users to switch between sandbox and live views in the web panel.

#### Scenario: Switch environment
- **WHEN** user toggles between sandbox and live in the UI
- **THEN** all data displays, WebSocket subscriptions, and controls switch to the selected environment

### Requirement: Analytics page
The system SHALL display advanced analytics including monthly returns heatmap, drawdown chart, returns distribution, and rolling Sharpe.

#### Scenario: Analytics loads
- **WHEN** user navigates to analytics
- **THEN** page displays monthly returns heatmap, drawdown over time chart, returns distribution histogram, and rolling Sharpe ratio chart

### Requirement: Portfolio page
The system SHALL display portfolio allocation and venue exposure.

#### Scenario: Portfolio loads
- **WHEN** user navigates to portfolio
- **THEN** page displays position allocation breakdown chart and venue exposure table

### Requirement: Settings page
The system SHALL display platform settings including API keys, risk limits, notifications, and system info.

#### Scenario: View and edit settings
- **WHEN** user navigates to settings
- **THEN** page displays Binance API key configuration, risk limit inputs, notification toggles, and NautilusTrader/Python/Redis version info

### Requirement: Additional pages
The system SHALL provide order history, watchlist, data catalog, and strategy editor pages matching webui.pen design.

#### Scenario: Order history
- **WHEN** user navigates to order history
- **THEN** page displays filterable, paginated order table

#### Scenario: Watchlist
- **WHEN** user navigates to watchlist
- **THEN** page displays real-time price cards for watched instruments

#### Scenario: Data catalog
- **WHEN** user navigates to data catalog
- **THEN** page displays available data sets with stats and a data table

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
