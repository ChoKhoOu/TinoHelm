## Why

TinoHelm's backtest system treats each strategy as an isolated unit — one strategy instance, one symbol, no cross-strategy coordination. This prevents portfolio-level backtesting (multiple symbols sharing one account with coordinated risk), forces risk logic to live inside individual strategies (where it can't see the full picture), and creates duplicate code across Runner/Scanner/Node with inconsistent loading logic. The platform needs a "portfolio-first" architecture where everything — even a single-symbol strategy — is modeled as a portfolio.

## What Changes

- **Introduce portfolio.yaml as the declarative configuration for backtests and live nodes** — defines which strategy class to use, which symbols to trade, which Actors to load, and account settings
- **Refactor BacktestRunner to create one strategy instance per symbol** instead of one instance for all symbols, with optional Actor support via `engine.add_actor()`
- **Create a shared `portfolio_loader` module** that both Runner and Node (sandbox/live) use for strategy/actor instantiation — eliminating the current three different loading implementations
- **Implement RiskGuardActor** — an optional cross-strategy risk overlay using NT msgbus for communication (daily loss limits, max drawdown, position limits, exposure caps)
- **Make the `strategies` DB table ephemeral** (rebuilt on rescan) with file system as source of truth, supporting both single `.py` files and portfolio folders
- **Fix Scanner to support msgspec `__struct_fields__`** alongside Pydantic `model_fields` — currently only detects Pydantic configs
- **Wire BridgeActor into sandbox/live nodes** instead of the current inline thread-based Redis listeners — single implementation principle

## Capabilities

### New Capabilities
- `portfolio-config`: Portfolio.yaml schema, loading, validation, and implicit portfolio wrapping for single-file strategies
- `portfolio-loader`: Shared module for strategy/actor instantiation used by Runner, Sandbox, and Live nodes
- `risk-guard-actor`: Optional cross-strategy risk overlay Actor with configurable breach actions (reduce_only / halt_new / flatten_all)

### Modified Capabilities
<!-- No existing openspec specs to modify — this is greenfield -->

## Impact

- **Backtest Runner** (`src/tinohelm/backtest/runner.py`): Major refactor — multi-instance loop, actor support, portfolio_loader integration
- **Strategy Scanner** (`src/tinohelm/strategy/registry.py`): Add folder detection, portfolio.yaml parsing, msgspec field extraction
- **Sandbox/Live Nodes** (`src/tinohelm/node/sandbox.py`, `live.py`): Replace inline Redis threads with BridgeActor, integrate portfolio_loader
- **DB Models** (`src/tinohelm/db/models.py`): strategies table becomes ephemeral cache, backtest_runs drops FK to strategies
- **CLI** (`src/tinohelm/cli/backtest.py`): No breaking changes — `tino backtest run <name>` works for both single files and portfolio folders
- **User strategies dir** (`~/.tino/strategies/`): New folder-based layout supported alongside existing single files
- **New dir** (`~/.tino/actors/`): Global reusable Actor storage
- **Dependencies**: No new external dependencies (NautilusTrader Actor/msgbus are already available)
