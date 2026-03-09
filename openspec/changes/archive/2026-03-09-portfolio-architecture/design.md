## Context

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader (NT). The backtest system currently creates one strategy instance per run, loads strategies via three different code paths (Runner uses `importlib.util`, Scanner uses `importlib.import_module`, Node uses `ImportableStrategyConfig`), and has no Actor support in backtesting. Sandbox/live nodes duplicate Redis command/heartbeat logic in inline threads instead of using the existing BridgeActor. The `strategies` DB table uses foreign keys from `backtest_runs`, making schema changes fragile.

Full architectural design is documented in `docs/design/portfolio-risk-architecture.md`. This design.md captures key decisions and rationale.

## Goals / Non-Goals

**Goals:**
- Unify all strategy/actor loading into a single `portfolio_loader` module shared by Runner, Sandbox, and Live nodes
- Support portfolio-level backtesting: N strategy instances (one per symbol) + optional Actors, all sharing one NT engine/account/portfolio
- Make RiskGuardActor fully optional — strategies work without it
- Support both single `.py` files (backward compat) and folder-based `portfolio.yaml` configurations
- Use NT-native patterns throughout (msgbus for Actor-Strategy communication, `engine.add_actor()` for registration)
- Make `strategies` table ephemeral (rebuilt on rescan), file system as source of truth

**Non-Goals:**
- Multi-strategy portfolios (different strategy classes for different symbols) — future work
- Cross-symbol signal Actors (correlation, BTC dominance) — not needed until strategies require cross-symbol factor dependencies
- Live HWM persistence to DB — backtest HWM is per-run; live persistence is future work
- Changing the strategy factor logic or risk gates (`disable_risk_gates` stays in strategy)

## Decisions

### 1. Everything is a Portfolio

Every backtest/live run is modeled as a Portfolio, even single-symbol runs. A single `.py` file is auto-wrapped as an implicit portfolio (1 strategy instance, 1 symbol, 0 actors). This eliminates conditional branching in Runner/Node code.

**Alternative considered**: Keep single-strategy and portfolio as separate code paths. Rejected because it doubles maintenance and creates divergence over time.

### 2. Shared `portfolio_loader` module

A single module handles: reading portfolio.yaml, importing strategy/actor classes, instantiating per-symbol strategy instances, and loading actors from `~/.tino/actors/`. Both Runner and Node call into this module.

**Alternative considered**: Keep Runner and Node loading separate with shared utility functions. Rejected — violates single-implementation principle, which is already causing bugs (Scanner missing msgspec support).

### 3. RiskGuardActor uses msgbus, not `set_trading_state()`

NT's `set_trading_state()` belongs to RiskEngine and is not accessible from Actor. RiskGuardActor publishes risk state to `msgbus` topic `risk.guard.state`. Strategies optionally subscribe and check before opening positions.

**Alternative considered**: Have Actor write to NT cache and strategies read from cache. Rejected — msgbus is the NT-native pub/sub mechanism; cache is for state storage, not event signaling.

### 4. Actor is fully optional

When no Actor is configured, strategies run exactly as they do today. The strategy's msgbus subscription uses a safe pattern — if no Actor publishes, the handler is never called and `_risk_halted` stays False.

**Alternative considered**: Require RiskGuardActor for all portfolio backtests. Rejected — adds unnecessary friction for simple single-symbol testing.

### 5. Three-tier configurable breach action

When a risk limit is breached, the action is configurable: `reduce_only` (default, blocks new entries), `halt_new` (same + lets existing positions exit naturally), `flatten_all` (closes everything). This mirrors NT RiskEngine's ACTIVE/REDUCING/HALTED states.

**Alternative considered**: Hard-code HALTED behavior. Rejected — different strategies need different risk responses; configurability is industry standard (QuantConnect, institutional platforms).

### 6. Daily boundary at UTC 00:00

RiskGuardActor detects trading day changes by comparing `bar.ts_event` UTC dates. This aligns with Binance and all major crypto exchange daily candle boundaries.

**Alternative considered**: Use `self.clock.set_timer(24h)`. Rejected — timer starts from engine start time, may not align with UTC midnight.

### 7. Equity includes unrealized PnL

RiskGuardActor uses `portfolio.account(venue).balance_total(currency)` which includes unrealized P&L. This gives accurate real-time equity for drawdown calculation.

**Alternative considered**: Use only realized balance. Rejected — misses floating losses, which is the whole point of drawdown monitoring.

### 8. Strategies table becomes ephemeral

The `strategies` DB table is rebuilt on every `rescan`. `backtest_runs` references strategies by `strategy_name` (string) instead of FK. The file system (`~/.tino/strategies/`) is the source of truth.

**Alternative considered**: Keep FK relationship. Rejected — makes rescan fragile (can't rebuild without orphaning backtest records).

### 9. Wire BridgeActor into sandbox/live nodes

Replace the inline thread-based Redis command listener and heartbeat in `sandbox.py`/`live.py` with the already-implemented `BridgeActor`. This eliminates ~100 lines of duplicated code per node.

**Alternative considered**: Keep inline threads. Rejected — violates single-implementation principle; BridgeActor already exists and does exactly this job.

## Risks / Trade-offs

- **[Breaking: strategies FK removal]** Existing `backtest_runs` rows with strategy_id FK will need migration. → Mitigation: Alembic migration that adds `strategy_name` column, backfills from joined strategy name, then drops FK.
- **[Complexity: portfolio_loader]** A single loader for 3 consumers (Runner/Sandbox/Live) means changes affect all paths. → Mitigation: Strong test coverage of the loader; each consumer has its own integration test.
- **[NT API stability]** Actor msgbus API may change across NT versions. → Mitigation: Wrap msgbus calls in a thin abstraction; pin NT version.
- **[Backward compat: single .py files]** Auto-wrapping as implicit portfolio adds a layer. → Mitigation: Extensive testing of the `tino backtest run single_file.py` path to ensure zero behavioral change.
- **[BridgeActor thread safety]** NT is single-threaded internally; BridgeActor's Redis operations must not block the event loop. → Mitigation: BridgeActor already handles this with background threads for Redis I/O (existing implementation).
