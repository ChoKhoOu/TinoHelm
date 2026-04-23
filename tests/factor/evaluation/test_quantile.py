"""Unit tests — ``tinohelm.factor.evaluation.quantile``.

Locks the numerical contract of ``compute_quantile_returns``:

* Schema — exactly 3 keys (``avg_returns``, ``cum_returns``, ``is_monotonic``).
* Thresholds — the ``< n_quantiles * 20`` short-circuit and the
  ``duplicates="drop"`` → all-NaN-bin collapse must both route to the
  canonical empty payload.
* Monotonicity uses ``>=`` (Q1 ≥ Q2 ≥ … ≥ QN), not strict ``>``, so a
  flat quantile profile still counts as monotonic.
* Precision — avg returns at 8 dp, cumulative points at 6 dp; sampling
  targets ≤ 100 points.

Pure-logic, deterministic, NT-free, < 200 ms.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.evaluation.quantile import compute_quantile_returns


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def monotone_pair():
    """Factor = index, fwd = -factor so lower-factor buckets earn more."""
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    factor = pd.Series(np.arange(n, dtype=float), index=idx)
    fwd = pd.Series(-np.arange(n, dtype=float), index=idx)
    return factor, fwd


@pytest.fixture
def noisy_pair():
    rng = np.random.default_rng(42)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    factor = pd.Series(rng.normal(0, 1, n), index=idx)
    fwd = pd.Series(0.1 * factor.values + rng.normal(0, 1, n), index=idx)
    return factor, fwd


# ──────────────────────────────────────────────────────────────────────
# Contract
# ──────────────────────────────────────────────────────────────────────


EMPTY = {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}


class TestSchema:
    def test_exactly_three_keys(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        assert set(out.keys()) == {"avg_returns", "cum_returns", "is_monotonic"}

    def test_avg_returns_labels_are_Q1_through_Qn(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert sorted(out["avg_returns"].keys()) == ["Q1", "Q2", "Q3", "Q4", "Q5"]

    def test_cum_returns_has_same_labels_as_avg(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=3)
        assert set(out["avg_returns"].keys()) == set(out["cum_returns"].keys())

    def test_is_monotonic_is_bool(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        assert isinstance(out["is_monotonic"], bool)


# ──────────────────────────────────────────────────────────────────────
# Short-circuit boundaries
# ──────────────────────────────────────────────────────────────────────


class TestShortCircuit:
    def test_paired_below_n_times_20_returns_empty(self):
        # 5 quantiles × 20 = 100 minimum; give 99 → empty.
        idx = pd.date_range("2024-01-01", periods=99, freq="1h")
        factor = pd.Series(np.arange(99, dtype=float), index=idx)
        fwd = pd.Series(np.arange(99, dtype=float), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY

    def test_paired_exactly_n_times_20_does_not_short_circuit(self):
        # Exactly 100 pairs with 5 quantiles — the `< 100` guard lets this
        # through (strict less-than).  We just assert a non-empty result,
        # not specific numbers, to avoid locking numerical noise.
        idx = pd.date_range("2024-01-01", periods=100, freq="1h")
        factor = pd.Series(np.arange(100, dtype=float), index=idx)
        fwd = pd.Series(-np.arange(100, dtype=float), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out["avg_returns"] != {}

    def test_custom_n_quantiles_scales_the_threshold(self):
        # 3 quantiles × 20 = 60 — 59 pairs should short-circuit.
        idx = pd.date_range("2024-01-01", periods=59, freq="1h")
        factor = pd.Series(np.arange(59, dtype=float), index=idx)
        fwd = pd.Series(np.arange(59, dtype=float), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=3)
        assert out == EMPTY

    def test_nan_rows_are_dropped_before_threshold(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series(np.arange(200, dtype=float), index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        # Blow out 150 rows → only 50 remain, below 5*20 = 100.
        factor.iloc[50:] = np.nan
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY

    def test_inf_rows_are_dropped_before_threshold(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series(np.arange(200, dtype=float), index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        factor.iloc[50:] = np.inf
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY


# ──────────────────────────────────────────────────────────────────────
# Degenerate factor handling
# ──────────────────────────────────────────────────────────────────────


class TestDegenerateInput:
    def test_constant_factor_yields_empty_avg_returns(self):
        idx = pd.date_range("2024-01-01", periods=300, freq="1h")
        factor = pd.Series([1.0] * 300, index=idx)
        fwd = pd.Series(np.arange(300, dtype=float), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # All-NaN bins get dropped → empty avg/cum.
        assert out == EMPTY

    def test_two_unique_values_with_five_quantiles_handles_gracefully(self):
        # Only two unique factor values → qcut with duplicates='drop' collapses
        # many bins.  Must not raise and must produce a well-formed result.
        rng = np.random.default_rng(0)
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series(rng.choice([1.0, 2.0], size=500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Fewer than 5 buckets survive; labels are a subset of Q1..Q5.
        assert set(out["avg_returns"].keys()) <= {"Q1", "Q2", "Q3", "Q4", "Q5"}


# ──────────────────────────────────────────────────────────────────────
# Monotonicity
# ──────────────────────────────────────────────────────────────────────


class TestMonotonicity:
    def test_perfect_descending_pair_is_monotonic(self, monotone_pair):
        factor, fwd = monotone_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Lowest-factor bucket (Q1) → highest fwd returns; monotone ≥.
        assert out["is_monotonic"] is True
        vals = list(out["avg_returns"].values())
        for a, b in zip(vals, vals[1:]):
            assert a >= b

    def test_random_pair_usually_not_monotonic(self):
        rng = np.random.default_rng(12345)
        n = 2000
        idx = pd.date_range("2024-01-01", periods=n, freq="1h")
        factor = pd.Series(rng.normal(0, 1, n), index=idx)
        fwd = pd.Series(rng.normal(0, 1, n), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Pure noise at 2000 samples → very unlikely to be monotone by chance.
        assert out["is_monotonic"] is False

    def test_flat_profile_counts_as_monotonic(self):
        # Perfectly equal avg returns across quantiles — ≥ is True everywhere,
        # so is_monotonic must be True (docstring explicitly says ≥, not >).
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series(np.tile(np.arange(5), 100), index=idx)
        fwd = pd.Series([1.0] * 500, index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # All quantile means == 1.0 → flat → monotonic by ≥ definition.
        if out["avg_returns"]:  # if the function produced any buckets
            assert out["is_monotonic"] is True


# ──────────────────────────────────────────────────────────────────────
# Precision / structure
# ──────────────────────────────────────────────────────────────────────


class TestPrecision:
    def test_avg_returns_values_rounded_to_8dp(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for v in out["avg_returns"].values():
            assert round(v, 8) == v

    def test_cum_returns_values_rounded_to_6dp(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for points in out["cum_returns"].values():
            for point in points:
                assert round(point["cum_ret"], 6) == point["cum_ret"]

    def test_cum_returns_sampled_to_at_most_100_points_per_bucket(self):
        # 2000 rows / 5 buckets ≈ 400 per bucket; sampling step = len/100.
        rng = np.random.default_rng(99)
        n = 2000
        idx = pd.date_range("2024-01-01", periods=n, freq="1h")
        factor = pd.Series(rng.normal(0, 1, n), index=idx)
        fwd = pd.Series(rng.normal(0, 0.1, n), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        for points in out["cum_returns"].values():
            # step = max(1, len//100); sampled length = ceil(len/step).
            # For len ≈ 400 → step = 4 → sampled ≈ 100.
            assert len(points) <= 110, f"too many cum points: {len(points)}"

    def test_cum_returns_point_shape(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for points in out["cum_returns"].values():
            for point in points:
                assert set(point.keys()) == {"date", "cum_ret"}
                # date is ISO string
                assert isinstance(point["date"], str)
                # Either contains T (ISO with time) or pure date component.
                pd.Timestamp(point["date"])  # will raise if unparsable


# ──────────────────────────────────────────────────────────────────────
# Defensive copies (payload isolation)
# ──────────────────────────────────────────────────────────────────────


class TestDefensiveBehavior:
    def test_caller_mutation_of_result_does_not_affect_rerun(self, noisy_pair):
        factor, fwd = noisy_pair
        out1 = compute_quantile_returns(factor, fwd)
        out1["avg_returns"]["Q1"] = 9999
        out2 = compute_quantile_returns(factor, fwd)
        assert out2["avg_returns"]["Q1"] != 9999
