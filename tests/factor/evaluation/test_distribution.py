"""Unit tests — ``tinohelm.factor.evaluation.distribution`` (polars-native).

Locks the numerical contract of ``compute_distribution``:

* Schema — exactly ``{"histogram": [...], "stats": {...}}``; each histogram
  entry has exactly ``{bin_start, bin_end, count}`` with ``bin_start`` < ``bin_end``.
* Short-circuit — < 10 finite values returns empty payload.
* NaN / ±Inf filtering — these must NOT poison the histogram or summary.
* Precision — mean/std/min/max at 6 dp, skew/kurt/zero_pct/autocorr at 4 dp.
* Mixed statistics conventions — ``np.std`` uses ddof=0 (population),
  :meth:`pl.Series.skew(bias=False)` and :meth:`pl.Series.kurtosis(bias=False,
  fisher=True)` mirror the legacy pandas/scipy "adjusted sample" outputs.
  This mixed convention is baked into the legacy output and must not drift.

Pure-logic, deterministic, NT-free, < 100 ms.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.distribution import compute_distribution


EMPTY = {"histogram": [], "stats": {}}


def _series(values) -> pl.Series:
    """Polars Series shorthand for tests."""
    return pl.Series("v", list(values))


class TestShortCircuit:
    def test_empty_series_returns_empty(self):
        assert compute_distribution(pl.Series("v", [], dtype=pl.Float64)) == EMPTY

    def test_fewer_than_10_finite_returns_empty(self):
        # 9 finite values → short-circuit.
        assert compute_distribution(_series([1.0] * 9)) == EMPTY

    def test_exactly_10_finite_passes_the_gate(self):
        out = compute_distribution(_series(np.linspace(0, 1, 10).tolist()))
        assert out["histogram"] != []
        assert out["stats"] != {}

    def test_nan_and_inf_rows_do_not_count_toward_the_10_floor(self):
        vals = [float("nan")] * 20 + [1.0] * 5 + [float("inf")] * 10
        # Only 5 finite values → below 10 → empty.
        assert compute_distribution(_series(vals)) == EMPTY


class TestSchema:
    def test_histogram_entry_keys(self):
        out = compute_distribution(_series(np.linspace(-1, 1, 200).tolist()), n_bins=5)
        for entry in out["histogram"]:
            assert set(entry.keys()) == {"bin_start", "bin_end", "count"}
            assert entry["bin_start"] <= entry["bin_end"]
            assert isinstance(entry["count"], int)

    def test_histogram_has_exactly_n_bins(self):
        out = compute_distribution(_series(np.linspace(-1, 1, 200).tolist()), n_bins=20)
        assert len(out["histogram"]) == 20

    def test_stats_has_all_nine_keys(self):
        out = compute_distribution(_series(np.linspace(-1, 1, 200).tolist()))
        assert set(out["stats"].keys()) == {
            "mean", "std", "skew", "kurtosis", "min", "max",
            "zero_pct", "autocorr_1", "autocorr_5",
        }

    def test_count_sum_equals_finite_input_size(self):
        series = _series(np.linspace(-1, 1, 300).tolist())
        out = compute_distribution(series, n_bins=10)
        total = sum(entry["count"] for entry in out["histogram"])
        assert total == 300


class TestFiltering:
    def test_nan_inf_filtered_from_histogram(self):
        # Finite payload 0..99, plus NaN/±Inf poison at the ends.
        finite = np.linspace(0.0, 99.0, 100)
        poisoned = np.concatenate([[float("nan"), float("inf"), float("-inf")], finite])
        out = compute_distribution(_series(poisoned.tolist()), n_bins=5)
        # Histogram range should be 0..99 (no ±Inf).
        assert out["histogram"][0]["bin_start"] >= -0.01  # allow float rounding
        assert out["histogram"][-1]["bin_end"] <= 99.01

    def test_poison_does_not_leak_into_stats(self):
        finite = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        poisoned = np.concatenate([
            [float("nan"), float("inf"), float("-inf"), float("nan")],
            finite,
        ])
        out = compute_distribution(_series(poisoned.tolist()))
        assert out["stats"]["min"] == 1.0
        assert out["stats"]["max"] == 10.0
        assert out["stats"]["mean"] == round(5.5, 6)


class TestPrecisionRounding:
    def test_mean_std_min_max_at_6dp(self):
        rng = np.random.default_rng(1)
        out = compute_distribution(_series(rng.normal(0, 1, 1000).tolist()))
        stats = out["stats"]
        assert round(stats["mean"], 6) == stats["mean"]
        assert round(stats["std"], 6) == stats["std"]
        assert round(stats["min"], 6) == stats["min"]
        assert round(stats["max"], 6) == stats["max"]

    def test_skew_kurtosis_zero_pct_autocorr_at_4dp(self):
        rng = np.random.default_rng(2)
        out = compute_distribution(_series(rng.normal(0, 1, 1000).tolist()))
        stats = out["stats"]
        assert round(stats["skew"], 4) == stats["skew"]
        assert round(stats["kurtosis"], 4) == stats["kurtosis"]
        assert round(stats["zero_pct"], 4) == stats["zero_pct"]
        assert round(stats["autocorr_1"], 4) == stats["autocorr_1"]
        assert round(stats["autocorr_5"], 4) == stats["autocorr_5"]

    def test_bin_edges_at_6dp(self):
        rng = np.random.default_rng(3)
        out = compute_distribution(_series(rng.normal(0, 1, 200).tolist()), n_bins=10)
        for entry in out["histogram"]:
            assert round(entry["bin_start"], 6) == entry["bin_start"]
            assert round(entry["bin_end"], 6) == entry["bin_end"]


class TestStatisticsConventions:
    def test_std_uses_ddof_zero_population(self):
        # For [0, 2]: population std = 1.0; sample std = sqrt(2) ≈ 1.414.
        # Function must use population (ddof=0) to match legacy semantics.
        # Need ≥ 10 values to pass the gate.
        values = [0.0, 2.0] * 5  # [0,2,0,2,...] 10 values, mean=1, var=1, std=1
        out = compute_distribution(_series(values))
        assert out["stats"]["std"] == 1.0

    def test_skew_uses_pandas_adjusted_sample_convention(self):
        # The polars ``Series.skew(bias=False)`` matches pandas' adjusted skew
        # to 1e-12 precision. We compare against an explicit polars-side
        # computation so the test is independent of pandas / scipy availability.
        data = [1.0, 2.0, 3.0, 10.0, 1.0, 2.0, 3.0, 10.0, 1.0, 2.0]
        expected_skew = round(pl.Series("v", data).skew(bias=False), 4)
        out = compute_distribution(_series(data))
        assert out["stats"]["skew"] == expected_skew

    def test_zero_pct_counts_exact_zero_values(self):
        # 3 zeros in 10 values → zero_pct = 0.3.
        data = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        out = compute_distribution(_series(data))
        assert out["stats"]["zero_pct"] == 0.3

    def test_autocorr_on_constant_series_coerces_nan_to_zero(self):
        # A constant series has zero variance → ``np.corrcoef`` returns NaN;
        # the module explicitly coerces to 0 via np.isfinite.
        data = [5.0] * 20
        out = compute_distribution(_series(data))
        assert out["stats"]["autocorr_1"] == 0
        assert out["stats"]["autocorr_5"] == 0

    def test_autocorr_5_requires_more_than_5_values(self):
        # Exactly 10 values → acf_5 is computable (len > 5).
        # Exactly 6 values → below the gate (< 10 finite).
        # So verify via 10+.
        data = list(np.linspace(0, 1, 20))
        out = compute_distribution(_series(data))
        # Monotone arithmetic progression → autocorr very close to 1.
        assert out["stats"]["autocorr_5"] > 0.5


class TestKnownDistributions:
    def test_uniform_histogram_is_roughly_flat(self):
        rng = np.random.default_rng(5)
        # 10000 uniform(0,1) samples → 10 bins → expect ~1000 each.
        out = compute_distribution(_series(rng.uniform(0, 1, 10000).tolist()), n_bins=10)
        counts = [e["count"] for e in out["histogram"]]
        for c in counts:
            assert 900 < c < 1100  # 10% tolerance

    def test_normal_stats_are_close_to_population_parameters(self):
        rng = np.random.default_rng(6)
        data = rng.normal(loc=0.0, scale=1.0, size=5000)
        out = compute_distribution(_series(data.tolist()))
        stats = out["stats"]
        assert abs(stats["mean"]) < 0.1
        assert abs(stats["std"] - 1.0) < 0.05
        # Normal distribution has skew ~ 0 and kurtosis ~ 0 (Fisher) for pandas.
        assert abs(stats["skew"]) < 0.2
        assert abs(stats["kurtosis"]) < 0.5


class TestPolarsDataFrameInput:
    """Distribution accepts both 2-col DataFrames and bare Series."""

    def test_two_col_dataframe_is_accepted(self):
        # ``[ts, value]`` shape — same convention used by IC / quantile / turnover.
        ts = pl.datetime_range(
            start=__import__("datetime").datetime(2024, 1, 1),
            end=__import__("datetime").datetime(2024, 1, 1) + __import__("datetime").timedelta(hours=99),
            interval="1h",
            eager=True,
        )
        rng = np.random.default_rng(7)
        df = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 100).tolist()})
        out = compute_distribution(df, n_bins=20)
        assert out["histogram"] != []
        assert out["stats"] != {}

    def test_dataframe_with_non_value_column_falls_back_to_first_non_ts(self):
        # When ``value`` column is missing, the implementation picks the first
        # non-``ts`` column. This mirrors how multi-symbol panels are passed
        # without explicit unpivoting in the orchestrator.
        rng = np.random.default_rng(8)
        df = pl.DataFrame({"factor_a": rng.normal(0, 1, 100).tolist()})
        out = compute_distribution(df, n_bins=20)
        # No ``ts`` column means the implementation interprets the lone
        # ``factor_a`` column as the value series.
        assert out["histogram"] != []
        assert out["stats"] != {}
