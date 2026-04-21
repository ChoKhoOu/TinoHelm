"""Tests for :mod:`tinohelm.node.actors.serialize` — the single source of truth
for NT position/fill/bar/equity payload construction used by both SnapshotActor
(Redis PubSub) and DbWriterActor (PostgreSQL UPSERT bind params).

These tests are NT-free: they feed duck-typed MagicMocks into the helpers and
assert the exact dict shapes on the wire and in the SQL bind params, so a field
rename or accidental deletion in either actor fails the suite at compile time.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tinohelm.node.actors.serialize import (
    build_bar_event,
    build_equity_snapshot,
    build_fill_event,
    build_order_lifecycle_event,
    build_position_update,
    build_strategy_signal_snapshot,
    fill_db_fields,
    position_db_fields,
    tag_risk_metrics,
)


# ---------------------------------------------------------------------------
# Fixtures — duck-typed NT stand-ins
# ---------------------------------------------------------------------------

def _make_enum(name: str) -> SimpleNamespace:
    """Cheap stand-in for NT enums (PositionSide, OrderSide, LiquiditySide)."""
    return SimpleNamespace(name=name)


def _make_money(value: float, currency: str = "USDT") -> MagicMock:
    m = MagicMock()
    m.as_double.return_value = value
    m.currency = currency
    return m


@pytest.fixture
def closed_position() -> MagicMock:
    """A fully-populated PositionClosed.position with realistic numeric shapes."""
    pos = MagicMock()
    pos.id = "P-1"
    pos.strategy_id = "S-MOMENTUM-01"
    pos.instrument_id = "BTCUSDT-PERP.BINANCE"
    pos.side = _make_enum("LONG")
    pos.quantity = "0.100"
    pos.signed_qty = 0.100
    pos.avg_px_open = 50000.0
    pos.avg_px_close = 51000.0
    pos.realized_pnl = _make_money(100.0, "USDT")
    pos.entry = _make_enum("BUY")
    pos.peak_qty = "0.100"
    pos.ts_opened = 1_700_000_000_000_000_000
    pos.ts_closed = 1_700_000_060_000_000_000  # 60 seconds later
    pos.duration_ns = 60_000_000_000
    pos.is_open = False
    pos.event_count = 3
    return pos


@pytest.fixture
def open_position() -> MagicMock:
    """Open position: ts_closed is 0, avg_px_close is None."""
    pos = MagicMock()
    pos.id = "P-2"
    pos.strategy_id = "S-MR-02"
    pos.instrument_id = "ETHUSDT-PERP.BINANCE"
    pos.side = _make_enum("SHORT")
    pos.quantity = "1.5"
    pos.signed_qty = -1.5
    pos.avg_px_open = 3500.0
    pos.avg_px_close = None
    pos.realized_pnl = _make_money(0.0, "USDT")
    pos.entry = _make_enum("SELL")
    pos.peak_qty = "1.5"
    pos.ts_opened = 1_700_000_000_000_000_000
    pos.ts_closed = 0
    pos.duration_ns = 0
    pos.is_open = True
    pos.event_count = 1
    return pos


@pytest.fixture
def order_filled_event() -> MagicMock:
    ev = MagicMock()
    ev.trade_id = "T-42"
    ev.position_id = "P-1"
    ev.client_order_id = "O-1"
    ev.venue_order_id = "VO-1"
    ev.strategy_id = "S-01"
    ev.instrument_id = "BTCUSDT-PERP.BINANCE"
    ev.order_side = _make_enum("BUY")
    ev.last_qty = "0.05"
    ev.last_px = "50000"
    ev.commission = _make_money(0.25)
    ev.liquidity_side = _make_enum("TAKER")
    ev.ts_event = 1_700_000_000_000_000_000
    return ev


# ---------------------------------------------------------------------------
# position_db_fields
# ---------------------------------------------------------------------------

class TestPositionDbFields:
    EXPECTED_KEYS = {
        "node_type", "position_id", "strategy_id_tag", "instrument_id",
        "side", "quantity", "signed_qty", "avg_px_open", "avg_px_close",
        "realized_pnl", "unrealized_pnl", "currency", "entry_side",
        "peak_qty", "ts_opened", "ts_closed", "duration", "is_open",
        "event_count",
    }

    def test_closed_position_all_19_fields(self, closed_position):
        result = position_db_fields(closed_position, "sandbox")
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_closed_position_values(self, closed_position):
        result = position_db_fields(closed_position, "live")
        assert result["node_type"] == "live"
        assert result["position_id"] == "P-1"
        assert result["strategy_id_tag"] == "S-MOMENTUM-01"
        assert result["instrument_id"] == "BTCUSDT-PERP.BINANCE"
        assert result["side"] == "LONG"
        assert result["quantity"] == "0.100"
        assert result["signed_qty"] == 0.100
        assert result["avg_px_open"] == 50000.0
        assert result["avg_px_close"] == 51000.0
        assert result["realized_pnl"] == 100.0
        assert result["unrealized_pnl"] is None  # DbWriter never populates this
        assert result["currency"] == "USDT"
        assert result["entry_side"] == "BUY"
        assert result["peak_qty"] == "0.100"
        assert result["ts_opened"].startswith("2023-")  # ISO formatted
        assert result["ts_closed"].startswith("2023-")
        assert result["duration"] == "60000000000"
        assert result["is_open"] is False
        assert result["event_count"] == 3

    def test_open_position_null_closed_ts(self, open_position):
        result = position_db_fields(open_position, "sandbox")
        assert result["ts_closed"] is None  # 0 → None
        assert result["avg_px_close"] is None
        assert result["duration"] is None  # 0 → None
        assert result["is_open"] is True

    def test_ts_closed_of_zero_is_none(self, closed_position):
        closed_position.ts_closed = 0
        result = position_db_fields(closed_position, "sandbox")
        assert result["ts_closed"] is None

    def test_duration_of_zero_is_none(self, closed_position):
        closed_position.duration_ns = 0
        result = position_db_fields(closed_position, "sandbox")
        assert result["duration"] is None

    def test_duration_of_none_is_none(self, closed_position):
        closed_position.duration_ns = None
        result = position_db_fields(closed_position, "sandbox")
        assert result["duration"] is None

    def test_missing_strategy_id_becomes_empty_str(self, closed_position):
        closed_position.strategy_id = None
        result = position_db_fields(closed_position, "sandbox")
        assert result["strategy_id_tag"] == ""

    def test_realized_pnl_of_none_null_currency(self, open_position):
        open_position.realized_pnl = None
        result = position_db_fields(open_position, "sandbox")
        assert result["realized_pnl"] is None
        assert result["currency"] is None

    def test_signed_qty_is_float_not_str(self, closed_position):
        result = position_db_fields(closed_position, "sandbox")
        assert isinstance(result["signed_qty"], float)

    def test_quantity_stays_string(self, closed_position):
        # DB column is VARCHAR/TEXT — we preserve the NT str() serialization
        result = position_db_fields(closed_position, "sandbox")
        assert isinstance(result["quantity"], str)
        assert isinstance(result["peak_qty"], str)

    def test_ts_opened_iso_format(self, closed_position):
        result = position_db_fields(closed_position, "sandbox")
        # Alembic migrations use TIMESTAMP WITHOUT TIME ZONE but serialize.py
        # still emits ISO strings with +00:00 suffix — asyncpg accepts both.
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result["ts_opened"])


# ---------------------------------------------------------------------------
# build_position_update (snapshot payload — superset of db fields)
# ---------------------------------------------------------------------------

class TestBuildPositionUpdate:
    EXTRA_KEYS = {"type", "event", "id", "strategy_id", "duration_ns", "ts"}

    def test_is_superset_of_db_fields(self, closed_position):
        ts_event = 1_700_000_060_000_000_000
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", ts_event)
        db = position_db_fields(closed_position, "sandbox")
        # Every DB key must appear in the snapshot payload (same values or wider)
        for key in db:
            assert key in snap, f"DB key {key} missing from snapshot payload"

    def test_adds_6_extra_keys_over_db_fields(self, closed_position):
        ts_event = 1_700_000_060_000_000_000
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", ts_event)
        db = position_db_fields(closed_position, "sandbox")
        extras = set(snap.keys()) - set(db.keys())
        assert extras == self.EXTRA_KEYS

    def test_event_type_recorded(self, closed_position):
        ts_event = 1_700_000_000_000_000_000
        snap = build_position_update(closed_position, "sandbox", "PositionOpened", ts_event)
        assert snap["type"] == "position.update"
        assert snap["event"] == "PositionOpened"

    def test_realized_pnl_defaults_to_zero_not_none(self, open_position):
        """Snapshot differs from DB: null realized_pnl becomes 0.0, not None."""
        open_position.realized_pnl = None
        snap = build_position_update(open_position, "sandbox", "PositionOpened", 1)
        assert snap["realized_pnl"] == 0.0
        # DB counterpart is None
        assert position_db_fields(open_position, "sandbox")["realized_pnl"] is None

    def test_strategy_id_and_tag_mirror_each_other(self, closed_position):
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", 1)
        assert snap["strategy_id"] == snap["strategy_id_tag"]
        assert snap["strategy_id"] == "S-MOMENTUM-01"

    def test_missing_strategy_id_empty_on_snapshot(self, closed_position):
        closed_position.strategy_id = None
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", 1)
        assert snap["strategy_id"] == ""
        assert snap["strategy_id_tag"] == ""

    def test_id_is_always_zero_placeholder(self, closed_position):
        """The ``id`` field is a legacy placeholder for a DB autoincrement PK."""
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", 1)
        assert snap["id"] == 0

    def test_duration_ns_is_raw_int_when_present(self, closed_position):
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", 1)
        assert snap["duration_ns"] == 60_000_000_000
        assert snap["duration"] == "60000000000"  # string version for DB parity

    def test_duration_ns_is_none_when_zero(self, open_position):
        snap = build_position_update(open_position, "sandbox", "PositionOpened", 1)
        assert snap["duration_ns"] is None
        assert snap["duration"] is None

    def test_ts_event_becomes_ts_field(self, closed_position):
        ts_event = 1_700_000_060_000_000_000
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", ts_event)
        assert snap["ts"].startswith("2023-")

    def test_node_type_propagates(self, closed_position):
        snap = build_position_update(closed_position, "live", "PositionOpened", 1)
        assert snap["node_type"] == "live"


# ---------------------------------------------------------------------------
# fill_db_fields
# ---------------------------------------------------------------------------

class TestFillDbFields:
    EXPECTED_KEYS = {
        "node_type", "trade_id", "position_id", "client_order_id",
        "venue_order_id", "strategy_id_tag", "instrument_id", "order_side",
        "last_qty", "last_px", "commission", "liquidity_side", "ts_event",
    }

    def test_shape(self, order_filled_event):
        result = fill_db_fields(order_filled_event, "sandbox")
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_values(self, order_filled_event):
        result = fill_db_fields(order_filled_event, "live")
        assert result["node_type"] == "live"
        assert result["trade_id"] == "T-42"
        assert result["position_id"] == "P-1"
        assert result["client_order_id"] == "O-1"
        assert result["venue_order_id"] == "VO-1"
        assert result["strategy_id_tag"] == "S-01"
        assert result["instrument_id"] == "BTCUSDT-PERP.BINANCE"
        assert result["order_side"] == "BUY"
        assert result["last_qty"] == "0.05"
        assert result["last_px"] == "50000"
        assert result["commission"] == "0.25"
        assert result["liquidity_side"] == "TAKER"
        assert result["ts_event"].startswith("2023-")

    def test_position_id_of_none(self, order_filled_event):
        order_filled_event.position_id = None
        result = fill_db_fields(order_filled_event, "sandbox")
        assert result["position_id"] is None

    def test_venue_order_id_of_none(self, order_filled_event):
        order_filled_event.venue_order_id = None
        result = fill_db_fields(order_filled_event, "sandbox")
        assert result["venue_order_id"] is None

    def test_strategy_id_of_none(self, order_filled_event):
        order_filled_event.strategy_id = None
        result = fill_db_fields(order_filled_event, "sandbox")
        assert result["strategy_id_tag"] is None

    def test_commission_of_none(self, order_filled_event):
        order_filled_event.commission = None
        result = fill_db_fields(order_filled_event, "sandbox")
        assert result["commission"] is None

    def test_liquidity_side_of_none(self, order_filled_event):
        order_filled_event.liquidity_side = None
        result = fill_db_fields(order_filled_event, "sandbox")
        assert result["liquidity_side"] is None


# ---------------------------------------------------------------------------
# build_fill_event (snapshot payload — superset of db fields)
# ---------------------------------------------------------------------------

class TestBuildFillEvent:
    EXTRA_KEYS = {"type", "id", "strategy_id", "ts"}

    def test_shape_is_superset(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        db = fill_db_fields(order_filled_event, "sandbox")
        for key in db:
            assert key in snap

    def test_has_4_extras(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        db = fill_db_fields(order_filled_event, "sandbox")
        assert set(snap.keys()) - set(db.keys()) == self.EXTRA_KEYS

    def test_type_and_id(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        assert snap["type"] == "fill.new"
        assert snap["id"] == 0

    def test_ts_event_and_ts_identical(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        assert snap["ts"] == snap["ts_event"]

    def test_strategy_id_and_tag_mirror(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        assert snap["strategy_id"] == snap["strategy_id_tag"] == "S-01"

    def test_missing_strategy_id_stays_none(self, order_filled_event):
        order_filled_event.strategy_id = None
        snap = build_fill_event(order_filled_event, "sandbox")
        assert snap["strategy_id"] is None
        assert snap["strategy_id_tag"] is None


# ---------------------------------------------------------------------------
# build_order_lifecycle_event
# ---------------------------------------------------------------------------

class TestBuildOrderLifecycleEvent:

    @pytest.fixture
    def order_event(self) -> MagicMock:
        ev = MagicMock()
        ev.client_order_id = "O-42"
        ev.instrument_id = "BTCUSDT-PERP.BINANCE"
        ev.ts_event = 1_700_000_000_000_000_000
        return ev

    @pytest.mark.parametrize("kind", [
        "order_accepted", "order_canceled", "order_expired",
    ])
    def test_non_rejected_kinds_have_4_keys(self, order_event, kind):
        payload = build_order_lifecycle_event(order_event, kind)
        assert set(payload.keys()) == {"event", "order_id", "instrument_id", "ts"}
        assert payload["event"] == kind
        assert payload["order_id"] == "O-42"
        assert payload["instrument_id"] == "BTCUSDT-PERP.BINANCE"
        assert payload["ts"] == "1700000000000000000"  # raw str

    def test_rejected_kind_includes_reason(self, order_event):
        order_event.reason = "INSUFFICIENT_BALANCE"
        payload = build_order_lifecycle_event(order_event, "order_rejected")
        assert set(payload.keys()) == {"event", "order_id", "instrument_id", "ts", "reason"}
        assert payload["reason"] == "INSUFFICIENT_BALANCE"


# ---------------------------------------------------------------------------
# build_bar_event
# ---------------------------------------------------------------------------

class TestBuildBarEvent:
    def test_all_9_fields(self):
        bar = MagicMock()
        bar.bar_type = MagicMock()
        bar.bar_type.__str__ = lambda self: "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        bar.bar_type.instrument_id = "BTCUSDT-PERP.BINANCE"
        bar.open = "50000"
        bar.high = "50100"
        bar.low = "49900"
        bar.close = "50050"
        bar.volume = "10.5"
        bar.ts_event = 1_700_000_000_000_000_000
        payload = build_bar_event(bar)
        assert set(payload.keys()) == {
            "event", "bar_type", "instrument_id", "open", "high", "low",
            "close", "volume", "ts",
        }
        assert payload["event"] == "bar"
        assert payload["bar_type"] == "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        assert payload["instrument_id"] == "BTCUSDT-PERP.BINANCE"
        assert payload["open"] == "50000"
        assert payload["high"] == "50100"
        assert payload["low"] == "49900"
        assert payload["close"] == "50050"
        assert payload["volume"] == "10.5"
        assert payload["ts"] == "1700000000000000000"


# ---------------------------------------------------------------------------
# build_strategy_signal_snapshot
# ---------------------------------------------------------------------------

class TestBuildStrategySignalSnapshot:
    def test_shape(self):
        snap = MagicMock()
        snap.strategy_id = "S-01"
        snap.instrument_id = "BTCUSDT-PERP.BINANCE"
        snap.ts_event = 1_700_000_000_000_000_000
        fields = {"rsi": 70, "macd": 0.5}

        payload = build_strategy_signal_snapshot(snap, "sandbox", fields)
        assert payload == {
            "type": "signal.snapshot",
            "node_type": "sandbox",
            "strategy_id": "S-01",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "fields": fields,
            "ts": payload["ts"],  # computed from ts_event
        }
        assert payload["ts"].startswith("2023-")

    def test_fields_dict_is_passed_by_reference(self):
        """Caller gives us a fresh dict — we don't copy it."""
        snap = MagicMock()
        snap.strategy_id = "S-01"
        snap.instrument_id = "X"
        snap.ts_event = 1
        fields = {"a": 1}
        payload = build_strategy_signal_snapshot(snap, "sandbox", fields)
        assert payload["fields"] is fields


# ---------------------------------------------------------------------------
# tag_risk_metrics
# ---------------------------------------------------------------------------

class TestTagRiskMetrics:
    def test_mutates_and_returns_same_dict(self):
        data = {"drawdown": 0.12, "exposure": 5000.0}
        result = tag_risk_metrics(data, "live")
        assert result is data  # mutate, not copy
        assert data["type"] == "risk.metrics"
        assert data["node_type"] == "live"
        assert data["drawdown"] == 0.12
        assert data["exposure"] == 5000.0

    def test_overwrites_preexisting_type(self):
        """Keep the documented behaviour: caller data cannot inject a fake type."""
        data = {"type": "attacker-injected", "node_type": "wrong"}
        tag_risk_metrics(data, "sandbox")
        assert data["type"] == "risk.metrics"
        assert data["node_type"] == "sandbox"


# ---------------------------------------------------------------------------
# build_equity_snapshot
# ---------------------------------------------------------------------------

class TestBuildEquitySnapshot:
    def test_basic_shape(self):
        p = build_equity_snapshot("sandbox", 10000.0, 9000.0, 1000.0, "2024-01-01T00:00:00+00:00")
        assert p == {
            "type": "equity.snapshot",
            "node_type": "sandbox",
            "equity": 10000.00,
            "balance": 9000.00,
            "unrealized_pnl": 1000.00,
            "ts": "2024-01-01T00:00:00+00:00",
        }

    def test_rounds_to_two_decimals(self):
        p = build_equity_snapshot("live", 10000.12345, 9000.99999, 123.456, "t")
        assert p["equity"] == 10000.12
        assert p["balance"] == 9001.00
        assert p["unrealized_pnl"] == 123.46

    def test_negative_values_preserved(self):
        p = build_equity_snapshot("live", 5000.0, 6000.0, -1000.0, "t")
        assert p["equity"] == 5000.0
        assert p["unrealized_pnl"] == -1000.0

    def test_node_type_passthrough(self):
        p = build_equity_snapshot("live", 1, 1, 0, "t")
        assert p["node_type"] == "live"


# ---------------------------------------------------------------------------
# Cross-cutting: snapshot payloads match DB bind params where they overlap
# ---------------------------------------------------------------------------

class TestSnapshotDbOverlap:
    """Guardrail: for every field the DB persists, the snapshot payload exposes
    the same value. Without this, a frontend user's "current position PnL" could
    diverge from the DB record of the same event.
    """

    def test_position_overlap_values_identical(self, closed_position):
        ts_event = closed_position.ts_closed
        snap = build_position_update(closed_position, "sandbox", "PositionClosed", ts_event)
        db = position_db_fields(closed_position, "sandbox")
        # For every shared key except `realized_pnl` (snapshot defaults null→0.0)
        # the values must match byte-for-byte.
        shared = set(db.keys()) & set(snap.keys())
        for key in shared:
            assert snap[key] == db[key], f"mismatch on {key}: {snap[key]!r} vs {db[key]!r}"

    def test_position_overlap_realized_pnl_diverges_only_on_none(self, open_position):
        """When `realized_pnl` is None on the NT object, DB keeps it None but
        snapshot coerces to 0.0 — this is the documented intentional divergence.
        """
        open_position.realized_pnl = None
        snap = build_position_update(open_position, "sandbox", "PositionOpened", 1)
        db = position_db_fields(open_position, "sandbox")
        assert snap["realized_pnl"] == 0.0
        assert db["realized_pnl"] is None

    def test_fill_overlap_values_identical(self, order_filled_event):
        snap = build_fill_event(order_filled_event, "sandbox")
        db = fill_db_fields(order_filled_event, "sandbox")
        shared = set(db.keys()) & set(snap.keys())
        for key in shared:
            assert snap[key] == db[key], f"mismatch on {key}"
