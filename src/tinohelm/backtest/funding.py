"""Funding cost tracking for perpetual futures backtests.

Perpetual contracts charge funding fees every 8 hours. This module provides
an Actor that tracks funding costs during backtest execution by checking open
positions at each funding timestamp and accumulating the cost.

Funding cost = position_notional * funding_rate
  - Long + positive rate → pay (cost > 0)
  - Long + negative rate → receive (cost < 0)
  - Short + positive rate → receive (cost < 0)
  - Short + negative rate → pay (cost > 0)
"""
from __future__ import annotations

import logging
from typing import Any

from nautilus_trader.common.actor import Actor, ActorConfig

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

        for bt_str in self.__class__._bar_type_strs:
            try:
                self.subscribe_bars(BarType.from_str(bt_str))
            except Exception:
                pass

    def on_bar(self, bar) -> None:
        current_ns = bar.ts_init
        funding_events = self.__class__._funding_events

        # Process all funding events up to the current bar timestamp
        while self._next_event_idx < len(funding_events):
            event = funding_events[self._next_event_idx]
            if event["timestamp_ns"] <= current_ns:
                self._apply_funding(event)
                self._next_event_idx += 1
            else:
                break

    def _apply_funding(self, event: dict[str, Any]) -> None:
        """Calculate and record funding cost for all open positions matching the symbol."""
        symbol_prefix = event["symbol"]  # e.g. "BTCUSDT-PERP.BINANCE"
        rate = event["rate"]
        mark_price = event["mark_price"]

        positions = self.cache.positions_open()
        for pos in positions:
            pos_symbol = str(pos.instrument_id)
            if pos_symbol != symbol_prefix:
                continue

            qty = float(pos.quantity)
            notional = qty * mark_price

            # Long pays positive rate, short receives positive rate
            if pos.side.name == "LONG":
                cost = notional * rate
            else:  # SHORT
                cost = -notional * rate

            self._total_funding_cost += cost
            self._per_symbol_cost[pos_symbol] = self._per_symbol_cost.get(pos_symbol, 0.0) + cost
            self._funding_records.append({
                "timestamp": event["timestamp_iso"],
                "symbol": pos_symbol,
                "side": pos.side.name,
                "quantity": qty,
                "mark_price": mark_price,
                "funding_rate": rate,
                "cost": round(cost, 6),
            })

    def get_results(self) -> dict[str, Any]:
        """Return accumulated funding cost data for result extraction."""
        return {
            "total_funding_cost": round(self._total_funding_cost, 4),
            "funding_event_count": len(self._funding_records),
            "per_symbol_funding": {
                k: round(v, 4) for k, v in self._per_symbol_cost.items()
            },
            "funding_records": self._funding_records,
        }
