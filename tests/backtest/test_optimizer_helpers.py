"""Unit tests for the NT-free / Optuna-free helpers in
:mod:`tinohelm.backtest.optimizer_helpers`.

These tests intentionally do **not** import :mod:`nautilus_trader` or
:mod:`optuna` so they run in the standard CI Python environment without the
heavy backtesting stack.
"""
from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace

import pytest

from tinohelm.backtest.optimizer_helpers import (
    FAIL_VALUE,
    FITNESS_METRICS,
    PatienceTracker,
    auto_n_trials,
    auto_patience,
    auto_sampler,
    auto_workers,
    build_full_result,
    build_progress_payload,
    build_walk_forward_fold_record,
    compute_dsr,
    compute_param_sensitivity,
    compute_param_stability,
    extract_fitness,
    filter_completed_trials,
    select_best_params,
    serialize_trial,
    slim_result,
    split_dates,
    walk_forward_windows,
)


# ---------------------------------------------------------------------------
# split_dates
# ---------------------------------------------------------------------------

class TestSplitDates:
    def test_50_percent_split(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 11), 50.0)
        assert ts == date(2024, 1, 1)
        # 10 days * 50% = 5 day train window, then test starts day 7
        assert te == date(2024, 1, 6)
        assert vs == date(2024, 1, 7)
        assert ve == date(2024, 1, 11)

    def test_70_percent_split(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 11), 70.0)
        # 10 days * 70% = 7 day train window
        assert te == date(2024, 1, 8)
        assert vs == date(2024, 1, 9)
        assert ve == date(2024, 1, 11)

    def test_zero_train_pct(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 11), 0.0)
        # train_days = 0 → train_end == start
        assert te == date(2024, 1, 1)
        assert vs == date(2024, 1, 2)

    def test_full_train_pct(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 11), 100.0)
        # train_days = 10 → train_end == end, test_start = end + 1
        assert te == date(2024, 1, 11)
        assert vs == date(2024, 1, 12)

    def test_returns_start_and_end_unchanged(self) -> None:
        s, e = date(2023, 6, 1), date(2023, 12, 31)
        ts, te, vs, ve = split_dates(s, e, 60.0)
        assert ts == s
        assert ve == e


# ---------------------------------------------------------------------------
# walk_forward_windows
# ---------------------------------------------------------------------------

class TestWalkForwardWindows:
    def test_returns_n_folds(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, 3)
        assert len(windows) == 3

    def test_fold_test_segments_are_disjoint(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, 3)
        for i in range(len(windows) - 1):
            assert windows[i][3] < windows[i + 1][2]  # test_end_i < test_start_{i+1}

    def test_each_fold_has_train_before_test(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, 3)
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s <= tr_e
            assert tr_e < te_s
            assert te_s <= te_e

    def test_zero_folds_falls_back_to_simple_split(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, 0)
        assert len(windows) == 1
        assert windows[0] == split_dates(date(2024, 1, 1), date(2024, 4, 30), 70.0)

    def test_negative_folds_falls_back(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, -2)
        assert len(windows) == 1

    def test_full_train_pct_falls_back(self) -> None:
        # test_ratio = 0 → fall back to single window
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 100.0, 5)
        assert len(windows) == 1

    def test_clamps_train_start_to_data_boundary(self) -> None:
        # A small range with high fold count forces train_start clamp on early folds
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 1, 31), 80.0, 4)
        for tr_s, _, _, _ in windows:
            assert tr_s >= date(2024, 1, 1)

    def test_last_fold_test_end_does_not_exceed_data_end(self) -> None:
        windows = walk_forward_windows(date(2024, 1, 1), date(2024, 4, 30), 70.0, 4)
        for _, _, _, te_e in windows:
            assert te_e <= date(2024, 4, 30)


# ---------------------------------------------------------------------------
# extract_fitness
# ---------------------------------------------------------------------------

class TestExtractFitness:
    @pytest.mark.parametrize(
        "objective,key,value,expected",
        [
            ("sharpe", "sharpe_ratio", 1.5, 1.5),
            ("calmar", "calmar_ratio", 2.3, 2.3),
            ("sortino", "sortino_ratio", 0.8, 0.8),
            ("profit", "total_pnl", 1234.56, 1234.56),
        ],
    )
    def test_known_objectives(self, objective, key, value, expected) -> None:
        result = {"statistics": {key: value}}
        assert extract_fitness(result, objective) == expected

    def test_unknown_objective_returns_fail_value(self) -> None:
        assert extract_fitness({"statistics": {"sharpe_ratio": 1.0}}, "bogus") == FAIL_VALUE

    def test_missing_statistics_returns_fail_value(self) -> None:
        assert extract_fitness({}, "sharpe") == FAIL_VALUE

    def test_none_statistics_returns_fail_value(self) -> None:
        assert extract_fitness({"statistics": None}, "sharpe") == FAIL_VALUE

    def test_none_value_returns_fail_value(self) -> None:
        assert extract_fitness({"statistics": {"sharpe_ratio": None}}, "sharpe") == FAIL_VALUE

    def test_unparseable_value_returns_fail_value(self) -> None:
        assert extract_fitness(
            {"statistics": {"sharpe_ratio": "not-a-number"}}, "sharpe",
        ) == FAIL_VALUE

    def test_none_result_returns_fail_value(self) -> None:
        assert extract_fitness(None, "sharpe") == FAIL_VALUE

    def test_int_value_coerced_to_float(self) -> None:
        out = extract_fitness({"statistics": {"sharpe_ratio": 2}}, "sharpe")
        assert out == 2.0
        assert isinstance(out, float)

    def test_custom_fail_value_respected(self) -> None:
        assert extract_fitness({}, "sharpe", fail_value=-1.0) == -1.0

    def test_fitness_metrics_table_has_four_objectives(self) -> None:
        assert set(FITNESS_METRICS.keys()) == {"sharpe", "calmar", "sortino", "profit"}


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

class TestAutoNTrials:
    def test_empty_returns_fifty(self) -> None:
        assert auto_n_trials({}) == 50

    def test_one_param_returns_fifty(self) -> None:
        # max(50, 1*20) = 50
        assert auto_n_trials({"a": {}}) == 50

    def test_three_params_returns_sixty(self) -> None:
        # max(50, 3*20) = 60
        assert auto_n_trials({"a": {}, "b": {}, "c": {}}) == 60

    def test_ten_params_returns_two_hundred(self) -> None:
        ranges = {f"p{i}": {} for i in range(10)}
        assert auto_n_trials(ranges) == 200


class TestAutoSampler:
    def test_few_floats_picks_cmaes(self) -> None:
        ranges = {"a": {"type": "float"}, "b": {"type": "float"}}
        assert auto_sampler(ranges) == "cmaes"

    def test_three_floats_still_cmaes(self) -> None:
        ranges = {f"p{i}": {"type": "float"} for i in range(3)}
        assert auto_sampler(ranges) == "cmaes"

    def test_four_floats_picks_tpe(self) -> None:
        ranges = {f"p{i}": {"type": "float"} for i in range(4)}
        assert auto_sampler(ranges) == "tpe"

    def test_int_param_picks_tpe(self) -> None:
        ranges = {"a": {"type": "int"}}
        assert auto_sampler(ranges) == "tpe"

    def test_mixed_int_and_float_picks_tpe(self) -> None:
        ranges = {"a": {"type": "int"}, "b": {"type": "float"}}
        assert auto_sampler(ranges) == "tpe"

    def test_empty_picks_cmaes(self) -> None:
        # 0 dims, no ints — qualifies as low-dim all-float
        assert auto_sampler({}) == "cmaes"


class TestAutoWorkers:
    @pytest.mark.parametrize(
        "cpu,expected",
        [
            (1, 1),     # cpu // 2 = 0 → max(1, 0) = 1
            (2, 1),     # cpu // 2 = 1
            (4, 2),
            (8, 4),
            (16, 4),    # capped at 4
            (32, 4),
        ],
    )
    def test_picks_cpu_over_two_capped_at_four(self, cpu, expected) -> None:
        assert auto_workers(cpu_count=cpu) == expected

    def test_zero_cpu_count_returns_one(self) -> None:
        # Edge case: cpu // 2 = 0 → clamps up to 1
        assert auto_workers(cpu_count=0) == 1


class TestAutoPatience:
    @pytest.mark.parametrize("n_trials", [1, 10, 39])
    def test_below_threshold_returns_zero(self, n_trials) -> None:
        assert auto_patience(n_trials) == 0

    def test_at_threshold_returns_ten(self) -> None:
        # n_trials=40, n // 4 = 10, max(10, 10) = 10
        assert auto_patience(40) == 10

    def test_large_returns_quarter(self) -> None:
        # n_trials=200 → max(10, 50) = 50
        assert auto_patience(200) == 50

    def test_floor_of_ten(self) -> None:
        # n_trials=44 → n // 4 = 11 → max(10, 11) = 11
        assert auto_patience(44) == 11


# ---------------------------------------------------------------------------
# slim_result
# ---------------------------------------------------------------------------

class TestSlimResult:
    def test_keeps_three_fields(self) -> None:
        full = {
            "statistics": {"sharpe_ratio": 1.5},
            "equity_curve": [1, 2, 3],
            "monthly_returns": {"2024-01": 0.05},
            "trades": [{"pnl": 100}],   # discarded
            "extra_field": "xxx",        # discarded
        }
        slim = slim_result(full)
        assert set(slim.keys()) == {"statistics", "equity_curve", "monthly_returns"}
        assert slim["statistics"] == {"sharpe_ratio": 1.5}
        assert slim["equity_curve"] == [1, 2, 3]

    def test_none_input_returns_none(self) -> None:
        assert slim_result(None) is None

    def test_missing_keys_become_none(self) -> None:
        slim = slim_result({})
        assert slim == {
            "statistics": None,
            "equity_curve": None,
            "monthly_returns": None,
        }


# ---------------------------------------------------------------------------
# build_progress_payload
# ---------------------------------------------------------------------------

class TestBuildProgressPayload:
    def test_canonical_seven_keys(self) -> None:
        payload = build_progress_payload(
            optimization_id=42,
            trials_completed=5,
            total_trials=100,
            best_value=1.5,
            best_params={"x": 1},
            status="running",
        )
        assert set(payload.keys()) == {
            "optimization_id", "trials_completed", "total_trials",
            "best_value", "best_params", "status", "message",
        }

    def test_message_default_is_none(self) -> None:
        payload = build_progress_payload(
            optimization_id=1, trials_completed=0, total_trials=10,
            best_value=0.0, best_params={}, status="running",
        )
        assert payload["message"] is None

    def test_explicit_message_preserved(self) -> None:
        payload = build_progress_payload(
            optimization_id=1, trials_completed=0, total_trials=10,
            best_value=0.0, best_params={}, status="failed",
            message="boom",
        )
        assert payload["message"] == "boom"

    def test_best_params_is_copied(self) -> None:
        original = {"x": 1}
        payload = build_progress_payload(
            optimization_id=1, trials_completed=0, total_trials=10,
            best_value=0.0, best_params=original, status="running",
        )
        original["x"] = 999
        assert payload["best_params"]["x"] == 1

    def test_running_and_completed_status_share_shape(self) -> None:
        running = build_progress_payload(
            optimization_id=1, trials_completed=5, total_trials=10,
            best_value=1.0, best_params={"x": 1}, status="running",
        )
        completed = build_progress_payload(
            optimization_id=1, trials_completed=10, total_trials=10,
            best_value=1.5, best_params={"x": 2}, status="completed",
        )
        assert set(running.keys()) == set(completed.keys())


# ---------------------------------------------------------------------------
# serialize_trial
# ---------------------------------------------------------------------------

class TestSerializeTrial:
    def test_basic_trial(self) -> None:
        trial = SimpleNamespace(
            number=3,
            params={"a": 1.5, "b": 2},
            value=0.85,
            state=SimpleNamespace(name="COMPLETE"),
        )
        out = serialize_trial(trial)
        assert out == {
            "number": 3,
            "params": {"a": 1.5, "b": 2},
            "value": 0.85,
            "state": "COMPLETE",
        }

    def test_state_string_passthrough(self) -> None:
        trial = SimpleNamespace(number=0, params={}, value=None, state="PRUNED")
        assert serialize_trial(trial)["state"] == "PRUNED"

    def test_params_dict_is_copied(self) -> None:
        original_params = {"a": 1}
        trial = SimpleNamespace(
            number=0, params=original_params, value=1.0,
            state=SimpleNamespace(name="COMPLETE"),
        )
        out = serialize_trial(trial)
        original_params["a"] = 999
        assert out["params"]["a"] == 1


# ---------------------------------------------------------------------------
# filter_completed_trials
# ---------------------------------------------------------------------------

class TestFilterCompletedTrials:
    def test_keeps_only_complete_with_real_value(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.5},
            {"state": "PRUNED", "value": 1.0},
            {"state": "FAIL", "value": -999.0},
            {"state": "COMPLETE", "value": None},
            {"state": "COMPLETE", "value": -999.0},  # sentinel
            {"state": "COMPLETE", "value": 0.0},
        ]
        out = filter_completed_trials(trials)
        assert len(out) == 2
        assert {t["value"] for t in out} == {1.5, 0.0}

    def test_empty_input(self) -> None:
        assert filter_completed_trials([]) == []

    def test_custom_fail_value(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": -1.0},
            {"state": "COMPLETE", "value": 1.0},
        ]
        out = filter_completed_trials(trials, fail_value=-1.0)
        assert len(out) == 1
        assert out[0]["value"] == 1.0


# ---------------------------------------------------------------------------
# select_best_params
# ---------------------------------------------------------------------------

class TestSelectBestParams:
    def test_filters_to_param_ranges_keys(self) -> None:
        trial_params = {"a": 1, "b": 2, "noise": 99}
        ranges = {"a": {}, "b": {}}
        assert select_best_params(trial_params, ranges) == {"a": 1, "b": 2}

    def test_empty_ranges_returns_empty(self) -> None:
        assert select_best_params({"a": 1}, {}) == {}

    def test_missing_param_in_trial(self) -> None:
        # Only keys present in *both* dicts are kept
        assert select_best_params({"a": 1}, {"a": {}, "b": {}}) == {"a": 1}


# ---------------------------------------------------------------------------
# build_walk_forward_fold_record
# ---------------------------------------------------------------------------

class TestBuildWalkForwardFoldRecord:
    def test_iso_format_and_one_based_fold(self) -> None:
        rec = build_walk_forward_fold_record(
            fold_idx=2,
            train_start=date(2024, 1, 1), train_end=date(2024, 2, 1),
            test_start=date(2024, 2, 2), test_end=date(2024, 3, 1),
            test_value=1.23,
        )
        assert rec == {
            "fold": 3,
            "train_start": "2024-01-01",
            "train_end": "2024-02-01",
            "test_start": "2024-02-02",
            "test_end": "2024-03-01",
            "test_value": 1.23,
        }

    def test_first_fold_is_one(self) -> None:
        rec = build_walk_forward_fold_record(
            fold_idx=0,
            train_start=date(2024, 1, 1), train_end=date(2024, 1, 1),
            test_start=date(2024, 1, 1), test_end=date(2024, 1, 1),
            test_value=0.0,
        )
        assert rec["fold"] == 1


# ---------------------------------------------------------------------------
# build_full_result
# ---------------------------------------------------------------------------

class TestBuildFullResult:
    def _kwargs(self, **overrides):
        base = dict(
            best_params={"a": 1},
            best_value=1.5,
            trials=[{"number": 0}],
            train_start=date(2024, 1, 1),
            train_end=date(2024, 1, 5),
            test_start=date(2024, 1, 6),
            test_end=date(2024, 1, 10),
            validation={"statistics": {}},
            train_validation=None,
            param_importances={"a": 0.7},
            convergence_history=[1.0, 1.5],
            sampler="tpe",
            n_workers=2,
            pruning_enabled=True,
            total_pruned=3,
        )
        base.update(overrides)
        return base

    def test_canonical_top_level_keys_no_walk_forward(self) -> None:
        full = build_full_result(**self._kwargs())
        assert set(full.keys()) == {
            "best_params", "best_value", "trials",
            "train_period", "test_period",
            "validation", "train_validation",
            "param_importances", "convergence_history",
            "sampler", "n_workers", "pruning_enabled", "total_pruned",
            "dsr", "parameter_sensitivity", "parameter_stability_score",
        }

    def test_walk_forward_results_added_when_provided(self) -> None:
        wf = [{"fold": 1, "test_value": 1.0}]
        full = build_full_result(**self._kwargs(walk_forward_results=wf))
        assert "walk_forward_results" in full
        assert full["walk_forward_results"] == wf

    def test_walk_forward_omitted_when_none(self) -> None:
        full = build_full_result(**self._kwargs(walk_forward_results=None))
        assert "walk_forward_results" not in full

    def test_period_dicts_iso_formatted(self) -> None:
        full = build_full_result(**self._kwargs())
        assert full["train_period"] == {"start": "2024-01-01", "end": "2024-01-05"}
        assert full["test_period"] == {"start": "2024-01-06", "end": "2024-01-10"}

    def test_dsr_and_sensitivity_default_none(self) -> None:
        full = build_full_result(**self._kwargs())
        assert full["dsr"] is None
        assert full["parameter_sensitivity"] is None
        assert full["parameter_stability_score"] is None

    def test_dsr_value_passed_through(self) -> None:
        full = build_full_result(**self._kwargs(dsr=0.42))
        assert full["dsr"] == 0.42

    def test_lists_are_copied(self) -> None:
        original_trials = [{"number": 0}]
        original_history = [1.0, 1.5]
        full = build_full_result(**self._kwargs(
            trials=original_trials, convergence_history=original_history,
        ))
        original_trials.append({"number": 99})
        original_history.append(99.0)
        assert len(full["trials"]) == 1
        assert len(full["convergence_history"]) == 2

    def test_best_params_dict_copied(self) -> None:
        original = {"a": 1}
        full = build_full_result(**self._kwargs(best_params=original))
        original["a"] = 999
        assert full["best_params"]["a"] == 1


# ---------------------------------------------------------------------------
# PatienceTracker
# ---------------------------------------------------------------------------

class TestPatienceTracker:
    def test_first_observation_sets_best(self) -> None:
        t = PatienceTracker(patience=3)
        assert t.observe(1.5) is False
        assert t.best == 1.5
        assert t.no_improve_count == 0

    def test_improvement_resets_counter(self) -> None:
        t = PatienceTracker(patience=3)
        t.observe(1.0)
        t.observe(0.5)   # no improvement
        t.observe(0.9)   # no improvement
        assert t.no_improve_count == 2
        t.observe(2.0)   # improvement
        assert t.no_improve_count == 0
        assert t.best == 2.0

    def test_stops_after_patience_consecutive_non_improvements(self) -> None:
        t = PatienceTracker(patience=3)
        t.observe(1.0)
        assert t.observe(0.9) is False
        assert t.observe(0.8) is False
        assert t.observe(0.7) is True   # 3rd non-improvement → stop

    def test_none_value_counts_as_non_improvement(self) -> None:
        t = PatienceTracker(patience=2)
        t.observe(1.0)
        assert t.observe(None) is False
        assert t.observe(None) is True

    def test_equal_value_does_not_count_as_improvement(self) -> None:
        # Optuna semantics: only strictly greater values reset the counter.
        t = PatienceTracker(patience=2)
        t.observe(1.0)
        assert t.observe(1.0) is False
        assert t.observe(1.0) is True

    def test_negative_inf_initial_best(self) -> None:
        t = PatienceTracker(patience=5)
        assert t.best == -math.inf


# ---------------------------------------------------------------------------
# compute_dsr
# ---------------------------------------------------------------------------

def _norm_ppf_stub(p: float) -> float:
    """Identity-ish stub — sufficient for exercising the math."""
    if p == 0.5:
        return 0.0
    if p > 0.5:
        return 2.0
    return -2.0


def _norm_cdf_stub(z: float) -> float:
    """Linear-clipped stub returning a value in (0, 1)."""
    return max(0.001, min(0.999, 0.5 + z * 0.1))


class TestComputeDsr:
    def _trials(self, values: list[float]) -> list[dict]:
        return [
            {"state": "COMPLETE", "value": v}
            for v in values
        ]

    def test_too_few_trials_returns_none(self) -> None:
        out = compute_dsr(
            best_sharpe=1.5,
            trials_data=self._trials([1.0, 1.2, 1.4]),  # only 3 valid
            skewness=0.0, kurtosis=3.0, n_obs=100,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None

    def test_too_few_obs_returns_none(self) -> None:
        out = compute_dsr(
            best_sharpe=1.5,
            trials_data=self._trials([1.0, 1.1, 1.2, 1.3, 1.4]),
            skewness=0.0, kurtosis=3.0, n_obs=4,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None

    def test_none_best_sharpe_returns_none(self) -> None:
        out = compute_dsr(
            best_sharpe=None,
            trials_data=self._trials([1.0, 1.1, 1.2, 1.3, 1.4]),
            skewness=0.0, kurtosis=3.0, n_obs=100,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None

    def test_zero_variance_trials_returns_none(self) -> None:
        # All identical Sharpe values → variance == 0 → degenerate
        out = compute_dsr(
            best_sharpe=1.5,
            trials_data=self._trials([1.0, 1.0, 1.0, 1.0, 1.0]),
            skewness=0.0, kurtosis=3.0, n_obs=100,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None

    def test_filters_failed_and_pruned_trials(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0},
            {"state": "PRUNED", "value": 0.5},
            {"state": "COMPLETE", "value": -999.0},  # sentinel filtered
            {"state": "COMPLETE", "value": None},
        ]
        # Only 1 valid trial → should return None
        out = compute_dsr(
            best_sharpe=1.5,
            trials_data=trials,
            skewness=0.0, kurtosis=3.0, n_obs=100,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None

    def test_normal_path_returns_rounded_float(self) -> None:
        out = compute_dsr(
            best_sharpe=2.0,
            trials_data=self._trials([0.5, 1.0, 1.5, 2.0, 2.5, 3.0]),
            skewness=0.0, kurtosis=3.0, n_obs=252,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is not None
        assert isinstance(out, float)
        # Rounded to 4 decimals
        assert round(out, 4) == out

    def test_degenerate_skewness_kurtosis_returns_none(self) -> None:
        # Pick parameters so denom_sq becomes ≤ 0:
        # 1 - skew*sr + ((kurt-1)/4)*sr^2 ≤ 0
        # With sr small (best_sharpe/sqrt(252) ≈ 0.06), need huge skew.
        # Force degenerate by big positive skewness.
        out = compute_dsr(
            best_sharpe=1.0,
            trials_data=self._trials([0.5, 1.0, 1.5, 2.0, 2.5, 3.0]),
            skewness=100.0, kurtosis=-1000.0, n_obs=252,
            norm_ppf=_norm_ppf_stub, norm_cdf=_norm_cdf_stub,
        )
        assert out is None


# ---------------------------------------------------------------------------
# compute_param_sensitivity
# ---------------------------------------------------------------------------

class TestComputeParamSensitivity:
    def _make_trials(self, n: int, value_fn=lambda i: float(i)) -> list[dict]:
        return [
            {
                "state": "COMPLETE",
                "value": value_fn(i),
                "params": {"x": float(i), "y": float(i * 2)},
            }
            for i in range(n)
        ]

    def test_too_few_trials_returns_none(self) -> None:
        out = compute_param_sensitivity(
            self._make_trials(5),
            param_ranges={"x": {}, "y": {}},
            param_importances={"x": 0.6, "y": 0.4},
        )
        assert out is None

    def test_returns_single_param_and_grid_keys(self) -> None:
        out = compute_param_sensitivity(
            self._make_trials(20),
            param_ranges={"x": {}, "y": {}},
            param_importances={"x": 0.6, "y": 0.4},
        )
        assert out is not None
        assert "single_param" in out
        assert "grid" in out

    def test_single_param_per_param_keys(self) -> None:
        out = compute_param_sensitivity(
            self._make_trials(20),
            param_ranges={"x": {}, "y": {}},
            param_importances={"x": 0.6, "y": 0.4},
        )
        assert "x" in out["single_param"]
        assert "y" in out["single_param"]
        for p in ("x", "y"):
            sp = out["single_param"][p]
            assert "bins" in sp and "values" in sp
            assert len(sp["bins"]) == len(sp["values"])

    def test_grid_uses_top_pairs_by_importance(self) -> None:
        trials = []
        for i in range(20):
            trials.append({
                "state": "COMPLETE",
                "value": float(i),
                "params": {"x": float(i), "y": float(i * 2), "z": float(-i)},
            })
        out = compute_param_sensitivity(
            trials,
            param_ranges={"x": {}, "y": {}, "z": {}},
            param_importances={"x": 0.5, "y": 0.4, "z": 0.1},
            max_pairs=1,
        )
        # Only top pair (x, y) should appear
        assert list(out["grid"].keys()) == ["x__y"]

    def test_max_pairs_limit_respected(self) -> None:
        trials = []
        for i in range(20):
            trials.append({
                "state": "COMPLETE",
                "value": float(i % 5),
                "params": {f"p{j}": float((i + j) % 7) for j in range(4)},
            })
        out = compute_param_sensitivity(
            trials,
            param_ranges={f"p{j}": {} for j in range(4)},
            param_importances={f"p{j}": 1.0 - j * 0.1 for j in range(4)},
            max_pairs=2,
        )
        assert len(out["grid"]) <= 2

    def test_grid_entry_has_required_shape(self) -> None:
        trials = []
        for i in range(30):
            trials.append({
                "state": "COMPLETE",
                "value": float(i),
                "params": {"x": float(i), "y": float(i % 5)},
            })
        out = compute_param_sensitivity(
            trials,
            param_ranges={"x": {}, "y": {}},
            param_importances={"x": 0.6, "y": 0.4},
        )
        entry = out["grid"]["x__y"]
        assert set(entry.keys()) == {"x_bins", "y_bins", "values", "x_label", "y_label"}
        assert entry["x_label"] == "x"
        assert entry["y_label"] == "y"

    def test_filters_failed_trials(self) -> None:
        trials = self._make_trials(15)
        # Pollute with failures and pruned trials
        for i in range(5):
            trials.append({"state": "PRUNED", "value": -1.0, "params": {"x": 0.0, "y": 0.0}})
            trials.append({"state": "COMPLETE", "value": -999.0, "params": {"x": 0.0, "y": 0.0}})
        out = compute_param_sensitivity(
            trials,
            param_ranges={"x": {}, "y": {}},
            param_importances={"x": 0.5, "y": 0.5},
        )
        assert out is not None  # 15 valid trials, well over min


# ---------------------------------------------------------------------------
# compute_param_stability
# ---------------------------------------------------------------------------

class TestComputeParamStability:
    def test_empty_best_params_returns_none(self) -> None:
        assert compute_param_stability([], {}) is None

    def test_too_few_neighbors_returns_none(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"x": 1.0}},
            {"state": "COMPLETE", "value": 2.0, "params": {"x": 100.0}},  # far
        ]
        # Only one trial within ±20% of best={"x": 1.0} → < 3 neighbors
        out = compute_param_stability(trials, {"x": 1.0})
        assert out is None

    def test_normal_stability_returns_std(self) -> None:
        # 3 neighbors with values {1.0, 2.0, 3.0} → std ≈ 1.0
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"x": 1.0}},
            {"state": "COMPLETE", "value": 2.0, "params": {"x": 1.05}},
            {"state": "COMPLETE", "value": 3.0, "params": {"x": 1.1}},
        ]
        out = compute_param_stability(trials, {"x": 1.0}, threshold=0.20)
        assert out is not None
        # std of (1, 2, 3) using sample (n-1) variance = 1.0
        assert abs(out - 1.0) < 0.01

    def test_filters_failed_and_pruned_trials(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"x": 1.0}},
            {"state": "PRUNED", "value": 5.0, "params": {"x": 1.05}},
            {"state": "COMPLETE", "value": -999.0, "params": {"x": 1.05}},
            {"state": "COMPLETE", "value": 2.0, "params": {"x": 1.05}},
            {"state": "COMPLETE", "value": 3.0, "params": {"x": 1.1}},
        ]
        out = compute_param_stability(trials, {"x": 1.0})
        # Should compute std of [1.0, 2.0, 3.0] only
        assert out is not None and abs(out - 1.0) < 0.01

    def test_missing_param_disqualifies_neighbor(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"x": 1.0, "y": 2.0}},
            {"state": "COMPLETE", "value": 2.0, "params": {"x": 1.05}},  # missing y
            {"state": "COMPLETE", "value": 3.0, "params": {"x": 1.05, "y": 2.05}},
        ]
        # Best requires both x and y, so trial #2 is excluded
        out = compute_param_stability(trials, {"x": 1.0, "y": 2.0}, threshold=0.20)
        assert out is None  # only 2 neighbors qualify

    def test_zero_best_value_does_not_zerodiv(self) -> None:
        # max(abs(0), 1e-9) = 1e-9 → tolerance becomes effectively zero,
        # but trials at exactly 0 still pass with delta=0.
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"x": 0.0}},
            {"state": "COMPLETE", "value": 2.0, "params": {"x": 0.0}},
            {"state": "COMPLETE", "value": 3.0, "params": {"x": 0.0}},
        ]
        out = compute_param_stability(trials, {"x": 0.0})
        assert out is not None
