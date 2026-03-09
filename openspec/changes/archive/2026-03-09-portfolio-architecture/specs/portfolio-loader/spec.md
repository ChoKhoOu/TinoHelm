## ADDED Requirements

### Requirement: Shared portfolio loader module
The system SHALL provide a single `portfolio_loader` module that creates strategy instances and actor instances from a PortfolioConfig. This module SHALL be used by BacktestRunner, Sandbox node, and Live node — no other strategy/actor loading code paths SHALL exist.

#### Scenario: Loader creates per-symbol strategy instances
- **WHEN** a PortfolioConfig specifies strategy class `BTCMultiFactor` with symbols `[BTCUSDT-PERP, ETHUSDT-PERP, XRPUSDT-PERP]`
- **THEN** the loader SHALL create 3 independent strategy instances, each with its own `instrument_id` and `bar_type` injected into the config

#### Scenario: Loader creates actors from global actors directory
- **WHEN** a PortfolioConfig references actor `name: risk_guard` with params `{max_positions: 10}`
- **THEN** the loader SHALL import the Actor class from `~/.tino/actors/risk_guard.py`, find the ActorConfig subclass, apply the params, and return an instantiated Actor

#### Scenario: Loader creates actors from local portfolio directory
- **WHEN** a PortfolioConfig references actor `class: ./custom_monitor:MyMonitor` with params
- **THEN** the loader SHALL import from the portfolio folder's `custom_monitor.py` file

#### Scenario: Loader returns empty list when no actors configured
- **WHEN** a PortfolioConfig has no actors
- **THEN** the loader SHALL return an empty actor list without error

### Requirement: Runner uses portfolio loader
The BacktestRunner SHALL use portfolio_loader to create all strategy and actor instances, adding them to the BacktestEngine via `engine.add_strategy()` and `engine.add_actor()`.

#### Scenario: Multi-symbol backtest
- **WHEN** a portfolio backtest is run with 3 symbols
- **THEN** the Runner SHALL call `engine.add_strategy()` 3 times (once per symbol instance) and `engine.add_actor()` for each configured actor, then run the engine

#### Scenario: Single-symbol backtest via implicit portfolio
- **WHEN** a single .py file backtest is run with 1 symbol
- **THEN** the Runner SHALL call `engine.add_strategy()` once and `engine.add_actor()` zero times, producing identical results to the current behavior

### Requirement: Node uses portfolio loader
Sandbox and Live nodes SHALL use portfolio_loader to create strategy and actor instances, replacing current inline loading logic.

#### Scenario: Sandbox node loads portfolio
- **WHEN** a sandbox node starts with strategy `crypto_momentum`
- **THEN** the node SHALL use portfolio_loader to create strategy instances and actors, adding them via `node.trader.add_strategy()` and `node.trader.add_actor()`

### Requirement: BridgeActor wired into nodes
Sandbox and Live nodes SHALL use the existing BridgeActor for Redis event bridging and command listening, replacing inline thread implementations.

#### Scenario: BridgeActor replaces inline threads
- **WHEN** a sandbox node starts
- **THEN** the node SHALL add a BridgeActor instance via `node.trader.add_actor()` and SHALL NOT create inline Redis command listener or heartbeat threads

### Requirement: Unified config field extraction
A shared utility function SHALL extract config fields from both Pydantic (`model_fields`) and msgspec (`__struct_fields__`) strategy configs. This function SHALL be used by both the scanner and the portfolio loader.

#### Scenario: msgspec config detection
- **WHEN** a strategy config class inherits from NT's StrategyConfig (msgspec Struct)
- **THEN** the utility SHALL return field names from `__struct_fields__` and defaults from `__struct_defaults__`

#### Scenario: Pydantic config detection
- **WHEN** a strategy config class uses Pydantic `model_fields`
- **THEN** the utility SHALL return field names and defaults from `model_fields`
