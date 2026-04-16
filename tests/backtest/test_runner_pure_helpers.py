"""Tests for ``tinohelm.backtest.runner_helpers`` — all NT-free.

The helpers in this module are deliberately NautilusTrader-independent
so they can be tested in environments where the NT wheel isn't
installed (e.g. lean CI jobs that only need to validate pure logic).

See ``test_runner_helpers.py`` for helpers that *do* require NT
(fee parsing, fill models, latency models) — those live on
:class:`BacktestRunner` itself and are gated behind an import guard.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tinohelm.backtest.runner_helpers import (
    TIMEFRAME_PRIORITY,
    assemble_funding_events,
    build_composite_bar_type_str,
    build_progress_payload,
    candidate_source_intervals,
    compute_bar_progress_fields,
    compute_warmup_adjusted_start,
    extract_benchmark_daily_closes,
    interval_to_minutes,
    resolve_symbols_intervals,
)


# ────────────────────────────────────────────────────────────────────
# interval_to_minutes
# ────────────────────────────────────────────────────────────────────


class TestIntervalToMinutes:
    """Locks the behavior the existing runner module relied on."""

    @pytest.mark.parametrize("s, expected", [
        ("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30),
        ("1h", 60), ("2h", 120), ("4h", 240), ("12h", 720),
        ("1d", 1440),
        ("30s", 1), ("60s", 1), ("120s", 2),
    ])
    def test_happy_paths(self, s, expected):
        assert interval_to_minutes(s) == expected

    @pytest.mark.parametrize("s", ["invalid", "", "5x", "m", "h", "1"])
    def test_invalid_returns_zero(self, s):
        assert interval_to_minutes(s) == 0

    @pytest.mark.parametrize("s, expected", [
        ("5M", 5), ("1H", 60), ("1D", 1440),
    ])
    def test_case_insensitive(self, s, expected):
        assert interval_to_minutes(s) == expected


# ────────────────────────────────────────────────────────────────────
# compute_warmup_adjusted_start
# ────────────────────────────────────────────────────────────────────


class TestComputeWarmupAdjustedStart:
    """Warmup should rewind the data-loading window by N bars."""

    _BASE = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_happy_path_minute(self):
        # 10 bars of 5m = 50 minutes
        out = compute_warmup_adjusted_start(self._BASE, "5m", 10)
        assert out == datetime(2026, 1, 15, 11, 10, 0, tzinfo=timezone.utc)

    def test_happy_path_hour(self):
        # 4 bars of 1h = 240 minutes
        out = compute_warmup_adjusted_start(self._BASE, "1h", 4)
        assert out == datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)

    def test_happy_path_day(self):
        out = compute_warmup_adjusted_start(self._BASE, "1d", 3)
        assert out == datetime(2026, 1, 12, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_start_returns_none(self):
        assert compute_warmup_adjusted_start(None, "5m", 10) is None

    def test_empty_interval_returns_start(self):
        assert compute_warmup_adjusted_start(self._BASE, "", 10) is self._BASE

    def test_zero_warmup_returns_start(self):
        assert compute_warmup_adjusted_start(self._BASE, "5m", 0) is self._BASE

    def test_negative_warmup_returns_start(self):
        assert compute_warmup_adjusted_start(self._BASE, "5m", -5) is self._BASE

    def test_none_warmup_returns_start(self):
        assert compute_warmup_adjusted_start(self._BASE, "5m", None) is self._BASE

    def test_invalid_interval_returns_start(self):
        # Unknown unit → 0 minutes → no adjustment
        assert compute_warmup_adjusted_start(self._BASE, "5x", 10) is self._BASE


# ────────────────────────────────────────────────────────────────────
# resolve_symbols_intervals
# ────────────────────────────────────────────────────────────────────


class TestResolveSymbolsIntervals:
    """Runner-level inputs win; bundle-level fill the gaps."""

    def test_current_symbols_win_over_bundle(self):
        syms, ivls = resolve_symbols_intervals(
            ["BTC"], "1h", ["ETH", "SOL"], ["5m"],
        )
        assert syms == ["ETH", "SOL"]
        assert ivls == ["5m"]

    def test_bundle_fallback_when_current_empty(self):
        syms, ivls = resolve_symbols_intervals(
            ["BTC"], "1h", [], [],
        )
        assert syms == ["BTC"]
        assert ivls == ["1h"]

    def test_partial_fallback_symbols_only(self):
        syms, ivls = resolve_symbols_intervals(
            ["BTC"], "1h", [], ["5m", "15m"],
        )
        assert syms == ["BTC"]
        assert ivls == ["5m", "15m"]

    def test_partial_fallback_intervals_only(self):
        syms, ivls = resolve_symbols_intervals(
            ["BTC"], "1h", ["ETH"], [],
        )
        assert syms == ["ETH"]
        assert ivls == ["1h"]

    def test_all_empty(self):
        syms, ivls = resolve_symbols_intervals(None, None, [], [])
        assert syms == []
        assert ivls == []

    def test_bundle_none_handled(self):
        syms, ivls = resolve_symbols_intervals(None, "", [], [])
        assert syms == []
        assert ivls == []

    def test_result_is_fresh_list_not_reference(self):
        """Callers mutate the returned list; it must not share state."""
        src_syms = ["BTC"]
        syms, _ = resolve_symbols_intervals(src_syms, "1h", [], [])
        syms.append("ETH")
        assert src_syms == ["BTC"]


# ────────────────────────────────────────────────────────────────────
# candidate_source_intervals
# ────────────────────────────────────────────────────────────────────


class TestCandidateSourceIntervals:
    """The list of timeframes strictly below a target, in priority order."""

    def test_5m_target(self):
        assert candidate_source_intervals("5m") == ["1m", "3m"]

    def test_1h_target(self):
        assert candidate_source_intervals("1h") == [
            "1m", "3m", "5m", "15m", "30m",
        ]

    def test_1m_target_empty(self):
        # 1m is the lowest — nothing below it
        assert candidate_source_intervals("1m") == []

    def test_1d_target_has_all_others(self):
        out = candidate_source_intervals("1d")
        assert out == list(TIMEFRAME_PRIORITY[:-1])

    def test_unknown_target_empty(self):
        assert candidate_source_intervals("5x") == []

    def test_empty_target_empty(self):
        assert candidate_source_intervals("") == []

    def test_order_preserved(self):
        # Callers iterate in priority order and pick the first that has data,
        # so ordering matters.
        assert candidate_source_intervals("4h") == [
            "1m", "3m", "5m", "15m", "30m", "1h", "2h",
        ]

    def test_custom_priority_tuple(self):
        priority = ("a", "b", "c", "d")
        assert candidate_source_intervals("c", priority) == ["a", "b"]


# ────────────────────────────────────────────────────────────────────
# build_composite_bar_type_str
# ────────────────────────────────────────────────────────────────────


class TestBuildCompositeBarTypeStr:
    """NT composite bar-type string shape."""

    _INTERVAL_MAP = {
        "1m": "1-MINUTE", "5m": "5-MINUTE", "15m": "15-MINUTE", "1h": "1-HOUR",
    }

    def test_1m_to_5m(self):
        out = build_composite_bar_type_str(
            "BTCUSDT-PERP.BINANCE", "1m", "5m", self._INTERVAL_MAP,
        )
        assert out == "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"

    def test_5m_to_1h(self):
        out = build_composite_bar_type_str(
            "ETHUSDT-PERP.BINANCE", "5m", "1h", self._INTERVAL_MAP,
        )
        assert out == "ETHUSDT-PERP.BINANCE-1-HOUR-LAST-INTERNAL@5-MINUTE-EXTERNAL"

    def test_unknown_source_falls_back_to_1m(self):
        out = build_composite_bar_type_str(
            "X", "unknown", "5m", self._INTERVAL_MAP,
        )
        assert out == "X-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"

    def test_unknown_target_falls_back_to_1m(self):
        out = build_composite_bar_type_str(
            "X", "1m", "unknown", self._INTERVAL_MAP,
        )
        assert out == "X-1-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"

    def test_empty_interval_map_both_fallback(self):
        out = build_composite_bar_type_str("Y", "5m", "1h", {})
        assert out == "Y-1-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"


# ────────────────────────────────────────────────────────────────────
# extract_benchmark_daily_closes
# ────────────────────────────────────────────────────────────────────


class TestExtractBenchmarkDailyCloses:
    """Collapse (ts_ns, close) sequence into {YYYY-MM-DD: last_close}."""

    @staticmethod
    def _ts(year, month, day, hour=0, minute=0):
        return int(datetime(
            year, month, day, hour, minute, tzinfo=timezone.utc,
        ).timestamp() * 1e9)

    def test_empty(self):
        assert extract_benchmark_daily_closes([]) == {}

    def test_single_bar(self):
        out = extract_benchmark_daily_closes([(self._ts(2026, 1, 15), 50000.0)])
        assert out == {"2026-01-15": 50000.0}

    def test_last_bar_per_day_wins(self):
        # Three bars on Jan 15, one on Jan 16 — closing price is the last bar.
        out = extract_benchmark_daily_closes([
            (self._ts(2026, 1, 15, 9), 100.0),
            (self._ts(2026, 1, 15, 15), 110.0),
            (self._ts(2026, 1, 15, 23), 120.0),
            (self._ts(2026, 1, 16, 1), 130.0),
        ])
        assert out == {"2026-01-15": 120.0, "2026-01-16": 130.0}

    def test_spans_month_boundary(self):
        out = extract_benchmark_daily_closes([
            (self._ts(2026, 1, 31, 23), 100.0),
            (self._ts(2026, 2, 1, 0), 101.0),
        ])
        assert out == {"2026-01-31": 100.0, "2026-02-01": 101.0}

    def test_float_conversion(self):
        # Accepts anything castable to float; produces float outputs.
        out = extract_benchmark_daily_closes([(self._ts(2026, 1, 15), 42)])
        assert out == {"2026-01-15": 42.0}
        assert isinstance(list(out.values())[0], float)

    def test_generator_input(self):
        # extract.py passes a generator expression — must accept any iterable.
        gen = ((self._ts(2026, 1, 15), 100.0) for _ in range(1))
        assert extract_benchmark_daily_closes(gen) == {"2026-01-15": 100.0}


# ────────────────────────────────────────────────────────────────────
# compute_bar_progress_fields
# ────────────────────────────────────────────────────────────────────


class TestComputeBarProgressFields:
    """The 10-90 pct mapping and ETA/BPS arithmetic for bar phase."""

    def test_zero_total_bars_returns_floor(self):
        fields = compute_bar_progress_fields(100, 0, 10.0)
        assert fields == {
            "pct": 10, "elapsed_secs": 10.0,
            "eta_secs": None, "bars_per_sec": None,
        }

    def test_halfway_through_backtest(self):
        # 50% processed → pct = 50 (10 + 40). At 10s elapsed → 10s remaining.
        fields = compute_bar_progress_fields(5000, 10000, 10.0)
        assert fields["pct"] == 50
        assert fields["elapsed_secs"] == 10.0
        assert fields["eta_secs"] == 10.0
        assert fields["bars_per_sec"] == 500.0

    def test_pct_capped_at_90(self):
        # Even if bar_count exceeds total, pct cannot exceed 90
        fields = compute_bar_progress_fields(20000, 10000, 10.0)
        assert fields["pct"] == 90

    def test_start_of_run(self):
        # 10% floor at bar 0
        fields = compute_bar_progress_fields(0, 10000, 0.1)
        assert fields["pct"] == 10
        assert fields["bars_per_sec"] == 0.0

    def test_zero_elapsed_means_no_bps(self):
        fields = compute_bar_progress_fields(100, 10000, 0.0)
        assert fields["bars_per_sec"] is None
        # eta is still computable from pct and elapsed=0 → 0 * ratio = 0
        assert fields["eta_secs"] == 0.0

    def test_negative_elapsed_clamped_to_zero(self):
        # Guards against a pathological monotonic drift
        fields = compute_bar_progress_fields(100, 10000, -1.0)
        assert fields["elapsed_secs"] == 0.0
        assert fields["bars_per_sec"] is None

    def test_elapsed_rounded_to_one_decimal(self):
        fields = compute_bar_progress_fields(100, 10000, 1.23456)
        assert fields["elapsed_secs"] == 1.2


# ────────────────────────────────────────────────────────────────────
# build_progress_payload
# ────────────────────────────────────────────────────────────────────


class TestBuildProgressPayload:
    """The canonical backtest.progress event shape for the frontend."""

    _REQUIRED_KEYS = frozenset({
        "type", "run_id", "pct", "elapsed_secs", "eta_secs",
        "total_bars", "processed_bars", "bars_per_sec", "trades", "message",
    })

    def test_shape_minimal(self):
        p = build_progress_payload("run-1", pct=5, elapsed_secs=1.5)
        assert frozenset(p.keys()) == self._REQUIRED_KEYS
        assert p["type"] == "backtest.progress"
        assert p["run_id"] == "run-1"
        assert p["pct"] == 5
        assert p["elapsed_secs"] == 1.5
        # All unspecified fields default to None
        assert p["eta_secs"] is None
        assert p["total_bars"] is None
        assert p["processed_bars"] is None
        assert p["bars_per_sec"] is None
        assert p["trades"] is None
        assert p["message"] is None

    def test_shape_full(self):
        p = build_progress_payload(
            "run-42", pct=50, elapsed_secs=10.0, eta_secs=10.0,
            total_bars=10000, processed_bars=5000, bars_per_sec=500.0,
            message="running",
        )
        assert p["pct"] == 50
        assert p["eta_secs"] == 10.0
        assert p["total_bars"] == 10000
        assert p["processed_bars"] == 5000
        assert p["bars_per_sec"] == 500.0
        assert p["message"] == "running"

    def test_trades_always_present_and_none(self):
        # Legacy field that WS consumers look up unconditionally
        p = build_progress_payload("r", pct=0, elapsed_secs=0)
        assert "trades" in p
        assert p["trades"] is None

    def test_keys_are_stable_order_independent(self):
        # Equality of dicts is order-independent, but the key set matters
        a = build_progress_payload("r", pct=10, elapsed_secs=1.0)
        b = build_progress_payload("r", pct=10, elapsed_secs=1.0)
        assert a == b


# ────────────────────────────────────────────────────────────────────
# assemble_funding_events
# ────────────────────────────────────────────────────────────────────


class TestAssembleFundingEvents:
    """Per-symbol rate lists → sorted flat funding-event stream."""

    def test_empty(self):
        out = assemble_funding_events({}, {}, {})
        assert out == []

    def test_single_symbol_single_rate(self):
        rates = {
            "BTCUSDT-PERP": [
                {"funding_time_ms": 1_700_000_000_000,
                 "funding_rate": 0.0001,
                 "mark_price": 50000.0},
            ],
        }
        nt = {"BTCUSDT-PERP": "BTCUSDT-PERP.BINANCE"}
        mins = {"BTCUSDT-PERP": 480}  # 8h
        out = assemble_funding_events(rates, nt, mins)
        assert len(out) == 1
        ev = out[0]
        assert ev["symbol"] == "BTCUSDT-PERP.BINANCE"
        assert ev["rate"] == 0.0001
        assert ev["mark_price"] == 50000.0
        assert ev["timestamp_ns"] == 1_700_000_000_000 * 1_000_000
        assert ev["funding_interval_minutes"] == 480
        assert ev["timestamp_iso"].endswith("+00:00") or "T" in ev["timestamp_iso"]

    def test_zero_mark_price_dropped(self):
        rates = {
            "X": [
                {"funding_time_ms": 1000, "funding_rate": 0.0001, "mark_price": 0},
                {"funding_time_ms": 2000, "funding_rate": 0.0002, "mark_price": None},
                {"funding_time_ms": 3000, "funding_rate": 0.0003, "mark_price": 100.0},
            ],
        }
        nt = {"X": "X.BINANCE"}
        mins = {"X": 480}
        out = assemble_funding_events(rates, nt, mins)
        assert len(out) == 1
        assert out[0]["mark_price"] == 100.0

    def test_multi_symbol_sorted_by_timestamp(self):
        rates = {
            "A": [
                {"funding_time_ms": 3000, "funding_rate": 0.01, "mark_price": 100.0},
                {"funding_time_ms": 1000, "funding_rate": 0.02, "mark_price": 200.0},
            ],
            "B": [
                {"funding_time_ms": 2000, "funding_rate": 0.03, "mark_price": 300.0},
            ],
        }
        nt = {"A": "A.BINANCE", "B": "B.BINANCE"}
        mins = {"A": 480, "B": 240}
        out = assemble_funding_events(rates, nt, mins)
        assert [e["timestamp_ns"] for e in out] == [
            1000 * 1_000_000, 2000 * 1_000_000, 3000 * 1_000_000,
        ]
        assert [e["symbol"] for e in out] == [
            "A.BINANCE", "B.BINANCE", "A.BINANCE",
        ]

    def test_per_symbol_interval_minutes_honored(self):
        rates = {
            "A": [{"funding_time_ms": 1000, "funding_rate": 0.01, "mark_price": 1.0}],
            "B": [{"funding_time_ms": 2000, "funding_rate": 0.01, "mark_price": 1.0}],
        }
        nt = {"A": "A.BINANCE", "B": "B.BINANCE"}
        mins = {"A": 480, "B": 60}
        out = assemble_funding_events(rates, nt, mins)
        by_sym = {e["symbol"]: e["funding_interval_minutes"] for e in out}
        assert by_sym["A.BINANCE"] == 480
        assert by_sym["B.BINANCE"] == 60

    def test_default_interval_when_missing(self):
        rates = {"A": [
            {"funding_time_ms": 1000, "funding_rate": 0.01, "mark_price": 1.0},
        ]}
        nt = {"A": "A.BINANCE"}
        # Empty interval dict → default to 8h = 480 minutes
        out = assemble_funding_events(rates, nt, {})
        assert out[0]["funding_interval_minutes"] == 480

    def test_missing_nt_symbol_falls_back_to_key(self):
        # Defensive fallback: if the nt_symbols mapping is stale, the raw
        # symbol key is used so events are never silently discarded.
        rates = {"X": [
            {"funding_time_ms": 1000, "funding_rate": 0.01, "mark_price": 1.0},
        ]}
        out = assemble_funding_events(rates, {}, {})
        assert out[0]["symbol"] == "X"

    def test_timestamp_iso_is_utc(self):
        rates = {"A": [
            {"funding_time_ms": 0, "funding_rate": 0.0, "mark_price": 1.0},
        ]}
        out = assemble_funding_events(rates, {"A": "A"}, {"A": 480})
        # 0 ms epoch is 1970-01-01T00:00:00+00:00 UTC
        assert out[0]["timestamp_iso"].startswith("1970-01-01T00:00:00")
        assert "+00:00" in out[0]["timestamp_iso"]

    def test_empty_rates_for_symbol(self):
        rates = {"A": [], "B": [
            {"funding_time_ms": 1000, "funding_rate": 0.01, "mark_price": 1.0},
        ]}
        out = assemble_funding_events(
            rates, {"A": "A", "B": "B"}, {"A": 480, "B": 480},
        )
        assert len(out) == 1
        assert out[0]["symbol"] == "B"
