"""Tests for MetricsActor commission deviation monitoring logic.

The canonical event name is ``signal.commission.deviation``.  The config
field is ``expected_commission_bps_per_side``.  Payload keys are
``expected_commission_bps`` / ``actual_commission_bps`` with a
``metric: "commission_only"`` discriminator.

NT Actor is a Cython extension class that cannot be instantiated in
isolation.  We use a stub pattern (same as ``tests/actors/test_risk_guard.py``)
that replicates only the ``_check_commission_deviation`` method with the
minimal attributes it needs.

Attributes required by ``_check_commission_deviation``:
  - self._expected_commission_bps: float
  - self._deviation_threshold_bps: float
  - self._node_type: str
  - self._redis: mock / None
  - self.log: mock

The method uses ``redis_publish()`` from ``_utils`` which constructs the
channel as ``f"tino:{node_type}:{channel_suffix}"`` and calls
``redis.publish()``.  We capture publish calls via the mock redis client.
"""
from __future__ import annotations

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stub — replicates _check_commission_deviation without NT Cython base class
# ---------------------------------------------------------------------------

class _MetricsActorStub:
    """Lightweight stand-in for MetricsActor commission deviation logic.

    Copies ``_check_commission_deviation`` verbatim so any bugs in that
    method surface in tests rather than silently diverging.
    """

    def __init__(
        self,
        node_type: str = "sandbox",
        expected_commission_bps: float = 5.0,
        deviation_threshold_bps: float = 5.0,
    ) -> None:
        self._node_type = node_type
        self._expected_commission_bps = expected_commission_bps
        self._deviation_threshold_bps = deviation_threshold_bps

        # Mock redis: captures publish(channel, payload) calls.
        self._redis = MagicMock()
        self.log = MagicMock()

    def _check_commission_deviation(self, fill_event: object) -> None:
        """Exact copy of MetricsActor._check_commission_deviation."""
        from tinohelm.node.actors._utils import redis_publish
        from tinohelm.node.topics import SIGNAL_COMMISSION_DEVIATION

        try:
            commission = float(getattr(fill_event, "commission").as_double())
            quantity = float(getattr(fill_event, "last_qty").as_double())
            last_px = float(getattr(fill_event, "last_px").as_double())
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            return

        if quantity == 0.0 or last_px == 0.0:
            return

        actual_commission_bps = (commission / (quantity * last_px)) * 10_000.0
        deviation_bps = abs(actual_commission_bps - self._expected_commission_bps)

        if deviation_bps <= self._deviation_threshold_bps:
            return

        from typing import Any
        payload: dict[str, Any] = {
            "fill_id": str(getattr(fill_event, "trade_id", "")),
            "instrument_id": str(getattr(fill_event, "instrument_id", "")),
            "metric": "commission_only",
            "expected_commission_bps": self._expected_commission_bps,
            "actual_commission_bps": round(actual_commission_bps, 6),
            "deviation_bps": round(deviation_bps, 6),
            "ts_ns": getattr(fill_event, "ts_init", 0),
        }

        redis_publish(
            self._redis,
            self._node_type,
            SIGNAL_COMMISSION_DEVIATION,
            payload,
        )

        self.log.warning(
            f"signal.commission.deviation: expected={self._expected_commission_bps}bps "
            f"actual={actual_commission_bps:.2f}bps deviation={deviation_bps:.2f}bps"
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


def _published_channels(actor: _MetricsActorStub) -> list[str]:
    """Return channel arg from every redis.publish() call on the stub."""
    return [call.args[0] for call in actor._redis.publish.call_args_list]


def _first_payload(actor: _MetricsActorStub) -> dict:
    """Decode the first published payload as a dict."""
    import json
    raw = actor._redis.publish.call_args_list[0].args[1]
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCommissionDeviationPublishesWhenExceeded:
    def test_publishes_event_when_actual_far_exceeds_expected(self):
        """actual_commission >> expected + threshold → redis publish called on canonical channel."""
        actor = _MetricsActorStub(expected_commission_bps=5.0, deviation_threshold_bps=5.0)

        # commission=1.0, qty=1, px=100 → actual = 1.0/(1*100) * 10000 = 100 bps
        # deviation = |100 - 5| = 95 > threshold=5 → PUBLISH
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)
        actor._check_commission_deviation(fill)

        assert actor._redis.publish.call_count == 1
        channels = _published_channels(actor)
        assert "tino:sandbox:signal.commission.deviation" in channels

        payload = _first_payload(actor)
        assert payload["metric"] == "commission_only"
        assert payload["expected_commission_bps"] == 5.0
        assert abs(payload["actual_commission_bps"] - 100.0) < 0.01
        assert abs(payload["deviation_bps"] - 95.0) < 0.01

    def test_payload_contains_fill_metadata(self):
        """Published payload includes fill_id, instrument_id, ts_ns."""
        actor = _MetricsActorStub(expected_commission_bps=1.0, deviation_threshold_bps=0.5)
        # actual = 0.5/(1*100)*10000 = 50 bps; deviation = 49 > 0.5 → publish
        fill = _make_fill(
            commission=0.5, quantity=1.0, last_px=100.0,
            trade_id="trade-xyz", instrument_id="ETHUSDT-PERP.BINANCE", ts_init=999,
        )
        actor._check_commission_deviation(fill)

        payload = _first_payload(actor)
        assert payload["fill_id"] == "trade-xyz"
        assert payload["instrument_id"] == "ETHUSDT-PERP.BINANCE"
        assert payload["ts_ns"] == 999

    def test_payload_does_not_contain_legacy_aliases(self):
        """Legacy keys ``expected_bps``/``actual_bps`` have been removed."""
        actor = _MetricsActorStub(expected_commission_bps=5.0, deviation_threshold_bps=5.0)
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)
        actor._check_commission_deviation(fill)

        payload = _first_payload(actor)
        assert "expected_bps" not in payload
        assert "actual_bps" not in payload


class TestCommissionDeviationSilentWhenWithinThreshold:
    def test_no_publish_when_actual_equals_expected(self):
        """actual exactly matches expected → no publish."""
        actor = _MetricsActorStub(expected_commission_bps=5.0, deviation_threshold_bps=5.0)
        # commission=0.5, qty=1, px=1000 → actual = 0.5/1000 * 10000 = 5.0 bps
        # deviation = |5.0 - 5.0| = 0.0 ≤ threshold=5.0 → SILENT
        fill = _make_fill(commission=0.5, quantity=1.0, last_px=1000.0)
        actor._check_commission_deviation(fill)

        actor._redis.publish.assert_not_called()

    def test_no_publish_when_deviation_at_threshold(self):
        """deviation exactly at threshold boundary → no publish (not strictly greater)."""
        actor = _MetricsActorStub(expected_commission_bps=5.0, deviation_threshold_bps=5.0)
        # actual = 10.0 bps → deviation = 5.0 ≤ 5.0 → SILENT
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=1000.0)
        actor._check_commission_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_no_publish_just_below_threshold(self):
        """deviation just under threshold → no publish."""
        actor = _MetricsActorStub(expected_commission_bps=10.0, deviation_threshold_bps=5.0)
        # actual = 14.0 bps → deviation = 4.0 < 5.0 → SILENT
        fill = _make_fill(commission=1.4, quantity=1.0, last_px=1000.0)
        actor._check_commission_deviation(fill)
        actor._redis.publish.assert_not_called()


class TestCommissionDeviationEdgeCases:
    def test_zero_quantity_does_not_raise(self):
        """quantity=0 → early return; no exception, no publish."""
        actor = _MetricsActorStub()
        fill = _make_fill(commission=0.0, quantity=0.0, last_px=100.0)
        actor._check_commission_deviation(fill)  # must not raise
        actor._redis.publish.assert_not_called()

    def test_zero_price_does_not_raise(self):
        """last_px=0 → early return; no exception, no publish."""
        actor = _MetricsActorStub()
        fill = _make_fill(commission=1.0, quantity=10.0, last_px=0.0)
        actor._check_commission_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_missing_attribute_does_not_raise(self):
        """Missing commission attribute → AttributeError caught; no raise."""
        actor = _MetricsActorStub()
        fill = MagicMock(spec=[])  # no attributes at all
        actor._check_commission_deviation(fill)
        actor._redis.publish.assert_not_called()

    def test_zero_commission_with_positive_qty_px(self):
        """Zero commission → actual = 0 bps; deviation is expected itself."""
        actor = _MetricsActorStub(expected_commission_bps=10.0, deviation_threshold_bps=5.0)
        # actual = 0 bps, deviation = 10.0 > 5.0 → publish
        fill = _make_fill(commission=0.0, quantity=1.0, last_px=1000.0)
        actor._check_commission_deviation(fill)
        assert actor._redis.publish.call_count == 1


class TestCommissionDeviationNodeTypeInChannel:
    def test_sandbox_node_type_in_canonical_channel(self):
        """node_type=sandbox → canonical channel = tino:sandbox:signal.commission.deviation."""
        actor = _MetricsActorStub(node_type="sandbox", expected_commission_bps=1.0, deviation_threshold_bps=0.1)
        fill = _make_fill(commission=10.0, quantity=1.0, last_px=100.0)  # 1000 bps
        actor._check_commission_deviation(fill)

        channels = _published_channels(actor)
        assert "tino:sandbox:signal.commission.deviation" in channels

    def test_live_node_type_in_canonical_channel(self):
        """node_type=live → canonical channel = tino:live:signal.commission.deviation."""
        actor = _MetricsActorStub(node_type="live", expected_commission_bps=1.0, deviation_threshold_bps=0.1)
        fill = _make_fill(commission=10.0, quantity=1.0, last_px=100.0)  # 1000 bps
        actor._check_commission_deviation(fill)

        channels = _published_channels(actor)
        assert "tino:live:signal.commission.deviation" in channels

    def test_warning_logged_on_deviation(self):
        """log.warning is called when deviation exceeds threshold."""
        actor = _MetricsActorStub(expected_commission_bps=5.0, deviation_threshold_bps=1.0)
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)  # 100 bps
        actor._check_commission_deviation(fill)

        actor.log.warning.assert_called_once()
        warning_msg = actor.log.warning.call_args[0][0]
        # Warning text uses the canonical event name.
        assert "signal.commission.deviation" in warning_msg


class TestNoDualPublish:
    """After legacy removal, only the canonical channel is published."""

    def test_only_canonical_channel_published(self):
        actor = _MetricsActorStub(node_type="sandbox", expected_commission_bps=5.0, deviation_threshold_bps=1.0)
        fill = _make_fill(commission=1.0, quantity=1.0, last_px=100.0)  # 100 bps
        actor._check_commission_deviation(fill)

        channels = _published_channels(actor)
        assert "tino:sandbox:signal.commission.deviation" in channels
        assert "tino:sandbox:signal.cost.deviation" not in channels
        assert actor._redis.publish.call_count == 1
