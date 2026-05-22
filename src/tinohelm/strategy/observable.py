"""ObservableStrategy — base class for strategies that publish signal snapshots."""
from __future__ import annotations

import json

from nautilus_trader.trading.strategy import Strategy

from tinohelm.data.strategy_snapshot import StrategySnapshot


class ObservableStrategy(Strategy):
    """Strategy base class that publishes observable signal snapshots.

    Subclasses override ``snapshot_fields()`` to declare what to publish,
    then call ``self._publish_snapshot(ts_event)`` at the end of ``on_bar()``.
    """

    def snapshot_fields(self) -> dict:
        """Override to return current signal/indicator values.

        Supports nested dicts for section grouping in the frontend:
        ``{"factors": {"rsi": 65, "atr": 0.03}, "decision": {"signal": 1}}``
        """
        return {}

    def _publish_snapshot(self, ts_event: int) -> None:
        """Publish current snapshot via NT DataEngine."""
        fields = self.snapshot_fields()
        if not fields:
            return
        instrument_id = ""
        if hasattr(self.config, "instrument_id") and self.config.instrument_id:
            instrument_id = str(self.config.instrument_id)
        snapshot = StrategySnapshot(
            strategy_id=str(self.id),
            instrument_id=instrument_id,
            fields_json=json.dumps(fields),
            ts_event=ts_event,
            ts_init=ts_event,
        )
        self.publish_data(StrategySnapshot, snapshot)
