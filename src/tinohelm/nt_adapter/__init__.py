"""NautilusTrader adapter — turn a :class:`SignalSpec` into an executable Strategy.

The ``nt_adapter`` package is the single, narrow bridge between the pure-logic
``signal/`` framework and the NT execution engine.  Direction of dependency:

* ``signal/``    →  pure polars/numpy logic (no NT).
* ``nt_adapter/``→  imports both ``signal/`` and ``nautilus_trader``.
* ``backtest/`` / ``node/``  →  load strategies via ``loader.create_strategies``,
  which can produce a :class:`SignalDrivenStrategy` instance from a
  ``portfolio.yaml`` exported by ``/api/signal/export/{id}``.

Public surface
--------------
* :class:`SignalDrivenStrategy` — generic NT Strategy that executes any
  ``SignalSpec`` against a multi-symbol universe.
* :class:`SignalDrivenStrategyConfig` — msgspec ``StrategyConfig`` carrying
  the serialised ``SignalSpec`` JSON, bar types, and warmup hints.
* :class:`BarSynchronizer` — multi-symbol cross-section gating: holds
  per-symbol bars until all expected symbols arrive at the same timestamp.
* :class:`OrderManager` — translates target-weight diffs into NT
  :class:`MarketOrder` submissions using ``instrument.make_qty`` for
  exchange-correct lot rounding.
"""
from tinohelm.nt_adapter.bar_synchronizer import (
    BarSynchronizer,
    BarSynchronizerConfig,
)
from tinohelm.nt_adapter.order_manager import OrderManager
from tinohelm.nt_adapter.signal_driven_strategy import (
    SignalDrivenStrategy,
    SignalDrivenStrategyConfig,
)

__all__ = [
    "BarSynchronizer",
    "BarSynchronizerConfig",
    "OrderManager",
    "SignalDrivenStrategy",
    "SignalDrivenStrategyConfig",
]
