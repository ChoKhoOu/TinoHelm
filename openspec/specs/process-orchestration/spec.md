# Process Orchestration

## Purpose

Manages TradingNode subprocess lifecycle including starting, stopping, health monitoring via heartbeat, crash recovery with auto-restart, and a three-level Kill Switch for emergency live trading control.

## Requirements

### Requirement: Process Manager lifecycle control
The system SHALL provide a Process Manager component that starts, stops, and monitors TradingNode subprocesses (sandbox and live) and backtest worker subprocesses.

#### Scenario: Start sandbox node
- **WHEN** user requests sandbox node start via API or CLI
- **THEN** Process Manager spawns a new subprocess running TradingNode in sandbox mode with `trader_id="SANDBOX-001"` and returns success status

#### Scenario: Start live node
- **WHEN** user requests live node start via API or CLI
- **THEN** Process Manager spawns a new subprocess running TradingNode in live mode with `trader_id="LIVE-001"` and returns success status

#### Scenario: Simultaneous sandbox and live
- **WHEN** both sandbox and live nodes are requested to run
- **THEN** Process Manager runs them as separate subprocesses with isolated Redis DB (sandbox=DB0, live=DB1) and separate PubSub channel prefixes

#### Scenario: Graceful stop
- **WHEN** user requests node stop
- **THEN** Process Manager sends stop command, waits for graceful shutdown (up to `timeout_post_stop` seconds), then confirms stopped status

### Requirement: Health monitoring via heartbeat
The system SHALL monitor TradingNode subprocess health using Redis-based heartbeat with 5-second interval and 15-second TTL expiry.

#### Scenario: Healthy node
- **WHEN** TradingNode subprocess is running normally
- **THEN** BridgeActor publishes heartbeat to Redis key `tino:heartbeat:{node_type}` every 5 seconds with current trading state, strategy count, and position count

#### Scenario: Heartbeat timeout detected
- **WHEN** Process Manager detects heartbeat key has expired (no update for 15 seconds)
- **THEN** Process Manager checks subprocess status and logs a health alert

### Requirement: Crash recovery with auto-restart
The system SHALL detect subprocess crashes and attempt automatic restart up to 3 times.

#### Scenario: Subprocess crash with retries remaining
- **WHEN** a TradingNode subprocess exits unexpectedly AND restart attempts < 3
- **THEN** Process Manager logs the crash to audit log, waits 5 seconds, and restarts the subprocess with the same configuration

#### Scenario: Subprocess crash with retries exhausted
- **WHEN** a TradingNode subprocess exits unexpectedly AND restart attempts >= 3
- **THEN** Process Manager marks the node as errored, notifies connected WebSocket clients, and does NOT attempt further restarts

### Requirement: Three-level Kill Switch
The system SHALL provide three escalating levels of emergency stop for live trading.

#### Scenario: Level 1 PAUSE
- **WHEN** user triggers Level 1 Kill Switch for a specific strategy
- **THEN** system calls `Trader.stop_strategy(target)` and sets `RiskEngine.set_trading_state(REDUCING)`, preventing new orders while keeping existing positions

#### Scenario: Level 2 FLATTEN
- **WHEN** user triggers Level 2 Kill Switch
- **THEN** system calls `strategy.market_exit()` for all running strategies (which cancels all orders and closes all positions) and sets `RiskEngine.set_trading_state(HALTED)`

#### Scenario: Level 3 KILL
- **WHEN** user triggers Level 3 Kill Switch (requires 3-second long press confirmation in UI)
- **THEN** system calls `RiskEngine.shutdown_system()`, waits 2 seconds for cancel requests to reach exchange, sends SIGTERM to TradingNode subprocess, waits 5 seconds, then SIGKILL if still alive

#### Scenario: Post-KILL reconciliation
- **WHEN** TradingNode is restarted after a Level 3 KILL
- **THEN** TradingNode starts with `reconciliation=True` and `reconciliation_lookback_mins=1440` to detect and handle any orphan orders on the exchange
