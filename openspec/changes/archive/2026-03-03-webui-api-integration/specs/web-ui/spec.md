## MODIFIED Requirements

### Requirement: Dashboard page
The system SHALL display a dashboard overview with equity curve, key metrics, and active strategy list fetched from the backend API.

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
The system SHALL display backtest configuration with strategy and venue lists fetched from the backend API.

#### Scenario: Strategy list from API
- **WHEN** user opens the backtest page
- **THEN** strategy select dropdown is populated from `GET /api/strategies` response

#### Scenario: Submit new backtest
- **WHEN** user fills in backtest configuration form and clicks "Run Backtest"
- **THEN** system submits the backtest and shows real-time progress via polling

### Requirement: Live trading page
The system SHALL display real-time positions, open orders, and risk metrics all fetched from backend API and WebSocket.

#### Scenario: Risk metrics from API
- **WHEN** user is on the live trading page
- **THEN** risk metrics panel displays data from `GET /api/node/status` risk_metrics field

#### Scenario: Live data streaming
- **WHEN** user is on the live trading page and a node is running
- **THEN** page displays real-time positions, open orders updated via WebSocket

### Requirement: Analytics page
The system SHALL display analytics charts with data fetched from backend analytics API endpoints.

#### Scenario: Analytics loads with API data
- **WHEN** user navigates to analytics
- **THEN** page fetches all four analytics endpoints and renders charts with real data

#### Scenario: Analytics with no data
- **WHEN** analytics API returns empty data arrays
- **THEN** page displays empty state messages in each chart area

### Requirement: Portfolio page
The system SHALL display portfolio allocation and venue exposure fetched from `GET /api/portfolio/allocation`.

#### Scenario: Portfolio loads with API data
- **WHEN** user navigates to portfolio
- **THEN** page fetches allocation data and renders position breakdown and venue exposure

#### Scenario: Portfolio empty state
- **WHEN** no positions exist
- **THEN** page displays empty state message

### Requirement: Settings page
The system SHALL display and allow editing of platform settings fetched from the backend API.

#### Scenario: Settings loads with API data
- **WHEN** user navigates to settings
- **THEN** page fetches `GET /api/health` for system info and `GET /api/settings` for risk limits; displays real values

#### Scenario: Update risk limits
- **WHEN** user modifies a risk limit value
- **THEN** page sends `PUT /api/settings/risk-limits` and shows success/error feedback

### Requirement: Additional pages
The system SHALL provide order history, watchlist, data catalog, and strategy list pages fetched from backend APIs.

#### Scenario: Order history from API
- **WHEN** user navigates to order history
- **THEN** page fetches `GET /api/orders` with filter parameters and displays real order data

#### Scenario: Watchlist from API
- **WHEN** user navigates to watchlist
- **THEN** page fetches `GET /api/watchlist` and displays saved instruments

#### Scenario: Add instrument to watchlist
- **WHEN** user clicks "Add Instrument" and submits the form
- **THEN** page sends `POST /api/watchlist` and refreshes the list

#### Scenario: Data catalog from API
- **WHEN** user navigates to data catalog
- **THEN** page fetches `GET /api/data/catalog` and displays real dataset information

#### Scenario: Strategies list from API
- **WHEN** user navigates to strategies page
- **THEN** page fetches `GET /api/strategies` and displays discovered strategies with their metadata
