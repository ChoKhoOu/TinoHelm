## ADDED Requirements

### Requirement: CLI backtest commands
The system SHALL provide one-shot CLI commands for backtest management: `run`, `list`, `result`, `status`, `cancel`. Output SHALL match the current Python CLI format (table for `list`, detail view for `result`). All commands SHALL support `--json` flag for machine-readable output.

#### Scenario: List backtests
- **WHEN** user runs `tino backtest list`
- **THEN** system prints a formatted table with columns: ID (8-char prefix), Strategy, Symbol, Interval, Period, Status, Trades, PnL, Ret%, Sharpe, WinRate

#### Scenario: List backtests as JSON
- **WHEN** user runs `tino backtest list --json`
- **THEN** system prints the raw JSON array from the API to stdout

#### Scenario: Run a backtest
- **WHEN** user runs `tino backtest run btc_multi_factor --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01`
- **THEN** system submits the backtest via POST API, prints the run_id, and exits

#### Scenario: Get result with short ID prefix
- **WHEN** user runs `tino backtest result ac5ef`
- **THEN** system sends the short prefix to the API which resolves it, and displays the full result

#### Scenario: Cancel a running backtest
- **WHEN** user runs `tino backtest cancel ac5ef`
- **THEN** system sends cancel request to the API and confirms cancellation

### Requirement: CLI strategy commands
The system SHALL provide commands: `list`, `info`, `validate`, `rescan`. Output format SHALL match the current Python CLI.

#### Scenario: List strategies
- **WHEN** user runs `tino strategy list`
- **THEN** system prints a table with columns: Name, Type, Class, Symbols, Updated

#### Scenario: Rescan strategies
- **WHEN** user runs `tino strategy rescan`
- **THEN** system triggers a rescan via POST API and prints discovery count

### Requirement: CLI data commands
The system SHALL provide commands: `fetch`, `list`, `info`. These wrap the corresponding FastAPI data endpoints.

#### Scenario: Fetch historical data
- **WHEN** user runs `tino data fetch BTCUSDT-PERP 1m 2024-01-01 2025-01-01`
- **THEN** system triggers data fetch via POST API and prints progress/completion status

#### Scenario: List available data
- **WHEN** user runs `tino data list`
- **THEN** system prints available instruments and their data ranges

### Requirement: CLI node commands
The system SHALL provide commands: `start`, `stop`, `status` for sandbox and live nodes.

#### Scenario: Check node status
- **WHEN** user runs `tino node status`
- **THEN** system queries node heartbeat from the API and displays running/stopped state

### Requirement: CLI version and help
The system SHALL display version info via `tino version` and help text via `tino --help`.

#### Scenario: Show version
- **WHEN** user runs `tino version`
- **THEN** system prints the version string and exits

### Requirement: CLI API connection error handling
The system SHALL print a human-readable error and exit with code 1 when the API server is unreachable.

#### Scenario: API unreachable
- **WHEN** user runs any CLI command and the API is not running
- **THEN** system prints "Connection Error: Cannot connect to API at <url>" and a hint to start Docker

### Requirement: CLI configuration
The system SHALL read API URL from (in priority order): `--api-url` flag, `TINO_API_URL` env var, `~/.tino/config/user.yaml`, default `http://localhost:8000`.

#### Scenario: Custom API URL via flag
- **WHEN** user runs `tino --api-url http://remote:8000 backtest list`
- **THEN** system sends requests to `http://remote:8000`

#### Scenario: API URL from environment
- **WHEN** env var `TINO_API_URL=http://remote:8000` is set and no `--api-url` flag is given
- **THEN** system sends requests to `http://remote:8000`
