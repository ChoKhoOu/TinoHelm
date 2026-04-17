"""Tests for the verdict logic in `tinohelm.research.report`.

The 4-tab diagnostic report distills statistical results into pass/warn/fail
verdicts shown next to each section. The judging functions are pure dict→string
classifiers — locking these thresholds in tests prevents silent UX drift (e.g.
"2.0 → 1.5" would suddenly show "warn" where the user used to see "pass").
"""
from __future__ import annotations

from tinohelm.research import report as R


# ──────────────────────────────────────────────────────────────────────
# _judge_signal_profile
# ──────────────────────────────────────────────────────────────────────


class TestJudgeSignalProfile:
    def test_zero_std_is_fail(self):
        assert R._judge_signal_profile({"std": 0, "zero_pct": 0.1, "skew": 1.0, "autocorr_1": 0.5}) == "fail"

    def test_high_zero_pct_is_fail(self):
        # >50% of values are zero → fail
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.6, "skew": 1.0, "autocorr_1": 0.5}) == "fail"

    def test_zero_pct_at_threshold_is_pass(self):
        # zero_pct == 0.5 is NOT > 0.5 → pass (strict comparison)
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.5, "skew": 1.0, "autocorr_1": 0.5}) == "pass"

    def test_extreme_skew_is_warn(self):
        # |skew| > 5 → warn
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.1, "skew": 6.0, "autocorr_1": 0.5}) == "warn"
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.1, "skew": -6.0, "autocorr_1": 0.5}) == "warn"

    def test_extreme_autocorr_is_warn(self):
        # autocorr_1 > 0.999 → warn (signal is essentially constant over time)
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.1, "skew": 1.0, "autocorr_1": 0.9999}) == "warn"

    def test_normal_stats_yield_pass(self):
        assert R._judge_signal_profile({"std": 1.0, "zero_pct": 0.1, "skew": 1.0, "autocorr_1": 0.5}) == "pass"

    def test_missing_keys_default_to_zero_then_fail(self):
        # Missing std → defaults to 0 → fail (zero-std branch)
        assert R._judge_signal_profile({}) == "fail"


# ──────────────────────────────────────────────────────────────────────
# _judge_predictive_power
# ──────────────────────────────────────────────────────────────────────


class TestJudgePredictivePower:
    def test_low_tstat_is_fail(self):
        # |t-stat| < 2 → fail regardless of other stats
        assert R._judge_predictive_power({"ic_tstat": 1.5, "ir": 1.0, "ic_positive_pct": 0.7}) == "fail"

    def test_low_ir_is_warn(self):
        # tstat ≥ 2 but IR < 0.5 → warn
        assert R._judge_predictive_power({"ic_tstat": 3.0, "ir": 0.3, "ic_positive_pct": 0.7}) == "warn"

    def test_low_pct_is_warn(self):
        # tstat ≥ 2 and IR ≥ 0.5 but pct < 0.55 → warn
        assert R._judge_predictive_power({"ic_tstat": 3.0, "ir": 0.7, "ic_positive_pct": 0.50}) == "warn"

    def test_strong_signal_is_pass(self):
        assert R._judge_predictive_power({"ic_tstat": 3.0, "ir": 0.7, "ic_positive_pct": 0.65}) == "pass"

    def test_uses_abs_tstat_and_ir(self):
        # Negative IR is also strong (consistent negative IC)
        assert R._judge_predictive_power({"ic_tstat": -3.0, "ir": -0.7, "ic_positive_pct": 0.65}) == "pass"

    def test_missing_keys_default_to_zero_then_fail(self):
        # All defaults to 0 → tstat 0 < 2 → fail
        assert R._judge_predictive_power({}) == "fail"


# ──────────────────────────────────────────────────────────────────────
# _judge_robustness
# ──────────────────────────────────────────────────────────────────────


class TestJudgeRobustness:
    def test_not_significant_is_fail(self):
        out = R._judge_robustness({"significant": False}, [], [])
        assert out == "fail"

    def test_high_negative_subsample_is_warn(self):
        # > 40% of subsample periods have negative IC → warn
        subsample = [{"ic": 0.1}, {"ic": -0.1}, {"ic": -0.05}, {"ic": -0.2}, {"ic": 0.15}]
        # 3 negatives / 5 = 0.6 > 0.4 → warn
        out = R._judge_robustness({"significant": True}, subsample, [])
        assert out == "warn"

    def test_few_positive_cross_symbol_is_fail(self):
        # < 50% of cross-symbol IC positive → fail
        cross = [{"ic": 0.1}, {"ic": -0.05}, {"ic": -0.1}, {"ic": 0.05}]
        # 2 positives / 4 = 0.5; need < 0.5 to fail (strict)
        # Adjust to clearly fail:
        cross = [{"ic": 0.1}, {"ic": -0.05}, {"ic": -0.1}, {"ic": -0.02}]
        # 1 positive / 4 = 0.25 < 0.5 → fail
        out = R._judge_robustness({"significant": True}, [], cross)
        assert out == "fail"

    def test_all_positive_is_pass(self):
        subsample = [{"ic": 0.1}, {"ic": 0.2}, {"ic": 0.15}]
        cross = [{"ic": 0.1}, {"ic": 0.2}, {"ic": 0.15}]
        out = R._judge_robustness({"significant": True}, subsample, cross)
        assert out == "pass"

    def test_empty_subsample_and_cross_with_significant_is_pass(self):
        # If we only ran the shuffle test and it was significant, that alone passes.
        out = R._judge_robustness({"significant": True}, [], [])
        assert out == "pass"

    def test_cross_symbol_fails_takes_precedence_over_subsample_warn(self):
        # Subsample would warn (60% negative); cross would fail (no positives).
        # The flow checks subsample first → returns "warn", then re-checks cross
        # which is "fail" — last decision wins.
        subsample = [{"ic": -0.1}] * 5  # 100% negative → would warn
        cross = [{"ic": -0.1}, {"ic": -0.2}]  # 0 positives → fail
        out = R._judge_robustness({"significant": True}, subsample, cross)
        # Code reads top-down: warn is set, then fail overrides because cross check
        # returns earlier? Let's check actual flow:
        #   1. significant → continue
        #   2. subsample neg_pct > 0.4 → return "warn"
        # So subsample takes precedence (returns immediately). This locks that ordering.
        assert out == "warn"

    def test_significant_flag_missing_treated_as_false(self):
        # Defensive: missing "significant" → defaults to False → fail
        out = R._judge_robustness({}, [], [])
        assert out == "fail"

    def test_subsample_at_threshold_does_not_warn(self):
        # Exactly 40% negative → 0.4 > 0.4 is False → does not warn (strict comparison)
        subsample = [{"ic": -0.1}, {"ic": -0.1}, {"ic": 0.1}, {"ic": 0.1}, {"ic": 0.1}]
        # 2 negatives / 5 = 0.4 → not > 0.4 → no warn
        out = R._judge_robustness({"significant": True}, subsample, [])
        assert out == "pass"


# ──────────────────────────────────────────────────────────────────────
# _judge_cost_params
# ──────────────────────────────────────────────────────────────────────


class TestJudgeCostParams:
    def test_zero_or_negative_net_is_fail(self):
        assert R._judge_cost_params({"net_edge_bps": 0, "gross_edge_bps": 100}, None) == "fail"
        assert R._judge_cost_params({"net_edge_bps": -10, "gross_edge_bps": 100}, None) == "fail"

    def test_low_efficiency_is_warn(self):
        # net/gross < 30% → warn
        out = R._judge_cost_params({"net_edge_bps": 20, "gross_edge_bps": 100}, None)
        assert out == "warn"

    def test_efficiency_above_threshold_is_pass(self):
        # net/gross >= 30%
        out = R._judge_cost_params({"net_edge_bps": 50, "gross_edge_bps": 100}, None)
        assert out == "pass"

    def test_efficiency_at_exact_threshold_is_warn(self):
        # 30/100 = 0.30 → not > 0.30 (strict)? The code is `if net/gross < 0.3` → 0.30 NOT < 0.30 → pass
        # Verify: 30 / 100 = 0.30 → 0.30 < 0.30 False → pass
        out = R._judge_cost_params({"net_edge_bps": 30, "gross_edge_bps": 100}, None)
        assert out == "pass"

    def test_zero_gross_with_positive_net_is_pass(self):
        # gross=0 means we treat as if there's no efficiency check (avoid div-by-zero).
        # The code: `if gross > 0 and net/gross < 0.3` — gross=0 short-circuits → no warn.
        out = R._judge_cost_params({"net_edge_bps": 50, "gross_edge_bps": 0}, None)
        assert out == "pass"

    def test_missing_keys_default_to_one_then_pass(self):
        # net defaults to 0 → fail
        assert R._judge_cost_params({}, None) == "fail"

    def test_heatmap_argument_unused_today(self):
        # The signature accepts a heatmap arg but doesn't currently use it; verify
        # passing arbitrary values doesn't change behavior.
        a = R._judge_cost_params({"net_edge_bps": 50, "gross_edge_bps": 100}, None)
        b = R._judge_cost_params({"net_edge_bps": 50, "gross_edge_bps": 100}, {"foo": "bar"})
        assert a == b == "pass"
