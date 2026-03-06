## ADDED Requirements

### Requirement: Binance historical data fetching
The system SHALL fetch historical bar data from Binance API with rate limit awareness and incremental download support.

#### Scenario: Fetch new data range
- **WHEN** user requests data fetch for a symbol/interval/date range that is not cached locally
- **THEN** system downloads bars from Binance REST API, respecting rate limits, and stores as Parquet files in NT's ParquetDataCatalog format under `data/catalog/`

#### Scenario: Incremental fetch for partial range
- **WHEN** user requests a date range where part of the data already exists locally
- **THEN** system queries the data_catalog table to identify missing segments and only fetches the gaps from Binance

#### Scenario: Rate limit handling
- **WHEN** Binance API rate limit is approached during data fetching
- **THEN** system backs off and retries, logging the rate limit event

### Requirement: ParquetDataCatalog integration
The system SHALL store historical data in NT's native ParquetDataCatalog format for zero-conversion-overhead backtest loading.

#### Scenario: Data stored in catalog format
- **WHEN** historical data is fetched and stored
- **THEN** data is written as Parquet files organized by `data/catalog/{data_type}/{instrument_id}/{year-month}.parquet`

#### Scenario: Backtest loads from catalog
- **WHEN** a backtest is configured with a data range
- **THEN** BacktestDataConfig points directly to the catalog path and NT loads Parquet files natively

### Requirement: Data catalog management
The system SHALL maintain a database table tracking available data ranges per symbol/interval with API and CLI access.

#### Scenario: List available data
- **WHEN** user requests `GET /api/data/catalog` or `tino data catalog`
- **THEN** system returns list of available symbols, data types, intervals, and date ranges from the data_catalog table

#### Scenario: Fetch data via CLI
- **WHEN** user runs `tino data fetch BTCUSDT-PERP 1h 2024-01-01 2024-12-31`
- **THEN** CLI sends request to API, which triggers background data fetch and reports progress
