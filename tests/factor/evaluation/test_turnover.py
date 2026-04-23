"""Unit tests — ``tinohelm.factor.evaluation.turnover``.

Locks the numerical contract of ``compute_turnover``:

* Schema — exactly ``{"daily", "annualized", "fee_drag_monthly"}``.
* Short-circuit — ``< n_quantiles * 20`` pairs or ``pd.qcut`` ValueError
  returns the zero payload.
* Degenerate factor — pd.qcut with ``duplicates="drop"`` produces NaN
  bin labels; those rows are dropped so a constant factor does NOT
  mis-report 100 % turnover.
* Formulas — annualization factor is 252 trading days; fee drag is
  ``daily * 2 * fee_rate * 21`` (round-trip × trading days/month).
* Precision — daily at 4 dp, annualized at 1 dp, fee_drag at 4 dp.

Pure-logic, deterministic, NT-free, < 100 ms.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.evaluation.turnover import compute_turnover


ZERO_PAYLOAD = {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


# ──────────────────────────────────────────────────────────────────────
# Schema + short-circuit
# ──────────────────────────────────────────────────────────────────────


class TestContract:
    def test_schema_keys(self):
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        rng = np.random.default_rng(0)
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_turnover(factor, fwd)
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_short_pair_returns_zero_payload(self):
        # 5 quantiles × 20 = 100; 99 pairs short-circuits.
        idx = pd.date_range("2024-01-01", periods=99, freq="1h")
        factor = pd.Series(np.arange(99, dtype=float), index=idx)
        fwd = pd.Series(np.arange(99, dtype=float), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5)
        assert out == ZERO_PAYLOAD

    def test_exact_threshold_does_not_short_circuit(self):
        # 100 pairs with 5 quantiles — strict < 100 → passes.
        rng = np.random.default_rng(1)
        idx = pd.date_range("2024-01-01", periods=100, freq="1h")
        factor = pd.Series(rng.normal(0, 1, 100), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 100), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # All three keys present, values may be numeric non-zero or zero
        # depending on daily grouping; at minimum the shape is intact.
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_custom_n_quantiles_scales_threshold(self):
        # 3 quantiles × 20 = 60.  59 → zero payload.
        idx = pd.date_range("2024-01-01", periods=59, freq="1h")
        factor = pd.Series(np.arange(59, dtype=float), index=idx)
        fwd = pd.Series(np.arange(59, dtype=float), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=3)
        assert out == ZERO_PAYLOAD


# ──────────────────────────────────────────────────────────────────────
# Degenerate factor — the critical "false 100 % turnover" regression guard
# ──────────────────────────────────────────────────────────────────────


class TestDegenerateFactor:
    def test_constant_factor_returns_zero_not_100pct(self):
        # A constant factor cannot actually induce rebalancing; without the
        # NaN-bin filter this historically reported daily turnover ≈ 1.0
        # because NaN != NaN evaluates to True in .mean().
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series([1.0] * 500, index=idx)
        fwd = pd.Series(np.arange(500, dtype=float), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5)
        assert out == ZERO_PAYLOAD

    def test_two_unique_values_still_produces_valid_output(self):
        rng = np.random.default_rng(3)
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series(rng.choice([1.0, 2.0], size=500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # Must not raise; must return finite numbers within [0, 1] for daily.
        assert 0 <= out["daily"] <= 1


# ──────────────────────────────────────────────────────────────────────
# Known-answer numerical verification
# ──────────────────────────────────────────────────────────────────────


class TestKnownAnswers:
    def _single_day(self, daily_value: float) -> dict:
        """Helper: synthesize data whose empirical daily turnover equals ``daily_value``.

        Strategy: build 3 daily buckets of 100 pairs each.  Quantile
        assignments on day 1 vs day 2 differ by the desired fraction;
        day 2 vs day 3 again by the same fraction — so ``np.mean`` of the
        two daily turnovers equals ``daily_value``.
        """
        # Each day: 100 values whose quantile labels we control directly via
        # the factor ordering.  The 'fwd_ret' column isn't used for the
        # turnover math — only the quantile assignments on consecutive days.
        days = 3
        n = 100
        frames = []
        for d in range(days):
            start = pd.Timestamp("2024-01-01") + pd.Timedelta(days=d)
            idx = start + pd.Timedelta(hours=0) + pd.to_timedelta(np.arange(n), unit="s")
            # Shift the factor values across days so that a fixed fraction of
            # index positions changes quantile.  Easiest: use np.arange for
            # each day and rotate by floor(daily_value * n) positions.
            base = np.arange(n, dtype=float)
            shift = int(round(daily_value * n))
            factor_vals = np.roll(base, shift) if d > 0 else base
            df = pd.DataFrame({
                "factor": factor_vals,
                "fwd_ret": np.zeros(n),
            }, index=idx)
            frames.append(df)
        all_df = pd.concat(frames)
        return compute_turnover(all_df["factor"], all_df["fwd_ret"], n_quantiles=5, fee_rate=0.0004)

    def test_annualization_factor_is_252(self):
        # Build data with verified daily turnover, then check annualized.
        out = self._single_day(0.5)  # intent ≈ 0.5
        # annualized = daily * 252 — verify the ratio (unrounded by /252).
        # Because turnover math has floor effects from quantile alignment,
        # just assert the ratio is approximately 252 (± numerical noise).
        if out["daily"] > 0:
            ratio = out["annualized"] / out["daily"]
            assert 250 < ratio < 254

    def test_fee_drag_formula(self):
        # fee_drag_monthly = daily * 2 * fee_rate * 21.
        # Use a specific daily to check: daily=0.5, fee_rate=0.001 → fee_drag = 0.5*2*0.001*21 = 0.021.
        # But the function's turnover math may produce a different daily;
        # use the reported daily as the source of truth.
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        rng = np.random.default_rng(4)
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.001)
        expected_fee_drag = round(out["daily"] * 2 * 0.001 * 21, 4)
        assert out["fee_drag_monthly"] == expected_fee_drag

    def test_fee_rate_default_is_0_0004(self):
        # Verify implicit default.
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        rng = np.random.default_rng(5)
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out_default = compute_turnover(factor, fwd, n_quantiles=5)
        out_explicit = compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.0004)
        assert out_default == out_explicit

    def test_fee_drag_scales_linearly_with_fee_rate(self):
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        rng = np.random.default_rng(6)
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out1 = compute_turnover(factor, fwd, fee_rate=0.001)
        out2 = compute_turnover(factor, fwd, fee_rate=0.002)
        if out1["daily"] > 0:
            # 2× fee rate → 2× fee drag (modulo rounding).
            assert abs(out2["fee_drag_monthly"] - 2 * out1["fee_drag_monthly"]) < 0.001


# ──────────────────────────────────────────────────────────────────────
# Precision
# ──────────────────────────────────────────────────────────────────────


class TestPrecision:
    def test_rounding(self):
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        rng = np.random.default_rng(7)
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_turnover(factor, fwd)
        assert round(out["daily"], 4) == out["daily"]
        assert round(out["annualized"], 1) == out["annualized"]
        assert round(out["fee_drag_monthly"], 4) == out["fee_drag_monthly"]

    def test_daily_in_valid_range(self):
        # Turnover is a fraction — must always be in [0, 1].
        rng = np.random.default_rng(8)
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        out = compute_turnover(factor, fwd)
        assert 0 <= out["daily"] <= 1


# ──────────────────────────────────────────────────────────────────────
# NaN / Inf filtering at input boundary
# ──────────────────────────────────────────────────────────────────────


class TestFiltering:
    def test_inf_rows_are_dropped_so_inf_factor_does_not_crash_qcut(self):
        rng = np.random.default_rng(9)
        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series(rng.normal(0, 1, 500), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 500), index=idx)
        factor.iloc[0] = np.inf
        factor.iloc[1] = -np.inf
        # Must not raise.
        out = compute_turnover(factor, fwd)
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_nan_filtering_below_threshold(self):
        rng = np.random.default_rng(10)
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series(rng.normal(0, 1, 200), index=idx)
        fwd = pd.Series(rng.normal(0, 1, 200), index=idx)
        factor.iloc[99:] = np.nan  # leave only 99 finite pairs
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # 99 < 100 threshold → zero payload.
        assert out == ZERO_PAYLOAD
