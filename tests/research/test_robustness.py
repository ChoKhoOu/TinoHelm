"""Tests for `tinohelm.research.robustness` — shuffle test + subsample IC.

The shuffle test internally uses ProcessPoolExecutor for the parallel permutation
loop, which is slow and brittle in pytest. We split coverage into:

1. ``_single_shuffle_ic`` — the worker function (pure, fully unit-testable)
2. ``summarize_shuffle_distribution`` — the new pure helper that aggregates the
   raw shuffle ICs into the wire-format payload (histogram + p_value + significant)
3. ``shuffle_test`` — one end-to-end run with a small ``n_iter`` to verify the
   parallel pipeline still wires correctly
4. ``subsample_ic`` — period-grouped IC list

We also lock the public ``SHUFFLE_SIGNIFICANCE_THRESHOLD`` and
``SHUFFLE_MIN_OBSERVATIONS`` constants so they can't drift silently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tinohelm.research import robustness as R


# ──────────────────────────────────────────────────────────────────────
# Constants contract
# ──────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_significance_threshold_is_five_percent(self):
        # Standard alpha=0.05 — locked so a future "alpha=0.01" tweak is
        # impossible to ship without intentionally updating this constant
        # (and therefore the frontend that consumes "significant").
        assert R.SHUFFLE_SIGNIFICANCE_THRESHOLD == 0.05

    def test_min_observations_threshold_is_one_hundred(self):
        # Below this, shuffle_test short-circuits without spawning processes.
        assert R.SHUFFLE_MIN_OBSERVATIONS == 100


# ──────────────────────────────────────────────────────────────────────
# _single_shuffle_ic worker
# ──────────────────────────────────────────────────────────────────────


class TestSingleShuffleIc:
    def test_returns_finite_float(self):
        rng = np.random.default_rng(0)
        f = rng.normal(0, 1, 200)
        r = rng.normal(0, 1, 200)
        ic = R._single_shuffle_ic((f, r, 1))
        assert isinstance(ic, float)
        assert np.isfinite(ic)

    def test_deterministic_for_same_seed(self):
        rng = np.random.default_rng(0)
        f = rng.normal(0, 1, 200)
        r = rng.normal(0, 1, 200)
        ic1 = R._single_shuffle_ic((f, r, 42))
        ic2 = R._single_shuffle_ic((f, r, 42))
        assert ic1 == ic2

    def test_different_seeds_produce_different_ics(self):
        rng = np.random.default_rng(0)
        f = rng.normal(0, 1, 200)
        r = rng.normal(0, 1, 200)
        ics = {R._single_shuffle_ic((f, r, s)) for s in range(20)}
        # Almost certainly all different — at least 18 unique out of 20
        assert len(ics) >= 18

    def test_returns_zero_when_correlation_undefined(self):
        # Constant arrays → spearmanr returns NaN → function returns 0.0.
        # scipy emits a ConstantInputWarning in this case (expected, not a bug).
        import warnings

        from scipy.stats import ConstantInputWarning

        f = np.array([1.0] * 50)
        r = np.array([1.0] * 50)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConstantInputWarning)
            assert R._single_shuffle_ic((f, r, 1)) == 0.0


# ──────────────────────────────────────────────────────────────────────
# summarize_shuffle_distribution (the new pure helper)
# ──────────────────────────────────────────────────────────────────────


class TestSummarizeShuffleDistribution:
    def test_returns_four_keys(self):
        out = R.summarize_shuffle_distribution(0.1, [0.05, -0.05, 0.0])
        assert set(out.keys()) == {"real_ic", "shuffle_distribution", "p_value", "significant"}

    def test_empty_input_returns_no_signal_payload(self):
        out = R.summarize_shuffle_distribution(0.5, [])
        assert out == {
            "real_ic": 0.5,
            "shuffle_distribution": [],
            "p_value": 1.0,
            "significant": False,
        }

    def test_p_value_is_fraction_above_or_equal_real_ic(self):
        # real_ic = 0.10, shuffle = [-0.05, 0.05, 0.10, 0.15, 0.20]
        # |shuffle| = [0.05, 0.05, 0.10, 0.15, 0.20]
        # >= 0.10: indices 2,3,4 → 3/5 = 0.6
        out = R.summarize_shuffle_distribution(0.10, [-0.05, 0.05, 0.10, 0.15, 0.20])
        assert out["p_value"] == pytest.approx(0.6)

    def test_significant_when_p_below_threshold(self):
        # real_ic large, shuffles all small → p_value = 0
        out = R.summarize_shuffle_distribution(0.5, [0.01, -0.02, 0.01, -0.01, 0.0] * 20)
        assert out["p_value"] < 0.05
        assert out["significant"] is True

    def test_not_significant_when_p_at_or_above_threshold(self):
        # All shuffles >= real_ic → p = 1.0
        out = R.summarize_shuffle_distribution(0.0, [0.5, 0.4, 0.3])
        assert out["p_value"] == 1.0
        assert out["significant"] is False

    def test_p_value_threshold_is_strict_less_than(self):
        # Exactly at the threshold: 0.05 should be NOT significant.
        # Construct: 100 shuffles, 5 with |IC| >= |real_ic| → p = 0.05 exactly.
        shuffles = [0.0] * 95 + [0.5] * 5
        out = R.summarize_shuffle_distribution(0.4, shuffles)
        assert out["p_value"] == pytest.approx(0.05)
        assert out["significant"] is False

    def test_histogram_has_default_50_bins(self):
        rng = np.random.default_rng(0)
        out = R.summarize_shuffle_distribution(0.0, rng.normal(0, 0.1, 500).tolist())
        assert len(out["shuffle_distribution"]) == 50

    def test_custom_bin_count_respected(self):
        out = R.summarize_shuffle_distribution(0.0, [0.0, 0.1, 0.2, 0.3, 0.4], bins=5)
        assert len(out["shuffle_distribution"]) == 5

    def test_histogram_counts_sum_to_input_size(self):
        ics = [0.01, 0.02, 0.03, -0.01, -0.02, -0.03] * 10
        out = R.summarize_shuffle_distribution(0.0, ics, bins=10)
        assert sum(b["count"] for b in out["shuffle_distribution"]) == len(ics)

    def test_histogram_bins_are_contiguous(self):
        rng = np.random.default_rng(0)
        out = R.summarize_shuffle_distribution(0.0, rng.normal(0, 0.1, 200).tolist(), bins=20)
        bins = out["shuffle_distribution"]
        for i in range(len(bins) - 1):
            assert bins[i]["bin_end"] == bins[i + 1]["bin_start"]

    def test_real_ic_inf_replaced_with_zero(self):
        # Defensive: inf real_ic → coerced to 0 (not propagated to JSON output)
        out = R.summarize_shuffle_distribution(float("inf"), [0.0, 0.1])
        assert out["real_ic"] == 0.0

    def test_real_ic_nan_replaced_with_zero(self):
        out = R.summarize_shuffle_distribution(float("nan"), [0.0, 0.1])
        assert out["real_ic"] == 0.0

    def test_real_ic_rounded_to_six_decimals(self):
        out = R.summarize_shuffle_distribution(0.12345678, [0.0, 0.1])
        assert out["real_ic"] == pytest.approx(0.123457)


# ──────────────────────────────────────────────────────────────────────
# shuffle_test end-to-end (small n_iter, single worker)
# ──────────────────────────────────────────────────────────────────────


class TestShuffleTest:
    def test_returns_no_signal_payload_for_tiny_input(self):
        # < SHUFFLE_MIN_OBSERVATIONS = 100 → short-circuits, no pool spawn.
        idx = pd.date_range("2024-01-01", periods=50, freq="1h")
        factor = pd.Series(np.arange(50, dtype=float), index=idx)
        fwd = pd.Series(np.arange(50, dtype=float), index=idx)
        out = R.shuffle_test(factor, fwd, n_iter=10, max_workers=1)
        assert out == {"real_ic": 0, "shuffle_distribution": [], "p_value": 1.0, "significant": False}

    def test_filters_inf_and_nan_before_count_check(self):
        # 200 rows, but 50 are NaN/inf → still 150 valid pairs > 100 threshold
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor_vals = list(np.arange(150, dtype=float)) + [np.nan, np.inf, -np.inf] * 17 + [1.0]
        factor = pd.Series(factor_vals[:200], index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        # Should not raise; just reach the pool with a smaller paired set.
        out = R.shuffle_test(factor, fwd, n_iter=4, max_workers=1)
        assert "real_ic" in out
        assert "shuffle_distribution" in out


# ──────────────────────────────────────────────────────────────────────
# subsample_ic
# ──────────────────────────────────────────────────────────────────────


class TestSubsampleIc:
    def test_groups_by_freq_and_returns_per_period_ics(self):
        # 3 months of hourly data, all groups should have ≥ 20 obs
        rng = np.random.default_rng(7)
        idx = pd.date_range("2024-01-01", periods=24 * 90, freq="1h")
        factor = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
        fwd = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
        out = R.subsample_ic(factor, fwd, freq="ME")
        # Should produce 3 monthly entries
        assert len(out) == 3
        for entry in out:
            assert set(entry.keys()) == {"period", "ic"}
            # period formatted as YYYY-MM
            assert len(entry["period"]) == 7 and entry["period"][4] == "-"

    def test_skips_groups_below_min_obs(self):
        # Two monthly groups: Jan has 25 obs, Feb has 5 — Feb skipped.
        rng = np.random.default_rng(3)
        jan = pd.date_range("2024-01-01", periods=25, freq="1h")
        feb = pd.date_range("2024-02-01", periods=5, freq="1h")
        idx = jan.append(feb)
        factor = pd.Series(rng.normal(0, 1, 30), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 30), index=idx)
        out = R.subsample_ic(factor, fwd, freq="ME")
        assert len(out) == 1
        assert out[0]["period"] == "2024-01"

    def test_filters_nan_and_inf(self):
        # Inject NaN/inf — should not crash
        rng = np.random.default_rng(5)
        idx = pd.date_range("2024-01-01", periods=24 * 30, freq="1h")
        factor_vals = rng.normal(0, 1, len(idx)).copy()
        factor_vals[10] = np.inf
        factor_vals[20] = np.nan
        factor = pd.Series(factor_vals, index=idx)
        fwd = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
        out = R.subsample_ic(factor, fwd, freq="ME")
        # Single month → 1 entry, IC must be finite
        assert len(out) == 1
        assert np.isfinite(out[0]["ic"])

    def test_returns_empty_list_for_short_input(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="1h")
        factor = pd.Series(np.arange(5, dtype=float), index=idx)
        fwd = pd.Series(np.arange(5, dtype=float), index=idx)
        out = R.subsample_ic(factor, fwd, freq="ME")
        assert out == []
