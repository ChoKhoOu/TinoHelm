# CLI Interface

## Purpose

Provides the `tino` command-line interface that communicates with the FastAPI server over HTTP, supporting strategy management, data operations, backtest lifecycle, and node control commands.

## Requirements

### Requirement: CLI communicates via API server
The system SHALL provide a `tino` CLI that communicates with the FastAPI server over HTTP, supporting both human-readable and JSON output formats.

#### Scenario: Default human-readable output
- **WHEN** user runs any `tino` command without `--format` flag
- **THEN** CLI outputs formatted, human-readable text to stdout

#### Scenario: JSON output for AI consumption
- **WHEN** user runs any `tino` command with `--format json`
- **THEN** CLI outputs structured JSON to stdout suitable for machine parsing

#### Scenario: API server unreachable
- **WHEN** CLI cannot connect to the API server
- **THEN** CLI prints a clear error message with the attempted URL and suggests checking if the server is running

### Requirement: Strategy management commands
The system SHALL provide CLI commands for strategy creation, listing, validation, and info.

#### Scenario: Create strategy scaffold
- **WHEN** user runs `tino strategy create <name> [--type bar|tick|book]`
- **THEN** CLI requests scaffold generation via API, creating `strategies/<name>.py` with appropriate skeleton

#### Scenario: List strategies
- **WHEN** user runs `tino strategy list`
- **THEN** CLI displays all discovered strategies with name, version, and status

#### Scenario: Validate strategy
- **WHEN** user runs `tino strategy validate <name>`
- **THEN** CLI returns validation result including validity, config parameters, and any warnings

#### Scenario: Strategy info
- **WHEN** user runs `tino strategy info <name>`
- **THEN** CLI returns strategy metadata including config schema, implemented hooks, and version history

### Requirement: Data management commands
The system SHALL provide CLI commands for data fetching and catalog browsing.

#### Scenario: Fetch historical data
- **WHEN** user runs `tino data fetch <symbol> <interval> <start> <end>`
- **THEN** CLI triggers data download via API and reports progress

#### Scenario: List data catalog
- **WHEN** user runs `tino data catalog`
- **THEN** CLI displays available data with symbols, intervals, and date ranges

### Requirement: Backtest commands
The system SHALL provide CLI commands for backtest submission, status checking, and result retrieval.

#### Scenario: Run backtest
- **WHEN** user runs `tino backtest run <strategy> --symbol <sym> --start <date> --end <date> [--param key=value]`
- **THEN** CLI submits backtest job via API and returns run_id

#### Scenario: Check backtest status
- **WHEN** user runs `tino backtest status <run_id>`
- **THEN** CLI returns current status and progress percentage

#### Scenario: Wait for backtest completion
- **WHEN** user runs `tino backtest wait <run_id>`
- **THEN** CLI polls status until terminal state, then outputs the result

#### Scenario: Get backtest result
- **WHEN** user runs `tino backtest result <run_id>`
- **THEN** CLI returns full result including statistics

#### Scenario: List backtest runs
- **WHEN** user runs `tino backtest list`
- **THEN** CLI returns paginated list of backtest runs

### Requirement: Node control commands
The system SHALL provide CLI commands for starting, stopping, and monitoring trading nodes.

#### Scenario: Start sandbox
- **WHEN** user runs `tino sandbox start --strategy <name>`
- **THEN** CLI starts sandbox node via API and confirms status

#### Scenario: Start live
- **WHEN** user runs `tino live start --strategy <name>`
- **THEN** CLI starts live node via API and confirms status

#### Scenario: Stop node
- **WHEN** user runs `tino sandbox stop` or `tino live stop`
- **THEN** CLI stops the node via API and confirms

#### Scenario: Kill switch
- **WHEN** user runs `tino live kill [--level 1|2|3]`
- **THEN** CLI triggers kill switch at specified level (default 3) via API

#### Scenario: Node status
- **WHEN** user runs `tino node status`
- **THEN** CLI displays status of all nodes including running state, strategies, and positions
