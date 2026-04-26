"""Tests for MetricsActor signal.cost.deviation monitoring logic.

NT Actor is a Cython extension class that cannot be instantiated in isolation.
We use a stub pattern (same as tests/actors/test_risk_guard.py) that replicates
only the _check_cost_deviation method with the minimal attributes it needs.

Attributes required by _check_cost_deviation:
  - self._cost_model_fee_bps: float
  - self._deviation_threshold_bps: float
  - self._node_type: str
  - self._redis: mock / None
  - self.log: mock

The method uses redis_publish() from _utils which constructs the channel as
f"tino:{node_type}:{channel_suffix}" and calls redis.publish().
We capture publish calls via the mock redis client.
"""
from __future__ import annotations

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stub — replicates _check_cost_deviation without NT Cython base class
# ---------------------------------------------------------------------------

class _MetricsActorStub:
    """Lightweight stand-in for MetricsActor cost deviation logic.

    Copies _check_cost_deviation verbatim so any bugs in that method
    surface in tests rather than silently diverging.
    """

    def __init__(
        self,
        node_type: str = "sandbox",
        cost_model_fee_bps: float = 5.0,
        deviation_threshold_bps: float = 5.0,
    ) -> None:
        self._node_type = node_type
        self._cost_model_fee_bps = cost_model_fee_bps
        self._deviation_threshold_bps = deviation_threshold_bps

        # Mock redis: captures publish(channel, payload) calls.
        self._redis = MagicMock()
        self.log = MagicMock()

    def _check_cost_deviation(self, fill_event: object) -> None:
        """Exact copy of MetricsActor._check_cost_deviation."""
        import json
        from tinohelm.node.actors._utils import redis_publish
        from tinohelm.node.topics import SIGNAL_COST_DEVIATION

        try:
            commission = float(getattr(fill_event, "commission").as_double())
            quantity = float(getattr(fill_event, "last_qty").as_double())
            last_px = float(getattr(fill_event, "last_px").as_double())
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            return

        if quantity == 0.0 or last_px == 0.0:
            return

        actual_cost_bps = (commission / (quantity * last_px)) * 10_000.0
        deviation_bps = abs(actual_cost_bps - self._cost_model_fee_bps)

        if deviation_bps <= self._deviation_threshold_bps:
            return

        from typing import Any
        payload: dict[str, Any] = {
            "fill_id": str(getattr(fill_event, "trade_id", "")),
            "instrument_id": str(getattr(fill_event, "instrument_id", "")),
            "expected_bps": self._cost_model_fee_bps,
            "actual_bps": round(actual_cost_bps, 6),
            "deviation_bps": round(deviation_bps, 6),
            "ts_ns": getattr(fill_event, "ts_init", 0),
        }

        redis_publish(
            self._redis,
            self._node_type,
            SIGNAL_COST_DEVIATION,
            payload,
        )

        self.log.warning(
            f"signal.cost.deviation: expected={self._cost_model_fee_bps}bps "
            f"actual={actual_cost_bps:.2f}bps deviation={deviation_bps:.2f}bps"
        )


# ---------------------------------------------------------------------------
# Fill mock helpers
# ---------------------------------------------------------------------------

def _make_fill(*, commission: float, quantity: float, last_px: float,
               trade_id: str = "trade-1", instrument_id: str = "BTCUSDT-PERP.BINANCE",
               ts_init: int = 0):
    """Build a minimal mock OrderFilled-like object."""
    fill = MagicMock()

    comm_obj = MagicMock()
    comm_obj.as_double.return_value = commission
    fill.commission = comm_obj

    qty_obj = MagicMock()
    qty_obj.as_double.return_value = quantity
    fill.last_qty = qty_obj

    px_obj = MagicMock()
    px_obj.as_double.return_value = last_px
    fill.last_px = px_obj

    fill.trade_id = trade_id
    fill.instrument_id = instrument_id
    fill.ts_init = ts_init
    return fill


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCostDeviationPublishesWhenExceeded:
    def test_publishes_event_when_actual_far_exceeds_expected(self):
        """actual_cost >> expected + threshold → redis publish called."""
        actor = _MetricsActorStub(cost_model_fee_bps=5.0, deviation_threshold_bps=5.0)

        # commission=1.0, qty=1, px=100 → actual = 1.0/(1*100) * 10000 = 100 bps
        # deviation = |100 - 5| = 95 > threshold=5 → PUBLISH
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)
        actor._check_cost_deviation(fill)

        actor._redis.publish.assert_called_once()
        channel, raw_payload = actor._redis.publish.call_args[0]
        assert channel == "tino:sandbox:signal.cost.deviation"

        import json
        payload = json.loads(raw_payload)
        assert payload["expected_bps"] == 5.0
        assert abs(payload["actual_bps"] - 100.0) < 0.01
        assert abs(payload["deviation_bps"] - 95.0) < 0.01

    def test_payload_contains_fill_metadata(self):
        """Published payload includes fill_id, instrument_id, ts_ns."""
        actor = _MetricsActorStub(cost_model_fee_bps=1.0, deviation_threshold_bps=0.5)
        # actual = 0.5/(1*100)*10000 = 50 bps; deviation = 49 > 0.5 → publish
        fill = _make_fill(
            commission=0.5, quantity=1.0, last_px=100.0,
            trade_id="trade-xyz", instrument_id="ETHUSDT-PERP.BINANCE", ts_init=999,
        )
        actor._check_cost_deviation(fill)

        import json
        payload = json.loads(actor._redis.publish.call_args[0][1])
        assert payload["fill_id"] == "trade-xyz"
        assert payload["instrument_id"] == "ETHUSDT-PERP.BINANCE"
        assert payload["ts_ns"] == 999


class TestCostDeviationSilentWhenWithinThreshold:
    def test_no_publish_when_actual_equals_expected(self):
        """actual exactly matches expected → no publish."""
        actor = _MetricsActorStub(cost_model_fee_bps=5.0, deviation_threshold_bps=5.0)
        # commission=0.5, qty=1, px=1000 → actual = 0.5/1000 * 10000 = 5.0 bps
        # deviation = |5.0 - 5.0| = 0.0 ≤ threshold=5.0 → SILENT
        fill = _make_fill(commission=0.5, quantity=1.0, last_px=1000.0)
        actor._check_cost_deviation(fill)

        actor._redis.publish.assert_not_called()

    def test_no_publish_when_deviation_at_threshold(self):
        """deviation exactly at threshold boundary → no publish (not strictly greater)."""
        actor = _MetricsActorStub(cost_model_fee_bps=5.0, deviation_threshold_bps=5.0)
        # actual = 10.0 bps → deviation = 5.0 ≤ 5.0 → SILENT
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=1000.0)
        actor._check_cost_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_no_publish_just_below_threshold(self):
        """deviation just under threshold → no publish."""
        actor = _MetricsActorStub(cost_model_fee_bps=10.0, deviation_threshold_bps=5.0)
        # actual = 14.0 bps → deviation = 4.0 < 5.0 → SILENT
        fill = _make_fill(commission=1.4, quantity=1.0, last_px=1000.0)
        actor._check_cost_deviation(fill)
        actor._redis.publish.assert_not_called()


class TestCostDeviationEdgeCases:
    def test_zero_quantity_does_not_raise(self):
        """quantity=0 → early return; no exception, no publish."""
        actor = _MetricsActorStub()
        fill = _make_fill(commission=0.0, quantity=0.0, last_px=100.0)
        actor._check_cost_deviation(fill)  # must not raise
        actor._redis.publish.assert_not_called()

    def test_zero_price_does_not_raise(self):
        """last_px=0 → early return; no exception, no publish."""
        actor = _MetricsActorStub()
        fill = _make_fill(commission=1.0, quantity=10.0, last_px=0.0)
        actor._check_cost_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_missing_attribute_does_not_raise(self):
        """Missing commission attribute → AttributeError caught; no raise."""
        actor = _MetricsActorStub()
        fill = MagicMock(spec=[])  # no attributes at all
        actor._check_cost_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_zero_commission_with_positive_qty_px(self):
        """Zero commission → actual cost = 0 bps; deviation is expected_bps itself."""
        actor = _MetricsActorStub(cost_model_fee_bps=10.0, deviation_threshold_bps=5.0)
        # actual = 0 bps, deviation = 10.0 > 5.0 → publish
        fill = _make_fill(commission=0.0, quantity=1.0, last_px=1000.0)
        actor._check_cost_deviation(fill)
        actor._redis.publish.assert_called_once()


class TestCostDeviationNodeTypeInChannel:
    def test_sandbox_node_type_in_channel(self):
        """node_type=sandbox → channel = tino:sandbox:signal.cost.deviation."""
        actor = _MetricsActorStub(node_type="sandbox", cost_model_fee_bps=1.0, deviation_threshold_bps=0.1)
        fill = _make_fill(commission=10.0, quantity=1.0, last_px=100.0)  # 1000 bps
        actor._check_cost_deviation(fill)

        channel = actor._redis.publish.call_args[0][0]
        assert channel == "tino:sandbox:signal.cost.deviation"

    def test_live_node_type_in_channel(self):
        """node_type=live → channel = tino:live:signal.cost.deviation."""
        actor = _MetricsActorStub(node_type="live", cost_model_fee_bps=1.0, deviation_threshold_bps=0.1)
        fill = _make_fill(commission=10.0, quantity=1.0, last_px=100.0)  # 1000 bps
        actor._check_cost_deviation(fill)

        channel = actor._redis.publish.call_args[0][0]
        assert channel == "tino:live:signal.cost.deviation"

    def test_warning_logged_on_deviation(self):
        """log.warning is called when deviation exceeds threshold."""
        actor = _MetricsActorStub(cost_model_fee_bps=5.0, deviation_threshold_bps=1.0)
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)  # 100 bps
        actor._check_cost_deviation(fill)

        actor.log.warning.assert_called_once()
        warning_msg = actor.log.warning.call_args[0][0]
        assert "signal.cost.deviation" in warning_msg
