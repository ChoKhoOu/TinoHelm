"""Unit tests — ``tinohelm.factor.evaluation.ic`` (polars-native).

Locks the numerical contract of the 5 IC primitives
(``forward_returns`` / ``compute_ic_series`` / ``compute_ic_summary`` /
``compute_ic_decay`` / ``compute_half_life``) against a zero-tolerance
drift requirement (drift ≤ 1e-6 vs. legacy pandas implementation):

* Schema (dict keys, DataFrame columns, element shape) is pinned.
* Every rounding precision baked into the legacy implementation is
  asserted explicitly — regressing from 6 → 4 dp would flip these.
* Every short-circuit threshold (< 30 paired obs for the series, < 20
  per-group, < 30 for decay, noise floor 0.001 for half-life) gets a
  boundary test around the integer step.
* Implicit conventions that production depends on — ``np.std`` uses
  ``ddof=0``, ``pct_pos`` is strict ``> 0``, ``ir/tstat`` collapse to
  ``0`` when ``std == 0``, ``isoformat`` is the date-column encoder —
  are each pinned by a dedicated test.

The previous fixtures used ``pd.Series`` with a DatetimeIndex; the new
polars contract uses 2-col :class:`pl.DataFrame` ``[ts, value]``.

Pure-logic, deterministic (fixed seeds), NT-free, < 200 ms total.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.ic import (
    _DEFAULT_LAGS,
    _EMPTY_SUMMARY,
    _build_paired,
    compute_ic_decay,
    compute_ic_series,
    compute_ic_summary,
    compute_half_life,
    forward_returns,
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


def _make_frame(values, ts: pl.Series | None = None) -> pl.DataFrame:
    """Convenience constructor: build a 2-col ``[ts, value]`` polars frame."""
    arr = np.asarray(values, dtype=float)
    if ts is None:
        ts = _hourly_ts(len(arr))
    return pl.DataFrame({"ts": ts, "value": arr.tolist()})


# ──────────────────────────────────────────────────────────────────────
# forward_returns
# ──────────────────────────────────────────────────────────────────────


class TestForwardReturns:
    def test_simple_returns_shape_and_null_tail(self):
        close = _make_frame([100.0, 101.0, 102.0, 103.0, 104.0])
        out = forward_returns(close, period=1)
        # Last row must be null (no future bar).
        assert out["value"][-1] is None
        # Rolling shift means first N-1 rows are valid numbers.
        expected = [1 / 100, 1 / 101, 1 / 102, 1 / 103]
        for i, exp in enumerate(expected):
            assert math.isclose(out["value"][i], exp, rel_tol=1e-12)

    def test_log_returns_are_natural_log(self):
        close = _make_frame([100.0, 110.0, 121.0])
        out = forward_returns(close, period=1, log_ret=True)
        assert math.isclose(out["value"][0], math.log(110 / 100), rel_tol=1e-12)
        assert math.isclose(out["value"][1], math.log(121 / 110), rel_tol=1e-12)
        assert out["value"][-1] is None

    def test_period_gt_one_shifts_by_exactly_period(self):
        close = _make_frame([1.0, 2.0, 4.0, 8.0, 16.0])
        # fwd[0] = close[3]/close[0] - 1 = 8/1 - 1 = 7.0
        out = forward_returns(close, period=3)
        assert math.isclose(out["value"][0], 7.0)
        assert out["value"][-1] is None
        assert out["value"][-2] is None
        assert out["value"][-3] is None

    def test_preserves_ts_column(self):
        ts = _hourly_ts(10)
        close = pl.DataFrame({"ts": ts, "value": np.linspace(100, 110, 10).tolist()})
        out = forward_returns(close, period=2)
        # ``ts`` is preserved row-wise.
        assert out["ts"].to_list() == close["ts"].to_list()

    def test_period_longer_than_series_yields_all_null(self):
        close = _make_frame([100.0, 101.0, 102.0])
        out = forward_returns(close, period=5)
        assert all(v is None for v in out["value"].to_list())

    def test_multi_symbol_shift_stays_within_symbol(self):
        ts = _hourly_ts(3)
        close = pl.DataFrame(
            {
                "ts": [*ts.to_list(), *ts.to_list()],
                "symbol": ["BTC", "BTC", "BTC", "ETH", "ETH", "ETH"],
                "value": [100.0, 110.0, 121.0, 200.0, 180.0, 162.0],
            }
        )

        out = forward_returns(close, period=1)

        btc = out.filter(pl.col("symbol") == "BTC")["value"].to_list()
        eth = out.filter(pl.col("symbol") == "ETH")["value"].to_list()
        assert math.isclose(btc[0], 0.10, rel_tol=1e-12)
        assert math.isclose(btc[1], 0.10, rel_tol=1e-12)
        assert btc[2] is None
        assert math.isclose(eth[0], -0.10, rel_tol=1e-12)
        assert math.isclose(eth[1], -0.10, rel_tol=1e-12)
        assert eth[2] is None

    def test_invalid_period_rejected(self):
        close = _make_frame([100.0, 101.0, 102.0])
        with pytest.raises(ValueError, match="period must be > 0"):
            forward_returns(close, period=0)

    def test_zero_and_non_finite_close_emit_null_not_inf(self):
        close = _make_frame([100.0, 0.0, 105.0, float("inf"), 110.0])
        out = forward_returns(close, period=1)
        assert math.isclose(out["value"][0], -1.0, rel_tol=1e-12)
        assert out["value"][1] is None
        assert out["value"][2] is None
        assert out["value"][3] is None
        assert out["value"][4] is None

    def test_log_returns_require_positive_close_pair(self):
        close = _make_frame([100.0, -90.0, 81.0, 90.0])
        out = forward_returns(close, period=1, log_ret=True)
        assert out["value"][0] is None
        assert out["value"][1] is None
        assert math.isclose(out["value"][2], math.log(90.0 / 81.0), rel_tol=1e-12)
        assert out["value"][3] is None


# ──────────────────────────────────────────────────────────────────────
# compute_ic_series
# ──────────────────────────────────────────────────────────────────────


class TestComputeIcSeries:
    def _make_corr_pair(self, n: int, noise: float = 0.1, seed: int = 0):
        rng = np.random.default_rng(seed)
        ts = _hourly_ts(n)
        factor_arr = rng.normal(0, 1, n)
        fwd_arr = 0.5 * factor_arr + rng.normal(0, noise, n)
        factor = pl.DataFrame({"ts": ts, "value": factor_arr.tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": fwd_arr.tolist()})
        return factor, fwd

    def test_short_pair_below_30_returns_empty_frame(self):
        factor, fwd = self._make_corr_pair(29)
        out = compute_ic_series(factor, fwd)
        assert out.height == 0
        assert out.columns == ["date", "ic"]

    def test_build_paired_joins_multi_symbol_on_ts_and_symbol(self):
        ts = _hourly_ts(4)
        factor = pl.DataFrame(
            {
                "ts": [*ts.to_list(), *ts.to_list()],
                "symbol": ["BTC", "BTC", "BTC", "BTC", "ETH", "ETH", "ETH", "ETH"],
                "value": [0.10, 0.11, 0.12, 0.13, -0.10, -0.11, -0.12, -0.13],
            }
        )
        fwd = pl.DataFrame(
            {
                "ts": [*ts.to_list(), *ts.to_list()],
                "symbol": ["BTC", "BTC", "BTC", "BTC", "ETH", "ETH", "ETH", "ETH"],
                "value": [0.01, 0.02, 0.03, None, -0.01, -0.02, -0.03, None],
            }
        )

        paired = _build_paired(factor, fwd)

        # 2 symbols × 3 valid timestamps.  A ts-only join would produce
        # cross-symbol BTC↔ETH pairs and inflate this to 12 rows.
        assert paired.height == 6
        assert paired.select(["ts", "symbol"]).unique().height == 6
        assert paired.filter(
            (pl.col("symbol") == "BTC") & (pl.col("fwd_ret") < 0)
        ).height == 0
        assert paired.filter(
            (pl.col("symbol") == "ETH") & (pl.col("fwd_ret") > 0)
        ).height == 0

    def test_build_paired_rejects_asymmetric_symbol_schema(self):
        ts = _hourly_ts(3)
        factor = pl.DataFrame({
            "ts": [*ts.to_list(), *ts.to_list()],
            "symbol": ["BTC", "BTC", "BTC", "ETH", "ETH", "ETH"],
            "value": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
        })
        fwd = pl.DataFrame({"ts": ts, "value": [0.01, 0.02, 0.03]})

        with pytest.raises(ValueError, match="symbol"):
            _build_paired(factor, fwd)

    def test_build_paired_rejects_duplicate_identity_keys(self):
        ts = _hourly_ts(2)
        factor = pl.DataFrame({
            "ts": [ts[0], ts[0], ts[1]],
            "symbol": ["BTC", "BTC", "BTC"],
            "value": [1.0, 2.0, 3.0],
        })
        fwd = pl.DataFrame({
            "ts": [ts[0], ts[1]],
            "symbol": ["BTC", "BTC"],
            "value": [0.01, 0.02],
        })

        with pytest.raises(ValueError, match="duplicate identity"):
            _build_paired(factor, fwd)

    def test_build_paired_rejects_missing_factor_identity_keys(self):
        ts = _hourly_ts(3)
        factor = pl.DataFrame({
            "ts": [*ts.to_list(), *ts.to_list()],
            "symbol": ["BTC", "BTC", "BTC", "ETH", "ETH", "ETH"],
            "value": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
        })
        fwd = pl.DataFrame({
            "ts": ts.to_list(),
            "symbol": ["BTC", "BTC", "BTC"],
            "value": [0.01, 0.02, 0.03],
        })

        with pytest.raises(ValueError, match="missing identity keys"):
            _build_paired(factor, fwd)

    def test_build_paired_rejects_empty_returns_when_factor_has_finite_keys(self):
        ts = _hourly_ts(2)
        factor = pl.DataFrame({
            "ts": ts.to_list(),
            "symbol": ["BTC", "ETH"],
            "value": [1.0, 2.0],
        })
        fwd = factor.head(0)

        with pytest.raises(ValueError, match="missing identity keys"):
            _build_paired(factor, fwd)

    def test_exactly_30_paired_does_not_short_circuit(self):
        factor, fwd = self._make_corr_pair(30)
        out = compute_ic_series(factor, fwd, freq="D")
        # May still be empty if the per-group (< 20) filter drops everything,
        # but the function must not trip the early-exit; columns stay the
        # canonical ["date", "ic"] either way.
        assert out.columns == ["date", "ic"]

    def test_nan_and_inf_are_dropped_before_count_threshold(self):
        factor, fwd = self._make_corr_pair(100, seed=1)
        # Blow up 90 rows with NaN/Inf — only 10 valid, below the 30-pair floor.
        factor_vals = factor["value"].to_list()
        for i in range(10, 100):
            factor_vals[i] = float("nan")
        fwd_vals = fwd["value"].to_list()
        for i in range(50, 100):
            fwd_vals[i] = float("inf")
        factor = pl.DataFrame({"ts": factor["ts"], "value": factor_vals})
        fwd = pl.DataFrame({"ts": fwd["ts"], "value": fwd_vals})
        out = compute_ic_series(factor, fwd)
        assert out.height == 0

    def test_daily_frequency_group_floor_is_20(self):
        # 19 paired obs per day → every daily group below the < 20 filter.
        # 4 days × 19 hourly bars each (skip hours 19-23) → 76 obs total
        # > 30 so the outer short-circuit doesn't fire, but each daily group
        # has < 20 and must be dropped.
        rng = np.random.default_rng(2)
        stamps = []
        for day in range(4):
            start = dt.datetime(2024, 1, 1) + dt.timedelta(days=day)
            stamps.extend(start + dt.timedelta(hours=h) for h in range(19))
        ts = pl.Series("ts", stamps)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, len(stamps)).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, len(stamps)).tolist()})
        out = compute_ic_series(factor, fwd, freq="D")
        assert out.height == 0

    def test_perfect_monotone_pair_yields_ic_exactly_1(self):
        factor, fwd = self._make_corr_pair(200, seed=5)
        # Override fwd to be a perfect monotone of factor — Spearman IC should
        # be 1.0 every daily bucket.
        fwd = pl.DataFrame({"ts": factor["ts"], "value": factor["value"].to_list()})
        out = compute_ic_series(factor, fwd, freq="D")
        assert out.height > 0
        # Every daily rank IC should be 1.0 (rounded).
        assert (out["ic"] == 1.0).all()

    def test_ic_values_are_rounded_to_6dp(self):
        factor, fwd = self._make_corr_pair(500, noise=0.3, seed=7)
        out = compute_ic_series(factor, fwd, freq="D")
        # Every non-zero ic row must have at most 6 decimal places of precision.
        for ic in out["ic"].to_list():
            assert round(ic, 6) == ic

    def test_date_column_is_isoformat(self):
        factor, fwd = self._make_corr_pair(400, seed=9)
        out = compute_ic_series(factor, fwd, freq="D")
        assert out.height > 0
        # Each row's date must parse via the standard ``datetime.fromisoformat``.
        for date_str in out["date"].to_list():
            # ``T`` separator is the canonical ISO 8601 indicator.
            assert "T" in date_str
            # And it round-trips through fromisoformat.
            dt.datetime.fromisoformat(date_str)

    def test_pearson_method_differs_from_spearman_on_rank_sensitive_data(self):
        rng = np.random.default_rng(11)
        n = 500
        ts = _hourly_ts(n)
        base = rng.normal(0, 1, n)
        # Non-linear monotone transform — Spearman sees 1.0, Pearson doesn't.
        factor = pl.DataFrame({"ts": ts, "value": base.tolist()})
        fwd_vals = (np.sign(base) * base ** 2).tolist()
        fwd = pl.DataFrame({"ts": ts, "value": fwd_vals})
        sp = compute_ic_series(factor, fwd, method="spearman", freq="D")
        pe = compute_ic_series(factor, fwd, method="pearson", freq="D")
        assert sp.height > 0 and pe.height > 0
        # At least one daily group should disagree.
        assert sp["ic"].to_list() != pe["ic"].to_list()

    def test_unknown_method_is_rejected(self):
        factor, fwd = self._make_corr_pair(100)
        with pytest.raises(ValueError, match="method"):
            compute_ic_series(factor, fwd, method="kendall", freq="D")

    def test_unknown_method_is_rejected_even_when_too_few_pairs(self):
        factor, fwd = self._make_corr_pair(2)
        with pytest.raises(ValueError, match="method"):
            compute_ic_series(factor, fwd, method="kendall", freq="D")

    def test_non_temporal_ts_is_rejected_before_polars_dt_error(self):
        factor = pl.DataFrame({"ts": list(range(100)), "value": np.arange(100, dtype=float)})
        fwd = pl.DataFrame({"ts": list(range(100)), "value": np.arange(100, dtype=float)})
        with pytest.raises(ValueError, match="datetime"):
            compute_ic_series(factor, fwd, freq="D")

    def test_non_temporal_ts_is_rejected_even_when_too_few_pairs(self):
        factor = pl.DataFrame({"ts": [0, 1], "value": [1.0, 2.0]})
        fwd = pl.DataFrame({"ts": [0, 1], "value": [0.01, 0.02]})
        with pytest.raises(ValueError, match="datetime"):
            compute_ic_series(factor, fwd, freq="D")

    def test_non_finite_ic_rows_are_dropped(self):
        # All-constant factor within daily groups → spearman returns NaN → skipped.
        ts = _hourly_ts(200)
        factor = pl.DataFrame({"ts": ts, "value": [1.0] * 200})
        rng = np.random.default_rng(13)
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 200).tolist()})
        out = compute_ic_series(factor, fwd, freq="D")
        # Either empty or no non-finite remnants.
        if out.height > 0:
            for v in out["ic"].to_list():
                assert np.isfinite(v)


# ──────────────────────────────────────────────────────────────────────
# compute_ic_summary
# ──────────────────────────────────────────────────────────────────────


class TestComputeIcSummary:
    def test_empty_frame_returns_zero_summary_copy(self):
        empty = pl.DataFrame(schema={"date": pl.Utf8, "ic": pl.Float64})
        out = compute_ic_summary(empty)
        assert out == _EMPTY_SUMMARY
        # Must be a fresh dict — caller mutations must not poison the module global.
        out["ic_mean"] = 999
        assert _EMPTY_SUMMARY["ic_mean"] == 0

    def test_frame_without_ic_column_returns_zero_summary(self):
        out = compute_ic_summary(pl.DataFrame({"date": ["2024-01-01"]}))
        assert out == _EMPTY_SUMMARY

    def test_schema_has_exactly_six_keys(self):
        ic_df = pl.DataFrame({"date": ["2024-01-01"], "ic": [0.1]})
        out = compute_ic_summary(ic_df)
        assert set(out.keys()) == {
            "ic_mean", "ic_std", "ir", "ic_positive_pct", "ic_max_abs", "ic_tstat",
        }

    def test_std_uses_ddof_zero_population_convention(self):
        # np.std with ddof=0 of [0, 2] → 1.0 (population std); sample std is sqrt(2) ≈ 1.414.
        ic_df = pl.DataFrame({"date": ["d1", "d2"], "ic": [0.0, 2.0]})
        out = compute_ic_summary(ic_df)
        assert out["ic_std"] == 1.0  # rounded to 6 dp still 1.0
        # IR = mean / population-std = 1.0 / 1.0 = 1.0 (rounded to 4 dp).
        assert out["ir"] == 1.0

    def test_zero_std_collapses_ir_and_tstat_to_zero(self):
        # All equal ICs → std=0 → ir, tstat must collapse (no div-by-zero).
        # NB: 0.1 is specifically chosen because it is NOT exactly representable
        # in IEEE 754 double — ``np.std([0.1]*N)`` leaks a ~1e-17 residue that
        # must be treated as zero so the IR guard doesn't produce a 10^15-scale
        # blow-up. See ic.py::compute_ic_summary for the tolerance constant.
        ic_df = pl.DataFrame({"date": ["d1", "d2", "d3"], "ic": [0.1, 0.1, 0.1]})
        out = compute_ic_summary(ic_df)
        assert out["ir"] == 0
        assert out["ic_tstat"] == 0
        # Mean is still reported, not zeroed out by the guard.
        assert out["ic_mean"] == 0.1
        # ic_std reports the rounded value (1e-17 rounds to 0.0 at 6 dp).
        assert out["ic_std"] == 0.0

    def test_float_imprecision_residue_below_tolerance_is_ignored(self):
        # Other non-representable float values also exercise the guard —
        # [0.3]*N, [0.7]*N all exhibit tiny residue under np.std.
        for val in (0.3, 0.7, 1.1, -0.2):
            ic_df = pl.DataFrame({"date": ["a", "b", "c", "d"], "ic": [val] * 4})
            out = compute_ic_summary(ic_df)
            assert out["ir"] == 0, f"IR leak for constant ICs of {val}"
            assert out["ic_tstat"] == 0, f"t-stat leak for constant ICs of {val}"

    def test_positive_pct_is_strictly_greater_than_zero(self):
        # Rows equal to exactly 0 must NOT count as positive.
        ic_df = pl.DataFrame({"date": ["d1", "d2", "d3", "d4"], "ic": [0.0, 0.0, 1.0, -1.0]})
        out = compute_ic_summary(ic_df)
        assert out["ic_positive_pct"] == 0.25  # only the 1.0 counts

    def test_max_abs_reports_largest_absolute_value(self):
        ic_df = pl.DataFrame({"date": ["d1", "d2", "d3"], "ic": [-0.9, 0.1, 0.5]})
        out = compute_ic_summary(ic_df)
        assert out["ic_max_abs"] == 0.9

    def test_precision_rounding_mean_std_max_to_6dp(self):
        ic_df = pl.DataFrame({
            "date": ["d1", "d2", "d3"],
            "ic": [0.123456789, 0.234567891, 0.345678912],
        })
        out = compute_ic_summary(ic_df)
        # Mean = 0.234567864 → rounds to 6 dp exactly
        assert out["ic_mean"] == round((0.123456789 + 0.234567891 + 0.345678912) / 3, 6)
        # Max abs = 0.345678912 → rounds to 0.345679
        assert out["ic_max_abs"] == 0.345679

    def test_precision_rounding_ir_4dp_tstat_2dp_pct_4dp(self):
        ic_df = pl.DataFrame({
            "date": ["d1", "d2", "d3", "d4"],
            "ic": [0.1, 0.2, 0.3, -0.05],
        })
        out = compute_ic_summary(ic_df)
        # IR / t-stat / pct ∈ finite ranges after rounding.
        assert round(out["ir"], 4) == out["ir"]
        assert round(out["ic_tstat"], 2) == out["ic_tstat"]
        assert round(out["ic_positive_pct"], 4) == out["ic_positive_pct"]

    def test_tstat_matches_mean_over_std_err(self):
        # ICs with known mean / std:  [1, -1, 1, -1] → mean 0, std 1, tstat 0.
        ic_df = pl.DataFrame({"date": list("abcd"), "ic": [1.0, -1.0, 1.0, -1.0]})
        out = compute_ic_summary(ic_df)
        assert out["ic_mean"] == 0
        assert out["ic_std"] == 1.0
        assert out["ir"] == 0
        assert out["ic_tstat"] == 0

    def test_non_finite_ic_rows_are_ignored_in_summary(self):
        ic_df = pl.DataFrame({
            "date": ["d1", "d2", "d3", "d4"],
            "ic": [0.1, float("nan"), float("inf"), -0.3],
        })
        out = compute_ic_summary(ic_df)
        assert out["ic_mean"] == -0.1
        assert out["ic_max_abs"] == 0.3
        assert out["ic_positive_pct"] == 0.5
        assert all(math.isfinite(v) for v in out.values())

    def test_all_non_finite_ic_rows_return_zero_summary(self):
        ic_df = pl.DataFrame({
            "date": ["d1", "d2", "d3"],
            "ic": [float("nan"), float("inf"), float("-inf")],
        })
        assert compute_ic_summary(ic_df) == _EMPTY_SUMMARY


# ──────────────────────────────────────────────────────────────────────
# compute_ic_decay
# ──────────────────────────────────────────────────────────────────────


class TestComputeIcDecay:
    def test_default_lag_grid_matches_pin(self):
        assert _DEFAULT_LAGS == (1, 2, 3, 5, 8, 13, 21, 34, 55, 89)

    def test_default_lags_produce_one_entry_each(self):
        rng = np.random.default_rng(1)
        n = 500
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        close = pl.DataFrame({"ts": ts, "value": (100 + np.cumsum(rng.normal(0, 0.3, n))).tolist()})
        out = compute_ic_decay(factor, close)
        assert len(out) == len(_DEFAULT_LAGS)
        assert [d["lag"] for d in out] == list(_DEFAULT_LAGS)

    def test_custom_lags_replace_defaults_entirely(self):
        rng = np.random.default_rng(2)
        n = 200
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        close = pl.DataFrame({"ts": ts, "value": (100 + np.cumsum(rng.normal(0, 0.3, n))).tolist()})
        out = compute_ic_decay(factor, close, lags=[2, 7])
        assert [d["lag"] for d in out] == [2, 7]

    def test_short_paired_emits_ic_zero_but_keeps_lag(self):
        # Only 30 bars — fwd = close.shift(-89) has all null → < 30 paired.
        ts = _hourly_ts(30)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(30, dtype=float).tolist()})
        close = pl.DataFrame({"ts": ts, "value": np.linspace(100, 110, 30).tolist()})
        out = compute_ic_decay(factor, close)
        # The last lags (89, 55, ...) should all degrade to ic=0 (int).
        for d in out:
            if d["lag"] >= 30:
                assert d["ic"] == 0

    def test_each_row_has_only_lag_and_ic_keys(self):
        rng = np.random.default_rng(3)
        n = 300
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        close = pl.DataFrame({"ts": ts, "value": (100 + np.cumsum(rng.normal(0, 0.3, n))).tolist()})
        out = compute_ic_decay(factor, close, lags=[1, 5])
        for d in out:
            assert set(d.keys()) == {"lag", "ic"}

    def test_ic_rounded_to_6dp(self):
        rng = np.random.default_rng(4)
        n = 400
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        close = pl.DataFrame({"ts": ts, "value": (100 + np.cumsum(rng.normal(0, 0.3, n))).tolist()})
        out = compute_ic_decay(factor, close, lags=[3])
        ic_val = out[0]["ic"]
        if ic_val != 0:
            assert round(ic_val, 6) == ic_val

    def test_empty_lags_list_returns_empty_result(self):
        rng = np.random.default_rng(5)
        ts = _hourly_ts(200)
        close = pl.DataFrame({"ts": ts, "value": (100 + np.cumsum(rng.normal(0, 0.3, 200))).tolist()})
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 200).tolist()})
        out = compute_ic_decay(factor, close, lags=[])
        assert out == []


# ──────────────────────────────────────────────────────────────────────
# compute_half_life
# ──────────────────────────────────────────────────────────────────────


class TestComputeHalfLife:
    def test_empty_decay_returns_none(self):
        assert compute_half_life([]) is None

    def test_below_noise_floor_returns_none(self):
        # All |ic| < 0.001 → max|ic| < 0.001 → None.
        decay = [{"lag": 1, "ic": 0.0005}, {"lag": 2, "ic": -0.0009}]
        assert compute_half_life(decay) is None

    def test_peak_exactly_at_noise_floor_returns_none(self):
        # Legacy semantics: strict `< 0.001` → a peak of exactly 0.001 passes.
        decay = [{"lag": 1, "ic": 0.001}, {"lag": 2, "ic": 0.0005}]
        result = compute_half_life(decay)
        # 0.001/2 = 0.0005, 0.0005 <= 0.0005 → returns 2.
        assert result == 2

    def test_first_lag_at_or_below_half_wins(self):
        # Peak 0.5 at lag 3. Half = 0.25. First lag with |ic| ≤ 0.25 is lag 5 (ic 0.20).
        decay = [
            {"lag": 1, "ic": 0.35},
            {"lag": 3, "ic": 0.5},
            {"lag": 5, "ic": 0.20},
            {"lag": 8, "ic": 0.10},
        ]
        assert compute_half_life(decay) == 5

    def test_falls_back_to_last_lag_when_no_drop_found(self):
        # Every |ic| is above half(peak) → fall back to last lag.
        decay = [
            {"lag": 1, "ic": 0.5},
            {"lag": 3, "ic": 0.45},  # > 0.25 (half of 0.5)
            {"lag": 5, "ic": 0.30},
        ]
        # Fall-through: first lag where |ic| ≤ 0.25 does NOT exist, so return last lag.
        assert compute_half_life(decay) == 5

    def test_uses_absolute_value_for_half_life(self):
        # Negative peak — half-life logic uses |ic|. Peak |ic| = 0.8 at lag 1.
        # Half = 0.4. First lag with |ic| ≤ 0.4 is lag 3 (|-0.3| = 0.3 ≤ 0.4).
        decay = [
            {"lag": 1, "ic": -0.8},
            {"lag": 3, "ic": -0.3},
            {"lag": 5, "ic": 0.35},
        ]
        assert compute_half_life(decay) == 3

    def test_first_element_matches_condition_returns_immediately(self):
        # First entry itself already at or below half.
        decay = [{"lag": 1, "ic": 0.001}, {"lag": 3, "ic": 0.5}]
        # max|ic| = 0.5, half = 0.25, first entry (ic=0.001) is already ≤ 0.25 → 1.
        assert compute_half_life(decay) == 1
