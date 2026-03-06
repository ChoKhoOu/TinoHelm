## ADDED Requirements

### Requirement: RiskGuardActor is optional
The RiskGuardActor SHALL be fully optional. When no RiskGuardActor is configured in portfolio.yaml, strategies SHALL run without any cross-strategy risk checks. The system SHALL not require Actor presence for correct operation.

#### Scenario: No actor configured
- **WHEN** a portfolio.yaml has `actors: []` or no actors section
- **THEN** strategies SHALL execute normally without any risk guard intervention

#### Scenario: Strategy works without actor subscription
- **WHEN** a strategy subscribes to `risk.guard.state` msgbus topic but no RiskGuardActor exists
- **THEN** the subscription handler SHALL never fire and `_risk_halted` SHALL remain False

### Requirement: Daily PnL monitoring
The RiskGuardActor SHALL track daily PnL using UTC 00:00 as the trading day boundary, detected via `bar.ts_event` timestamps. When the daily return (from day start equity) breaches `daily_stop_loss_pct`, the actor SHALL trigger the configured breach action.

#### Scenario: Daily loss limit breached
- **WHEN** daily return drops to -2.5% and `daily_stop_loss_pct` is configured as -0.02
- **THEN** the actor SHALL publish the configured `breach_action` to msgbus topic `risk.guard.state`

#### Scenario: Day boundary resets PnL tracking
- **WHEN** bar timestamps cross from 2025-02-14 23:59 UTC to 2025-02-15 00:00 UTC
- **THEN** the actor SHALL reset `_day_start_equity` to the current equity value

### Requirement: Max drawdown monitoring
The RiskGuardActor SHALL track portfolio drawdown using a high-water mark (HWM). In backtesting, HWM starts at starting_balance and is tracked per-run. When drawdown breaches `max_drawdown_pct`, the actor SHALL trigger the configured breach action.

#### Scenario: Drawdown limit breached
- **WHEN** equity drops from peak 12000 to 10800 (drawdown -10%) and `max_drawdown_pct` is -0.09
- **THEN** the actor SHALL publish the configured `breach_action` to msgbus topic `risk.guard.state`

#### Scenario: HWM updates on new equity peak
- **WHEN** equity rises from 10000 to 11000
- **THEN** the actor SHALL update `_peak_equity` to 11000

### Requirement: Total exposure monitoring
The RiskGuardActor SHALL monitor total portfolio exposure by iterating open positions and summing per-instrument `net_exposure`. When total exposure exceeds `max_total_exposure`, the actor SHALL trigger the configured breach action.

#### Scenario: Exposure limit breached
- **WHEN** BTC position exposure is 60000 USDT and ETH position exposure is 50000 USDT (total 110000) and `max_total_exposure` is 100000
- **THEN** the actor SHALL publish the configured `breach_action` to msgbus topic `risk.guard.state`

### Requirement: Position count monitoring
The RiskGuardActor SHALL count all open positions across all instruments. When the count reaches `max_positions`, the actor SHALL trigger the configured breach action.

#### Scenario: Position count limit reached
- **WHEN** there are 10 open positions across BTC, ETH, XRP and `max_positions` is 10
- **THEN** the actor SHALL publish the configured `breach_action` to msgbus topic `risk.guard.state`

### Requirement: Configurable breach action
The RiskGuardActor SHALL support three breach actions configured via `breach_action` parameter: `reduce_only` (default — blocks new entries), `halt_new` (blocks new entries, existing positions exit via their stops), `flatten_all` (publishes flatten command for all open positions).

#### Scenario: reduce_only breach action
- **WHEN** a risk limit is breached and `breach_action` is `reduce_only`
- **THEN** the actor SHALL publish `"reduce_only"` to msgbus topic `risk.guard.state`

#### Scenario: flatten_all breach action
- **WHEN** a risk limit is breached and `breach_action` is `flatten_all`
- **THEN** the actor SHALL publish `"flatten_all"` to msgbus topic `risk.guard.state` AND publish each open position's instrument_id to msgbus topic `risk.guard.flatten`

### Requirement: Communication via NT msgbus
The RiskGuardActor SHALL communicate with strategies exclusively through NT's msgbus publish/subscribe mechanism. The actor SHALL NOT call `set_trading_state()` (which belongs to RiskEngine and is not accessible from Actor).

#### Scenario: Strategy receives risk state via msgbus
- **WHEN** the actor publishes `"reduce_only"` to topic `risk.guard.state`
- **THEN** any strategy subscribed to that topic SHALL receive the message in its handler callback

### Requirement: Equity calculation includes unrealized PnL
The RiskGuardActor SHALL compute equity using `portfolio.account(venue).balance_total(currency)` which includes unrealized PnL, providing accurate real-time equity for drawdown monitoring.

#### Scenario: Floating loss reflected in equity
- **WHEN** account balance is 10000 USDT and there is -500 USDT unrealized PnL
- **THEN** the actor SHALL compute equity as 9500 USDT for risk checks
