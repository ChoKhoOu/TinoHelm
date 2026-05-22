"""Minimal NT Controller to enable runtime strategy add/remove on Trader.

When registered via ``TradingNodeConfig.controller``, this sets
``has_controller=True`` on the Trader, which is REQUIRED for
``trader.add_strategy()`` and ``trader.remove_strategy()`` to work
at runtime.  Without it, those calls silently fail.

All actual lifecycle logic stays in LifecycleController + CommandActor.
"""
from __future__ import annotations

from nautilus_trader.trading.controller import Controller


class TinoController(Controller):
    """Minimal controller — enables runtime strategy management on Trader."""
    pass
