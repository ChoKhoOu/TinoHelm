"""Tests for ``tinohelm.backtest.optimizer_helpers`` — all NT/optuna-free.

Helpers extracted from ``optimizer.py`` should run in any environment
that has Python 3.11 + numpy.  No optuna, no nautilus_trader, no Redis,
no SQLAlchemy.  This file pins:

- the canonical sentinel/objective constants,
- the date/window math used by walk-forward analysis,
- the smart-default heuristics used to fill ``n_trials`` / ``sampler`` /
  ``n_workers`` when the user passes 0,
- the trial-filter predicate that DSR / sensitivity / stability all share,
- the inlined normal-distribution approximations against textbook values,
- the Layer-2/3 robustness statistics (DSR, sensitivity, stability),

so any drift in semantics gets caught the moment a helper changes.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from tinohelm.backtest.optimizer_helpers import (
    FAIL_VALUE,
    FITNESS_METRICS,
    TRADING_DAYS_PER_YEAR,
    _norm_cdf,
    _norm_ppf,
    auto_n_trials,
    auto_sampler,
    auto_workers,
    compute_dsr,
    compute_param_sensitivity,
    compute_param_stability,
    extract_fitness,
    filter_completed_trials,
    slim_result,
    split_dates,
    walk_forward_windows,
)


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────


class TestConstants:
    """Pin the canonical constants — they're cross-module contracts."""

    def test_fail_value(self):
        assert FAIL_VALUE == -999.0
        assert isinstance(FAIL_VALUE, float)

    def test_fitness_metrics_complete(self):
        # Both keys (CLI/UI accept) and values (paths into result["statistics"])
        # are part of the public contract — the API serializes them.
        assert FITNESS_METRICS == {
            "sharpe": "sharpe_ratio",
            "calmar": "calmar_ratio",
            "sortino": "sortino_ratio",
            "profit": "total_pnl",
        }

    def test_trading_days_per_year(self):
        assert TRADING_DAYS_PER_YEAR == 252


# ────────────────────────────────────────────────────────────────────
# split_dates
# ────────────────────────────────────────────────────────────────────


class TestSplitDates:
    def test_70_30_split(self):
        train_s, train_e, test_s, test_e = split_dates(
            date(2024, 1, 1), date(2024, 12, 31), 70.0,
        )
        assert train_s == date(2024, 1, 1)
        # 365 days * 0.70 = 255 days → train_end = Jan 1 + 255 = Sep 12
        assert train_e == date(2024, 9, 12)
        assert test_s == date(2024, 9, 13)
        assert test_e == date(2024, 12, 31)

    def test_train_test_disjoint(self):
        # Test segment must start the day after train ends.
        _, train_e, test_s, _ = split_dates(date(2024, 1, 1), date(2024, 6, 1), 50.0)
        assert (test_s - train_e).days == 1

    def test_50_50_split(self):
        train_s, train_e, test_s, test_e = split_dates(
            date(2024, 1, 1), date(2024, 1, 11), 50.0,
        )
        # 10 days * 0.50 = 5 → train_end = Jan 6
        assert train_s == date(2024, 1, 1)
        assert train_e == date(2024, 1, 6)
        assert test_s == date(2024, 1, 7)
        assert test_e == date(2024, 1, 11)

    def test_zero_train_pct(self):
        train_s, train_e, test_s, test_e = split_dates(
            date(2024, 1, 1), date(2024, 1, 10), 0.0,
        )
        assert train_e == train_s  # train_days == 0
        assert test_s == date(2024, 1, 2)
        assert test_e == date(2024, 1, 10)

    def test_full_train_pct(self):
        # 100% train: train_end = end_date, test starts day after end_date.
        train_s, train_e, test_s, test_e = split_dates(
            date(2024, 1, 1), date(2024, 1, 10), 100.0,
        )
        assert train_e == date(2024, 1, 10)
        assert test_s == date(2024, 1, 11)
        assert test_e == date(2024, 1, 10)


# ────────────────────────────────────────────────────────────────────
# walk_forward_windows
# ────────────────────────────────────────────────────────────────────


class TestWalkForwardWindows:
    def test_three_folds(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 3,
        )
        assert len(windows) == 3

    def test_test_segments_non_overlapping(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 4,
        )
        for i in range(len(windows) - 1):
            _, _, _, te_end_i = windows[i]
            _, _, te_start_next, _ = windows[i + 1]
            assert te_start_next > te_end_i, (
                f"fold {i} test_end ({te_end_i}) must be < "
                f"fold {i+1} test_start ({te_start_next})"
            )

    def test_each_window_train_before_test(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 3,
        )
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s <= tr_e
            assert tr_e < te_s
            assert te_s <= te_e

    def test_train_pct_100_falls_back_to_simple_split(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 100.0, 3,
        )
        assert len(windows) == 1
        # And the single window matches split_dates output.
        assert windows[0] == split_dates(date(2024, 1, 1), date(2024, 12, 31), 100.0)

    def test_zero_folds_falls_back(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 0,
        )
        assert len(windows) == 1
        assert windows[0] == split_dates(date(2024, 1, 1), date(2024, 12, 31), 70.0)

    def test_negative_folds_falls_back(self):
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, -2,
        )
        assert len(windows) == 1

    def test_clamps_to_data_boundaries(self):
        # Each train_start should be >= start_date, each test_end <= end_date.
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 6, 30), 80.0, 5,
        )
        for tr_s, _, _, te_e in windows:
            assert tr_s >= date(2024, 1, 1)
            assert te_e <= date(2024, 6, 30)

    def test_last_fold_test_end_at_data_end(self):
        # The last fold's test_end should align with the global end_date.
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 4,
        )
        assert windows[-1][3] == date(2024, 12, 31)


# ────────────────────────────────────────────────────────────────────
# extract_fitness
# ────────────────────────────────────────────────────────────────────


class TestExtractFitness:
    def test_sharpe_objective_happy_path(self):
        result = {"statistics": {"sharpe_ratio": 1.42}}
        assert extract_fitness(result, "sharpe") == 1.42

    @pytest.mark.parametrize("obj, key", [
        ("sharpe", "sharpe_ratio"),
        ("calmar", "calmar_ratio"),
        ("sortino", "sortino_ratio"),
        ("profit", "total_pnl"),
    ])
    def test_all_objectives_route_correctly(self, obj, key):
        result = {"statistics": {key: 3.14}}
        assert extract_fitness(result, obj) == 3.14

    def test_unknown_objective_returns_fail(self):
        assert extract_fitness({"statistics": {"sharpe_ratio": 1.0}}, "unknown") == FAIL_VALUE

    def test_missing_statistics_returns_fail(self):
        assert extract_fitness({}, "sharpe") == FAIL_VALUE

    def test_missing_metric_returns_fail(self):
        assert extract_fitness({"statistics": {}}, "sharpe") == FAIL_VALUE

    def test_none_metric_returns_fail(self):
        assert extract_fitness({"statistics": {"sharpe_ratio": None}}, "sharpe") == FAIL_VALUE

    def test_non_numeric_metric_returns_fail(self):
        assert extract_fitness({"statistics": {"sharpe_ratio": "n/a"}}, "sharpe") == FAIL_VALUE

    def test_int_metric_coerced_to_float(self):
        out = extract_fitness({"statistics": {"total_pnl": 42}}, "profit")
        assert out == 42.0
        assert isinstance(out, float)


# ────────────────────────────────────────────────────────────────────
# filter_completed_trials
# ────────────────────────────────────────────────────────────────────


class TestFilterCompletedTrials:
    def test_empty(self):
        assert filter_completed_trials([]) == []

    def test_keeps_complete_with_value(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {}},
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        assert len(filter_completed_trials(trials)) == 2

    def test_drops_pruned(self):
        trials = [
            {"state": "PRUNED", "value": 1.0, "params": {}},
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        kept = filter_completed_trials(trials)
        assert len(kept) == 1
        assert kept[0]["value"] == 2.0

    def test_drops_failed(self):
        trials = [
            {"state": "FAIL", "value": 1.0, "params": {}},
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        kept = filter_completed_trials(trials)
        assert len(kept) == 1

    def test_drops_none_value(self):
        trials = [
            {"state": "COMPLETE", "value": None, "params": {}},
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        assert len(filter_completed_trials(trials)) == 1

    def test_drops_fail_value_sentinel(self):
        trials = [
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {}},
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        kept = filter_completed_trials(trials)
        assert len(kept) == 1
        assert kept[0]["value"] == 2.0

    def test_missing_state_key_treated_as_invalid(self):
        trials = [
            {"value": 1.0, "params": {}},  # no state key
            {"state": "COMPLETE", "value": 2.0, "params": {}},
        ]
        kept = filter_completed_trials(trials)
        assert len(kept) == 1


# ────────────────────────────────────────────────────────────────────
# Smart defaults
# ────────────────────────────────────────────────────────────────────


class TestAutoNTrials:
    def test_empty_returns_floor(self):
        assert auto_n_trials({}) == 50

    def test_single_param_floored_at_50(self):
        # 1 dim * 20 = 20, floored to 50.
        assert auto_n_trials({"x": {"type": "float", "min": 0, "max": 1}}) == 50

    def test_three_params_floored(self):
        ranges = {f"p{i}": {"type": "float", "min": 0, "max": 1} for i in range(3)}
        # 3 * 20 = 60, but floor is 50 -> max(50, 60) = 60.
        assert auto_n_trials(ranges) == 60

    def test_high_dim_grows_linearly(self):
        ranges = {f"p{i}": {"type": "float", "min": 0, "max": 1} for i in range(10)}
        assert auto_n_trials(ranges) == 200  # max(50, 10*20)


class TestAutoSampler:
    def test_low_dim_continuous_picks_cmaes(self):
        ranges = {
            "x": {"type": "float", "min": 0, "max": 1},
            "y": {"type": "float", "min": 0, "max": 1},
        }
        assert auto_sampler(ranges) == "cmaes"

    def test_low_dim_with_int_picks_tpe(self):
        # CMA-ES doesn't handle integers cleanly -> fall back to TPE.
        ranges = {
            "x": {"type": "float", "min": 0, "max": 1},
            "n": {"type": "int", "min": 1, "max": 10},
        }
        assert auto_sampler(ranges) == "tpe"

    def test_high_dim_picks_tpe(self):
        ranges = {f"p{i}": {"type": "float", "min": 0, "max": 1} for i in range(5)}
        assert auto_sampler(ranges) == "tpe"

    def test_empty_picks_cmaes(self):
        # 0 dims, no ints -> matches the low-dim continuous branch.
        assert auto_sampler({}) == "cmaes"


class TestAutoWorkers:
    def test_explicit_cpu_count_8(self):
        # 8 // 2 = 4, clamped at upper bound 4.
        assert auto_workers(cpu_count=8) == 4

    def test_explicit_cpu_count_4(self):
        assert auto_workers(cpu_count=4) == 2

    def test_explicit_cpu_count_2(self):
        assert auto_workers(cpu_count=2) == 1

    def test_explicit_cpu_count_1(self):
        # 1 // 2 = 0, clamped to 1.
        assert auto_workers(cpu_count=1) == 1

    def test_explicit_cpu_count_huge(self):
        assert auto_workers(cpu_count=128) == 4

    def test_default_uses_os_cpu_count(self):
        # Don't assert a specific value (machine-dependent), just type & range.
        out = auto_workers()
        assert isinstance(out, int)
        assert 1 <= out <= 4


# ────────────────────────────────────────────────────────────────────
# slim_result
# ────────────────────────────────────────────────────────────────────


class TestSlimResult:
    def test_none_passes_through(self):
        assert slim_result(None) is None

    def test_keeps_three_fields(self):
        result = {
            "statistics": {"sharpe": 1.0},
            "equity_curve": [{"ts": 1, "v": 100}],
            "monthly_returns": {"2024-01": 0.05},
            "trades": [1, 2, 3],
            "junk": "noise",
        }
        slim = slim_result(result)
        assert set(slim.keys()) == {"statistics", "equity_curve", "monthly_returns"}
        assert slim["statistics"] == {"sharpe": 1.0}
        assert slim["equity_curve"] == [{"ts": 1, "v": 100}]
        assert slim["monthly_returns"] == {"2024-01": 0.05}

    def test_missing_fields_become_none(self):
        slim = slim_result({})
        assert slim == {"statistics": None, "equity_curve": None, "monthly_returns": None}


# ────────────────────────────────────────────────────────────────────
# _norm_ppf / _norm_cdf — pin against textbook values
# ────────────────────────────────────────────────────────────────────


class TestNormalApproximations:
    """Numerical equivalence with Abramowitz-Stegun reference values.

    These two functions are inlined from ``result/statistics.py`` so they
    can stay NT-free.  Drift here would silently break DSR.
    """

    def test_ppf_975(self):
        # _norm_ppf(0.975) ≈ 1.96 (textbook); A&S 26.2.23 is accurate ~4.5e-4.
        assert abs(_norm_ppf(0.975) - 1.96) < 0.01

    def test_ppf_95(self):
        # _norm_ppf(0.95) ≈ 1.645
        assert abs(_norm_ppf(0.95) - 1.645) < 0.01

    def test_ppf_50(self):
        # _norm_ppf(0.50) ≈ 0 (median).  A&S 26.2.23 has a residual ~1e-7
        # at p=0.5 because the formula's domain extends to (0, 0.5) via
        # symmetric reflection — exact zero is not promised.
        assert abs(_norm_ppf(0.5)) < 1e-6

    def test_ppf_below_50_negative(self):
        # Symmetric: _norm_ppf(0.025) ≈ -1.96
        assert abs(_norm_ppf(0.025) - (-1.96)) < 0.01

    def test_ppf_zero_returns_zero(self):
        # Out-of-domain guards (don't raise).
        assert _norm_ppf(0.0) == 0.0
        assert _norm_ppf(-0.5) == 0.0
        assert _norm_ppf(1.0) == 0.0
        assert _norm_ppf(1.5) == 0.0

    def test_cdf_zero(self):
        # CDF at the mean = 0.5.
        assert _norm_cdf(0.0) == 0.5

    def test_cdf_one_sigma(self):
        # CDF at +1σ ≈ 0.8413
        assert abs(_norm_cdf(1.0) - 0.8413) < 0.001

    def test_cdf_two_sigma(self):
        # CDF at +2σ ≈ 0.9772
        assert abs(_norm_cdf(2.0) - 0.9772) < 0.001

    def test_cdf_negative_two_sigma(self):
        assert abs(_norm_cdf(-2.0) - 0.0228) < 0.001

    def test_cdf_extreme_positive(self):
        assert _norm_cdf(10.0) > 0.999999

    def test_cdf_extreme_negative(self):
        assert _norm_cdf(-10.0) < 0.000001

    def test_cdf_symmetric(self):
        # CDF(-x) == 1 - CDF(x) for any x.
        for x in [0.5, 1.0, 1.5, 2.5]:
            assert abs(_norm_cdf(-x) + _norm_cdf(x) - 1.0) < 1e-6


# ────────────────────────────────────────────────────────────────────
# compute_dsr — Deflated Sharpe Ratio
# ────────────────────────────────────────────────────────────────────


def _make_complete_trial(value: float, params: dict | None = None) -> dict:
    return {"state": "COMPLETE", "value": value, "params": params or {}, "number": 0}


class TestComputeDSR:
    def test_too_few_trials_returns_none(self):
        # < 5 valid trials -> None.
        trials = [_make_complete_trial(1.0) for _ in range(4)]
        assert compute_dsr(1.5, trials, 0.0, 3.0, 100) is None

    def test_too_few_observations_returns_none(self):
        # < 5 daily-return observations -> None.
        trials = [_make_complete_trial(1.0 + i * 0.1) for i in range(10)]
        assert compute_dsr(1.5, trials, 0.0, 3.0, 4) is None

    def test_none_best_sharpe_returns_none(self):
        trials = [_make_complete_trial(1.0 + i * 0.1) for i in range(10)]
        assert compute_dsr(None, trials, 0.0, 3.0, 100) is None

    def test_zero_variance_does_not_crash(self):
        # All trials identical -> sr_var ≈ 0 (modulo float noise).  When the
        # spread vanishes the multiple-testing inflation goes to zero and
        # DSR collapses to plain PSR — the implementation gracefully falls
        # through that edge instead of dividing by zero.
        trials = [_make_complete_trial(1.5) for _ in range(10)]
        out = compute_dsr(1.5, trials, 0.0, 3.0, 100)
        # Either None (true zero variance) or a valid probability in [0, 1].
        assert out is None or 0.0 <= out <= 1.0

    def test_happy_path_returns_probability(self):
        # Healthy spread of trial Sharpes; result must be in (0, 1).
        trials = [_make_complete_trial(0.5 + i * 0.2) for i in range(20)]
        out = compute_dsr(2.0, trials, 0.0, 3.0, 252)
        assert out is not None
        assert 0.0 < out < 1.0

    def test_uses_filter_completed_trials(self):
        # Mix of valid + invalid trials — only valid count toward the
        # denominator's degrees of freedom.
        trials = [_make_complete_trial(0.5 + i * 0.2) for i in range(20)]
        trials += [
            {"state": "PRUNED", "value": 99.0, "params": {}},
            {"state": "COMPLETE", "value": None, "params": {}},
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {}},
        ]
        with_noise = compute_dsr(2.0, trials, 0.0, 3.0, 252)
        clean = compute_dsr(
            2.0,
            [_make_complete_trial(0.5 + i * 0.2) for i in range(20)],
            0.0, 3.0, 252,
        )
        # Same answer regardless of how many invalid trials are in the list.
        assert with_noise == clean

    def test_negative_denominator_returns_none(self):
        # Build a degenerate skew/kurt that makes denom_sq <= 0.
        trials = [_make_complete_trial(1.0 + i * 0.1) for i in range(10)]
        # very high best_sharpe with extreme skew/kurt forces denom_sq < 0.
        out = compute_dsr(50.0, trials, 100.0, -5.0, 100)
        assert out is None


# ────────────────────────────────────────────────────────────────────
# compute_param_sensitivity
# ────────────────────────────────────────────────────────────────────


class TestComputeParamSensitivity:
    def test_too_few_trials_returns_none(self):
        trials = [_make_complete_trial(1.0, {"x": 0.5}) for _ in range(9)]
        assert compute_param_sensitivity(
            trials, {"x": {"type": "float", "min": 0, "max": 1}}, {"x": 1.0},
        ) is None

    def test_single_param_returns_bins(self):
        trials = [_make_complete_trial(0.5 + i * 0.05, {"x": float(i)}) for i in range(20)]
        result = compute_param_sensitivity(
            trials, {"x": {"type": "float", "min": 0, "max": 19}}, {"x": 1.0},
        )
        assert result is not None
        assert "x" in result["single_param"]
        assert "bins" in result["single_param"]["x"]
        assert "values" in result["single_param"]["x"]
        assert len(result["single_param"]["x"]["bins"]) == \
            len(result["single_param"]["x"]["values"])

    def test_grid_for_top_pairs(self):
        trials = []
        for i in range(40):
            trials.append(_make_complete_trial(
                value=0.5 + (i % 5) * 0.1,
                params={"x": float(i), "y": float(i * 2), "z": float(i * 3)},
            ))
        ranges = {
            "x": {"type": "float", "min": 0, "max": 40},
            "y": {"type": "float", "min": 0, "max": 80},
            "z": {"type": "float", "min": 0, "max": 120},
        }
        importances = {"x": 0.5, "y": 0.3, "z": 0.2}
        result = compute_param_sensitivity(trials, ranges, importances, max_pairs=2)
        # 3 params -> top 4 = all 3 -> pairs = (x,y), (x,z), (y,z) but capped at 2.
        assert len(result["grid"]) == 2
        # Top pair by importance is (x, y).
        assert "x__y" in result["grid"]

    def test_grid_entry_shape(self):
        trials = []
        for i in range(30):
            trials.append(_make_complete_trial(
                value=float(i) / 10,
                params={"x": float(i), "y": float(i * 2)},
            ))
        result = compute_param_sensitivity(
            trials,
            {"x": {"type": "float", "min": 0, "max": 30},
             "y": {"type": "float", "min": 0, "max": 60}},
            {"x": 0.6, "y": 0.4},
        )
        entry = result["grid"]["x__y"]
        assert set(entry.keys()) == {"x_bins", "y_bins", "values", "x_label", "y_label"}
        assert entry["x_label"] == "x"
        assert entry["y_label"] == "y"
        assert isinstance(entry["values"], list)
        assert len(entry["values"]) == len(entry["x_bins"])
        for row in entry["values"]:
            assert len(row) == len(entry["y_bins"])

    def test_skips_param_with_too_few_samples(self):
        # 'y' only present on 5 trials -> skipped; 'x' on all 20 -> kept.
        trials = []
        for i in range(20):
            params = {"x": float(i)}
            if i < 5:
                params["y"] = float(i)
            trials.append(_make_complete_trial(0.5 + i * 0.05, params))
        result = compute_param_sensitivity(
            trials,
            {"x": {"type": "float", "min": 0, "max": 19},
             "y": {"type": "float", "min": 0, "max": 4}},
            {"x": 1.0, "y": 0.1},
        )
        assert "x" in result["single_param"]
        assert "y" not in result["single_param"]

    def test_filters_failed_trials(self):
        # Mix complete + fail-value + None; only completes count.
        trials = [_make_complete_trial(float(i) / 10, {"x": float(i)}) for i in range(20)]
        trials += [
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {"x": 999.0}},
            {"state": "PRUNED", "value": 99.0, "params": {"x": -100.0}},
        ]
        result = compute_param_sensitivity(
            trials, {"x": {"type": "float", "min": 0, "max": 19}}, {"x": 1.0},
        )
        # The poison values (999, -100) shouldn't appear in any bin centers.
        bin_centers = result["single_param"]["x"]["bins"]
        for c in bin_centers:
            assert -50 < c < 50, f"Poison value leaked into bin {c}"


# ────────────────────────────────────────────────────────────────────
# compute_param_stability
# ────────────────────────────────────────────────────────────────────


class TestComputeParamStability:
    def test_empty_best_params_returns_none(self):
        trials = [_make_complete_trial(1.0, {"x": 0.5}) for _ in range(10)]
        assert compute_param_stability(trials, {}) is None

    def test_too_few_nearby_returns_none(self):
        # All trials are far from best -> < 3 nearby.
        trials = [_make_complete_trial(1.0, {"x": 100.0 + i}) for i in range(10)]
        assert compute_param_stability(trials, {"x": 0.5}) is None

    def test_uniform_nearby_zero_std(self):
        # 10 nearby trials, all with the same value -> std == 0.
        trials = [_make_complete_trial(1.0, {"x": 0.5 + i * 0.001}) for i in range(10)]
        out = compute_param_stability(trials, {"x": 0.5}, threshold=0.20)
        assert out == 0.0

    def test_varying_nearby_returns_std(self):
        trials = [
            _make_complete_trial(1.0, {"x": 0.50}),
            _make_complete_trial(1.5, {"x": 0.51}),
            _make_complete_trial(2.0, {"x": 0.52}),
        ]
        out = compute_param_stability(trials, {"x": 0.5}, threshold=0.20)
        assert out is not None
        assert out > 0.0

    def test_threshold_excludes_far_trials(self):
        # Within threshold: 3 trials. Outside: 5 trials with wild values.
        trials = [_make_complete_trial(1.0 + i * 0.01, {"x": 0.50 + i * 0.001})
                  for i in range(3)]
        trials += [_make_complete_trial(100.0, {"x": 5.0 + i}) for i in range(5)]
        out = compute_param_stability(trials, {"x": 0.5}, threshold=0.05)
        # Should be small (using only the 3 close trials), not dominated by 100s.
        assert out is not None
        assert out < 1.0

    def test_filters_failed_trials(self):
        trials = [_make_complete_trial(1.0, {"x": 0.5}) for _ in range(10)]
        trials += [
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {"x": 0.5}},
            {"state": "PRUNED", "value": 99.0, "params": {"x": 0.5}},
        ]
        # Filtered trials don't contribute to the std.
        out = compute_param_stability(trials, {"x": 0.5}, threshold=0.20)
        assert out == 0.0

    def test_missing_param_in_trial_excluded(self):
        # Some trials lack the 'x' param -> those are not "near".
        trials = [_make_complete_trial(1.0, {"x": 0.5}) for _ in range(3)]
        trials += [_make_complete_trial(99.0, {"y": 0.5}) for _ in range(5)]  # no 'x'
        out = compute_param_stability(trials, {"x": 0.5}, threshold=0.20)
        # Std of three identical values = 0.
        assert out == 0.0

    def test_zero_best_value_uses_eps(self):
        # best_param = 0 — division-by-zero guard via max(abs, 1e-9).
        trials = [_make_complete_trial(1.0, {"x": 0.0 + i * 1e-12}) for i in range(5)]
        out = compute_param_stability(trials, {"x": 0.0}, threshold=0.20)
        assert out is not None  # Doesn't crash; nearby check uses eps.
