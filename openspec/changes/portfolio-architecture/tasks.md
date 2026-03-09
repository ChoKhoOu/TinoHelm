## 1. Shared Utilities (Foundation)

- [x] 1.1 Create `src/tinohelm/strategy/utils.py` with `get_config_fields(cls)` that extracts fields from both Pydantic `model_fields` and msgspec `__struct_fields__`
- [x] 1.2 Fix Scanner (`registry.py`) to use `get_config_fields()` instead of only checking `model_fields`
- [x] 1.3 Fix Runner (`runner.py`) to use `get_config_fields()` instead of its inline field detection logic

## 2. Portfolio Config Schema

- [x] 2.1 Create `src/tinohelm/portfolio/config.py` with `PortfolioConfig` dataclass (strategy class/config refs, symbols, interval, params, actors list, account settings)
- [x] 2.2 Implement `load_portfolio_config(name_or_path)` — reads `portfolio.yaml` from folder or auto-wraps single `.py` file into implicit PortfolioConfig
- [x] 2.3 Add portfolio.yaml validation: required fields check, symbol format check, SYMBOL_PROFILES warning for unrecognized symbols
- [x] 2.4 Write unit tests for PortfolioConfig parsing (valid yaml, missing fields, no actors, implicit wrapping)

## 3. Portfolio Loader

- [x] 3.1 Create `src/tinohelm/portfolio/loader.py` with `create_strategies(config) -> list[Strategy]` — imports strategy class, creates one instance per symbol with injected `instrument_id`/`bar_type`
- [x] 3.2 Implement `create_actors(config) -> list[Actor]` — loads from `~/.tino/actors/` (by name) or portfolio folder (by class path), applies params
- [x] 3.3 Create `~/.tino/actors/` directory convention support — scan for Actor/ActorConfig subclasses in `.py` files
- [x] 3.4 Write unit tests for loader (multi-symbol instantiation, actor loading, empty actors, msgspec configs)

## 4. RiskGuardActor

- [x] 4.1 Create `~/.tino/actors/risk_guard.py` (or a bundled default at `src/tinohelm/actors/risk_guard.py`) with `RiskGuardConfig` and `RiskGuardActor`
- [x] 4.2 Implement daily PnL monitoring with UTC 00:00 day boundary via `bar.ts_event`
- [x] 4.3 Implement max drawdown monitoring with per-run HWM
- [x] 4.4 Implement total exposure monitoring (iterate positions, sum per-instrument `net_exposure`)
- [x] 4.5 Implement position count monitoring
- [x] 4.6 Implement configurable `breach_action` (reduce_only / halt_new / flatten_all) with msgbus publish to `risk.guard.state`
- [x] 4.7 Implement `flatten_all` action — publish instrument IDs to `risk.guard.flatten` topic
- [x] 4.8 Write unit tests for RiskGuardActor (daily reset, drawdown breach, exposure check, position count, breach actions)

## 5. Runner Refactor

- [x] 5.1 Refactor `BacktestRunner.__init__` to accept a PortfolioConfig (or auto-wrap from existing params)
- [x] 5.2 Replace single `engine.add_strategy()` call with loop: one strategy instance per symbol via portfolio_loader
- [x] 5.3 Add `engine.add_actor()` call for each actor from portfolio_loader
- [x] 5.4 Ensure data loading handles all symbols × intervals combinations (already partially supported)
- [x] 5.5 Verify `extract_backtest_results()` works correctly with multi-strategy engine (per-instrument breakdown should auto-populate)
- [x] 5.6 Write integration test: multi-symbol portfolio backtest end-to-end (BTC + ETH, verify separate positions, combined equity curve)

## 6. Scanner Enhancement

- [x] 6.1 Update `scan_strategies()` to detect folders with `portfolio.yaml` alongside single `.py` files
- [x] 6.2 Add `type` field to scanner output: `"single"` or `"portfolio"`
- [x] 6.3 Extract metadata from portfolio.yaml (symbols, actors) for display in `tino strategy list`
- [x] 6.4 Write tests for mixed directory scanning (single files + portfolio folders)

## 7. Database Migration

- [x] 7.1 Create Alembic migration: add `strategy_name` string column to `backtest_runs`, backfill from joined strategy name
- [x] 7.2 Create Alembic migration: drop FK constraint from `backtest_runs.strategy_id` to `strategies.id`
- [x] 7.3 Add `type` column to `strategies` table (`single` / `portfolio`)
- [x] 7.4 Update `persist_strategies()` to support full table rebuild on rescan
- [x] 7.5 Update backtest result storage to use `strategy_name` instead of `strategy_id` FK

## 8. Node Integration (Sandbox / Live)

- [x] 8.1 Refactor `sandbox.py` to use portfolio_loader for strategy/actor creation instead of `ImportableStrategyConfig`
- [x] 8.2 Wire BridgeActor into sandbox.py via `node.trader.add_actor()`, remove inline command listener and heartbeat threads
- [x] 8.3 Refactor `live.py` same as sandbox — portfolio_loader + BridgeActor
- [x] 8.4 Update `node/factory.py` to accept portfolio config for node startup
- [x] 8.5 Write integration test: sandbox node starts with portfolio config and BridgeActor

## 9. CLI Updates

- [x] 9.1 Update `tino backtest run` to detect portfolio folder vs single file and route accordingly
- [x] 9.2 Update `tino strategy list` to show `type` column (single/portfolio) and symbol count for portfolios
- [x] 9.3 Update `tino strategy info` to show portfolio details (symbols, actors) when type is portfolio
- [x] 9.4 Verify `tino backtest run single_file --symbol X --interval Y` backward compatibility (zero behavior change)

## 10. Strategy Migration (btc_multi_factor)

- [x] 10.1 Create `~/.tino/strategies/crypto_momentum/` folder structure
- [x] 10.2 Create `portfolio.yaml` with BTC/ETH/XRP symbols, strategy params, and risk_guard actor reference
- [x] 10.3 Copy strategy code to `strategy.py`, extract factors to `factors.py`
- [x] 10.4 Add optional msgbus subscription for `risk.guard.state` in strategy's `on_start()`
- [x] 10.5 Verify old `btc_multi_factor.py` still works as single-file strategy (backward compat)
- [x] 10.6 Run end-to-end portfolio backtest: `tino backtest run crypto_momentum --start 2025-01-01 --end 2025-03-01`
