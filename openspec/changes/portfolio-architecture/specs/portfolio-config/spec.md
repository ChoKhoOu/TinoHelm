## ADDED Requirements

### Requirement: Portfolio YAML schema
The system SHALL support a `portfolio.yaml` file inside a strategy folder (`~/.tino/strategies/<name>/portfolio.yaml`) that declares: strategy class/config class references, symbol list, interval, strategy params, optional actor references with params, and account settings (starting_balance, currency, leverage).

#### Scenario: Valid portfolio.yaml is parsed
- **WHEN** a folder `~/.tino/strategies/crypto_momentum/` contains a valid `portfolio.yaml` with strategy class `strategy:BTCMultiFactor`, symbols `[BTCUSDT-PERP, ETHUSDT-PERP]`, and account starting_balance `10000`
- **THEN** the system SHALL parse it into a `PortfolioConfig` object with 2 symbols, the correct strategy class reference, and starting_balance of 10000

#### Scenario: Missing required fields
- **WHEN** a `portfolio.yaml` is missing the `strategy.class` field
- **THEN** the system SHALL raise a validation error with a message identifying the missing field

#### Scenario: Portfolio.yaml with no actors section
- **WHEN** a `portfolio.yaml` has no `actors` key or `actors: []`
- **THEN** the system SHALL produce a PortfolioConfig with an empty actors list and proceed without error

### Requirement: Implicit portfolio for single .py files
The system SHALL auto-wrap a single `.py` strategy file into an implicit PortfolioConfig when the user runs `tino backtest run <single_file>` with `--symbol` and `--interval` CLI flags.

#### Scenario: Single file backward compatibility
- **WHEN** the user runs `tino backtest run simple_ma --symbol BTCUSDT-PERP --interval 5m --start 2025-01-01 --end 2025-03-01`
- **THEN** the system SHALL create an implicit PortfolioConfig with 1 symbol, 1 strategy instance, 0 actors, and the CLI-provided parameters — behaving identically to the current single-strategy backtest

#### Scenario: Portfolio folder auto-detection
- **WHEN** the user runs `tino backtest run crypto_momentum --start 2025-01-01 --end 2025-03-01` and `~/.tino/strategies/crypto_momentum/portfolio.yaml` exists
- **THEN** the system SHALL load the portfolio.yaml instead of looking for a `.py` file

### Requirement: Scanner detects portfolio folders
The strategy scanner SHALL detect both single `.py` files and folders containing `portfolio.yaml`, registering them in the strategies table with a `type` field (`single` or `portfolio`).

#### Scenario: Mixed directory scanning
- **WHEN** `~/.tino/strategies/` contains `simple_ma.py` and `crypto_momentum/portfolio.yaml`
- **THEN** the scanner SHALL register `simple_ma` with type `single` and `crypto_momentum` with type `portfolio`

#### Scenario: Scanner extracts msgspec config fields
- **WHEN** a strategy config class uses msgspec `__struct_fields__` instead of Pydantic `model_fields`
- **THEN** the scanner SHALL correctly extract the parameter names and defaults using `__struct_fields__` and `__struct_defaults__`

### Requirement: SYMBOL_PROFILES validation on portfolio load
The system SHALL warn when a symbol in portfolio.yaml has no matching entry in the strategy's SYMBOL_PROFILES (or the profile has `enabled: False`).

#### Scenario: Unrecognized symbol warning
- **WHEN** portfolio.yaml lists `DOGEUSDT-PERP` but the strategy's SYMBOL_PROFILES has no entry for `DOGE-USDT`
- **THEN** the system SHALL log a warning that this symbol will use DEFAULT_PROFILE and may not generate trading signals

### Requirement: Strategies table is ephemeral
The strategies DB table SHALL be treated as a cache rebuilt on rescan. The `backtest_runs` table SHALL reference strategies by `strategy_name` (string), not by foreign key.

#### Scenario: Rescan rebuilds table
- **WHEN** the user runs `tino strategy rescan`
- **THEN** the system SHALL clear and repopulate the strategies table from the file system, without affecting existing backtest_runs records

#### Scenario: Backtest results survive rescan
- **WHEN** the strategies table is rebuilt via rescan
- **THEN** all existing `backtest_runs` records SHALL remain intact and queryable by `strategy_name`
