"""Tests for pure-computation section helpers in ``sections.py``.

Loads ``sections.py`` directly by file path (same pattern as
``test_rolling_metrics.py``) to avoid triggering
``tinohelm.backtest.result.__init__.py`` which re-exports from ``extract.py``
(that depends on ``nautilus_trader``, not installed in CI).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_RESULT_DIR = Path(__file__).resolve().parents[2] / "src" / "tinohelm" / "backtest" / "result"


def _load_from_path(name: str, filename: str):
    path = _RESULT_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-load statistics under the fully-qualified name that sections.py expects
# so its "from tinohelm.backtest.result.statistics import ..." resolves.
_stats = _load_from_path("tinohelm.backtest.result.statistics", "statistics.py")

# Create stub package nodes so the qualified import works
import types as _types
if "tinohelm" not in sys.modules:
    sys.modules["tinohelm"] = _types.ModuleType("tinohelm")
if "tinohelm.backtest" not in sys.modules:
    sys.modules["tinohelm.backtest"] = _types.ModuleType("tinohelm.backtest")
if "tinohelm.backtest.result" not in sys.modules:
    sys.modules["tinohelm.backtest.result"] = _types.ModuleType("tinohelm.backtest.result")

_sections = _load_from_path("_sections_under_test", "sections.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ts_ns(y: int, m: int, d: int, h: int = 0) -> int:
    dt = datetime(y, m, d, h, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)


@pytest.fixture
def simple_equity_curve():
    """Deterministic 5-day equity curve."""
    return [
        {"timestamp": "2025-01-01", "equity": 10100.0, "returns_pct": 1.0, "drawdown_pct": 0.0},
        {"timestamp": "2025-01-02", "equity": 10200.0, "returns_pct": 1.0, "drawdown_pct": 0.0},
        {"timestamp": "2025-01-03", "equity": 10050.0, "returns_pct": -1.5, "drawdown_pct": -1.47},
        {"timestamp": "2025-01-04", "equity": 10180.0, "returns_pct": 1.3, "drawdown_pct": -0.2},
        {"timestamp": "2025-01-05", "equity": 10300.0, "returns_pct": 1.18, "drawdown_pct": 0.0},
    ]


@pytest.fixture
def daily_rets_100():
    rng = np.random.default_rng(seed=7)
    return rng.normal(0.001, 0.01, size=100)


# ---------------------------------------------------------------------------
# build_equity_curve
# ---------------------------------------------------------------------------

class TestBuildEquityCurve:

    def test_empty_returns_empty(self):
        assert _sections.build_equity_curve([], 10000) == []

    def test_ignores_zero_or_missing_ts(self):
        out = _sections.build_equity_curve([(0, 100.0), (-1, 50.0)], 10000)
        assert out == []

    def test_aggregates_by_close_date(self):
        trades = [
            (_ts_ns(2025, 1, 1, 2), 100.0),
            (_ts_ns(2025, 1, 1, 20), -40.0),
            (_ts_ns(2025, 1, 2, 10), 30.0),
        ]
        out = _sections.build_equity_curve(trades, 10000)
        assert len(out) == 2
        assert out[0]["timestamp"] == "2025-01-01"
        # 100 - 40 = 60 → equity = 10060
        assert out[0]["equity"] == pytest.approx(10060.0)
        assert out[1]["equity"] == pytest.approx(10090.0)

    def test_drawdown_tracks_peak(self):
        trades = [
            (_ts_ns(2025, 1, 1), 500.0),   # 10500
            (_ts_ns(2025, 1, 2), 500.0),   # 11000 peak
            (_ts_ns(2025, 1, 3), -1000.0), # 10000 → dd vs peak 11000
        ]
        out = _sections.build_equity_curve(trades, 10000)
        assert out[1]["drawdown_pct"] == pytest.approx(0.0, abs=1e-6)
        assert out[2]["drawdown_pct"] < 0

    def test_returns_pct_relative_to_starting_balance(self):
        trades = [(_ts_ns(2025, 1, 1), 1000.0)]
        out = _sections.build_equity_curve(trades, 10000)
        assert out[0]["returns_pct"] == pytest.approx(10.0)

    def test_downsample_caps_length(self):
        trades = [(_ts_ns(2025, 1, 1) + i * 86400 * 10**9, 10.0) for i in range(500)]
        out = _sections.build_equity_curve(trades, 10000, max_points=100)
        assert len(out) == 100
        assert out[-1]["timestamp"] == "2026-05-15"  # last date preserved


# ---------------------------------------------------------------------------
# recompute_risk_metrics_from_equity_curve
# ---------------------------------------------------------------------------

class TestRecomputeRiskMetrics:

    def test_none_when_too_few_points(self):
        assert _sections.recompute_risk_metrics_from_equity_curve([], 10000) is None
        one = [{"timestamp": "2025-01-01", "equity": 10100, "returns_pct": 0, "drawdown_pct": 0}]
        assert _sections.recompute_risk_metrics_from_equity_curve(one, 10000) is None

    def test_positive_trajectory_metrics(self, simple_equity_curve):
        out = _sections.recompute_risk_metrics_from_equity_curve(simple_equity_curve, 10000)
        assert out is not None
        assert out["n_days"] == 5
        assert out["sharpe"] is not None
        assert out["total_return_pct"] == pytest.approx(3.0, rel=1e-3)
        assert out["cagr"] is not None
        assert out["max_drawdown"] <= 0

    def test_mutates_returns_pct_in_place(self, simple_equity_curve):
        before = [pt["returns_pct"] for pt in simple_equity_curve]
        _sections.recompute_risk_metrics_from_equity_curve(simple_equity_curve, 10000)
        after = [pt["returns_pct"] for pt in simple_equity_curve]
        assert before != after  # values replaced with proper daily portfolio returns

    def test_flat_equity_gives_zero_metrics(self):
        ec = [
            {"timestamp": f"2025-01-0{i+1}", "equity": 10000.0, "returns_pct": 0, "drawdown_pct": 0}
            for i in range(5)
        ]
        out = _sections.recompute_risk_metrics_from_equity_curve(ec, 10000)
        assert out["sharpe"] is None  # std == 0
        assert out["sortino"] is None
        assert out["total_return_pct"] == 0.0


# ---------------------------------------------------------------------------
# compute_extended_statistics
# ---------------------------------------------------------------------------

class TestExtendedStatistics:

    def test_empty_returns_empty_dict(self):
        out = _sections.compute_extended_statistics(np.array([]), np.array([]), 0, 0)
        assert out == {}

    def test_best_worst_day(self):
        rets = np.array([0.01, -0.02, 0.03, -0.015, 0.005])
        mean, std = float(rets.mean()), float(rets.std(ddof=1))
        out = _sections.compute_extended_statistics(rets, np.zeros(5), mean, std)
        assert out["best_day"] == pytest.approx(3.0)
        assert out["worst_day"] == pytest.approx(-2.0)

    def test_positive_days_pct(self):
        rets = np.array([0.01, -0.01, 0.02, -0.005, 0.003])
        out = _sections.compute_extended_statistics(
            rets, np.zeros(5), float(rets.mean()), float(rets.std(ddof=1))
        )
        assert out["positive_days_pct"] == pytest.approx(60.0)

    def test_omega_with_no_losses(self):
        rets = np.array([0.01, 0.02, 0.005, 0.015])
        out = _sections.compute_extended_statistics(
            rets, np.zeros(4), float(rets.mean()), float(rets.std(ddof=1))
        )
        assert out["omega_ratio"] is None  # denominator is zero

    def test_all_values_json_safe(self, daily_rets_100):
        dd = np.linspace(-0.05, 0.0, 100)
        out = _sections.compute_extended_statistics(
            daily_rets_100, dd, float(daily_rets_100.mean()), float(daily_rets_100.std(ddof=1))
        )
        import math
        for k, v in out.items():
            if v is None:
                continue
            assert not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))), f"{k}={v}"


# ---------------------------------------------------------------------------
# compute_per_instrument_basic
# ---------------------------------------------------------------------------

class TestPerInstrumentBasic:

    def test_empty(self):
        assert _sections.compute_per_instrument_basic([], 10000) == {}

    def test_single_instrument_stats(self):
        trades = [
            {"instrument": "BTC", "pnl": 100.0},
            {"instrument": "BTC", "pnl": -50.0},
            {"instrument": "BTC", "pnl": 80.0},
        ]
        out = _sections.compute_per_instrument_basic(trades, 10000)
        assert "BTC" in out
        assert out["BTC"]["total_trades"] == 3
        assert out["BTC"]["winning_trades"] == 2
        assert out["BTC"]["total_pnl"] == pytest.approx(130.0)
        assert out["BTC"]["gross_profit"] == pytest.approx(180.0)
        assert out["BTC"]["gross_loss"] == pytest.approx(50.0)
        assert out["BTC"]["profit_factor"] == pytest.approx(3.6, rel=1e-3)

    def test_multiple_instruments(self):
        trades = [
            {"instrument": "BTC", "pnl": 100.0},
            {"instrument": "ETH", "pnl": 50.0},
            {"instrument": "ETH", "pnl": -30.0},
        ]
        out = _sections.compute_per_instrument_basic(trades, 10000)
        assert set(out.keys()) == {"BTC", "ETH"}
        assert out["ETH"]["total_trades"] == 2
        assert out["ETH"]["total_pnl"] == pytest.approx(20.0)

    def test_profit_factor_none_when_no_losses(self):
        trades = [
            {"instrument": "BTC", "pnl": 100.0},
            {"instrument": "BTC", "pnl": 50.0},
        ]
        out = _sections.compute_per_instrument_basic(trades, 10000)
        assert out["BTC"]["profit_factor"] is None

    def test_zero_starting_balance_return_pct_zero(self):
        trades = [{"instrument": "BTC", "pnl": 100.0}]
        out = _sections.compute_per_instrument_basic(trades, 0)
        assert out["BTC"]["return_pct"] == 0.0


# ---------------------------------------------------------------------------
# compute_drawdown_periods
# ---------------------------------------------------------------------------

class TestDrawdownPeriods:

    def test_empty_input(self):
        assert _sections.compute_drawdown_periods([]) == []

    def test_no_drawdown_when_monotonic(self):
        ec = [
            {"timestamp": f"2025-01-0{i+1}", "drawdown_pct": 0.0}
            for i in range(5)
        ]
        assert _sections.compute_drawdown_periods(ec) == []

    def test_identifies_single_period(self):
        ec = [
            {"timestamp": "2025-01-01", "drawdown_pct": 0.0},
            {"timestamp": "2025-01-02", "drawdown_pct": -2.0},
            {"timestamp": "2025-01-03", "drawdown_pct": -5.0},  # trough
            {"timestamp": "2025-01-04", "drawdown_pct": -1.0},
            {"timestamp": "2025-01-05", "drawdown_pct": 0.0},   # recovered
        ]
        out = _sections.compute_drawdown_periods(ec)
        assert len(out) == 1
        assert out[0]["start"] == "2025-01-02"
        assert out[0]["trough_date"] == "2025-01-03"
        assert out[0]["recovery_date"] == "2025-01-05"
        assert out[0]["max_drawdown_pct"] == pytest.approx(-5.0)
        assert out[0]["duration_days"] == 3

    def test_ongoing_drawdown_at_end(self):
        ec = [
            {"timestamp": "2025-01-01", "drawdown_pct": 0.0},
            {"timestamp": "2025-01-02", "drawdown_pct": -3.0},
            {"timestamp": "2025-01-03", "drawdown_pct": -2.0},
        ]
        out = _sections.compute_drawdown_periods(ec)
        assert len(out) == 1
        assert out[0]["recovery_date"] is None
        assert out[0]["recovery_days"] is None

    def test_top_n_sorting(self):
        ec = [
            {"timestamp": "2025-01-01", "drawdown_pct": 0.0},
            {"timestamp": "2025-01-02", "drawdown_pct": -1.0},
            {"timestamp": "2025-01-03", "drawdown_pct": 0.0},
            {"timestamp": "2025-01-04", "drawdown_pct": -5.0},
            {"timestamp": "2025-01-05", "drawdown_pct": 0.0},
            {"timestamp": "2025-01-06", "drawdown_pct": -3.0},
            {"timestamp": "2025-01-07", "drawdown_pct": 0.0},
        ]
        out = _sections.compute_drawdown_periods(ec, top_n=2)
        assert len(out) == 2
        assert out[0]["max_drawdown_pct"] == pytest.approx(-5.0)
        assert out[1]["max_drawdown_pct"] == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# compute_annual_returns
# ---------------------------------------------------------------------------

class TestAnnualReturns:

    def test_empty(self):
        assert _sections.compute_annual_returns([], 10000) == []

    def test_single_year(self):
        ec = [
            {"timestamp": "2025-01-01", "equity": 10100.0},
            {"timestamp": "2025-06-01", "equity": 10500.0},
            {"timestamp": "2025-12-31", "equity": 11000.0},
        ]
        out = _sections.compute_annual_returns(ec, 10000)
        assert len(out) == 1
        assert out[0]["year"] == 2025
        # (11000 / 10000 - 1) * 100 = 10.0 (first_eq captured as starting_balance)
        assert out[0]["return_pct"] == pytest.approx(10.0, rel=1e-2)

    def test_multiple_years(self):
        ec = [
            {"timestamp": "2024-06-01", "equity": 10500.0},
            {"timestamp": "2024-12-31", "equity": 11000.0},
            {"timestamp": "2025-01-02", "equity": 11050.0},
            {"timestamp": "2025-12-31", "equity": 12000.0},
        ]
        out = _sections.compute_annual_returns(ec, 10000)
        assert [r["year"] for r in out] == [2024, 2025]
        assert out[1]["return_pct"] > 0


# ---------------------------------------------------------------------------
# compute_returns_distribution
# ---------------------------------------------------------------------------

class TestReturnsDistribution:

    def test_empty(self):
        assert _sections.compute_returns_distribution(np.array([])) == []
        assert _sections.compute_returns_distribution(np.array([0.01])) == []

    def test_basic_histogram(self):
        rets = np.array([0.0, 0.001, 0.002, -0.001, 0.0005, 0.003, -0.002])
        out = _sections.compute_returns_distribution(rets, bins=5)
        assert len(out) == 5
        total = sum(b["count"] for b in out)
        assert total == len(rets)

    def test_bin_monotonic(self):
        rng = np.random.default_rng(0)
        rets = rng.normal(0, 0.01, 200)
        out = _sections.compute_returns_distribution(rets, bins=20)
        for i in range(len(out) - 1):
            assert out[i]["bin_end"] == out[i + 1]["bin_start"]


# ---------------------------------------------------------------------------
# compute_qq_plot_data
# ---------------------------------------------------------------------------

class TestQQPlotData:

    def test_empty(self):
        assert _sections.compute_qq_plot_data(np.array([])) == []

    def test_sorted_empirical(self):
        rng = np.random.default_rng(1)
        rets = rng.normal(0, 0.01, 100)
        out = _sections.compute_qq_plot_data(rets)
        assert len(out) == 100
        emp = [p["empirical"] for p in out]
        assert emp == sorted(emp)

    def test_downsample(self):
        rng = np.random.default_rng(2)
        rets = rng.normal(0, 0.01, 500)
        out = _sections.compute_qq_plot_data(rets, max_points=50)
        assert len(out) == 50


# ---------------------------------------------------------------------------
# compute_benchmark_relative_metrics
# ---------------------------------------------------------------------------

class TestBenchmarkRelative:

    def test_none_when_insufficient(self):
        out = _sections.compute_benchmark_relative_metrics(
            np.arange(10, dtype=float) / 100, np.arange(10, dtype=float) / 100
        )
        assert out["alpha"] is None
        assert out["beta"] is None

    def test_perfect_correlation_beta_one(self):
        rng = np.random.default_rng(42)
        bm = rng.normal(0, 0.01, 100)
        sr = bm.copy()  # identical
        out = _sections.compute_benchmark_relative_metrics(sr, bm)
        assert out["beta"] == pytest.approx(1.0, rel=1e-3)
        assert out["r_squared"] == pytest.approx(1.0, rel=1e-3)
        assert out["alpha"] == pytest.approx(0.0, abs=1e-6)

    def test_zero_beta_when_uncorrelated(self):
        rng = np.random.default_rng(0)
        bm = rng.normal(0, 0.01, 500)
        sr = rng.normal(0, 0.01, 500)
        out = _sections.compute_benchmark_relative_metrics(sr, bm)
        assert abs(out["beta"]) < 0.3  # weak correlation
        assert 0 <= out["r_squared"] <= 1

    def test_handles_none_inputs(self):
        out = _sections.compute_benchmark_relative_metrics(None, None)
        assert all(v is None for v in out.values())


# ---------------------------------------------------------------------------
# compute_streak_sequence
# ---------------------------------------------------------------------------

class TestStreakSequence:

    def test_empty(self):
        assert _sections.compute_streak_sequence([]) == []

    def test_alternating_single_streaks(self):
        out = _sections.compute_streak_sequence([1.0, -1.0, 1.0, -1.0])
        assert len(out) == 4
        assert all(s["count"] == 1 for s in out)

    def test_grouped_streaks(self):
        out = _sections.compute_streak_sequence([1.0, 2.0, -1.0, -2.0, -3.0, 1.0])
        assert len(out) == 3
        assert out[0] == {"streak_num": 1, "type": "win", "count": 2, "total_pnl": 3.0}
        assert out[1] == {"streak_num": 2, "type": "loss", "count": 3, "total_pnl": -6.0}
        assert out[2] == {"streak_num": 3, "type": "win", "count": 1, "total_pnl": 1.0}

    def test_zero_counts_as_loss(self):
        out = _sections.compute_streak_sequence([0.0, 0.0, 1.0])
        assert out[0]["type"] == "loss"
        assert out[0]["count"] == 2
        assert out[1]["type"] == "win"


# ---------------------------------------------------------------------------
# compute_long_vs_short
# ---------------------------------------------------------------------------

class TestLongVsShort:

    def test_empty(self):
        out = _sections.compute_long_vs_short([])
        assert out["long"]["trades"] == 0
        assert out["short"]["trades"] == 0

    def test_split_by_side(self):
        trades = [("BUY", 100.0), ("SELL", -50.0), ("BUY", -30.0), ("SELL", 80.0)]
        out = _sections.compute_long_vs_short(trades)
        assert out["long"]["trades"] == 2
        assert out["long"]["total_pnl"] == pytest.approx(70.0)
        assert out["long"]["win_rate"] == pytest.approx(0.5)
        assert out["short"]["trades"] == 2
        assert out["short"]["total_pnl"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# compute_return_by_dow / compute_return_by_hour
# ---------------------------------------------------------------------------

class TestReturnByTime:

    def test_dow_buckets_zero_by_default(self):
        out = _sections.compute_return_by_dow([])
        assert len(out) == 7
        assert all(len(b["values"]) == 0 for b in out)
        assert out[0]["dow_name"] == "Mon"

    def test_hour_buckets_zero_by_default(self):
        out = _sections.compute_return_by_hour([])
        assert len(out) == 24
        assert all(len(b["values"]) == 0 for b in out)

    def test_dow_placement(self):
        # 2025-01-06 is a Monday (weekday=0)
        trades = [(_ts_ns(2025, 1, 6, 12), 10.0), (_ts_ns(2025, 1, 7, 12), 20.0)]
        out = _sections.compute_return_by_dow(trades)
        assert out[0]["values"] == [10.0]
        assert out[1]["values"] == [20.0]

    def test_hour_placement(self):
        trades = [(_ts_ns(2025, 1, 6, 3), 5.0), (_ts_ns(2025, 1, 6, 3), 10.0)]
        out = _sections.compute_return_by_hour(trades)
        assert out[3]["values"] == [5.0, 10.0]

    def test_ignores_invalid_ts(self):
        out = _sections.compute_return_by_dow([(0, 1.0), (-1, 2.0)])
        for b in out:
            assert b["values"] == []


# ---------------------------------------------------------------------------
# compute_periodic_returns
# ---------------------------------------------------------------------------

class TestPeriodicReturns:

    def test_empty(self):
        monthly, weekly = _sections.compute_periodic_returns([], 10000)
        assert monthly == []
        assert weekly == []

    def test_multi_month(self):
        ec = [
            {"timestamp": "2025-01-15", "equity": 10100.0},
            {"timestamp": "2025-01-31", "equity": 10200.0},
            {"timestamp": "2025-02-10", "equity": 10300.0},
            {"timestamp": "2025-02-28", "equity": 10400.0},
        ]
        monthly, weekly = _sections.compute_periodic_returns(ec, 10000)
        assert [m["period"] for m in monthly] == ["2025-01", "2025-02"]
        assert monthly[0]["return_pct"] > 0
        assert len(weekly) >= 2

    def test_weekly_key_is_sunday(self):
        # 2025-01-06 is a Monday; week ends Sunday 2025-01-12
        ec = [
            {"timestamp": "2025-01-06", "equity": 10100.0},
            {"timestamp": "2025-01-08", "equity": 10200.0},
        ]
        _, weekly = _sections.compute_periodic_returns(ec, 10000)
        assert weekly[0]["period"] == "2025-01-12"
