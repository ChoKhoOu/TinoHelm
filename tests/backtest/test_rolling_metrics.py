"""Tests for rolling metric computation helpers in statistics.py.

These tests import directly from the ``statistics`` module (not from the
``result`` package) to avoid pulling in ``nautilus_trader`` via
``extract.py``.  This allows running the test suite in CI environments
where NT is not installed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

# Load statistics.py directly by file path to avoid triggering
# tinohelm.backtest.result.__init__.py which re-exports from extract.py
# (that depends on nautilus_trader, unavailable in CI).
_STATS_PATH = Path(__file__).resolve().parents[2] / "src" / "tinohelm" / "backtest" / "result" / "statistics.py"
_spec = importlib.util.spec_from_file_location("_statistics", _STATS_PATH)
_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stats)

_compute_rolling_series = _stats._compute_rolling_series
_make_rolling_beta_fn = _stats._make_rolling_beta_fn
_rolling_cumret_fn = _stats._rolling_cumret_fn
_rolling_sharpe_fn = _stats._rolling_sharpe_fn
_rolling_sortino_fn = _stats._rolling_sortino_fn
_rolling_volatility_fn = _stats._rolling_volatility_fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def daily_rets_100():
    """100 days of synthetic daily returns (mean ~0.1%, std ~1%)."""
    rng = np.random.default_rng(seed=42)
    return rng.normal(0.001, 0.01, size=100)


@pytest.fixture
def timestamps_100():
    """100 date strings starting from 2025-01-01."""
    from datetime import date, timedelta
    start = date(2025, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(100)]


@pytest.fixture
def daily_rets_300():
    """300 days of returns — enough for 252-day (12m) windows."""
    rng = np.random.default_rng(seed=99)
    return rng.normal(0.0005, 0.012, size=300)


@pytest.fixture
def timestamps_300():
    from datetime import date, timedelta
    start = date(2024, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(300)]


# ---------------------------------------------------------------------------
# _compute_rolling_series — core engine
# ---------------------------------------------------------------------------

class TestComputeRollingSeries:
    """Tests for the generic rolling series computation."""

    def test_basic_output_structure(self, daily_rets_100, timestamps_100):
        """Each entry has timestamp + one key per window."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w5": 5, "w10": 10},
            _rolling_sharpe_fn,
            max_points=0,
        )
        assert len(result) == 100
        assert result[0]["timestamp"] == "2025-01-01"
        assert "w5" in result[0]
        assert "w10" in result[0]

    def test_values_none_before_window_filled(self, daily_rets_100, timestamps_100):
        """Values should be None until enough data points for the window."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w10": 10},
            _rolling_sharpe_fn,
            max_points=0,
        )
        # First 9 entries (indices 0-8) should be None
        for i in range(9):
            assert result[i]["w10"] is None, f"index {i} should be None"
        # Index 9 (10th point) should have a value
        assert result[9]["w10"] is not None

    def test_downsampling(self, daily_rets_100, timestamps_100):
        """Output is capped to max_points with evenly-spaced sampling."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w5": 5},
            _rolling_sharpe_fn,
            max_points=20,
        )
        assert len(result) == 20
        # First and last timestamps preserved
        assert result[0]["timestamp"] == "2025-01-01"
        assert result[-1]["timestamp"] == timestamps_100[-1]

    def test_no_downsampling_when_under_limit(self, daily_rets_100, timestamps_100):
        """No downsampling when result length <= max_points."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w5": 5},
            _rolling_sharpe_fn,
            max_points=200,
        )
        assert len(result) == 100

    def test_downsampling_disabled_with_zero(self, daily_rets_100, timestamps_100):
        """max_points=0 disables downsampling entirely."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w5": 5},
            _rolling_sharpe_fn,
            max_points=0,
        )
        assert len(result) == 100

    def test_multiple_windows(self, daily_rets_300, timestamps_300):
        """Multiple windows produce independent values per entry."""
        result = _compute_rolling_series(
            daily_rets_300, timestamps_300,
            {"short": 10, "long": 50},
            _rolling_sharpe_fn,
            max_points=0,
        )
        # At index 30: short=10 should have value, long=50 should be None
        assert result[30]["short"] is not None
        assert result[30]["long"] is None
        # At index 60: both should have values
        assert result[60]["short"] is not None
        assert result[60]["long"] is not None

    def test_empty_inputs(self):
        """Empty arrays produce empty result."""
        result = _compute_rolling_series(
            np.array([]), [],
            {"w5": 5},
            _rolling_sharpe_fn,
        )
        assert result == []

    def test_single_point(self):
        """Single data point — no window can be filled."""
        result = _compute_rolling_series(
            np.array([0.01]), ["2025-01-01"],
            {"w5": 5},
            _rolling_sharpe_fn,
            max_points=0,
        )
        assert len(result) == 1
        assert result[0]["w5"] is None

    def test_custom_metric_fn(self, timestamps_100):
        """Custom metric function receives correct slice indices."""
        # Metric returns the sum of the window
        def sum_fn(rets, start, end):
            return round(float(rets[start:end].sum()), 4)

        rets = np.arange(10, dtype=float)
        timestamps = [f"2025-01-{i+1:02d}" for i in range(10)]
        result = _compute_rolling_series(
            rets, timestamps,
            {"w3": 3},
            sum_fn,
            max_points=0,
        )
        # At index 2 (3rd point): sum of [0, 1, 2] = 3.0
        assert result[2]["w3"] == 3.0
        # At index 5: sum of [3, 4, 5] = 12.0
        assert result[5]["w3"] == 12.0


# ---------------------------------------------------------------------------
# _rolling_sharpe_fn
# ---------------------------------------------------------------------------

class TestRollingSharpe:
    """Tests for the rolling Sharpe ratio metric function."""

    def test_positive_sharpe(self):
        """Positive mean with low volatility gives positive Sharpe."""
        rets = np.array([0.01, 0.02, 0.015, 0.01, 0.02, 0.005, 0.01, 0.015, 0.01, 0.02])
        result = _rolling_sharpe_fn(rets, 0, len(rets))
        assert result is not None
        assert result > 0

    def test_negative_sharpe(self):
        """Negative mean returns produce negative Sharpe."""
        rets = np.array([-0.01, -0.02, -0.015, -0.01, -0.005, -0.01, -0.02, -0.015, -0.01, -0.02])
        result = _rolling_sharpe_fn(rets, 0, len(rets))
        assert result is not None
        assert result < 0

    def test_zero_volatility_returns_none(self):
        """Constant returns (zero std) should return None."""
        rets = np.full(10, 0.01)
        result = _rolling_sharpe_fn(rets, 0, 10)
        assert result is None

    def test_annualized_with_sqrt_365(self):
        """Verify annualization uses sqrt(365)."""
        rets = np.array([0.001] * 50 + [-0.001] * 50)
        daily_sharpe = float(rets.mean() / rets.std(ddof=1))
        annualized = round(daily_sharpe * np.sqrt(365), 4)
        result = _rolling_sharpe_fn(rets, 0, len(rets))
        assert result == annualized

    def test_window_slicing(self):
        """Slicing indices are respected correctly."""
        rets = np.concatenate([np.full(5, 0.02), np.full(5, -0.02)])
        # First half (positive)
        r1 = _rolling_sharpe_fn(rets, 0, 5)
        # Second half (negative)
        r2 = _rolling_sharpe_fn(rets, 5, 10)
        assert r1 is None  # constant returns → zero std → None
        assert r2 is None  # constant returns → zero std → None

        # Mixed slice that has variance
        r3 = _rolling_sharpe_fn(rets, 3, 8)
        assert r3 is not None


# ---------------------------------------------------------------------------
# _rolling_sortino_fn
# ---------------------------------------------------------------------------

class TestRollingSortino:
    """Tests for the rolling Sortino ratio metric function."""

    def test_all_positive_returns(self):
        """No downside returns → downside std = 0 → None."""
        rets = np.array([0.01, 0.02, 0.015, 0.01, 0.02])
        result = _rolling_sortino_fn(rets, 0, len(rets))
        assert result is None

    def test_mixed_returns_positive_mean(self):
        """Positive mean with some negative returns gives positive Sortino."""
        rets = np.array([0.02, -0.01, 0.03, -0.005, 0.015, 0.01, -0.008, 0.02, 0.01, -0.003])
        result = _rolling_sortino_fn(rets, 0, len(rets))
        assert result is not None
        assert result > 0

    def test_all_negative_returns(self):
        """All negative returns give negative Sortino."""
        rets = np.array([-0.01, -0.02, -0.015, -0.01, -0.005, -0.01, -0.02, -0.015, -0.01, -0.02])
        result = _rolling_sortino_fn(rets, 0, len(rets))
        assert result is not None
        assert result < 0

    def test_single_negative_return(self):
        """Only one negative return → downside std needs ddof=1, len(ds)>1 → None."""
        rets = np.array([0.01, 0.02, -0.005, 0.01, 0.02])
        result = _rolling_sortino_fn(rets, 0, len(rets))
        # Only 1 negative → ds.std(ddof=1) would fail or be 0 → None
        assert result is None


# ---------------------------------------------------------------------------
# _rolling_volatility_fn
# ---------------------------------------------------------------------------

class TestRollingVolatility:
    """Tests for the rolling volatility metric function."""

    def test_basic_volatility(self):
        """Returns annualized volatility."""
        rets = np.array([0.01, -0.01, 0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.005, -0.005])
        result = _rolling_volatility_fn(rets, 0, len(rets))
        expected = round(float(rets.std(ddof=1)) * np.sqrt(365), 4)
        assert result == expected

    def test_always_returns_value(self):
        """Volatility function always returns a value (even for constant returns)."""
        rets = np.full(10, 0.01)
        result = _rolling_volatility_fn(rets, 0, 10)
        assert result is not None
        assert result == 0.0  # std of constant is 0


# ---------------------------------------------------------------------------
# _rolling_cumret_fn
# ---------------------------------------------------------------------------

class TestRollingCumret:
    """Tests for the rolling cumulative return metric function."""

    def test_zero_returns(self):
        """Zero returns produce 0% cumulative return."""
        rets = np.zeros(10)
        result = _rolling_cumret_fn(rets, 0, 10)
        assert result == 0.0

    def test_positive_returns(self):
        """1% daily for 10 days ≈ 10.46% cumulative."""
        rets = np.full(10, 0.01)
        result = _rolling_cumret_fn(rets, 0, 10)
        expected = round(float(np.prod(1 + rets) - 1) * 100, 4)
        assert result == expected

    def test_negative_returns(self):
        """-1% daily for 10 days gives negative cumulative return."""
        rets = np.full(10, -0.01)
        result = _rolling_cumret_fn(rets, 0, 10)
        assert result is not None
        assert result < 0

    def test_window_slicing(self):
        """Only the sliced window is used."""
        rets = np.array([0.0, 0.0, 0.0, 0.1, 0.1, 0.0, 0.0])
        # Slice [3:5] = [0.1, 0.1] → (1.1 * 1.1 - 1) * 100 = 21.0
        result = _rolling_cumret_fn(rets, 3, 5)
        assert result == round((1.1 * 1.1 - 1) * 100, 4)


# ---------------------------------------------------------------------------
# _make_rolling_beta_fn
# ---------------------------------------------------------------------------

class TestRollingBeta:
    """Tests for the rolling beta metric function factory."""

    def test_perfect_correlation(self):
        """Beta = 1 when strategy returns equal benchmark returns."""
        rets = np.array([0.01, -0.01, 0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.005, -0.005])
        beta_fn = _make_rolling_beta_fn(rets)
        result = beta_fn(rets, 0, len(rets))
        assert result is not None
        assert abs(result - 1.0) < 0.001

    def test_double_leverage(self):
        """Beta ≈ 2 when strategy = 2 * benchmark."""
        benchmark = np.array([0.01, -0.01, 0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.005, -0.005])
        strategy = benchmark * 2
        beta_fn = _make_rolling_beta_fn(benchmark)
        result = beta_fn(strategy, 0, len(strategy))
        assert result is not None
        assert abs(result - 2.0) < 0.001

    def test_inverse_correlation(self):
        """Beta ≈ -1 when strategy = -benchmark."""
        benchmark = np.array([0.01, -0.01, 0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.005, -0.005])
        strategy = -benchmark
        beta_fn = _make_rolling_beta_fn(benchmark)
        result = beta_fn(strategy, 0, len(strategy))
        assert result is not None
        assert abs(result - (-1.0)) < 0.001

    def test_zero_benchmark_variance(self):
        """Constant benchmark returns (zero variance) → None."""
        benchmark = np.full(10, 0.01)
        strategy = np.array([0.01, -0.01, 0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.005, -0.005])
        beta_fn = _make_rolling_beta_fn(benchmark)
        result = beta_fn(strategy, 0, len(strategy))
        assert result is None

    def test_window_slicing(self):
        """Beta function respects slice indices for both arrays."""
        benchmark = np.concatenate([np.full(5, 0.01), np.array([0.01, -0.01, 0.02, -0.02, 0.015])])
        strategy = np.concatenate([np.full(5, 0.02), np.array([0.02, -0.02, 0.04, -0.04, 0.03])])
        beta_fn = _make_rolling_beta_fn(benchmark)
        # First 5 points: constant → zero variance → None
        r1 = beta_fn(strategy, 0, 5)
        assert r1 is None
        # Last 5 points: strategy = 2 * benchmark → beta ≈ 2
        r2 = beta_fn(strategy, 5, 10)
        assert r2 is not None
        assert abs(r2 - 2.0) < 0.001


# ---------------------------------------------------------------------------
# Integration: _compute_rolling_series with each metric
# ---------------------------------------------------------------------------

class TestRollingSeriesIntegration:
    """End-to-end tests combining _compute_rolling_series with metric functions."""

    def test_sharpe_series(self, daily_rets_100, timestamps_100):
        """Rolling Sharpe produces reasonable values."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w10": 10},
            _rolling_sharpe_fn,
            max_points=0,
        )
        values = [r["w10"] for r in result if r["w10"] is not None]
        assert len(values) > 0
        # Sharpe values should be finite
        for v in values:
            assert np.isfinite(v)

    def test_sortino_series(self, daily_rets_100, timestamps_100):
        """Rolling Sortino works end-to-end."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w20": 20},
            _rolling_sortino_fn,
            max_points=0,
        )
        values = [r["w20"] for r in result if r["w20"] is not None]
        # Some windows may be None (if no downside), that's OK
        for v in values:
            assert np.isfinite(v)

    def test_volatility_series(self, daily_rets_100, timestamps_100):
        """Rolling volatility is always positive."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w10": 10},
            _rolling_volatility_fn,
            max_points=0,
        )
        values = [r["w10"] for r in result if r["w10"] is not None]
        assert len(values) > 0
        for v in values:
            assert v >= 0

    def test_cumret_series(self, daily_rets_100, timestamps_100):
        """Rolling cumulative returns work end-to-end."""
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w10": 10},
            _rolling_cumret_fn,
            max_points=0,
        )
        values = [r["w10"] for r in result if r["w10"] is not None]
        assert len(values) > 0

    def test_beta_series(self, daily_rets_100, timestamps_100):
        """Rolling beta with a correlated benchmark."""
        rng = np.random.default_rng(seed=7)
        benchmark = rng.normal(0.001, 0.01, size=100)
        beta_fn = _make_rolling_beta_fn(benchmark)
        result = _compute_rolling_series(
            daily_rets_100, timestamps_100,
            {"w20": 20},
            beta_fn,
            max_points=0,
        )
        values = [r["w20"] for r in result if r["w20"] is not None]
        assert len(values) > 0
        for v in values:
            assert np.isfinite(v)

    def test_multi_window_sharpe(self, daily_rets_300, timestamps_300):
        """3m/6m/12m windows match expected production configuration."""
        result = _compute_rolling_series(
            daily_rets_300, timestamps_300,
            {"rolling_3m": 63, "rolling_6m": 126, "rolling_12m": 252},
            _rolling_sharpe_fn,
            max_points=0,
        )
        assert len(result) == 300
        # 3m window fills at index 62
        assert result[61]["rolling_3m"] is None
        assert result[62]["rolling_3m"] is not None
        # 6m window fills at index 125
        assert result[124]["rolling_6m"] is None
        assert result[125]["rolling_6m"] is not None
        # 12m window fills at index 251
        assert result[250]["rolling_12m"] is None
        assert result[251]["rolling_12m"] is not None
