"""Tests for `tinohelm.research.analysis` — IC, decay, quantile, distribution, turnover.

Covers the full chain that backs the explorer panel + diagnostic report:

- ``forward_returns`` — alignment & log-return option
- ``compute_ic_series`` — period grouping, NaN/inf filtering, min-sample threshold
- ``compute_ic_summary`` — IR / t-stat / positive-pct math + empty-input fallback
- ``compute_rating`` — 0/1/2/3 thresholds
- ``compute_ic_decay`` + ``compute_half_life`` — multi-lag profile
- ``compute_quantile_returns`` — quantile assignment, monotonicity flag, sampling
- ``compute_distribution`` — histogram + autocorr stats
- ``compute_turnover`` — daily / annualized / fee-drag math
- ``sanitize_for_json`` — NaN/Inf scrubbing for PostgreSQL JSON columns
- ``run_explore`` — orchestration smoke test

These tests are deterministic (fixed seeds) and NT-free.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tinohelm.research import analysis as A


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def hourly_close() -> pd.Series:
    """500 hourly close prices — enough for a daily-grouped IC series with ≥20 obs/group."""
    rng = np.random.default_rng(42)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(100.0 + np.cumsum(rng.normal(0, 0.3, n)), index=idx)


@pytest.fixture
def positively_correlated_pair():
    """A factor and forward-return series with strong positive Spearman IC."""
    rng = np.random.default_rng(7)
    n = 600
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    factor = pd.Series(rng.normal(0, 1, n), index=idx)
    # Fwd return = 0.5 × factor + noise → IC strongly positive
    noise = pd.Series(rng.normal(0, 0.3, n), index=idx)
    fwd = 0.5 * factor + noise
    return factor, fwd


@pytest.fixture
def negatively_correlated_pair():
    rng = np.random.default_rng(11)
    n = 600
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    factor = pd.Series(rng.normal(0, 1, n), index=idx)
    fwd = -0.5 * factor + pd.Series(rng.normal(0, 0.3, n), index=idx)
    return factor, fwd


# ──────────────────────────────────────────────────────────────────────
# forward_returns
# ──────────────────────────────────────────────────────────────────────


class TestForwardReturns:
    def test_simple_pct_return(self):
        close = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        out = A.forward_returns(close, 1)
        # fwd[0] = 101/100 - 1 = 0.01
        assert out.iloc[0] == pytest.approx(0.01)
        # Last entry is NaN (no future bar)
        assert pd.isna(out.iloc[-1])

    def test_log_return_branch(self):
        close = pd.Series([100.0, 110.0, 121.0])
        out = A.forward_returns(close, 1, log_ret=True)
        assert out.iloc[0] == pytest.approx(math.log(110 / 100))
        assert out.iloc[1] == pytest.approx(math.log(121 / 110))
        assert pd.isna(out.iloc[-1])

    def test_period_3_returns_nan_for_last_3(self):
        close = pd.Series(np.arange(10, dtype=float) + 100)
        out = A.forward_returns(close, 3)
        assert out.iloc[-3:].isna().all()
        assert not pd.isna(out.iloc[-4])


# ──────────────────────────────────────────────────────────────────────
# compute_ic_series
# ──────────────────────────────────────────────────────────────────────


class TestComputeICSeries:
    def test_returns_dataframe_with_date_and_ic(self, hourly_close):
        factor = hourly_close.pct_change(5)
        fwd = A.forward_returns(hourly_close, 5)
        ic = A.compute_ic_series(factor, fwd, freq="D")
        assert list(ic.columns) == ["date", "ic"]
        assert len(ic) > 0
        # Every IC value must be finite
        assert np.all(np.isfinite(ic["ic"].values))

    def test_short_pair_returns_empty_frame(self):
        # Fewer than 30 valid pairs → empty frame
        idx = pd.date_range("2024-01-01", periods=20, freq="1h")
        factor = pd.Series(np.arange(20, dtype=float), index=idx)
        fwd = pd.Series(np.arange(20, dtype=float), index=idx)
        ic = A.compute_ic_series(factor, fwd, freq="D")
        assert ic.empty
        assert list(ic.columns) == ["date", "ic"]

    def test_filters_nan_and_inf(self, hourly_close):
        factor = hourly_close.pct_change(5).copy()
        fwd = A.forward_returns(hourly_close, 5).copy()
        # Inject some inf/-inf values — they must be filtered, not raise
        factor.iloc[10] = np.inf
        factor.iloc[20] = -np.inf
        fwd.iloc[30] = np.inf
        ic = A.compute_ic_series(factor, fwd, freq="D")
        assert len(ic) > 0
        assert np.all(np.isfinite(ic["ic"].values))

    def test_pearson_branch_does_not_crash(self, positively_correlated_pair):
        factor, fwd = positively_correlated_pair
        ic = A.compute_ic_series(factor, fwd, method="pearson", freq="D")
        assert len(ic) > 0
        # Strong positive structure → most IC values should be positive
        assert (ic["ic"] > 0).mean() > 0.5

    def test_groups_with_under_20_obs_skipped(self):
        # Mix of months: Jan has 25 obs, Feb has 5 (< 20 threshold).
        # The 30-pair guard at the top must not short-circuit (we have 30 total),
        # but the per-group <20 guard should skip Feb.
        rng = np.random.default_rng(3)
        jan = pd.date_range("2024-01-01", periods=25, freq="1h")
        feb = pd.date_range("2024-02-01", periods=5, freq="1h")
        idx = jan.append(feb)
        factor = pd.Series(rng.normal(0, 1, 30), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 30), index=idx)
        ic = A.compute_ic_series(factor, fwd, freq="ME")
        # Only Jan should appear (Feb < 20 obs filtered)
        assert len(ic) == 1


# ──────────────────────────────────────────────────────────────────────
# compute_ic_summary
# ──────────────────────────────────────────────────────────────────────


class TestComputeICSummary:
    _ZERO_KEYS = {"ic_mean", "ic_std", "ir", "ic_positive_pct", "ic_max_abs", "ic_tstat"}

    def test_empty_input_returns_zero_summary(self):
        empty = pd.DataFrame(columns=["date", "ic"])
        out = A.compute_ic_summary(empty)
        assert set(out.keys()) == self._ZERO_KEYS
        assert all(v == 0 for v in out.values())

    def test_missing_ic_column_returns_zero_summary(self):
        # Defensive: if a caller passes a malformed frame
        out = A.compute_ic_summary(pd.DataFrame({"foo": [1, 2, 3]}))
        assert set(out.keys()) == self._ZERO_KEYS
        assert all(v == 0 for v in out.values())

    def test_zero_std_yields_zero_ir_and_tstat(self):
        # Constant IC series → std = 0 → IR = 0 (avoid div-by-zero)
        df = pd.DataFrame({"date": ["2024-01-01"] * 5, "ic": [0.1] * 5})
        out = A.compute_ic_summary(df)
        assert out["ic_mean"] == pytest.approx(0.1)
        assert out["ic_std"] == 0
        assert out["ir"] == 0
        assert out["ic_tstat"] == 0
        assert out["ic_positive_pct"] == 1.0

    def test_simple_positive_series(self):
        df = pd.DataFrame({"date": ["d1", "d2", "d3", "d4"], "ic": [0.1, 0.2, 0.3, 0.4]})
        out = A.compute_ic_summary(df)
        assert out["ic_mean"] == pytest.approx(0.25)
        # std with ddof=0 of [0.1,0.2,0.3,0.4]
        expected_std = float(np.std([0.1, 0.2, 0.3, 0.4]))
        assert out["ic_std"] == pytest.approx(expected_std, abs=1e-6)
        assert out["ic_positive_pct"] == 1.0
        assert out["ic_max_abs"] == pytest.approx(0.4)


class TestComputeRating:
    @pytest.mark.parametrize("ir,pct,expected", [
        (1.5, 0.65, 3),   # strong: IR>1 AND pct>0.6
        (-1.5, 0.65, 3),  # uses |IR|, so negative also passes
        (0.7, 0.58, 2),   # usable: IR>0.5 AND pct>0.55
        (0.3, 0.50, 1),   # weak: IR>0.2 only
        (0.1, 0.50, 0),   # invalid
        (1.5, 0.50, 1),   # IR strong but pct fails strong+usable thresholds → only weak
        (0.7, 0.50, 1),   # IR usable but pct fails → only weak
    ])
    def test_rating_thresholds(self, ir, pct, expected):
        assert A.compute_rating({"ir": ir, "ic_positive_pct": pct}) == expected

    def test_missing_keys_default_to_zero(self):
        assert A.compute_rating({}) == 0


# ──────────────────────────────────────────────────────────────────────
# compute_ic_decay + half_life
# ──────────────────────────────────────────────────────────────────────


class TestComputeICDecay:
    def test_returns_one_dict_per_lag(self, hourly_close):
        factor = hourly_close.pct_change(5)
        out = A.compute_ic_decay(factor, hourly_close, lags=[1, 2, 5, 10])
        assert len(out) == 4
        assert [d["lag"] for d in out] == [1, 2, 5, 10]
        for d in out:
            assert "ic" in d

    def test_default_lags_are_fibonacci_like(self, hourly_close):
        factor = hourly_close.pct_change(5)
        out = A.compute_ic_decay(factor, hourly_close)
        assert [d["lag"] for d in out] == [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]

    def test_too_few_pairs_yields_zero_ic(self):
        # Tiny series → < 30 pairs after dropna → IC = 0
        close = pd.Series(np.arange(20, dtype=float) + 100)
        factor = pd.Series(np.arange(20, dtype=float), index=close.index)
        out = A.compute_ic_decay(factor, close, lags=[1])
        assert out[0]["ic"] == 0


class TestComputeHalfLife:
    def test_empty_decay_returns_none(self):
        assert A.compute_half_life([]) is None

    def test_max_ic_below_threshold_returns_none(self):
        # All very small ICs (max < 0.001) → no meaningful peak
        decay = [{"lag": 1, "ic": 0.0005}, {"lag": 2, "ic": 0.0002}]
        assert A.compute_half_life(decay) is None

    def test_finds_lag_at_or_below_half_max(self):
        # max=0.4, half=0.2; first lag with |IC|<=0.2 is lag 5
        decay = [
            {"lag": 1, "ic": 0.40},
            {"lag": 2, "ic": 0.30},
            {"lag": 3, "ic": 0.25},
            {"lag": 5, "ic": 0.10},
            {"lag": 8, "ic": 0.05},
        ]
        assert A.compute_half_life(decay) == 5

    def test_uses_abs_value(self):
        # max abs is 0.4 (from -0.4); first |IC| <= 0.2 is lag 3
        decay = [
            {"lag": 1, "ic": -0.40},
            {"lag": 2, "ic": -0.30},
            {"lag": 3, "ic": 0.10},
        ]
        assert A.compute_half_life(decay) == 3

    def test_returns_last_lag_when_no_drop_found(self):
        # IC stays near max forever → half_life = last lag in decay
        decay = [{"lag": 1, "ic": 0.5}, {"lag": 5, "ic": 0.45}]
        assert A.compute_half_life(decay) == 5


# ──────────────────────────────────────────────────────────────────────
# compute_quantile_returns
# ──────────────────────────────────────────────────────────────────────


class TestComputeQuantileReturns:
    def test_strongly_correlated_yields_monotonic_quantiles(self, positively_correlated_pair):
        factor, fwd = positively_correlated_pair
        out = A.compute_quantile_returns(factor, fwd, n_quantiles=5)
        # avg_returns should sort in increasing order Q1<Q2<...<Q5
        avgs = list(out["avg_returns"].values())
        assert len(avgs) == 5
        # Monotonic increasing
        assert all(avgs[i] <= avgs[i + 1] for i in range(len(avgs) - 1))
        # is_monotonic flag in the function checks Q1 >= Q2 >= ... (decreasing)
        # so for strongly increasing it should be False
        assert out["is_monotonic"] is False

    def test_short_series_returns_empty(self):
        # Need at least n_quantiles*20 = 100 pairs for n_quantiles=5
        idx = pd.date_range("2024-01-01", periods=50, freq="1h")
        out = A.compute_quantile_returns(
            pd.Series(np.arange(50, dtype=float), index=idx),
            pd.Series(np.arange(50, dtype=float), index=idx),
            n_quantiles=5,
        )
        assert out == {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}

    def test_degenerate_factor_returns_empty(self):
        # Regression: previously this crashed with `ValueError: cannot convert float NaN
        # to integer` because qcut(duplicates="drop") returns NaN labels for all rows
        # when the factor has too few unique values, and the try/except only caught
        # qcut raising — not the downstream NaN issue.
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series([1.0] * 200, index=idx)  # all identical → no quantiles
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        out = A.compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}

    def test_cum_returns_sampled_to_about_100_points(self, positively_correlated_pair):
        factor, fwd = positively_correlated_pair  # 600 pairs / 5 quantiles = 120 per group
        out = A.compute_quantile_returns(factor, fwd, n_quantiles=5)
        for label, series in out["cum_returns"].items():
            # Each group has ~120 points; sampled with step = max(1, 120//100) = 1 → all kept.
            # With more bars per group the step would grow. Just verify sane size.
            assert 1 <= len(series) <= 120
            # Each entry has "date" + "cum_ret"
            for entry in series:
                assert "date" in entry and "cum_ret" in entry


# ──────────────────────────────────────────────────────────────────────
# compute_distribution
# ──────────────────────────────────────────────────────────────────────


class TestComputeDistribution:
    def test_empty_or_short_returns_empty(self):
        out = A.compute_distribution(pd.Series([1.0, 2.0, 3.0]))
        assert out == {"histogram": [], "stats": {}}

    def test_histogram_has_n_bins_entries(self):
        rng = np.random.default_rng(0)
        s = pd.Series(rng.normal(0, 1, 200))
        out = A.compute_distribution(s, n_bins=20)
        assert len(out["histogram"]) == 20
        # Bin counts sum to total observations
        assert sum(b["count"] for b in out["histogram"]) == 200
        # Bins are contiguous: end[i] == start[i+1]
        for i in range(len(out["histogram"]) - 1):
            assert out["histogram"][i]["bin_end"] == out["histogram"][i + 1]["bin_start"]

    def test_stats_keys_complete(self):
        rng = np.random.default_rng(1)
        s = pd.Series(rng.normal(0, 1, 200))
        out = A.compute_distribution(s)
        expected = {"mean", "std", "skew", "kurtosis", "min", "max", "zero_pct", "autocorr_1", "autocorr_5"}
        assert set(out["stats"].keys()) == expected

    def test_zero_pct_counts_exact_zeros(self):
        s = pd.Series([0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0,
                       0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        out = A.compute_distribution(s, n_bins=5)
        # 8/20 = 0.4
        assert out["stats"]["zero_pct"] == pytest.approx(0.4)

    def test_filters_inf_from_clean_array(self):
        rng = np.random.default_rng(2)
        vals = rng.normal(0, 1, 200).tolist()
        vals[5] = math.inf
        vals[10] = -math.inf
        s = pd.Series(vals)
        out = A.compute_distribution(s)
        # Should not crash; histogram count = 198 (2 infs removed)
        assert sum(b["count"] for b in out["histogram"]) == 198


# ──────────────────────────────────────────────────────────────────────
# compute_turnover
# ──────────────────────────────────────────────────────────────────────


class TestComputeTurnover:
    def test_empty_keys_when_too_few_obs(self):
        idx = pd.date_range("2024-01-01", periods=50, freq="1h")
        factor = pd.Series(np.arange(50, dtype=float), index=idx)
        fwd = pd.Series(np.arange(50, dtype=float), index=idx)
        out = A.compute_turnover(factor, fwd, n_quantiles=5)
        assert out == {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}

    def test_full_keys_returned_for_sufficient_data(self, positively_correlated_pair):
        factor, fwd = positively_correlated_pair
        out = A.compute_turnover(factor, fwd, n_quantiles=5)
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}
        assert 0 <= out["daily"] <= 1
        # annualized ≈ daily × 252
        assert out["annualized"] == pytest.approx(out["daily"] * 252, rel=1e-3)

    def test_fee_drag_scales_with_fee_rate(self, positively_correlated_pair):
        factor, fwd = positively_correlated_pair
        low = A.compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.0001)
        high = A.compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.0010)
        # higher fee rate → larger fee_drag_monthly when there's any turnover
        if low["daily"] > 0:
            assert high["fee_drag_monthly"] > low["fee_drag_monthly"]

    def test_degenerate_factor_returns_zero_turnover(self):
        # Regression: with a constant factor, qcut returns NaN labels and the
        # numpy NaN!=NaN comparison was previously yielding 100% turnover. The
        # fix drops NaN q labels first.
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series([1.0] * 200, index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        out = A.compute_turnover(factor, fwd, n_quantiles=5)
        assert out == {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


# ──────────────────────────────────────────────────────────────────────
# sanitize_for_json
# ──────────────────────────────────────────────────────────────────────


class TestSanitizeForJson:
    def test_nan_replaced_with_none(self):
        assert A.sanitize_for_json(float("nan")) is None

    def test_pos_inf_replaced_with_none(self):
        assert A.sanitize_for_json(float("inf")) is None

    def test_neg_inf_replaced_with_none(self):
        assert A.sanitize_for_json(float("-inf")) is None

    def test_finite_float_passes_through(self):
        assert A.sanitize_for_json(3.14) == 3.14

    def test_int_str_passes_through(self):
        assert A.sanitize_for_json(42) == 42
        assert A.sanitize_for_json("hello") == "hello"

    def test_nested_dict_recurses(self):
        out = A.sanitize_for_json({"a": float("nan"), "b": {"c": float("inf"), "d": 1.0}})
        assert out == {"a": None, "b": {"c": None, "d": 1.0}}

    def test_nested_list_recurses(self):
        out = A.sanitize_for_json([1.0, float("nan"), [float("inf"), 2.0]])
        assert out == [1.0, None, [None, 2.0]]

    def test_tuple_returned_as_list(self):
        # The function recurses into tuples but returns a list (JSON-friendly)
        out = A.sanitize_for_json((1.0, float("nan"), 3.0))
        assert out == [1.0, None, 3.0]

    def test_numpy_float_with_nan_handled(self):
        # numpy float64('nan') is also a float subclass — must be sanitized
        assert A.sanitize_for_json(np.float64("nan")) is None
        assert A.sanitize_for_json(np.float64("inf")) is None
        assert A.sanitize_for_json(np.float64(2.5)) == pytest.approx(2.5)

    def test_none_passes_through(self):
        assert A.sanitize_for_json(None) is None

    def test_bool_passes_through(self):
        # bool is technically a numeric type in Python, but sanitize_for_json's
        # float check uses `isinstance(obj, float)` which excludes bool — verify.
        assert A.sanitize_for_json(True) is True
        assert A.sanitize_for_json(False) is False


# ──────────────────────────────────────────────────────────────────────
# run_explore (orchestration)
# ──────────────────────────────────────────────────────────────────────


class TestRunExplore:
    def test_returns_full_payload_shape(self, hourly_close):
        factor = hourly_close.pct_change(5)
        out = A.run_explore(factor, hourly_close, forward_period=5, n_quantiles=5)
        assert set(out.keys()) == {
            "summary", "ic_series", "ic_decay", "quantile_returns",
            "distribution", "turnover",
        }
        # summary has rating + half_life_bars added by run_explore
        assert "rating" in out["summary"]
        assert "half_life_bars" in out["summary"]
        # ic_decay is a list of dicts; ic_series is a list of dicts (from .to_dict("records"))
        assert isinstance(out["ic_decay"], list)
        assert isinstance(out["ic_series"], list)
        assert isinstance(out["quantile_returns"], dict)

    def test_log_return_branch_does_not_crash(self, hourly_close):
        factor = hourly_close.pct_change(5)
        out = A.run_explore(factor, hourly_close, forward_period=5, log_ret=True)
        # Just verify the same shape; the math is exercised in TestForwardReturns
        assert "summary" in out

    def test_short_series_returns_empty_ic_but_does_not_crash(self):
        # Fewer observations than the IC threshold — must not raise
        idx = pd.date_range("2024-01-01", periods=40, freq="1h")
        close = pd.Series(np.linspace(100, 105, 40), index=idx)
        factor = close.pct_change(5)
        out = A.run_explore(factor, close, forward_period=5, n_quantiles=5)
        assert out["ic_series"] == []
        # summary still has its 6 keys + rating + half_life_bars
        assert "ic_mean" in out["summary"]
