"""Unit tests — ``tinohelm.factor.evaluation.robustness`` (polars-native).

Covers only the deterministic pure helpers:

* ``summarize_shuffle_distribution(real_ic, shuffle_ics, bins=50)`` —
  histogram + p-value payload builder, wire-format contract for the
  shuffle-significance chart. Pure numpy under the hood — unchanged
  by the polars migration.
* ``subsample_ic(factor, fwd_ret, freq)`` — periodic IC buckets for the
  robustness card. Now consumes 2-col ``[ts, value]`` :class:`pl.DataFrame`
  inputs; per-bucket Spearman is computed via :func:`pl.corr` with
  ``method="spearman"``.
* ``_single_shuffle_ic((factor_vals, fwd_vals, seed))`` — single-shuffle
  worker function (numpy arrays in, deterministic under fixed seed).
* Module constants ``SHUFFLE_MIN_OBSERVATIONS`` and
  ``SHUFFLE_SIGNIFICANCE_THRESHOLD`` must not drift silently.

The process-pool entry points (``shuffle_test``, ``cross_symbol_ic``) are
deliberately skipped here — they are covered by the integration suite in
``tests/factor/test_evaluation.py`` and are slow/brittle.

Pure, deterministic, NT-free, < 100 ms.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.robustness import (
    SHUFFLE_MIN_OBSERVATIONS,
    SHUFFLE_SIGNIFICANCE_THRESHOLD,
    _single_shuffle_ic,
    subsample_ic,
    summarize_shuffle_distribution,
)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def _hourly_ts(n: int, start: dt.datetime = dt.datetime(2024, 1, 1)) -> pl.Series:
    """Build an N-row hourly :class:`pl.Datetime` index for fixtures."""
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


# ──────────────────────────────────────────────────────────────────────
# Module-level constants — prevent silent drift
# ──────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_significance_threshold_is_5pct(self):
        assert SHUFFLE_SIGNIFICANCE_THRESHOLD == 0.05

    def test_min_observations_threshold(self):
        assert SHUFFLE_MIN_OBSERVATIONS == 100


# ──────────────────────────────────────────────────────────────────────
# summarize_shuffle_distribution — schema + math
# ──────────────────────────────────────────────────────────────────────


class TestSummarizeShuffleDistribution:
    def test_schema_four_keys(self):
        out = summarize_shuffle_distribution(0.05, [0.01, -0.02, 0.03])
        assert set(out.keys()) == {
            "real_ic", "shuffle_distribution", "p_value", "significant",
        }

    def test_empty_distribution_yields_insignificant_payload(self):
        out = summarize_shuffle_distribution(0.05, [])
        assert out == {
            "real_ic": round(0.05, 6),
            "shuffle_distribution": [],
            "p_value": 1.0,
            "significant": False,
        }

    def test_p_value_is_fraction_where_abs_shuffle_ge_abs_real(self):
        real = 0.05
        # 3 of 10 shuffle |ics| are >= 0.05 → p = 0.3.
        shuffles = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, -0.07, -0.01, -0.02, -0.03]
        out = summarize_shuffle_distribution(real, shuffles)
        # |shuffle| >= |0.05|: 0.05, 0.06, 0.07 → 3 of 10 → 0.3.
        assert out["p_value"] == 0.3

    def test_significance_uses_strict_less_than(self):
        # p_value exactly at threshold 0.05 → NOT significant (strict <).
        # Construct: 5 of 100 shuffles have |ic| >= |real|.
        real = 0.5
        shuffles = [0.5] * 5 + [0.0] * 95  # 5 with |ic| >= 0.5
        out = summarize_shuffle_distribution(real, shuffles)
        assert out["p_value"] == 0.05
        assert out["significant"] is False

    def test_significance_just_below_threshold_marked_true(self):
        real = 0.5
        shuffles = [0.5] * 4 + [0.0] * 96  # 4 / 100 = 0.04 < 0.05
        out = summarize_shuffle_distribution(real, shuffles)
        assert out["p_value"] == 0.04
        assert out["significant"] is True

    def test_histogram_has_requested_bin_count(self):
        rng = np.random.default_rng(0)
        shuffles = rng.normal(0, 0.1, 1000).tolist()
        out = summarize_shuffle_distribution(0.05, shuffles, bins=25)
        assert len(out["shuffle_distribution"]) == 25

    def test_histogram_bin_shape(self):
        rng = np.random.default_rng(1)
        shuffles = rng.normal(0, 0.1, 500).tolist()
        out = summarize_shuffle_distribution(0.05, shuffles, bins=10)
        for entry in out["shuffle_distribution"]:
            assert set(entry.keys()) == {"bin_start", "bin_end", "count"}
            assert entry["bin_start"] <= entry["bin_end"]
            assert isinstance(entry["count"], int)

    def test_histogram_count_sum_equals_input_size(self):
        rng = np.random.default_rng(2)
        shuffles = rng.normal(0, 0.1, 300).tolist()
        out = summarize_shuffle_distribution(0.05, shuffles, bins=20)
        assert sum(e["count"] for e in out["shuffle_distribution"]) == 300

    def test_real_ic_rounded_to_6dp(self):
        out = summarize_shuffle_distribution(0.123456789, [0.01, 0.02])
        assert out["real_ic"] == round(0.123456789, 6)

    def test_p_value_rounded_to_4dp(self):
        rng = np.random.default_rng(3)
        shuffles = rng.normal(0, 0.1, 999).tolist()
        out = summarize_shuffle_distribution(0.01, shuffles)
        assert round(out["p_value"], 4) == out["p_value"]

    def test_histogram_edges_rounded_to_6dp(self):
        rng = np.random.default_rng(4)
        shuffles = rng.normal(0, 0.1, 500).tolist()
        out = summarize_shuffle_distribution(0.05, shuffles, bins=15)
        for e in out["shuffle_distribution"]:
            assert round(e["bin_start"], 6) == e["bin_start"]
            assert round(e["bin_end"], 6) == e["bin_end"]

    def test_nan_real_ic_coerced_to_zero(self):
        out = summarize_shuffle_distribution(float("nan"), [0.01, -0.02, 0.03])
        assert out["real_ic"] == 0.0

    def test_inf_real_ic_coerced_to_zero(self):
        out = summarize_shuffle_distribution(float("inf"), [0.01, -0.02, 0.03])
        assert out["real_ic"] == 0.0

    def test_uses_absolute_value_for_p_value(self):
        # real=-0.2; shuffle = 0.1, -0.1, 0.3 → |0.3| >= 0.2 → p = 1/3.
        out = summarize_shuffle_distribution(-0.2, [0.1, -0.1, 0.3])
        assert out["p_value"] == round(1 / 3, 4)

    def test_accepts_numpy_array_input(self):
        arr = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
        out = summarize_shuffle_distribution(0.03, arr)
        assert set(out.keys()) == {
            "real_ic", "shuffle_distribution", "p_value", "significant",
        }


# ──────────────────────────────────────────────────────────────────────
# subsample_ic — periodic IC buckets
# ──────────────────────────────────────────────────────────────────────


class TestSubsampleIc:
    def _make_correlated(self, n: int, seed: int = 0):
        rng = np.random.default_rng(seed)
        ts = _hourly_ts(n)
        factor_arr = rng.normal(0, 1, n)
        fwd_arr = 0.5 * factor_arr + rng.normal(0, 0.3, n)
        factor = pl.DataFrame({"ts": ts, "value": factor_arr.tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": fwd_arr.tolist()})
        return factor, fwd

    def test_returns_list_of_dicts_with_period_and_ic(self):
        factor, fwd = self._make_correlated(2400)  # ~100 days
        out = subsample_ic(factor, fwd, freq="ME")
        for entry in out:
            assert set(entry.keys()) == {"period", "ic"}

    def test_empty_input_returns_empty_list(self):
        empty_ts = pl.Series("ts", [], dtype=pl.Datetime)
        factor = pl.DataFrame({"ts": empty_ts, "value": pl.Series("value", [], dtype=pl.Float64)})
        fwd = pl.DataFrame({"ts": empty_ts, "value": pl.Series("value", [], dtype=pl.Float64)})
        assert subsample_ic(factor, fwd) == []

    def test_small_groups_below_20_are_skipped(self):
        # 30 hourly bars spread across 3 months — each bucket has 10 < 20.
        # Use explicit sparse timestamps: 10 hourly samples in January,
        # 10 in February, 10 in March → every monthly group has 10 < 20 →
        # all dropped.
        ts_list = []
        for month_start in [
            dt.datetime(2024, 1, 15),
            dt.datetime(2024, 2, 15),
            dt.datetime(2024, 3, 15),
        ]:
            ts_list.extend(month_start + dt.timedelta(hours=h) for h in range(10))
        ts = pl.Series("ts", ts_list)
        rng = np.random.default_rng(5)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 30).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 30).tolist()})
        assert subsample_ic(factor, fwd, freq="ME") == []

    def test_period_formatted_as_yyyy_mm(self):
        factor, fwd = self._make_correlated(2400)
        out = subsample_ic(factor, fwd, freq="ME")
        for entry in out:
            assert len(entry["period"]) == 7
            assert entry["period"][4] == "-"

    def test_ic_values_rounded_to_6dp(self):
        factor, fwd = self._make_correlated(2400)
        out = subsample_ic(factor, fwd, freq="ME")
        for entry in out:
            assert round(entry["ic"], 6) == entry["ic"]

    def test_perfect_correlation_within_each_period_yields_ic_1(self):
        # fwd is a monotone transform of factor on every bar → Spearman IC = 1.
        n = 2400  # ~100 days hourly
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(n, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(n, dtype=float).tolist()})
        out = subsample_ic(factor, fwd, freq="ME")
        assert len(out) > 0
        for entry in out:
            assert entry["ic"] == 1.0

    def test_nan_ic_group_is_skipped(self):
        # A period where factor is constant → spearman returns NaN → skipped.
        # Use 2 months where January has variable, February+March has constant.
        n = 1400
        ts = _hourly_ts(n)
        rng = np.random.default_rng(7)
        factor_vals = rng.normal(0, 1, n).tolist()
        # Pin February onwards to a constant.
        cutover_ts = dt.datetime(2024, 2, 1)
        for i, t in enumerate(ts.to_list()):
            if t >= cutover_ts:
                factor_vals[i] = 5.0
        factor = pl.DataFrame({"ts": ts, "value": factor_vals})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        out = subsample_ic(factor, fwd, freq="ME")
        # February entry is dropped (NaN), January entry remains.
        periods = {e["period"] for e in out}
        assert "2024-02" not in periods

    def test_filters_nan_and_inf_pairs(self):
        # Blow out 90 % of rows with NaN/Inf; the remainder still monthly-binned.
        n = 1400
        ts = _hourly_ts(n)
        rng = np.random.default_rng(8)
        f_vals = rng.normal(0, 1, n).tolist()
        r_vals = rng.normal(0, 1, n).tolist()
        for i in range(500):
            f_vals[i] = float("nan")
        for i in range(500, 1000):
            r_vals[i] = float("inf")
        factor = pl.DataFrame({"ts": ts, "value": f_vals})
        fwd = pl.DataFrame({"ts": ts, "value": r_vals})
        # Must not raise; must produce at most January + February buckets.
        out = subsample_ic(factor, fwd, freq="ME")
        assert isinstance(out, list)


# ──────────────────────────────────────────────────────────────────────
# _single_shuffle_ic — worker function (must be picklable top-level)
# ──────────────────────────────────────────────────────────────────────


class TestSingleShuffleIc:
    def test_returns_float(self):
        factor = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        fwd = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _single_shuffle_ic((factor, fwd, 42))
        assert isinstance(result, float)

    def test_is_deterministic_under_fixed_seed(self):
        factor = np.arange(20, dtype=float)
        fwd = np.arange(20, dtype=float)
        a = _single_shuffle_ic((factor, fwd, 123))
        b = _single_shuffle_ic((factor, fwd, 123))
        assert a == b

    def test_different_seeds_generally_yield_different_results(self):
        factor = np.arange(30, dtype=float)
        fwd = np.arange(30, dtype=float)
        a = _single_shuffle_ic((factor, fwd, 1))
        b = _single_shuffle_ic((factor, fwd, 2))
        # Chance of equal-by-accident is negligible for 30 values.
        assert a != b

    def test_nan_correlation_coerces_to_zero(self):
        # Constant fwd → spearman is NaN → coerced to 0.0.
        factor = np.arange(10, dtype=float)
        fwd = np.ones(10, dtype=float)
        result = _single_shuffle_ic((factor, fwd, 0))
        assert result == 0.0

    def test_does_not_mutate_input_arrays(self):
        factor = np.arange(10, dtype=float)
        fwd = np.arange(10, dtype=float)
        factor_copy = factor.copy()
        fwd_copy = fwd.copy()
        _single_shuffle_ic((factor, fwd, 42))
        assert (factor == factor_copy).all()
        assert (fwd == fwd_copy).all()


# ──────────────────────────────────────────────────────────────────────
# NT-free / IO-free discipline check
# ──────────────────────────────────────────────────────────────────────


class TestPureDiscipline:
    def test_source_does_not_import_nautilus_at_module_top(self):
        import tinohelm.factor.evaluation.robustness as mod
        src = open(mod.__file__, encoding="utf-8").read()
        # Top-level imports must not pull NT (NT is a heavy native dep).
        # We allow lazy imports inside functions (e.g. cross_symbol_ic worker).
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and not stripped.startswith("#"):
                # Only count module-level imports (no indent).
                if not line.startswith((" ", "\t")):
                    assert "nautilus" not in stripped, (
                        f"top-level NT import leaked into pure robustness module: {line}"
                    )

    def test_source_does_not_import_pandas_at_module_top(self):
        # Polars-only contract — AC-1 of the s09 migration.
        import tinohelm.factor.evaluation.robustness as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and not stripped.startswith("#"):
                if not line.startswith((" ", "\t")):
                    assert "pandas" not in stripped, (
                        f"top-level pandas import leaked into evaluation module: {line}"
                    )
