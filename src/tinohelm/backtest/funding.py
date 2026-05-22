"""Funding cost tracking for perpetual futures backtests.

Perpetual contracts charge funding fees every 8 hours. This module provides
an Actor that tracks funding costs during backtest execution by checking open
positions at each funding timestamp and accumulating the cost.

The actual cost formula, event-advancement, and result-summary shape live in
``funding_math`` — this file only bridges the NT Actor surface (``on_start``,
``on_bar``, ``self.cache.positions_open()``) to that pure layer.
"""
from __future__ import annotations

import logging
from typing import Any

from nautilus_trader.common.actor import Actor, ActorConfig

from tinohelm.backtest.funding_math import (
    advance_due_events,
    apply_funding_event,
    summarize_funding,
)

logger = logging.getLogger(__name__)


class _FundingCostTrackerConfig(ActorConfig, frozen=True):
    component_id: str = "FundingCostTracker-001"


class _FundingCostTracker(Actor):
    """Tracks funding costs by scanning open positions at funding timestamps.

    Uses class-level attributes set by BacktestRunner before ``engine.run()``.
    This is safe because the worker processes one backtest at a time.
    """

    # --- Class-level data (set before engine.run) ---
    # Sorted list of funding events:
    #   [{"timestamp_ns": int, "timestamp_iso": str, "symbol": str,
    #     "rate": float, "mark_price": float}, ...]
    _funding_events: list[dict[str, Any]] = []
    _bar_type_strs: list[str] = []

    def __init__(self, config: _FundingCostTrackerConfig | None = None) -> None:
        super().__init__(config or _FundingCostTrackerConfig())
        # Instance-level accumulators — avoids class-level mutable state leaking
        # between backtests (e.g. during optimizer runs where multiple backtests
        # share the same process and class object).
        self._total_funding_cost: float = 0.0
        self._funding_records: list[dict[str, Any]] = []
        self._per_symbol_cost: dict[str, float] = {}
        self._next_event_idx: int = 0

    def on_start(self) -> None:
        from nautilus_trader.model.data import BarType

        # Reset accumulators at the start of each backtest run
        self._total_funding_cost = 0.0
        self._funding_records = []
        self._per_symbol_cost = {}
        self._next_event_idx = 0

        for bt_str in _FundingCostTracker._bar_type_strs:
            try:
                self.subscribe_bars(BarType.from_str(bt_str))
            except Exception:
                pass

    def on_bar(self, bar) -> None:
        due, new_idx = advance_due_events(
            _FundingCostTracker._funding_events,
            current_ns=bar.ts_init,
            next_idx=self._next_event_idx,
        )
        for event in due:
            self._apply_funding(event)
        self._next_event_idx = new_idx

    def _apply_funding(self, event: dict[str, Any]) -> None:
        """Calculate and record funding cost for all open positions matching the symbol."""
        self._total_funding_cost, _, _ = apply_funding_event(
            event,
            self.cache.positions_open(),
            total_cost=self._total_funding_cost,
            per_symbol_cost=self._per_symbol_cost,
            records=self._funding_records,
        )

    def get_results(self) -> dict[str, Any]:
        """Return accumulated funding cost data for result extraction."""
        return summarize_funding(
            total_funding_cost=self._total_funding_cost,
            per_symbol_cost=self._per_symbol_cost,
            funding_records=self._funding_records,
        )
