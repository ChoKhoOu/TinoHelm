# Strategy Management

## Purpose

Handles strategy file discovery, automatic version tracking via code hash, structural validation, and scaffold generation for the TinoHelm trading platform.

## Requirements

### Requirement: File-based strategy discovery
The system SHALL discover strategy files by scanning the `strategies/` directory for Python files containing classes that inherit from NT's `Strategy` base class.

#### Scenario: Discover strategies on startup
- **WHEN** the API server starts
- **THEN** system scans `strategies/*.py`, imports each module, uses `inspect` to find `Strategy` subclasses and their paired `StrategyConfig` subclasses, and registers them in the database

#### Scenario: New strategy file added
- **WHEN** a new `.py` file appears in `strategies/` (detected on next scan or API call)
- **THEN** system imports the module, validates it contains Strategy/StrategyConfig classes, and adds metadata to the database

### Requirement: Automatic version tracking via code hash
The system SHALL track strategy versions by computing a SHA-256 hash of the strategy file content, creating a new version record when the hash changes.

#### Scenario: Strategy file modified
- **WHEN** a strategy file's content hash differs from the latest recorded version
- **THEN** system creates a new `strategy_versions` record with incremented version number and the new hash

#### Scenario: Backtest linked to version
- **WHEN** a backtest is submitted for a strategy
- **THEN** the backtest_runs record references the current strategy version, enabling traceability of which code produced which results

### Requirement: Strategy validation
The system SHALL validate strategy files for structural correctness without executing trading logic.

#### Scenario: Valid strategy
- **WHEN** user runs `tino strategy validate <name>` or calls validation API
- **THEN** system checks: module imports successfully, contains exactly one StrategyConfig subclass, contains exactly one Strategy subclass, Config has `instrument_id` field, and returns validation result with config parameter schema

#### Scenario: Invalid strategy
- **WHEN** validation fails (import error, missing classes, etc.)
- **THEN** system returns `valid: false` with specific error messages describing what is wrong

### Requirement: Strategy scaffold generation
The system SHALL generate a minimal strategy skeleton file from a built-in scaffold template when creating new strategies via CLI.

#### Scenario: Create bar-based strategy scaffold
- **WHEN** user runs `tino strategy create <name>` or `tino strategy create <name> --type bar`
- **THEN** system creates `strategies/<name>.py` containing a StrategyConfig subclass with common fields (instrument_id, bar_type, trade_size) and a Strategy subclass with all lifecycle hooks listed with type-annotated signatures and docstring comments explaining each hook's purpose and commonly used APIs

#### Scenario: Create tick-based strategy scaffold
- **WHEN** user runs `tino strategy create <name> --type tick`
- **THEN** system creates a scaffold with `on_quote_tick`/`on_trade_tick` as primary handlers and tick subscription in `on_start`

#### Scenario: List available strategies
- **WHEN** user runs `tino strategy list` or `GET /api/strategies`
- **THEN** system returns list of discovered strategies with name, file path, current version, config parameters, and implemented hooks
