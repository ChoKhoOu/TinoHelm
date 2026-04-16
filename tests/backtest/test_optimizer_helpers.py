"""Tests for ``tinohelm.backtest.optimizer_helpers`` — NT-free and Optuna-free.

The helpers in this module deliberately avoid both NautilusTrader and
Optuna, so they can be tested in environments where neither wheel is
installed.  Mirrors the pattern set by ``test_runner_pure_helpers.py``.

Scope:

* Date-range splitting and walk-forward window generation.
* Fitness extraction and smart defaults (``auto_*``).
* Result slimming.
* Deflated Sharpe Ratio math — every short-circuit branch documented.
* Parameter sensitivity & stability helpers (numpy-only).
* ``PatienceTracker`` — thread-safe bookkeeping for early stopping.
"""
from __future__ import annotations

import math
import threading
from datetime import date

import numpy as np
import pytest

from tinohelm.backtest.optimizer_helpers import (
    FAIL_VALUE,
    FITNESS_METRICS,
    PatienceTracker,
    auto_n_trials,
    auto_sampler,
    auto_workers,
    compute_dsr,
    compute_param_sensitivity,
    compute_param_stability,
    extract_fitness,
    slim_result,
    split_dates,
    walk_forward_windows,
)


# ────────────────────────────────────────────────────────────────────
# Module-level constants
# ────────────────────────────────────────────────────────────────────


class TestConstants:

    def test_fitness_metrics_contains_core_objectives(self):
        assert FITNESS_METRICS == {
            "sharpe": "sharpe_ratio",
            "calmar": "calmar_ratio",
            "sortino": "sortino_ratio",
            "profit": "total_pnl",
        }

    def test_fail_value_is_negative_sentinel(self):
        assert FAIL_VALUE == -999.0
        assert FAIL_VALUE < 0


# ────────────────────────────────────────────────────────────────────
# split_dates
# ────────────────────────────────────────────────────────────────────


class TestSplitDates:

    def test_happy_path_80_20(self):
        tr_s, tr_e, te_s, te_e = split_dates(
            date(2025, 1, 1), date(2025, 1, 11), 80,
        )
        # 10 days * 80% = 8 train days
        assert tr_s == date(2025, 1, 1)
        assert tr_e == date(2025, 1, 9)
        assert te_s == date(2025, 1, 10)
        assert te_e == date(2025, 1, 11)

    def test_fifty_fifty_split(self):
        tr_s, tr_e, te_s, te_e = split_dates(
            date(2025, 1, 1), date(2025, 1, 11), 50,
        )
        assert tr_s == date(2025, 1, 1)
        assert tr_e == date(2025, 1, 6)   # +5 days
        assert te_s == date(2025, 1, 7)
        assert te_e == date(2025, 1, 11)

    def test_zero_train_pct_collapses_train_to_start(self):
        tr_s, tr_e, te_s, te_e = split_dates(
            date(2025, 1, 1), date(2025, 1, 10), 0,
        )
        assert tr_s == date(2025, 1, 1)
        assert tr_e == date(2025, 1, 1)  # 0 days
        assert te_s == date(2025, 1, 2)

    def test_full_train_pct(self):
        tr_s, tr_e, te_s, te_e = split_dates(
            date(2025, 1, 1), date(2025, 1, 10), 100,
        )
        assert tr_e == date(2025, 1, 10)
        # test_start lands one past end_date — degenerate test window
        assert te_s == date(2025, 1, 11)
        assert te_e == date(2025, 1, 10)

    def test_single_day_range_zero_total_days(self):
        tr_s, tr_e, te_s, te_e = split_dates(
            date(2025, 1, 1), date(2025, 1, 1), 80,
        )
        assert tr_e == date(2025, 1, 1)
        assert te_s == date(2025, 1, 2)

    def test_fractional_train_pct(self):
        # 100 days * 33% = 33 train days
        tr_s, tr_e, _, _ = split_dates(
            date(2025, 1, 1), date(2025, 4, 11), 33,
        )
        assert tr_e == date(2025, 2, 3)


# ────────────────────────────────────────────────────────────────────
# walk_forward_windows
# ────────────────────────────────────────────────────────────────────


class TestWalkForwardWindows:

    def test_happy_path_3_folds(self):
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=70, n_folds=3,
        )
        assert len(windows) == 3
        # Last fold's test_end must exactly hit end_date
        assert windows[-1][3] == date(2025, 4, 1)
        # Windows must slide forward — test_start of fold i must be > fold i-1
        for i in range(1, len(windows)):
            assert windows[i][2] > windows[i - 1][2]

    def test_zero_folds_fallback_to_single_split(self):
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=80, n_folds=0,
        )
        assert len(windows) == 1
        # Matches simple split
        expected = split_dates(date(2025, 1, 1), date(2025, 4, 1), 80)
        assert windows[0] == expected

    def test_negative_folds_fallback_to_single_split(self):
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=70, n_folds=-3,
        )
        assert len(windows) == 1

    def test_full_train_pct_fallback(self):
        # test_ratio == 0 → fallback to simple split
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=100, n_folds=5,
        )
        assert len(windows) == 1

    def test_over_full_train_pct_fallback(self):
        # test_ratio < 0 → fallback
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=110, n_folds=5,
        )
        assert len(windows) == 1

    def test_folds_clamped_to_boundary(self):
        # First fold's training would extend before start_date → clamp
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 4, 1), train_pct=70, n_folds=3,
        )
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s >= date(2025, 1, 1)
            assert te_e <= date(2025, 4, 1)

    def test_non_overlapping_test_segments(self):
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 6, 1), train_pct=60, n_folds=4,
        )
        # Each fold's test segment must be strictly after the previous one's.
        for i in range(1, len(windows)):
            assert windows[i][2] > windows[i - 1][3]

    def test_windows_have_train_precede_test(self):
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 6, 1), train_pct=60, n_folds=4,
        )
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s < tr_e
            assert te_s < te_e
            assert tr_e < te_s  # training ends before test begins

    def test_degenerate_short_range_falls_back(self):
        # With only 2 days of data and 5 folds, nothing can fit.
        windows = walk_forward_windows(
            date(2025, 1, 1), date(2025, 1, 2), train_pct=70, n_folds=5,
        )
        # Either single-window fallback or degenerate-but-dropped → fallback
        assert len(windows) >= 1


# ────────────────────────────────────────────────────────────────────
# extract_fitness
# ────────────────────────────────────────────────────────────────────


class TestExtractFitness:

    def test_happy_path_sharpe(self):
        result = {"statistics": {"sharpe_ratio": 1.8}}
        assert extract_fitness(result, "sharpe") == 1.8

    def test_calmar(self):
        result = {"statistics": {"calmar_ratio": 2.1}}
        assert extract_fitness(result, "calmar") == 2.1

    def test_sortino(self):
        result = {"statistics": {"sortino_ratio": 1.5}}
        assert extract_fitness(result, "sortino") == 1.5

    def test_profit(self):
        result = {"statistics": {"total_pnl": 1234.56}}
        assert extract_fitness(result, "profit") == 1234.56

    def test_unknown_objective_returns_fail(self):
        assert extract_fitness({"statistics": {}}, "unknown") == FAIL_VALUE

    def test_missing_statistics_key_returns_fail(self):
        assert extract_fitness({}, "sharpe") == FAIL_VALUE

    def test_none_statistics_is_safe(self):
        # Bug fix: ``result.get("statistics") or {}`` short-circuits None.
        assert extract_fitness({"statistics": None}, "sharpe") == FAIL_VALUE

    def test_missing_metric_returns_fail(self):
        assert extract_fitness({"statistics": {"other": 1.0}}, "sharpe") == FAIL_VALUE

    def test_none_metric_returns_fail(self):
        result = {"statistics": {"sharpe_ratio": None}}
        assert extract_fitness(result, "sharpe") == FAIL_VALUE

    def test_string_metric_coerces_to_float(self):
        result = {"statistics": {"sharpe_ratio": "1.5"}}
        assert extract_fitness(result, "sharpe") == 1.5

    def test_non_numeric_string_returns_fail(self):
        result = {"statistics": {"sharpe_ratio": "not a number"}}
        assert extract_fitness(result, "sharpe") == FAIL_VALUE


# ────────────────────────────────────────────────────────────────────
# auto_n_trials / auto_sampler / auto_workers
# ────────────────────────────────────────────────────────────────────


class TestAutoNTrials:

    def test_empty_ranges_returns_floor(self):
        assert auto_n_trials({}) == 50

    def test_small_dim_scales_to_floor(self):
        # 2 dims * 20 = 40, below floor of 50
        assert auto_n_trials({"a": {}, "b": {}}) == 50

    def test_large_dim_scales_up(self):
        ranges = {f"p{i}": {} for i in range(10)}
        assert auto_n_trials(ranges) == 200  # 10 * 20

    def test_exact_floor_boundary(self):
        # 3 dims * 20 = 60, above floor
        ranges = {"a": {}, "b": {}, "c": {}}
        assert auto_n_trials(ranges) == 60


class TestAutoSampler:

    def test_low_dim_continuous_picks_cmaes(self):
        ranges = {"a": {"type": "float"}, "b": {"type": "float"}}
        assert auto_sampler(ranges) == "cmaes"

    def test_integer_param_forces_tpe(self):
        ranges = {"a": {"type": "float"}, "b": {"type": "int"}}
        assert auto_sampler(ranges) == "tpe"

    def test_high_dim_picks_tpe(self):
        ranges = {f"p{i}": {"type": "float"} for i in range(5)}
        assert auto_sampler(ranges) == "tpe"

    def test_boundary_3_dim_continuous_is_cmaes(self):
        ranges = {"a": {"type": "float"}, "b": {"type": "float"}, "c": {"type": "float"}}
        assert auto_sampler(ranges) == "cmaes"

    def test_boundary_4_dim_falls_back_to_tpe(self):
        ranges = {f"p{i}": {"type": "float"} for i in range(4)}
        assert auto_sampler(ranges) == "tpe"

    def test_empty_ranges_picks_cmaes(self):
        # 0 dims, no int → meets the CMA-ES criteria; defensible default
        assert auto_sampler({}) == "cmaes"


class TestAutoWorkers:

    def test_injected_cpu_count(self):
        assert auto_workers(cpu_count=8) == 4  # capped
        assert auto_workers(cpu_count=4) == 2
        assert auto_workers(cpu_count=2) == 1
        assert auto_workers(cpu_count=1) == 1  # floor

    def test_zero_cpu_count_clamped_to_one(self):
        assert auto_workers(cpu_count=0) == 1

    def test_cap_at_four(self):
        assert auto_workers(cpu_count=128) == 4

    def test_default_reads_os_cpu_count(self):
        # Just verify it returns something in-range; exact value depends on host.
        result = auto_workers()
        assert 1 <= result <= 4


# ────────────────────────────────────────────────────────────────────
# slim_result
# ────────────────────────────────────────────────────────────────────


class TestSlimResult:

    def test_none_passes_through(self):
        assert slim_result(None) is None

    def test_keeps_only_expected_keys(self):
        out = slim_result({
            "statistics": {"sharpe": 1.5},
            "equity_curve": [1, 2, 3],
            "monthly_returns": [0.01, 0.02],
            "trades": [{"huge": "blob"}],
            "bars": [1] * 10000,
            "events": ["many"],
        })
        assert set(out.keys()) == {"statistics", "equity_curve", "monthly_returns"}
        assert out["statistics"] == {"sharpe": 1.5}
        assert out["equity_curve"] == [1, 2, 3]
        assert out["monthly_returns"] == [0.01, 0.02]

    def test_missing_keys_become_none(self):
        out = slim_result({"statistics": {"x": 1}})
        assert out["statistics"] == {"x": 1}
        assert out["equity_curve"] is None
        assert out["monthly_returns"] is None


# ────────────────────────────────────────────────────────────────────
# compute_dsr
# ────────────────────────────────────────────────────────────────────


def _make_trials(values: list[float]) -> list[dict]:
    """Build a trials_data list where every trial is COMPLETE with a given value."""
    return [
        {"number": i, "params": {"x": i}, "value": v, "state": "COMPLETE"}
        for i, v in enumerate(values)
    ]


class TestComputeDsr:

    # Deterministic fake CDF/PPF so math is testable without numpy/scipy.
    @staticmethod
    def _identity_ppf(p: float) -> float:
        # Invertible, non-degenerate, monotonic in (0, 1).
        return math.log(p / (1 - p))

    @staticmethod
    def _identity_cdf(z: float) -> float:
        return 1 / (1 + math.exp(-z))

    def test_too_few_trials_returns_none(self):
        trials = _make_trials([1.0, 1.1, 1.2, 1.3])  # only 4
        out = compute_dsr(
            best_sharpe=2.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
        )
        assert out is None

    def test_too_few_obs_returns_none(self):
        trials = _make_trials([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
        out = compute_dsr(
            best_sharpe=2.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=3,
        )
        assert out is None

    def test_none_best_sharpe_returns_none(self):
        trials = _make_trials([1.0, 1.1, 1.2, 1.3, 1.4])
        out = compute_dsr(
            best_sharpe=None, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
        )
        assert out is None

    def test_filters_non_complete_trials(self):
        # Only 3 COMPLETE trials → too few.
        trials = [
            {"value": 1.0, "state": "COMPLETE"},
            {"value": 1.1, "state": "COMPLETE"},
            {"value": 1.2, "state": "COMPLETE"},
            {"value": 1.3, "state": "PRUNED"},
            {"value": 1.4, "state": "FAIL"},
        ]
        out = compute_dsr(
            best_sharpe=2.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert out is None

    def test_filters_fail_value_trials(self):
        # 5 "complete" trials but two are at FAIL_VALUE → only 3 valid.
        trials = [
            {"value": FAIL_VALUE, "state": "COMPLETE"},
            {"value": FAIL_VALUE, "state": "COMPLETE"},
            {"value": 1.2, "state": "COMPLETE"},
            {"value": 1.3, "state": "COMPLETE"},
            {"value": 1.4, "state": "COMPLETE"},
        ]
        out = compute_dsr(
            best_sharpe=2.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert out is None  # only 3 valid

    def test_filters_none_value_trials(self):
        trials = [
            {"value": None, "state": "COMPLETE"},
            {"value": None, "state": "COMPLETE"},
            {"value": 1.2, "state": "COMPLETE"},
            {"value": 1.3, "state": "COMPLETE"},
            {"value": 1.4, "state": "COMPLETE"},
        ]
        out = compute_dsr(
            best_sharpe=2.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert out is None

    def test_zero_variance_returns_none(self):
        # All trials at exactly 0 → sr_var is exactly 0 (no float drift) →
        # short-circuit.  Non-zero identical values don't reliably hit the
        # branch because summed floating-point error lifts variance above 0.
        trials = _make_trials([0.0] * 10)
        out = compute_dsr(
            best_sharpe=0.0, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=100,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert out is None

    def test_happy_path_returns_probability(self):
        trials = _make_trials([1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 1.3, 1.5, 1.7, 1.9])
        out = compute_dsr(
            best_sharpe=2.5, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=252,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert out is not None
        assert 0.0 <= out <= 1.0
        # Rounded to 4 decimal places
        assert round(out, 4) == out

    def test_none_skewness_kurtosis_treated_as_zero(self):
        trials = _make_trials([1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 1.3, 1.5, 1.7, 1.9])
        with_zeros = compute_dsr(
            best_sharpe=2.5, trials_data=trials,
            skewness=0, kurtosis=0, n_obs=252,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        with_nones = compute_dsr(
            best_sharpe=2.5, trials_data=trials,
            skewness=None, kurtosis=None, n_obs=252,
            norm_ppf=self._identity_ppf, norm_cdf=self._identity_cdf,
        )
        assert with_zeros == with_nones

    def test_uses_default_norm_funcs_when_not_injected(self):
        # With the real statistics module, this path still executes without error.
        trials = _make_trials([1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 1.3, 1.5, 1.7, 1.9])
        out = compute_dsr(
            best_sharpe=2.5, trials_data=trials,
            skewness=0, kurtosis=3, n_obs=252,
        )
        assert out is not None
        assert 0.0 <= out <= 1.0


# ────────────────────────────────────────────────────────────────────
# compute_param_sensitivity
# ────────────────────────────────────────────────────────────────────


class TestComputeParamSensitivity:

    def _make_2d_trials(self, n: int) -> list[dict]:
        """Build deterministic 2D param trials where fitness = a - b."""
        rng = np.random.default_rng(seed=11)
        trials = []
        for i in range(n):
            a = float(rng.uniform(0, 10))
            b = float(rng.uniform(0, 10))
            trials.append({
                "number": i,
                "params": {"a": a, "b": b},
                "value": a - b,
                "state": "COMPLETE",
            })
        return trials

    def test_too_few_trials_returns_none(self):
        trials = self._make_2d_trials(9)
        out = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 0.5, "b": 0.5},
        )
        assert out is None

    def test_happy_path_returns_single_and_grid(self):
        trials = self._make_2d_trials(30)
        out = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 0.8, "b": 0.2},
        )
        assert out is not None
        assert "single_param" in out
        assert "grid" in out
        # Both params are present in single_param
        assert "a" in out["single_param"]
        assert "b" in out["single_param"]
        # One pair key exists (a__b, sorted by importance)
        assert "a__b" in out["grid"]

    def test_fail_value_trials_excluded(self):
        trials = self._make_2d_trials(12)
        for i in range(5):
            trials[i]["value"] = FAIL_VALUE
        # Only 7 valid → below the floor of 10
        out = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 0.5, "b": 0.5},
        )
        assert out is None

    def test_non_complete_trials_excluded(self):
        trials = self._make_2d_trials(15)
        for i in range(7):
            trials[i]["state"] = "PRUNED"
        # 8 valid → still below the floor
        out = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 0.5, "b": 0.5},
        )
        assert out is None

    def test_single_param_bin_shape(self):
        trials = self._make_2d_trials(40)
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0}, n_bins=5,
        )
        assert out is not None
        entry = out["single_param"]["a"]
        assert "bins" in entry and "values" in entry
        assert len(entry["bins"]) == len(entry["values"])
        assert len(entry["bins"]) <= 5

    def test_grid_max_pairs_caps_output(self):
        # 4 params, max_pairs=1 → exactly 1 grid entry
        trials = []
        rng = np.random.default_rng(seed=7)
        for i in range(30):
            p = {k: float(rng.uniform(0, 10)) for k in "abcd"}
            trials.append({
                "params": p, "value": sum(p.values()), "state": "COMPLETE",
            })
        out = compute_param_sensitivity(
            trials,
            {k: {} for k in "abcd"},
            {"a": 1.0, "b": 0.9, "c": 0.8, "d": 0.7},
            max_pairs=1,
        )
        assert len(out["grid"]) == 1

    def test_grid_respects_importance_ordering(self):
        # Only params with importance > 0 that exist in ranges → sorted desc
        trials = []
        rng = np.random.default_rng(seed=13)
        for _ in range(30):
            p = {k: float(rng.uniform(0, 10)) for k in "abc"}
            trials.append({
                "params": p, "value": sum(p.values()), "state": "COMPLETE",
            })
        out = compute_param_sensitivity(
            trials,
            {"a": {}, "b": {}, "c": {}},
            {"a": 0.5, "b": 0.9, "c": 0.1},
            max_pairs=5,
        )
        # Top 4 sorted: b, a, c → pairs are b__a, b__c, a__c
        assert "b__a" in out["grid"]
        # Not a__b (wrong order)
        assert "a__b" not in out["grid"]


# ────────────────────────────────────────────────────────────────────
# compute_param_stability
# ────────────────────────────────────────────────────────────────────


class TestComputeParamStability:

    def test_empty_best_params_returns_none(self):
        trials = [
            {"params": {"a": 1.0}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"a": 1.0}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"a": 1.0}, "value": 1.4, "state": "COMPLETE"},
        ]
        assert compute_param_stability(trials, {}) is None

    def test_too_few_nearby_returns_none(self):
        # Only 2 nearby trials — threshold not met.
        trials = [
            {"params": {"a": 1.0}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"a": 1.01}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"a": 5.0}, "value": 0.1, "state": "COMPLETE"},  # far away
        ]
        assert compute_param_stability(trials, {"a": 1.0}, threshold=0.05) is None

    def test_happy_path_returns_std(self):
        # Generate 5 nearby trials (±5%) with a specific std.
        trials = [
            {"params": {"a": 1.00}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"a": 1.02}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"a": 0.98}, "value": 1.4, "state": "COMPLETE"},
            {"params": {"a": 1.03}, "value": 1.55, "state": "COMPLETE"},
            {"params": {"a": 0.97}, "value": 1.45, "state": "COMPLETE"},
        ]
        out = compute_param_stability(trials, {"a": 1.0}, threshold=0.05)
        assert out is not None
        assert out > 0.0

    def test_fail_value_trials_excluded(self):
        trials = [
            {"params": {"a": 1.0}, "value": FAIL_VALUE, "state": "COMPLETE"},
            {"params": {"a": 1.0}, "value": FAIL_VALUE, "state": "COMPLETE"},
            {"params": {"a": 1.0}, "value": FAIL_VALUE, "state": "COMPLETE"},
        ]
        assert compute_param_stability(trials, {"a": 1.0}) is None

    def test_missing_param_treated_as_far(self):
        trials = [
            {"params": {"other": 1.0}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"other": 1.0}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"other": 1.0}, "value": 1.4, "state": "COMPLETE"},
        ]
        # 'a' missing from every trial → none are "near"
        assert compute_param_stability(trials, {"a": 1.0}) is None

    def test_zero_best_param_uses_epsilon_floor(self):
        # bv=0 → divisor is 1e-9, so any non-zero param is "far"
        trials = [
            {"params": {"a": 0.0}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"a": 0.0}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"a": 0.0}, "value": 1.4, "state": "COMPLETE"},
            {"params": {"a": 0.001}, "value": 2.0, "state": "COMPLETE"},  # far
        ]
        out = compute_param_stability(trials, {"a": 0.0})
        # Three trials with a=0 are "near" (|0-0|/1e-9 = 0 ≤ 0.2)
        assert out is not None
        assert out > 0

    def test_multi_param_all_must_be_near(self):
        # Trial must match ALL best_params to be nearby.
        trials = [
            {"params": {"a": 1.0, "b": 1.0}, "value": 1.5, "state": "COMPLETE"},
            {"params": {"a": 1.0, "b": 1.0}, "value": 1.6, "state": "COMPLETE"},
            {"params": {"a": 1.0, "b": 1.0}, "value": 1.4, "state": "COMPLETE"},
            # 'a' close, 'b' far → not nearby
            {"params": {"a": 1.0, "b": 5.0}, "value": 0.1, "state": "COMPLETE"},
        ]
        out = compute_param_stability(trials, {"a": 1.0, "b": 1.0}, threshold=0.1)
        assert out is not None

    def test_rounded_to_4_decimals(self):
        trials = [
            {"params": {"a": 1.0}, "value": v, "state": "COMPLETE"}
            for v in [1.50001, 1.51, 1.49999, 1.5001, 1.4999]
        ]
        out = compute_param_stability(trials, {"a": 1.0})
        assert out == round(out, 4)


# ────────────────────────────────────────────────────────────────────
# PatienceTracker
# ────────────────────────────────────────────────────────────────────


class TestPatienceTracker:

    def test_initial_state_best_is_minus_inf(self):
        pt = PatienceTracker(patience=3)
        assert pt.best == -math.inf
        assert pt.no_improve_count == 0

    def test_first_improvement_resets_counter(self):
        pt = PatienceTracker(patience=3)
        # First finite value is always an improvement
        stop = pt.observe(1.0)
        assert stop is False
        assert pt.best == 1.0
        assert pt.no_improve_count == 0

    def test_no_improvement_increments_counter(self):
        pt = PatienceTracker(patience=3)
        pt.observe(1.0)   # best=1, count=0
        stop = pt.observe(0.5)  # count=1
        assert stop is False
        stop = pt.observe(0.5)  # count=2
        assert stop is False
        stop = pt.observe(0.5)  # count=3 → stop
        assert stop is True

    def test_improvement_resets_counter(self):
        pt = PatienceTracker(patience=3)
        pt.observe(1.0)
        pt.observe(0.5)
        pt.observe(0.5)
        pt.observe(2.0)  # improvement → counter reset
        assert pt.no_improve_count == 0
        assert pt.best == 2.0

    def test_equal_to_best_is_not_improvement(self):
        pt = PatienceTracker(patience=3)
        pt.observe(1.0)
        pt.observe(1.0)  # equal → count=1, not reset
        assert pt.no_improve_count == 1

    def test_none_value_counts_as_no_improvement(self):
        pt = PatienceTracker(patience=2)
        pt.observe(1.0)   # best=1, count=0
        pt.observe(None)  # count=1
        stop = pt.observe(None)  # count=2 → stop
        assert stop is True

    def test_patience_one_stops_immediately_on_miss(self):
        pt = PatienceTracker(patience=1)
        pt.observe(1.0)  # improvement, count=0
        stop = pt.observe(0.5)  # no improve, count=1 → stop
        assert stop is True

    def test_thread_safety(self):
        pt = PatienceTracker(patience=1000)
        # 100 threads each bumping 100 unimproved values.
        def worker():
            for _ in range(100):
                pt.observe(0.5)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        pt.observe(1.0)  # establish best
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 10 threads × 100 ops = 1000 — exact count because Lock serializes
        assert pt.no_improve_count == 1000

    def test_observe_returns_bool(self):
        pt = PatienceTracker(patience=2)
        assert pt.observe(1.0) is False
        assert pt.observe(0.5) is False
        assert pt.observe(0.5) is True
