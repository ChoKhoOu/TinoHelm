"""Tests for pure-computation section helpers in ``sections.py``.

Strategy:

1. **Preferred path** — import ``sections`` normally from the installed
   package.  This works in CI where ``nautilus_trader`` is installed and
   ``tinohelm.backtest.result.__init__`` succeeds.

2. **Fallback path** — for local/NT-free dev environments, load ``sections.py``
   via file path.  We temporarily pre-register ``statistics`` under its
   dotted name (required by ``sections.py``'s absolute import), load
   ``sections.py``, then restore the original ``sys.modules`` state so
   subsequent tests are unaffected.
"""
from __future__ import annotations

import importlib.util
import sys
import types as _types
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Module loading — prefer direct import; fall back to isolated file-path load
# ---------------------------------------------------------------------------

_RESULT_DIR = Path(__file__).resolve().parents[2] / "src" / "tinohelm" / "backtest" / "result"


def _load_sections_isolated():
    """Load sections.py via file path without polluting sys.modules."""
    # Record original state of keys we will temporarily touch.
    keys = [
        "tinohelm",
        "tinohelm.backtest",
        "tinohelm.backtest.result",
        "tinohelm.backtest.result.statistics",
    ]
    saved: dict[str, object] = {k: sys.modules.get(k, KeyError) for k in keys}

    try:
        # Install minimal package stubs only when absent so sections.py's
        # ``from tinohelm.backtest.result.statistics import ...`` resolves.
        for name in ["tinohelm", "tinohelm.backtest", "tinohelm.backtest.result"]:
            if name not in sys.modules or sys.modules.get(name) is None:
                sys.modules[name] = _types.ModuleType(name)

        # Load statistics under its fully qualified name so the import inside
        # sections.py finds it.
        stats_spec = importlib.util.spec_from_file_location(
            "tinohelm.backtest.result.statistics", _RESULT_DIR / "statistics.py",
        )
        stats_mod = importlib.util.module_from_spec(stats_spec)
        sys.modules["tinohelm.backtest.result.statistics"] = stats_mod
        stats_spec.loader.exec_module(stats_mod)

        sections_spec = importlib.util.spec_from_file_location(
            "_sections_under_test", _RESULT_DIR / "sections.py",
        )
        sections_mod = importlib.util.module_from_spec(sections_spec)
        sections_spec.loader.exec_module(sections_mod)
        return sections_mod
    finally:
        # Restore sys.modules to exactly its original shape so subsequent
        # tests see a clean import state.
        for k, original in saved.items():
            if original is KeyError:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = original


try:
    from tinohelm.backtest.result import sections as _sections  # type: ignore[import]
except Exception:
    _sections = _load_sections_isolated()


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


# ---------------------------------------------------------------------------
# compute_per_instrument_advanced (section 9b)
# ---------------------------------------------------------------------------

def _trade(inst: str, y: int, m: int, d: int, pnl: float) -> dict:
    return {"instrument": inst, "ts_closed": _ts_ns(y, m, d, 12), "pnl": pnl}


class TestPerInstrumentAdvanced:

    def _basic(self, instruments: list[str]) -> dict:
        """Minimal stand-in for per_instrument_basic — only total_pnl is read."""
        return {i: {"total_pnl": 0.0} for i in instruments}

    def test_empty_when_single_instrument(self):
        trades = [_trade("BTC", 2025, 1, 1, 50.0), _trade("BTC", 2025, 1, 2, -10.0)]
        basic = self._basic(["BTC"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        assert out["per_instrument_updates"] == {}
        assert out["instrument_cumulative_pnl"] == {}
        assert out["instrument_correlation"] == {}
        assert out["monthly_pnl_heatmap"] == []
        assert out["portfolio_analytics"] == {}

    def test_empty_when_no_instruments(self):
        out = _sections.compute_per_instrument_advanced([], {}, 10000)
        assert out["per_instrument_updates"] == {}

    def test_cumulative_pnl_monotone_on_positive_streak(self):
        # Two instruments both with strictly positive daily PnL → cum_pnl increases
        trades = [
            _trade("BTC", 2025, 1, 1, 20.0), _trade("ETH", 2025, 1, 1, 10.0),
            _trade("BTC", 2025, 1, 2, 30.0), _trade("ETH", 2025, 1, 2, 5.0),
            _trade("BTC", 2025, 1, 3, 10.0),
        ]
        basic = {"BTC": {"total_pnl": 60.0}, "ETH": {"total_pnl": 15.0}}
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        btc = out["instrument_cumulative_pnl"]["BTC"]
        assert [p["cum_pnl"] for p in btc] == [20.0, 50.0, 60.0]
        eth = out["instrument_cumulative_pnl"]["ETH"]
        # ETH has no trade on 2025-01-03 but the date appears in the union
        assert [p["cum_pnl"] for p in eth] == [10.0, 15.0, 15.0]

    def test_cumulative_pnl_dates_are_union_sorted(self):
        trades = [
            _trade("BTC", 2025, 1, 3, 10.0),
            _trade("ETH", 2025, 1, 1, 20.0),
        ]
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        dates = [p["date"] for p in out["instrument_cumulative_pnl"]["BTC"]]
        assert dates == ["2025-01-01", "2025-01-03"]

    def test_correlation_requires_ten_days(self):
        # Only 5 trading days → correlation dict empty
        trades = []
        for i in range(5):
            d = i + 1
            trades.append(_trade("BTC", 2025, 1, d, float(i + 1)))
            trades.append(_trade("ETH", 2025, 1, d, float(-i)))
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        assert out["instrument_correlation"] == {}

    def test_correlation_perfect_positive(self):
        # 12 days of perfectly correlated PnL
        trades = []
        for i in range(12):
            d = i + 1
            trades.append(_trade("BTC", 2025, 1, d, 10.0 * (i + 1)))
            trades.append(_trade("ETH", 2025, 1, d, 5.0 * (i + 1)))
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        corr = out["instrument_correlation"]
        assert corr["BTC"]["ETH"] == pytest.approx(1.0, abs=1e-4)
        assert corr["ETH"]["BTC"] == pytest.approx(1.0, abs=1e-4)

    def test_correlation_perfect_negative(self):
        trades = []
        for i in range(12):
            d = i + 1
            trades.append(_trade("BTC", 2025, 1, d, 10.0 * (i + 1)))
            trades.append(_trade("ETH", 2025, 1, d, -10.0 * (i + 1)))
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        corr = out["instrument_correlation"]
        assert corr["BTC"]["ETH"] == pytest.approx(-1.0, abs=1e-4)

    def test_per_instrument_updates_contain_risk_keys(self):
        trades = []
        rng = np.random.default_rng(seed=11)
        for i in range(30):
            d = i + 1
            date_y, date_m, date_d = 2025, 1 + (d - 1) // 30, ((d - 1) % 30) + 1
            trades.append(_trade("BTC", date_y, date_m, date_d, float(rng.normal(5, 20))))
            trades.append(_trade("ETH", date_y, date_m, date_d, float(rng.normal(-2, 15))))
        basic = {"BTC": {"total_pnl": 150.0}, "ETH": {"total_pnl": -60.0}}
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        for inst in ("BTC", "ETH"):
            upd = out["per_instrument_updates"][inst]
            assert set(upd.keys()) == {"sharpe_ratio", "sortino_ratio", "max_drawdown", "recovery_factor"}

    def test_recovery_factor_nil_when_drawdown_tiny(self):
        # All-positive instrument has no meaningful drawdown → recovery_factor is None
        trades = []
        for i in range(12):
            trades.append(_trade("BTC", 2025, 1, i + 1, 100.0))
            trades.append(_trade("ETH", 2025, 1, i + 1, 50.0))
        basic = {"BTC": {"total_pnl": 1200.0}, "ETH": {"total_pnl": 600.0}}
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        assert out["per_instrument_updates"]["BTC"]["recovery_factor"] is None

    def test_recovery_factor_positive_when_drawdown_and_total_pnl_positive(self):
        # BTC has a strong recovery after a drawdown
        trades = [
            _trade("BTC", 2025, 1, 1, 500.0),
            _trade("BTC", 2025, 1, 2, -400.0),
            _trade("BTC", 2025, 1, 3, 300.0),
            _trade("BTC", 2025, 1, 4, 200.0),
            _trade("ETH", 2025, 1, 1, 100.0),
            _trade("ETH", 2025, 1, 2, 50.0),
            _trade("ETH", 2025, 1, 3, -20.0),
            _trade("ETH", 2025, 1, 4, 30.0),
        ]
        basic = {"BTC": {"total_pnl": 600.0}, "ETH": {"total_pnl": 160.0}}
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        # Only 4 dates so correlation empty, but risk keys still computed
        upd_btc = out["per_instrument_updates"]["BTC"]
        # Drawdown exists only if recovery_factor is set
        if upd_btc["recovery_factor"] is not None:
            assert upd_btc["recovery_factor"] > 0

    def test_max_drawdown_is_negative_ratio(self):
        trades = [
            _trade("BTC", 2025, 1, 1, 100.0),
            _trade("BTC", 2025, 1, 2, -500.0),
            _trade("ETH", 2025, 1, 1, 50.0),
            _trade("ETH", 2025, 1, 2, 20.0),
        ]
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        mdd = out["per_instrument_updates"]["BTC"]["max_drawdown"]
        assert mdd is not None
        assert mdd < 0

    def test_monthly_heatmap_contains_all_pairs(self):
        trades = [
            _trade("BTC", 2025, 1, 15, 100.0),
            _trade("BTC", 2025, 2, 10, -50.0),
            _trade("ETH", 2025, 1, 20, 30.0),
        ]
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        heat = out["monthly_pnl_heatmap"]
        # 2 instruments × 2 months = 4 rows
        assert len(heat) == 4
        btc_jan = next(r for r in heat if r["instrument"] == "BTC" and r["month"] == "2025-01")
        btc_feb = next(r for r in heat if r["instrument"] == "BTC" and r["month"] == "2025-02")
        eth_feb = next(r for r in heat if r["instrument"] == "ETH" and r["month"] == "2025-02")
        assert btc_jan["pnl"] == 100.0
        assert btc_feb["pnl"] == -50.0
        # Instrument with no trades that month still appears with zero PnL
        assert eth_feb["pnl"] == 0.0

    def test_diversification_ratio_present_with_ten_days(self):
        rng = np.random.default_rng(seed=3)
        trades = []
        for i in range(12):
            d = i + 1
            trades.append(_trade("BTC", 2025, 1, d, float(rng.normal(0, 50))))
            trades.append(_trade("ETH", 2025, 1, d, float(rng.normal(0, 50))))
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        pa = out["portfolio_analytics"]
        # Uncorrelated returns ⇒ diversification_ratio > 1
        assert "diversification_ratio" in pa
        assert pa["diversification_ratio"] > 0
        assert "diversification_benefit_pct" in pa

    def test_diversification_absent_when_under_ten_days(self):
        trades = []
        for i in range(5):
            d = i + 1
            trades.append(_trade("BTC", 2025, 1, d, 10.0))
            trades.append(_trade("ETH", 2025, 1, d, 10.0))
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        assert out["portfolio_analytics"] == {}

    def test_skips_trades_with_zero_or_negative_ts(self):
        trades = [
            {"instrument": "BTC", "ts_closed": 0, "pnl": 999.0},  # skipped
            {"instrument": "BTC", "ts_closed": -1, "pnl": 999.0},  # skipped
            _trade("BTC", 2025, 1, 1, 100.0),
            _trade("ETH", 2025, 1, 1, 50.0),
        ]
        basic = self._basic(["BTC", "ETH"])
        out = _sections.compute_per_instrument_advanced(trades, basic, 10000)
        # Only one date → a single cum_pnl point with the 100.0 trade
        btc_curve = out["instrument_cumulative_pnl"]["BTC"]
        assert len(btc_curve) == 1
        assert btc_curve[0]["cum_pnl"] == 100.0

    def test_safe_round_helper_nan_inf(self):
        assert _sections._safe_round(float("nan")) is None
        assert _sections._safe_round(float("inf")) is None
        assert _sections._safe_round(None) is None
        assert _sections._safe_round(1.23456, 2) == 1.23


# ---------------------------------------------------------------------------
# compute_benchmark_equity_curve (section 11f)
# ---------------------------------------------------------------------------

class TestBenchmarkEquityCurve:

    def test_empty_when_equity_curve_short(self):
        out = _sections.compute_benchmark_equity_curve(
            [],
            {"BTC": {"2025-01-01": 100.0}},
            10000,
        )
        assert out == []
        one = [{"timestamp": "2025-01-01", "equity": 10000.0}]
        assert _sections.compute_benchmark_equity_curve(one, {"BTC": {"2025-01-01": 100.0}}, 10000) == []

    def test_empty_when_no_closes(self):
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10100.0},
        ]
        assert _sections.compute_benchmark_equity_curve(ec, {}, 10000) == []

    def test_single_instrument_matches_price_ratio(self):
        # Buy 100 units of BTC at $100 (= $10000 allocation); BTC doubles → equity = $20000
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10500.0},
            {"timestamp": "2025-01-03", "equity": 10800.0},
        ]
        closes = {"BTC": {"2025-01-01": 100.0, "2025-01-02": 150.0, "2025-01-03": 200.0}}
        out = _sections.compute_benchmark_equity_curve(ec, closes, 10000)
        assert [pt["equity"] for pt in out] == [10000.0, 15000.0, 20000.0]
        assert [pt["timestamp"] for pt in out] == ["2025-01-01", "2025-01-02", "2025-01-03"]

    def test_equal_weight_basket_split(self):
        # Two instruments, each gets 5000 allocation
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10100.0},
        ]
        closes = {
            "BTC": {"2025-01-01": 100.0, "2025-01-02": 110.0},   # +10%
            "ETH": {"2025-01-01": 50.0, "2025-01-02": 55.0},     # +10%
        }
        out = _sections.compute_benchmark_equity_curve(ec, closes, 10000)
        # 5000 → 5500 for each ⇒ total 11000
        assert out[1]["equity"] == pytest.approx(11000.0)

    def test_forward_fill_missing_price(self):
        # BTC missing price on day 2 → use day-1 price
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10000.0},
            {"timestamp": "2025-01-03", "equity": 10000.0},
        ]
        closes = {"BTC": {"2025-01-01": 100.0, "2025-01-03": 120.0}}
        out = _sections.compute_benchmark_equity_curve(ec, closes, 10000)
        # Day 1: 100 units × $100 = $10000
        # Day 2: no price → forward fill to 100 → $10000
        # Day 3: 100 units × $120 = $12000
        assert out[0]["equity"] == pytest.approx(10000.0)
        assert out[1]["equity"] == pytest.approx(10000.0)
        assert out[2]["equity"] == pytest.approx(12000.0)

    def test_instrument_first_price_after_start(self):
        # BTC starts quoting on day 2 → allocation uses that first price
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10000.0},
            {"timestamp": "2025-01-03", "equity": 10500.0},
        ]
        closes = {"BTC": {"2025-01-02": 100.0, "2025-01-03": 110.0}}
        out = _sections.compute_benchmark_equity_curve(ec, closes, 10000)
        # Day 1 has no price at all → fallback: price = alloc/units = 100
        # units = 10000 / 100 = 100; day 3: 100 × 110 = 11000
        assert out[2]["equity"] == pytest.approx(11000.0)

    def test_skips_zero_or_negative_prices_for_allocation(self):
        # First valid (>0) price decides allocation
        ec = [
            {"timestamp": "2025-01-01", "equity": 10000.0},
            {"timestamp": "2025-01-02", "equity": 10000.0},
        ]
        closes = {"BTC": {"2025-01-01": 0.0, "2025-01-02": 200.0}}
        out = _sections.compute_benchmark_equity_curve(ec, closes, 10000)
        # Allocation price is 200 ⇒ units = 50
        # Day 1 price = 0 but it's present → used as market value (0) unless we forward fill
        # Current implementation: day-1 price is 0.0 (present in closes), so equity = 50 × 0 = 0
        # Day 2: 50 × 200 = 10000
        assert out[1]["equity"] == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# compute_benchmark_daily_returns
# ---------------------------------------------------------------------------

class TestBenchmarkDailyReturns:

    def test_none_when_empty(self):
        assert _sections.compute_benchmark_daily_returns([], 10000) is None

    def test_none_when_single_point(self):
        out = _sections.compute_benchmark_daily_returns(
            [{"timestamp": "2025-01-01", "equity": 10100.0}], 10000,
        )
        assert out is None

    def test_returns_array_length_equals_points(self):
        curve = [
            {"timestamp": "2025-01-01", "equity": 10100.0},
            {"timestamp": "2025-01-02", "equity": 10200.0},
            {"timestamp": "2025-01-03", "equity": 10300.0},
        ]
        out = _sections.compute_benchmark_daily_returns(curve, 10000)
        assert out is not None
        # 3 points prepended with starting_balance → 3 daily returns
        assert out.shape == (3,)

    def test_first_return_relative_to_starting_balance(self):
        curve = [{"timestamp": "2025-01-01", "equity": 10100.0}, {"timestamp": "2025-01-02", "equity": 10200.0}]
        out = _sections.compute_benchmark_daily_returns(curve, 10000)
        assert out[0] == pytest.approx(0.01)

    def test_none_when_zero_starting_balance(self):
        curve = [
            {"timestamp": "2025-01-01", "equity": 0.0},
            {"timestamp": "2025-01-02", "equity": 100.0},
        ]
        # starting_balance = 0 ⇒ denom has zero ⇒ returns None
        assert _sections.compute_benchmark_daily_returns(curve, 0) is None


# ---------------------------------------------------------------------------
# compute_trade_scalar_metrics
# ---------------------------------------------------------------------------

class TestTradeScalarMetrics:

    def _call(self, pnls, **kwargs):
        defaults = dict(
            n_orders=10,
            n_filled_orders=10,
            n_returns_periods=20,
            total_trades=len(pnls),
            total_pnl=float(sum(pnls)),
            max_drawdown=-0.05,
            starting_balance=10000.0,
            win_rate=0.6,
            avg_win=100.0,
            avg_loss=-50.0,
            expectancy=25.0,
        )
        defaults.update(kwargs)
        return _sections.compute_trade_scalar_metrics(pnls, **defaults)

    def test_empty_pnls_returns_all_none(self):
        out = self._call([])
        assert set(out.keys()) == {
            "median_trade_pnl", "std_trade_pnl", "fill_rate",
            "avg_trades_per_day", "recovery_factor", "sqn",
            "kelly_criterion", "k_ratio", "expectancy_r",
        }
        assert all(v is None for v in out.values())

    def test_median_and_std(self):
        out = self._call([10.0, 20.0, 30.0, -5.0, 15.0])
        assert out["median_trade_pnl"] == pytest.approx(15.0)
        assert out["std_trade_pnl"] is not None
        assert out["std_trade_pnl"] > 0

    def test_std_none_when_single_trade(self):
        out = self._call([10.0])
        assert out["median_trade_pnl"] == pytest.approx(10.0)
        assert out["std_trade_pnl"] is None

    def test_fill_rate_percent(self):
        out = self._call([10.0], n_orders=20, n_filled_orders=15)
        assert out["fill_rate"] == pytest.approx(75.0)

    def test_fill_rate_none_when_no_orders(self):
        out = self._call([10.0], n_orders=0, n_filled_orders=0)
        assert out["fill_rate"] is None

    def test_avg_trades_per_day(self):
        out = self._call([10.0] * 10, n_returns_periods=100, total_trades=10)
        assert out["avg_trades_per_day"] == pytest.approx(0.1)

    def test_avg_trades_per_day_none_when_no_periods(self):
        out = self._call([10.0], n_returns_periods=0)
        assert out["avg_trades_per_day"] is None

    def test_recovery_factor(self):
        # total_pnl=500, starting=10000 → net_return=0.05; dd=-0.05 → recovery=1.0
        out = self._call(
            [500.0], total_pnl=500.0, starting_balance=10000.0,
            max_drawdown=-0.05,
        )
        assert out["recovery_factor"] == pytest.approx(1.0)

    def test_recovery_factor_none_when_zero_drawdown(self):
        out = self._call([500.0], max_drawdown=0.0)
        assert out["recovery_factor"] is None

    def test_recovery_factor_none_when_none_drawdown(self):
        out = self._call([500.0], max_drawdown=None)
        assert out["recovery_factor"] is None

    def test_sqn_positive_for_winning_system(self):
        # Consistent wins should produce positive SQN
        out = self._call([10.0, 12.0, 8.0, 11.0, 9.0, 10.5, 11.5, 9.5])
        assert out["sqn"] is not None
        assert out["sqn"] > 0

    def test_sqn_none_when_zero_std(self):
        # All identical PnLs → std=0 → SQN undefined
        out = self._call([10.0, 10.0, 10.0, 10.0])
        assert out["sqn"] is None

    def test_kelly_criterion(self):
        # win_rate=0.6, avg_win=100, avg_loss=-50 → R=2 → kelly=(0.6-0.4/2)*100=40
        out = self._call([10.0], win_rate=0.6, avg_win=100.0, avg_loss=-50.0)
        assert out["kelly_criterion"] == pytest.approx(40.0)

    def test_kelly_none_when_no_avg_loss(self):
        out = self._call([10.0], avg_loss=None)
        assert out["kelly_criterion"] is None

    def test_kelly_none_when_zero_avg_loss(self):
        out = self._call([10.0], avg_loss=0.0)
        assert out["kelly_criterion"] is None

    def test_k_ratio_for_upward_trending_equity(self):
        # Consistent positive PnLs → strong k-ratio
        out = self._call([10.0] * 30)
        # Exactly linear equity → residuals=0 → k_ratio falls back to None (mse<=0)
        # So we test with slight noise:
        out = self._call([10.0, 12.0, 9.0, 11.0, 10.5, 10.0, 11.5, 9.5, 10.0, 12.5] * 3)
        assert out["k_ratio"] is not None

    def test_k_ratio_none_for_degenerate_equity(self):
        # Large negative PnLs → cum equity goes below zero → log undefined → fallback None
        out = self._call([-100000.0, 0.0, 0.0], starting_balance=10000.0)
        assert out["k_ratio"] is None

    def test_expectancy_r(self):
        # expectancy=25, avg_loss=-50 → expectancy_r = 25/50 = 0.5
        out = self._call([10.0], expectancy=25.0, avg_loss=-50.0)
        assert out["expectancy_r"] == pytest.approx(0.5)

    def test_expectancy_r_none_when_no_expectancy(self):
        out = self._call([10.0], expectancy=None)
        assert out["expectancy_r"] is None

    def test_values_are_json_safe(self):
        # Ensure returned floats are never NaN/Inf
        out = self._call([0.0, 0.0, 0.0, 0.0])
        import math as _m
        for v in out.values():
            if v is not None:
                assert not (_m.isnan(v) or _m.isinf(v))


# ---------------------------------------------------------------------------
# compute_trade_pnl_distribution
# ---------------------------------------------------------------------------

class TestTradePnlDistribution:

    def test_empty_returns_empty(self):
        assert _sections.compute_trade_pnl_distribution([]) == []

    def test_bin_count_respects_min_floor(self):
        # Small sample (5 trades) → bins = max(10, 5//5) = 10
        out = _sections.compute_trade_pnl_distribution([1.0, 2.0, 3.0, 4.0, 5.0])
        assert len(out) == 10

    def test_bin_count_respects_cap(self):
        # Very large sample → bins capped at 30
        rng = np.random.default_rng(seed=3)
        pnls = list(rng.normal(0, 10, size=1000))
        out = _sections.compute_trade_pnl_distribution(pnls)
        assert len(out) == 30

    def test_counts_sum_to_total(self):
        pnls = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0] * 5
        out = _sections.compute_trade_pnl_distribution(pnls)
        assert sum(bucket["count"] for bucket in out) == len(pnls)

    def test_bins_are_sorted(self):
        out = _sections.compute_trade_pnl_distribution([-10.0, -5.0, 0.0, 5.0, 10.0])
        starts = [b["bin_start"] for b in out]
        assert starts == sorted(starts)


# ---------------------------------------------------------------------------
# compute_cumulative_trade_pnl
# ---------------------------------------------------------------------------

class TestCumulativeTradePnl:

    def test_empty_returns_empty(self):
        assert _sections.compute_cumulative_trade_pnl([]) == []

    def test_one_based_trade_numbering(self):
        out = _sections.compute_cumulative_trade_pnl([10.0, 20.0])
        assert out[0]["trade_num"] == 1
        assert out[1]["trade_num"] == 2

    def test_cumulative_values(self):
        out = _sections.compute_cumulative_trade_pnl([10.0, -5.0, 20.0, -15.0])
        assert out[0]["cumulative_pnl"] == pytest.approx(10.0)
        assert out[1]["cumulative_pnl"] == pytest.approx(5.0)
        assert out[2]["cumulative_pnl"] == pytest.approx(25.0)
        assert out[3]["cumulative_pnl"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# compute_trade_pnl_scatter
# ---------------------------------------------------------------------------

class TestTradePnlScatter:

    def test_empty_returns_empty(self):
        assert _sections.compute_trade_pnl_scatter([]) == []

    def test_timestamp_formatting(self):
        trades = [
            {"ts_closed": _ts_ns(2025, 6, 15, 12), "pnl": 50.0,
             "side": "BUY", "instrument": "BTCUSDT"},
        ]
        out = _sections.compute_trade_pnl_scatter(trades)
        assert out[0]["timestamp"] is not None
        assert "2025-06-15" in out[0]["timestamp"]

    def test_zero_timestamp_becomes_null(self):
        trades = [{"ts_closed": 0, "pnl": 10.0, "side": "BUY", "instrument": "ETHUSDT"}]
        out = _sections.compute_trade_pnl_scatter(trades)
        assert out[0]["timestamp"] is None

    def test_missing_timestamp_becomes_null(self):
        trades = [{"ts_closed": None, "pnl": 10.0, "side": "BUY", "instrument": "ETHUSDT"}]
        out = _sections.compute_trade_pnl_scatter(trades)
        assert out[0]["timestamp"] is None

    def test_all_fields_preserved(self):
        trades = [
            {"ts_closed": _ts_ns(2025, 1, 1), "pnl": 123.456,
             "side": "SELL", "instrument": "SOLUSDT"},
        ]
        out = _sections.compute_trade_pnl_scatter(trades)
        assert out[0]["pnl"] == pytest.approx(123.456)
        assert out[0]["side"] == "SELL"
        assert out[0]["instrument"] == "SOLUSDT"


# ---------------------------------------------------------------------------
# compute_holding_time_distribution
# ---------------------------------------------------------------------------

class TestHoldingTimeDistribution:

    def test_empty_returns_empty(self):
        assert _sections.compute_holding_time_distribution([]) == []

    def test_ignores_zero_and_negative(self):
        # Zero and negative durations are skipped; remaining list is empty → []
        assert _sections.compute_holding_time_distribution([0, -100, 0]) == []

    def test_ignores_none(self):
        # None entries are ignored (mirrors ``if d and d > 0``)
        out = _sections.compute_holding_time_distribution([None, None, None])
        assert out == []

    def test_converts_ns_to_hours(self):
        # 3.6e12 ns = 1 hour. A bunch of 1h holds → bins around 1.0
        durations = [int(3.6e12)] * 15  # 15 one-hour holds
        out = _sections.compute_holding_time_distribution(durations)
        assert len(out) == 10  # min bin count floor
        # All values should fall into a single bin range
        total = sum(b["count"] for b in out)
        assert total == 15

    def test_bin_counts_sum_correctly(self):
        # 20 mixed durations
        durations = [int(3.6e12 * h) for h in range(1, 21)]
        out = _sections.compute_holding_time_distribution(durations)
        assert sum(b["count"] for b in out) == 20


# ---------------------------------------------------------------------------
# compute_mae_mfe
# ---------------------------------------------------------------------------

class TestMaeMfe:

    def _bars(self, inst, *hl_tuples, base_ts=_ts_ns(2025, 1, 1)):
        # Produce primitive (ts, high, low) tuples spaced 1h apart
        return {inst: [(base_ts + i * 3600 * 10**9, h, l)
                       for i, (h, l) in enumerate(hl_tuples)]}

    def test_empty_positions_returns_empty(self):
        assert _sections.compute_mae_mfe([], {}) == []

    def test_missing_bars_skipped(self):
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": _ts_ns(2025, 1, 1),
            "ts_closed": _ts_ns(2025, 1, 2),
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 10.0,
        }]
        assert _sections.compute_mae_mfe(positions, {}) == []

    def test_missing_ts_skipped(self):
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": 0,
            "ts_closed": _ts_ns(2025, 1, 2),
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 10.0,
        }]
        bars = self._bars("BTCUSDT", (110, 90))
        assert _sections.compute_mae_mfe(positions, bars) == []

    def test_buy_mae_mfe(self):
        # Entry at 100, bars have high=110, low=95 → MAE=5, MFE=10
        bars = self._bars(
            "BTCUSDT",
            (105, 98),   # bar 1
            (110, 95),   # bar 2 (deepest drawdown & highest point)
            (108, 99),   # bar 3
        )
        pos_open = _ts_ns(2025, 1, 1)
        pos_close = pos_open + 5 * 3600 * 10**9  # 5 hours later — covers all bars
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": pos_open,
            "ts_closed": pos_close,
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 8.0,
        }]
        out = _sections.compute_mae_mfe(positions, bars)
        assert len(out) == 1
        assert out[0]["mae"] == pytest.approx(5.0)   # 100 - 95
        assert out[0]["mfe"] == pytest.approx(10.0)  # 110 - 100
        assert out[0]["side"] == "BUY"
        assert out[0]["pnl"] == pytest.approx(8.0)

    def test_sell_mae_mfe_inverted(self):
        # Short position: MAE = max_high - entry, MFE = entry - min_low
        bars = self._bars(
            "BTCUSDT",
            (115, 90),
        )
        pos_open = _ts_ns(2025, 1, 1)
        pos_close = pos_open + 3600 * 10**9
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": pos_open,
            "ts_closed": pos_close,
            "entry_price": 100.0,
            "side": "SELL",
            "pnl": -5.0,
        }]
        out = _sections.compute_mae_mfe(positions, bars)
        assert out[0]["mae"] == pytest.approx(15.0)   # 115 - 100
        assert out[0]["mfe"] == pytest.approx(10.0)   # 100 - 90

    def test_bars_outside_window_ignored(self):
        # Only bar 2 falls within position window; bars 1 and 3 are outside
        pos_open = _ts_ns(2025, 1, 1, 12)
        pos_close = _ts_ns(2025, 1, 1, 14)
        bars = {
            "BTCUSDT": [
                (_ts_ns(2025, 1, 1, 10), 200, 50),   # way before
                (_ts_ns(2025, 1, 1, 13), 110, 95),   # inside window
                (_ts_ns(2025, 1, 1, 18), 500, 10),   # way after
            ],
        }
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": pos_open,
            "ts_closed": pos_close,
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 5.0,
        }]
        out = _sections.compute_mae_mfe(positions, bars)
        assert out[0]["mae"] == pytest.approx(5.0)   # 100 - 95
        assert out[0]["mfe"] == pytest.approx(10.0)  # 110 - 100

    def test_skips_positions_with_no_matching_bar(self):
        # Position window contains no bars → position omitted entirely
        pos_open = _ts_ns(2025, 6, 1)
        pos_close = _ts_ns(2025, 6, 2)
        bars = {
            "BTCUSDT": [(_ts_ns(2025, 1, 1), 105, 95)],  # far outside window
        }
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": pos_open,
            "ts_closed": pos_close,
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 0.0,
        }]
        assert _sections.compute_mae_mfe(positions, bars) == []

    def test_bars_endpoints_included(self):
        # Endpoints ts_o and ts_c are inclusive
        pos_open = _ts_ns(2025, 1, 1, 10)
        pos_close = _ts_ns(2025, 1, 1, 12)
        bars = {
            "BTCUSDT": [
                (pos_open, 105, 95),         # exactly at ts_opened
                (pos_close, 108, 92),        # exactly at ts_closed
            ],
        }
        positions = [{
            "instrument": "BTCUSDT",
            "ts_opened": pos_open,
            "ts_closed": pos_close,
            "entry_price": 100.0,
            "side": "BUY",
            "pnl": 0.0,
        }]
        out = _sections.compute_mae_mfe(positions, bars)
        # Both bars included → max_high=108, min_low=92
        assert out[0]["mae"] == pytest.approx(8.0)
        assert out[0]["mfe"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# compute_robustness
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_always_returns_dict_with_psr_keys(self):
        out = _sections.compute_robustness(
            [10.0, -5.0, 20.0, -10.0, 15.0],
            starting_balance=10000.0,
            daily_sharpe=0.05,
            n_days=100,
            skewness=0.1,
            kurtosis=0.2,
        )
        assert out is not None
        for key in ("psr", "min_backtest_length_days",
                    "actual_backtest_length_days",
                    "backtest_length_sufficient"):
            assert key in out

    def test_psr_none_when_no_daily_sharpe(self):
        out = _sections.compute_robustness(
            [10.0] * 10, starting_balance=10000.0,
            daily_sharpe=None, n_days=100,
            skewness=0.0, kurtosis=0.0,
        )
        assert out["psr"] is None
        assert out["min_backtest_length_days"] is None

    def test_backtest_length_sufficient_flag(self):
        # Positive sharpe, few observations → backtest short → flag False
        out = _sections.compute_robustness(
            [10.0, 20.0, 30.0, 40.0, 50.0],
            starting_balance=10000.0,
            daily_sharpe=0.001,  # tiny SR → large MBL required
            n_days=5,
            skewness=0.0, kurtosis=0.0,
        )
        assert out["min_backtest_length_days"] is not None
        assert out["backtest_length_sufficient"] is False

    def test_backtest_length_sufficient_none_when_mbl_none(self):
        out = _sections.compute_robustness(
            [10.0], starting_balance=10000.0,
            daily_sharpe=-0.1,  # negative SR → MBL undefined
            n_days=100,
            skewness=0.0, kurtosis=0.0,
        )
        assert out["min_backtest_length_days"] is None
        assert out["backtest_length_sufficient"] is None

    def test_none_skew_and_kurt_coerced_to_zero(self):
        # Passing skewness=None / kurtosis=None should not crash
        out = _sections.compute_robustness(
            [10.0, -5.0, 15.0, -8.0, 20.0, -3.0],
            starting_balance=10000.0,
            daily_sharpe=0.01,
            n_days=50,
            skewness=None, kurtosis=None,
        )
        assert out is not None
        assert "psr" in out

    def test_mc_keys_present_when_sufficient_trades(self):
        # >=2 trades → MC runs
        out = _sections.compute_robustness(
            [10.0, -5.0, 20.0, -8.0, 15.0] * 4,
            starting_balance=10000.0,
            daily_sharpe=0.02,
            n_days=50,
            skewness=0.1, kurtosis=0.2,
        )
        assert "mc_equity_cone" in out
        assert "mc_probability_of_loss" in out
        assert "mc_num_simulations" in out
        assert out["mc_num_simulations"] == 1000

    def test_mc_keys_absent_when_single_trade(self):
        out = _sections.compute_robustness(
            [10.0], starting_balance=10000.0,
            daily_sharpe=0.01, n_days=10,
            skewness=0.0, kurtosis=0.0,
        )
        # MC returns None for <2 trades → MC keys absent
        assert "mc_equity_cone" not in out
        assert "mc_num_simulations" not in out
