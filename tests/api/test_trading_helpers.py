"""Tests for pure helpers in tinohelm.api.routes.trading.

Covers the ORM-row → API response model mappers (`_position_to_item`,
`_fill_to_item`) and the default dict returned by GET /risk-metrics when Redis
is empty.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tinohelm.api.routes.trading import (
    _RISK_METRICS_DEFAULT,
    _fill_to_item,
    _position_to_item,
    FillItem,
    PositionItem,
)


# ---------------------------------------------------------------------------
# Risk metrics default dict
# ---------------------------------------------------------------------------


class TestRiskMetricsDefault:
    def test_default_shape_is_stable(self):
        assert _RISK_METRICS_DEFAULT == {
            "equity": 0,
            "peak_equity": 0,
            "drawdown_pct": 0,
            "daily_pnl_pct": 0,
            "total_exposure": 0,
            "position_count": 0,
            "breached": False,
            "breach_reason": "",
            "per_instrument_exposure": {},
        }

    def test_default_is_module_level_constant(self):
        """Modifying the dict via a copy must not mutate the constant."""
        # Callers always pass dict(_RISK_METRICS_DEFAULT) so they get a fresh
        # copy. But if anyone accidentally mutated the module-level dict
        # directly, tests in other modules would see stale state. Here we
        # verify it's a dict and document the semantics.
        assert isinstance(_RISK_METRICS_DEFAULT, dict)


# ---------------------------------------------------------------------------
# _position_to_item
# ---------------------------------------------------------------------------


def _make_position_row(**overrides):
    """Build a Position-like stand-in via SimpleNamespace.

    The mapper only reads attributes, never executes ORM logic, so a
    SimpleNamespace is a safe stand-in for `Position` across the entire
    attribute surface used by `_position_to_item`.
    """
    defaults = {
        "id": 1,
        "node_type": "sandbox",
        "position_id": "pos-001",
        "strategy_id_tag": "00",
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "side": "LONG",
        "quantity": "0.50",
        "signed_qty": 0.5,
        "avg_px_open": 20000.5,
        "avg_px_close": None,
        "realized_pnl": 0.0,
        "unrealized_pnl": 10.2,
        "currency": "USDT",
        "entry_side": "BUY",
        "peak_qty": "0.50",
        "ts_opened": "2026-04-01T00:00:00Z",
        "ts_closed": None,
        "duration": None,
        "is_open": True,
        "event_count": 3,
        "updated_at": datetime(2026, 4, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPositionToItem:
    def test_basic_mapping(self):
        row = _make_position_row()
        item = _position_to_item(row)
        assert isinstance(item, PositionItem)
        assert item.id == 1
        assert item.node_type == "sandbox"
        assert item.position_id == "pos-001"
        assert item.instrument_id == "BTCUSDT-PERP.BINANCE"
        assert item.side == "LONG"
        assert item.quantity == "0.50"
        assert item.signed_qty == 0.5
        assert item.avg_px_open == 20000.5
        assert item.unrealized_pnl == 10.2
        assert item.is_open is True
        assert item.event_count == 3

    def test_updated_at_formatted_as_isoformat(self):
        row = _make_position_row(updated_at=datetime(2026, 4, 1, 12, 30, 0))
        item = _position_to_item(row)
        assert item.updated_at == "2026-04-01T12:30:00"

    def test_updated_at_none_passes_through(self):
        row = _make_position_row(updated_at=None)
        item = _position_to_item(row)
        assert item.updated_at is None

    def test_closed_position(self):
        row = _make_position_row(
            is_open=False,
            avg_px_close=21000.0,
            realized_pnl=500.0,
            ts_closed="2026-04-02T00:00:00Z",
            duration="24h",
        )
        item = _position_to_item(row)
        assert item.is_open is False
        assert item.avg_px_close == 21000.0
        assert item.realized_pnl == 500.0
        assert item.duration == "24h"

    def test_optional_fields_preserve_none(self):
        row = _make_position_row(
            avg_px_open=None,
            avg_px_close=None,
            realized_pnl=None,
            unrealized_pnl=None,
            currency=None,
            entry_side=None,
            peak_qty=None,
            ts_opened=None,
            ts_closed=None,
            duration=None,
        )
        item = _position_to_item(row)
        for attr in ("avg_px_open", "avg_px_close", "realized_pnl",
                     "unrealized_pnl", "currency", "entry_side", "peak_qty",
                     "ts_opened", "ts_closed", "duration"):
            assert getattr(item, attr) is None


# ---------------------------------------------------------------------------
# _fill_to_item
# ---------------------------------------------------------------------------


def _make_fill_row(**overrides):
    defaults = {
        "id": 1,
        "node_type": "live",
        "trade_id": "T-001",
        "position_id": "pos-123",
        "client_order_id": "O-000-1",
        "venue_order_id": "V-5555",
        "strategy_id_tag": "01",
        "instrument_id": "ETHUSDT-PERP.BINANCE",
        "order_side": "SELL",
        "last_qty": "1.0",
        "last_px": "2500.5",
        "commission": "0.5 USDT",
        "liquidity_side": "TAKER",
        "ts_event": "2026-04-01T12:00:00Z",
        "created_at": datetime(2026, 4, 1, 12, 0, 1),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestFillToItem:
    def test_basic_mapping(self):
        row = _make_fill_row()
        item = _fill_to_item(row)
        assert isinstance(item, FillItem)
        assert item.id == 1
        assert item.trade_id == "T-001"
        assert item.client_order_id == "O-000-1"
        assert item.venue_order_id == "V-5555"
        assert item.order_side == "SELL"
        assert item.last_qty == "1.0"
        assert item.last_px == "2500.5"
        assert item.commission == "0.5 USDT"

    def test_created_at_isoformat(self):
        row = _make_fill_row(created_at=datetime(2026, 4, 1, 0, 0, 5))
        item = _fill_to_item(row)
        assert item.created_at == "2026-04-01T00:00:05"

    def test_created_at_none(self):
        row = _make_fill_row(created_at=None)
        item = _fill_to_item(row)
        assert item.created_at is None

    def test_optional_fields_preserve_none(self):
        row = _make_fill_row(
            position_id=None,
            venue_order_id=None,
            strategy_id_tag=None,
            commission=None,
            liquidity_side=None,
        )
        item = _fill_to_item(row)
        for attr in ("position_id", "venue_order_id", "strategy_id_tag",
                     "commission", "liquidity_side"):
            assert getattr(item, attr) is None
