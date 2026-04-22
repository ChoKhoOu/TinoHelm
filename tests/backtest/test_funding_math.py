"""Tests for tinohelm.backtest.funding_math — NT-free pure math.

This module is intentionally zero-dep: no nautilus_trader import, no I/O, no
fixtures beyond stdlib. All tests here must run in < 100ms combined.
"""
from __future__ import annotations

import sys

import pytest

from tinohelm.backtest.funding_math import (
    RECORD_COST_PRECISION,
    SIDE_LONG,
    SIDE_SHORT,
    SUMMARY_PER_SYMBOL_PRECISION,
    SUMMARY_TOTAL_PRECISION,
    advance_due_events,
    build_funding_record,
    compute_funding_cost,
    summarize_funding,
)


# ---------------------------------------------------------------------------
# Independence: the pure module must not pull in nautilus_trader.
# ---------------------------------------------------------------------------

class TestNtFreeIndependence:
    def test_module_does_not_import_nautilus_trader(self):
        import tinohelm.backtest.funding_math as mod

        # Resolve actual module source file — should not reference NT symbols.
        src = open(mod.__file__).read()
        assert "nautilus_trader" not in src
        assert "from nautilus" not in src

    def test_importing_funding_math_does_not_load_nautilus(self):
        # Snapshot whether NT was already imported (CI may preload it); assert
        # importing the pure module doesn't *add* nautilus_trader if absent.
        pre_has_nt = "nautilus_trader" in sys.modules
        # Fresh re-import of our module.
        sys.modules.pop("tinohelm.backtest.funding_math", None)
        import tinohelm.backtest.funding_math  # noqa: F401

        post_has_nt = "nautilus_trader" in sys.modules
        # If NT wasn't already loaded, we must not have loaded it.
        if not pre_has_nt:
            assert not post_has_nt


# ---------------------------------------------------------------------------
# compute_funding_cost — the core formula
# ---------------------------------------------------------------------------

class TestComputeFundingCost:
    def test_long_positive_rate_pays(self):
        # 1 BTC @ 50000 with +0.01% → pays 5 USDT
        cost = compute_funding_cost(
            side=SIDE_LONG, quantity=1.0, mark_price=50_000.0, rate=0.0001,
        )
        assert cost == pytest.approx(5.0)

    def test_long_negative_rate_receives(self):
        # Long with negative rate → negative cost (credit)
        cost = compute_funding_cost(
            side=SIDE_LONG, quantity=1.0, mark_price=50_000.0, rate=-0.0001,
        )
        assert cost == pytest.approx(-5.0)

    def test_short_positive_rate_receives(self):
        # Short with positive rate → negative cost (credit)
        cost = compute_funding_cost(
            side=SIDE_SHORT, quantity=1.0, mark_price=50_000.0, rate=0.0001,
        )
        assert cost == pytest.approx(-5.0)

    def test_short_negative_rate_pays(self):
        cost = compute_funding_cost(
            side=SIDE_SHORT, quantity=1.0, mark_price=50_000.0, rate=-0.0001,
        )
        assert cost == pytest.approx(5.0)

    def test_sign_symmetry_long_vs_short(self):
        # For any (qty, price, rate), long and short always produce mirror
        # costs. This is the invariant the whole module is built on.
        for qty, price, rate in [
            (0.5, 30_000.0, 0.0003),
            (2.0, 1800.5, -0.0002),
            (10.0, 0.000_123, 0.01),
        ]:
            long_cost = compute_funding_cost(
                side=SIDE_LONG, quantity=qty, mark_price=price, rate=rate,
            )
            short_cost = compute_funding_cost(
                side=SIDE_SHORT, quantity=qty, mark_price=price, rate=rate,
            )
            assert long_cost == pytest.approx(-short_cost)

    def test_zero_quantity_is_zero(self):
        assert compute_funding_cost(
            side=SIDE_LONG, quantity=0.0, mark_price=50_000.0, rate=0.0001,
        ) == 0.0

    def test_zero_rate_is_zero(self):
        assert compute_funding_cost(
            side=SIDE_LONG, quantity=1.0, mark_price=50_000.0, rate=0.0,
        ) == 0.0

    def test_zero_mark_price_is_zero(self):
        assert compute_funding_cost(
            side=SIDE_SHORT, quantity=1.0, mark_price=0.0, rate=0.0001,
        ) == 0.0

    def test_unknown_side_raises_value_error(self):
        # Behaviour change vs pre-extraction: previously FLAT/"" silently
        # applied the SHORT branch. We now fail loudly because production
        # only ever passes LONG/SHORT.
        with pytest.raises(ValueError, match="unknown side"):
            compute_funding_cost(
                side="FLAT", quantity=1.0, mark_price=100.0, rate=0.01,
            )

    def test_unknown_side_error_includes_expected_names(self):
        with pytest.raises(ValueError) as exc_info:
            compute_funding_cost(
                side="neutral", quantity=1.0, mark_price=100.0, rate=0.01,
            )
        msg = str(exc_info.value)
        assert "LONG" in msg
        assert "SHORT" in msg

    def test_side_is_case_sensitive(self):
        # NT's PositionSide.name returns exactly "LONG"/"SHORT" — anything
        # else is a contract violation, not a case-fold opportunity.
        with pytest.raises(ValueError):
            compute_funding_cost(
                side="long", quantity=1.0, mark_price=100.0, rate=0.01,
            )

    def test_numeric_coercion_from_decimal_like(self):
        # Strings/Decimals get coerced via float() — str-wrapped floats work.
        cost = compute_funding_cost(
            side=SIDE_LONG, quantity="1.5", mark_price="200", rate="0.0001",
        )
        assert cost == pytest.approx(0.03)

    def test_keyword_only(self):
        # Positional args should fail: the formula is safety-critical, so the
        # call site must be self-documenting.
        with pytest.raises(TypeError):
            compute_funding_cost(SIDE_LONG, 1.0, 100.0, 0.0001)


# ---------------------------------------------------------------------------
# build_funding_record — serialization shape
# ---------------------------------------------------------------------------

class TestBuildFundingRecord:
    def test_canonical_shape(self):
        rec = build_funding_record(
            timestamp_iso="2024-01-01T00:00:00+00:00",
            symbol="BTCUSDT-PERP.BINANCE",
            side=SIDE_LONG,
            quantity=1.5,
            mark_price=50_000.0,
            rate=0.0001,
            cost=7.5,
        )
        assert rec == {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "symbol": "BTCUSDT-PERP.BINANCE",
            "side": "LONG",
            "quantity": 1.5,
            "mark_price": 50_000.0,
            "funding_rate": 0.0001,
            "cost": 7.5,
        }

    def test_cost_rounded_to_6_dp(self):
        rec = build_funding_record(
            timestamp_iso="t", symbol="s", side=SIDE_LONG,
            quantity=1.0, mark_price=1.0, rate=0.0,
            cost=1.234_567_891_23,
        )
        assert rec["cost"] == round(1.234_567_891_23, RECORD_COST_PRECISION)
        assert rec["cost"] == 1.234568

    def test_precision_constant_is_6(self):
        # Pins the precision so refactors can't silently change the shape.
        assert RECORD_COST_PRECISION == 6

    def test_all_numeric_fields_are_floats(self):
        # msgspec/json-friendly — no Decimal, no np.float
        rec = build_funding_record(
            timestamp_iso="t", symbol="s", side=SIDE_LONG,
            quantity="1.5", mark_price="100", rate="0.01", cost="0.5",
        )
        for k in ("quantity", "mark_price", "funding_rate", "cost"):
            assert isinstance(rec[k], float), f"{k} not float: {type(rec[k])}"

    def test_key_set_is_stable(self):
        # Any new field added must come with a deliberate schema bump.
        rec = build_funding_record(
            timestamp_iso="t", symbol="s", side=SIDE_LONG,
            quantity=1.0, mark_price=1.0, rate=0.0, cost=0.0,
        )
        assert set(rec.keys()) == {
            "timestamp", "symbol", "side", "quantity",
            "mark_price", "funding_rate", "cost",
        }

    def test_keyword_only_call(self):
        with pytest.raises(TypeError):
            build_funding_record(
                "t", "s", "LONG", 1.0, 1.0, 0.0, 0.0,  # noqa: E501
            )

    def test_negative_cost_preserved(self):
        rec = build_funding_record(
            timestamp_iso="t", symbol="s", side=SIDE_SHORT,
            quantity=1.0, mark_price=100.0, rate=0.0001, cost=-0.01,
        )
        assert rec["cost"] == -0.01


# ---------------------------------------------------------------------------
# advance_due_events — cursor walk
# ---------------------------------------------------------------------------

def _ev(ts_ns: int) -> dict:
    return {"timestamp_ns": ts_ns, "marker": ts_ns}


class TestAdvanceDueEvents:
    def test_empty_events(self):
        due, idx = advance_due_events([], current_ns=1000, next_idx=0)
        assert due == []
        assert idx == 0

    def test_cursor_past_end(self):
        events = [_ev(100), _ev(200)]
        due, idx = advance_due_events(events, current_ns=999, next_idx=2)
        assert due == []
        assert idx == 2

    def test_no_events_due_yet(self):
        events = [_ev(500), _ev(600), _ev(700)]
        due, idx = advance_due_events(events, current_ns=400, next_idx=0)
        assert due == []
        assert idx == 0

    def test_exact_timestamp_match_is_due(self):
        # ts == current_ns is inclusive — mirrors the original `<=` semantics.
        events = [_ev(1000)]
        due, idx = advance_due_events(events, current_ns=1000, next_idx=0)
        assert due == [events[0]]
        assert idx == 1

    def test_all_events_due(self):
        events = [_ev(100), _ev(200), _ev(300)]
        due, idx = advance_due_events(events, current_ns=999, next_idx=0)
        assert due == events
        assert idx == 3

    def test_partial_drain(self):
        events = [_ev(100), _ev(200), _ev(300), _ev(400)]
        due, idx = advance_due_events(events, current_ns=250, next_idx=0)
        assert [e["marker"] for e in due] == [100, 200]
        assert idx == 2

    def test_resume_from_cursor(self):
        events = [_ev(100), _ev(200), _ev(300)]
        # First pass takes only the first event.
        due1, idx1 = advance_due_events(events, current_ns=150, next_idx=0)
        assert [e["marker"] for e in due1] == [100]
        assert idx1 == 1
        # Second pass picks up from cursor and takes the remaining two.
        due2, idx2 = advance_due_events(events, current_ns=999, next_idx=idx1)
        assert [e["marker"] for e in due2] == [200, 300]
        assert idx2 == 3

    def test_cursor_idempotent_when_nothing_new(self):
        events = [_ev(100), _ev(200)]
        due1, idx1 = advance_due_events(events, current_ns=50, next_idx=0)
        due2, idx2 = advance_due_events(events, current_ns=50, next_idx=idx1)
        assert due1 == due2 == []
        assert idx1 == idx2 == 0

    def test_returns_fresh_slice_not_shared_reference(self):
        # The returned list is a slice — mutating it must not corrupt the
        # source event list held by the tracker.
        events = [_ev(100), _ev(200)]
        due, _ = advance_due_events(events, current_ns=999, next_idx=0)
        due.append(_ev(999))
        assert len(events) == 2  # unchanged


# ---------------------------------------------------------------------------
# summarize_funding — get_results() shape
# ---------------------------------------------------------------------------

class TestSummarizeFunding:
    def test_empty_totals(self):
        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={},
            funding_records=[],
        )
        assert out == {
            "total_funding_cost": 0.0,
            "funding_event_count": 0,
            "per_symbol_funding": {},
            "funding_records": [],
        }

    def test_total_rounded_to_4_dp(self):
        out = summarize_funding(
            total_funding_cost=1.234_567_8,
            per_symbol_cost={},
            funding_records=[],
        )
        assert out["total_funding_cost"] == round(
            1.234_567_8, SUMMARY_TOTAL_PRECISION,
        )
        assert out["total_funding_cost"] == 1.2346

    def test_per_symbol_rounded_to_4_dp(self):
        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={
                "BTC.BINANCE": 1.234_567_8,
                "ETH.BINANCE": -0.999_999_9,
            },
            funding_records=[],
        )
        assert out["per_symbol_funding"] == {
            "BTC.BINANCE": 1.2346,
            "ETH.BINANCE": -1.0,
        }

    def test_precision_constants_pinned(self):
        assert SUMMARY_TOTAL_PRECISION == 4
        assert SUMMARY_PER_SYMBOL_PRECISION == 4

    def test_records_count_matches_len(self):
        records = [{"i": i} for i in range(7)]
        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={},
            funding_records=records,
        )
        assert out["funding_event_count"] == 7
        assert out["funding_records"] == records

    def test_iterable_consumed_only_once(self):
        # A generator is a valid input — the helper materialises it into a
        # list exactly once. The count and list must agree.
        def gen():
            yield {"a": 1}
            yield {"b": 2}

        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={},
            funding_records=gen(),
        )
        assert out["funding_event_count"] == 2
        assert len(out["funding_records"]) == 2

    def test_records_pass_through_unchanged(self):
        # Per-record "cost" is already rounded upstream by build_funding_record
        # — summarize_funding must NOT re-round (would double-round).
        record = {
            "timestamp": "t",
            "symbol": "s",
            "cost": 0.123_456,  # already 6-dp
        }
        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={},
            funding_records=[record],
        )
        assert out["funding_records"][0]["cost"] == 0.123_456

    def test_result_key_set_is_stable(self):
        # Pin the 4-key schema so the extraction pipeline contract can't
        # drift silently.
        out = summarize_funding(
            total_funding_cost=0.0,
            per_symbol_cost={},
            funding_records=[],
        )
        assert set(out.keys()) == {
            "total_funding_cost",
            "funding_event_count",
            "per_symbol_funding",
            "funding_records",
        }

    def test_keyword_only(self):
        with pytest.raises(TypeError):
            summarize_funding(0.0, {}, [])


# ---------------------------------------------------------------------------
# End-to-end composition: the three helpers combined mirror one tracker tick.
# ---------------------------------------------------------------------------

class TestEndToEndFundingMath:
    def test_full_cycle_mimics_tracker(self):
        # 3 funding events across 2 symbols, a mix of long/short positions.
        events = [
            {"timestamp_ns": 1_000, "timestamp_iso": "t1",
             "symbol": "BTC.BINANCE", "rate": 0.0001, "mark_price": 50_000.0},
            {"timestamp_ns": 2_000, "timestamp_iso": "t2",
             "symbol": "ETH.BINANCE", "rate": -0.0002, "mark_price": 2_000.0},
            {"timestamp_ns": 3_000, "timestamp_iso": "t3",
             "symbol": "BTC.BINANCE", "rate": 0.0003, "mark_price": 51_000.0},
        ]
        # Fake positions by symbol.
        positions = {
            "BTC.BINANCE": ("LONG", 0.1),
            "ETH.BINANCE": ("SHORT", 5.0),
        }

        total = 0.0
        per_symbol: dict[str, float] = {}
        records: list[dict] = []

        due, idx = advance_due_events(events, current_ns=9_999, next_idx=0)
        assert idx == 3

        for ev in due:
            side, qty = positions[ev["symbol"]]
            cost = compute_funding_cost(
                side=side,
                quantity=qty,
                mark_price=ev["mark_price"],
                rate=ev["rate"],
            )
            total += cost
            per_symbol[ev["symbol"]] = per_symbol.get(ev["symbol"], 0.0) + cost
            records.append(build_funding_record(
                timestamp_iso=ev["timestamp_iso"],
                symbol=ev["symbol"],
                side=side,
                quantity=qty,
                mark_price=ev["mark_price"],
                rate=ev["rate"],
                cost=cost,
            ))

        out = summarize_funding(
            total_funding_cost=total,
            per_symbol_cost=per_symbol,
            funding_records=records,
        )

        # Event 1: LONG 0.1 BTC, 50000, +0.0001  = +0.5
        # Event 2: SHORT 5 ETH, 2000, -0.0002    = +2.0  (short + neg → pays)
        # Event 3: LONG 0.1 BTC, 51000, +0.0003 = +1.53
        assert out["total_funding_cost"] == pytest.approx(4.03)
        assert out["funding_event_count"] == 3
        assert out["per_symbol_funding"]["BTC.BINANCE"] == pytest.approx(2.03)
        assert out["per_symbol_funding"]["ETH.BINANCE"] == pytest.approx(2.0)
        assert [r["side"] for r in out["funding_records"]] == [
            "LONG", "SHORT", "LONG",
        ]
