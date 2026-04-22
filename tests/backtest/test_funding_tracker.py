"""Tests for the funding tracker bridge — NT Cython Actor aside.

``_FundingCostTracker`` inherits from NT's Cython ``Actor``, which can't be
instantiated or have its attributes written outside a live kernel. So we
test the bridge layer two ways:

  1. Exercise ``apply_funding_event`` (the pure helper the tracker delegates
     to) with fake position objects — this is the whole accumulation path.
  2. Assert the tracker class correctly wires ``_apply_funding`` /
     ``on_bar`` / ``get_results`` to the pure layer, by pulling those three
     methods off the class and calling them against a dict-state stand-in.

This mirrors the stub approach in ``tests/actors/test_risk_guard.py``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tinohelm.backtest.funding_math import apply_funding_event


def _make_pos(instrument_id: str, side_name: str, qty: float) -> SimpleNamespace:
    """Lightweight stand-in for an NT Position object."""
    return SimpleNamespace(
        instrument_id=instrument_id,
        side=SimpleNamespace(name=side_name),
        quantity=qty,
    )


# ---------------------------------------------------------------------------
# apply_funding_event — the whole per-tick accumulation path
# ---------------------------------------------------------------------------

class TestApplyFundingEvent:
    def _state(self):
        return {"total": 0.0, "per_symbol": {}, "records": []}

    def test_long_pays_positive_rate(self):
        s = self._state()
        total, per_sym, records = apply_funding_event(
            {
                "symbol": "BTC.BINANCE",
                "rate": 0.0001,
                "mark_price": 50_000.0,
                "timestamp_iso": "2024-01-01T00:00:00+00:00",
            },
            [_make_pos("BTC.BINANCE", "LONG", 1.0)],
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        assert total == pytest.approx(5.0)
        assert per_sym == {"BTC.BINANCE": pytest.approx(5.0)}
        assert len(records) == 1
        assert records[0]["side"] == "LONG"
        assert records[0]["cost"] == 5.0

    def test_short_receives_positive_rate(self):
        s = self._state()
        total, _, records = apply_funding_event(
            {"symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0, "timestamp_iso": "t"},
            [_make_pos("BTC.BINANCE", "SHORT", 1.0)],
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        assert total == pytest.approx(-5.0)
        assert records[0]["cost"] == -5.0

    def test_non_matching_symbol_is_skipped(self):
        s = self._state()
        total, per_sym, records = apply_funding_event(
            {"symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0, "timestamp_iso": "t"},
            [_make_pos("ETH.BINANCE", "LONG", 10.0)],  # different instrument
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        assert total == 0.0
        assert per_sym == {}
        assert records == []

    def test_multiple_positions_same_symbol_all_accrue(self):
        s = self._state()
        total, _, records = apply_funding_event(
            {"symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0, "timestamp_iso": "t"},
            [
                _make_pos("BTC.BINANCE", "LONG", 0.5),
                _make_pos("BTC.BINANCE", "LONG", 0.3),
            ],
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        # 0.5 * 50000 * 0.0001 + 0.3 * 50000 * 0.0001 = 2.5 + 1.5 = 4.0
        assert total == pytest.approx(4.0)
        assert len(records) == 2

    def test_mixed_long_and_short_on_same_symbol(self):
        # Hedged book: long + short of equal size → net funding = 0.
        s = self._state()
        total, per_sym, records = apply_funding_event(
            {"symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0, "timestamp_iso": "t"},
            [
                _make_pos("BTC.BINANCE", "LONG", 1.0),
                _make_pos("BTC.BINANCE", "SHORT", 1.0),
            ],
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        assert total == pytest.approx(0.0)
        # The per-symbol tally nets to zero, but both records are captured.
        assert per_sym["BTC.BINANCE"] == pytest.approx(0.0)
        assert len(records) == 2

    def test_no_open_positions_is_noop(self):
        s = self._state()
        total, per_sym, records = apply_funding_event(
            {"symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0, "timestamp_iso": "t"},
            [],
            total_cost=s["total"],
            per_symbol_cost=s["per_symbol"],
            records=s["records"],
        )
        assert total == 0.0
        assert per_sym == {}
        assert records == []

    def test_mutates_in_place(self):
        # Callers that hold a reference to the dict/list see the updates.
        per_sym: dict[str, float] = {}
        records: list[dict] = []
        apply_funding_event(
            {"symbol": "X", "rate": 0.0001, "mark_price": 100.0, "timestamp_iso": "t"},
            [_make_pos("X", "LONG", 1.0)],
            total_cost=0.0,
            per_symbol_cost=per_sym,
            records=records,
        )
        assert per_sym == {"X": pytest.approx(0.01)}
        assert len(records) == 1

    def test_existing_per_symbol_cost_accumulates(self):
        # Calling the helper twice for the same symbol must fold into the
        # running total, not overwrite.
        per_sym = {"X": 0.5}
        records: list[dict] = []
        total, per_sym2, _ = apply_funding_event(
            {"symbol": "X", "rate": 0.0001, "mark_price": 100.0, "timestamp_iso": "t"},
            [_make_pos("X", "LONG", 1.0)],
            total_cost=10.0,
            per_symbol_cost=per_sym,
            records=records,
        )
        assert per_sym2["X"] == pytest.approx(0.51)
        assert total == pytest.approx(10.01)

    def test_returns_same_dict_and_list_references(self):
        # The functional return is for convenience — it must NOT be a deep
        # copy, otherwise the tracker's in-place updates and the returned
        # state would diverge.
        per_sym: dict[str, float] = {}
        records: list[dict] = []
        _, per_sym_out, records_out = apply_funding_event(
            {"symbol": "X", "rate": 0.0001, "mark_price": 100.0, "timestamp_iso": "t"},
            [_make_pos("X", "LONG", 1.0)],
            total_cost=0.0,
            per_symbol_cost=per_sym,
            records=records,
        )
        assert per_sym_out is per_sym
        assert records_out is records


# ---------------------------------------------------------------------------
# Wiring: the tracker class delegates on_bar/get_results to the pure layer.
# ---------------------------------------------------------------------------

class TestTrackerWiring:
    """Exercises _FundingCostTracker methods against a dict stand-in `self`.

    We can't instantiate the tracker (NT Cython Actor) but we *can* grab the
    unbound methods off the class and call them with any object that has
    the same instance attributes — proving the bridge correctly calls
    ``apply_funding_event`` / ``advance_due_events`` / ``summarize_funding``.
    """

    def _make_self(self):
        """Stand-in that mirrors the tracker's instance attributes exactly."""
        from tinohelm.backtest.funding import _FundingCostTracker

        s = SimpleNamespace(
            _total_funding_cost=0.0,
            _funding_records=[],
            _per_symbol_cost={},
            _next_event_idx=0,
            cache=MagicMock(positions_open=MagicMock(return_value=[])),
        )
        # Bind the bridge method directly — on_bar calls self._apply_funding.
        s._apply_funding = lambda ev: _FundingCostTracker._apply_funding(s, ev)
        return s

    def test_on_bar_advances_cursor_via_pure_layer(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        s.cache.positions_open.return_value = [_make_pos("X", "LONG", 1.0)]
        _FundingCostTracker._funding_events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t1",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
            {"timestamp_ns": 2_000, "timestamp_iso": "t2",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
        ]
        try:
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=1_500))
            assert s._next_event_idx == 1
            assert len(s._funding_records) == 1
            # A later bar drains the next event.
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=2_500))
            assert s._next_event_idx == 2
            assert len(s._funding_records) == 2
        finally:
            _FundingCostTracker._funding_events = []

    def test_on_bar_inclusive_timestamp_boundary(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        s.cache.positions_open.return_value = [_make_pos("X", "LONG", 1.0)]
        _FundingCostTracker._funding_events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
        ]
        try:
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=1_000))
            assert s._next_event_idx == 1
        finally:
            _FundingCostTracker._funding_events = []

    def test_on_bar_no_due_events_is_noop(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        _FundingCostTracker._funding_events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
        ]
        try:
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=500))
            assert s._next_event_idx == 0
            assert s._total_funding_cost == 0.0
        finally:
            _FundingCostTracker._funding_events = []

    def test_on_bar_replay_same_ts_is_idempotent(self):
        # Two bars at the same timestamp shouldn't double-charge.
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        s.cache.positions_open.return_value = [_make_pos("X", "LONG", 1.0)]
        _FundingCostTracker._funding_events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
        ]
        try:
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=1_500))
            total_after_first = s._total_funding_cost
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=1_500))
            assert s._total_funding_cost == total_after_first
        finally:
            _FundingCostTracker._funding_events = []

    def test_on_bar_drains_multiple_due_events(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        s.cache.positions_open.return_value = [_make_pos("X", "LONG", 1.0)]
        _FundingCostTracker._funding_events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t1",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
            {"timestamp_ns": 2_000, "timestamp_iso": "t2",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
            {"timestamp_ns": 3_000, "timestamp_iso": "t3",
             "symbol": "X", "rate": 0.0001, "mark_price": 100.0},
        ]
        try:
            _FundingCostTracker.on_bar(s, SimpleNamespace(ts_init=9_999))
            assert s._next_event_idx == 3
            assert len(s._funding_records) == 3
        finally:
            _FundingCostTracker._funding_events = []

    def test_get_results_delegates_to_summarize(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        s._total_funding_cost = 1.234_567_8
        s._per_symbol_cost = {"X.BINANCE": 1.234_567_8}
        s._funding_records = [{"timestamp": "t", "symbol": "X", "cost": 1.234_568}]

        out = _FundingCostTracker.get_results(s)

        # 4-dp rounding from summarize_funding
        assert out["total_funding_cost"] == 1.2346
        assert out["per_symbol_funding"]["X.BINANCE"] == 1.2346
        assert out["funding_event_count"] == 1
        assert set(out.keys()) == {
            "total_funding_cost", "funding_event_count",
            "per_symbol_funding", "funding_records",
        }

    def test_get_results_empty_returns_zero_totals(self):
        from tinohelm.backtest.funding import _FundingCostTracker

        s = self._make_self()
        out = _FundingCostTracker.get_results(s)
        assert out == {
            "total_funding_cost": 0.0,
            "funding_event_count": 0,
            "per_symbol_funding": {},
            "funding_records": [],
        }

    def test_config_default_component_id(self):
        from tinohelm.backtest.funding import _FundingCostTrackerConfig

        cfg = _FundingCostTrackerConfig()
        assert cfg.component_id == "FundingCostTracker-001"

    def test_config_is_frozen(self):
        from tinohelm.backtest.funding import _FundingCostTrackerConfig

        cfg = _FundingCostTrackerConfig()
        with pytest.raises(AttributeError):
            cfg.component_id = "mutated"
