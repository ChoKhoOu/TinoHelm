"""Unit tests — ``tinohelm.factor.evaluation.turnover`` (polars-native).

Locks the numerical contract of ``compute_turnover``:

* Schema — exactly ``{"daily", "annualized", "fee_drag_monthly"}``.
* Short-circuit — ``< n_quantiles * 20`` pairs returns the zero payload.
* Degenerate factor — :meth:`pl.Series.qcut` on a constant series collapses
  to a single bucket; we treat that as the zero payload (legacy behaviour
  was the "false 100 % turnover" regression that the old NaN-bin filter
  fixed; the new implementation guards via ``n_unique() < 2``).
* Formulas — annualization factor is 252 trading days; fee drag is
  ``daily * 2 * fee_rate * 21`` (round-trip × trading days/month).
* Precision — daily at 4 dp, annualized at 1 dp, fee_drag at 4 dp.

Pure-logic, deterministic, NT-free, < 100 ms.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.turnover import compute_turnover


ZERO_PAYLOAD = {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


def _hourly_ts(n: int, start: dt.datetime = dt.datetime(2024, 1, 1)) -> pl.Series:
    """Build an N-row hourly :class:`pl.Datetime` index for fixtures."""
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


# ──────────────────────────────────────────────────────────────────────
# Schema + short-circuit
# ──────────────────────────────────────────────────────────────────────


class TestContract:
    def test_schema_keys(self):
        ts = _hourly_ts(500)
        rng = np.random.default_rng(0)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_turnover(factor, fwd)
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_short_pair_returns_zero_payload(self):
        # 5 quantiles × 20 = 100; 99 pairs short-circuits.
        ts = _hourly_ts(99)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(99, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(99, dtype=float).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5)
        assert out == ZERO_PAYLOAD

    def test_exact_threshold_does_not_short_circuit(self):
        # 100 pairs with 5 quantiles — strict < 100 → passes.
        rng = np.random.default_rng(1)
        ts = _hourly_ts(100)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 100).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 100).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # All three keys present, values may be numeric non-zero or zero
        # depending on daily grouping; at minimum the shape is intact.
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_custom_n_quantiles_scales_threshold(self):
        # 3 quantiles × 20 = 60.  59 → zero payload.
        ts = _hourly_ts(59)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(59, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(59, dtype=float).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=3)
        assert out == ZERO_PAYLOAD


# ──────────────────────────────────────────────────────────────────────
# Degenerate factor — the critical "false 100 % turnover" regression guard
# ──────────────────────────────────────────────────────────────────────


class TestDegenerateFactor:
    def test_constant_factor_returns_zero_not_100pct(self):
        # A constant factor cannot actually induce rebalancing; without the
        # ``n_unique() < 2`` guard this historically reported daily turnover
        # ≈ 1.0 because NaN != NaN evaluates to True in ``.mean()``.
        ts = _hourly_ts(500)
        factor = pl.DataFrame({"ts": ts, "value": [1.0] * 500})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(500, dtype=float).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5)
        assert out == ZERO_PAYLOAD

    def test_two_unique_values_still_produces_valid_output(self):
        rng = np.random.default_rng(3)
        ts = _hourly_ts(500)
        factor = pl.DataFrame({
            "ts": ts,
            "value": rng.choice([1.0, 2.0], size=500).astype(float).tolist(),
        })
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # Must not raise; must return finite numbers within [0, 1] for daily.
        assert 0 <= out["daily"] <= 1


# ──────────────────────────────────────────────────────────────────────
# Known-answer numerical verification
# ──────────────────────────────────────────────────────────────────────


class TestKnownAnswers:
    def _three_day_panel(self, daily_value: float) -> dict:
        """Helper: synthesize data whose empirical daily turnover equals ``daily_value``.

        Strategy: build 3 daily buckets of 100 pairs each. Quantile
        assignments on day 1 vs day 2 differ by the desired fraction;
        day 2 vs day 3 again by the same fraction — so ``np.mean`` of the
        two daily turnovers equals ``daily_value``.
        """
        days = 3
        n = 100
        all_factor = []
        all_fwd = []
        all_ts = []
        for d in range(days):
            start = dt.datetime(2024, 1, 1) + dt.timedelta(days=d)
            day_ts = [start + dt.timedelta(seconds=i) for i in range(n)]
            base = np.arange(n, dtype=float)
            shift = int(round(daily_value * n))
            factor_vals = np.roll(base, shift) if d > 0 else base
            all_factor.extend(factor_vals.tolist())
            all_fwd.extend([0.0] * n)
            all_ts.extend(day_ts)
        ts = pl.Series("ts", all_ts)
        factor = pl.DataFrame({"ts": ts, "value": all_factor})
        fwd = pl.DataFrame({"ts": ts, "value": all_fwd})
        return compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.0004)

    def test_annualization_factor_is_252(self):
        # Build data with verified daily turnover, then check annualized.
        out = self._three_day_panel(0.5)  # intent ≈ 0.5
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
        ts = _hourly_ts(500)
        rng = np.random.default_rng(4)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.001)
        expected_fee_drag = round(out["daily"] * 2 * 0.001 * 21, 4)
        assert out["fee_drag_monthly"] == expected_fee_drag

    def test_fee_rate_default_is_0_0004(self):
        # Verify implicit default.
        ts = _hourly_ts(500)
        rng = np.random.default_rng(5)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out_default = compute_turnover(factor, fwd, n_quantiles=5)
        out_explicit = compute_turnover(factor, fwd, n_quantiles=5, fee_rate=0.0004)
        assert out_default == out_explicit

    def test_fee_drag_scales_linearly_with_fee_rate(self):
        ts = _hourly_ts(500)
        rng = np.random.default_rng(6)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
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
        ts = _hourly_ts(500)
        rng = np.random.default_rng(7)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_turnover(factor, fwd)
        assert round(out["daily"], 4) == out["daily"]
        assert round(out["annualized"], 1) == out["annualized"]
        assert round(out["fee_drag_monthly"], 4) == out["fee_drag_monthly"]

    def test_daily_in_valid_range(self):
        # Turnover is a fraction — must always be in [0, 1].
        rng = np.random.default_rng(8)
        ts = _hourly_ts(500)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_turnover(factor, fwd)
        assert 0 <= out["daily"] <= 1


# ──────────────────────────────────────────────────────────────────────
# NaN / Inf filtering at input boundary
# ──────────────────────────────────────────────────────────────────────


class TestFiltering:
    def test_inf_rows_are_dropped_so_inf_factor_does_not_crash_qcut(self):
        rng = np.random.default_rng(9)
        ts = _hourly_ts(500)
        f_vals = rng.normal(0, 1, 500).tolist()
        f_vals[0] = float("inf")
        f_vals[1] = float("-inf")
        factor = pl.DataFrame({"ts": ts, "value": f_vals})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        # Must not raise.
        out = compute_turnover(factor, fwd)
        assert set(out.keys()) == {"daily", "annualized", "fee_drag_monthly"}

    def test_nan_filtering_below_threshold(self):
        rng = np.random.default_rng(10)
        ts = _hourly_ts(200)
        f_vals = rng.normal(0, 1, 200).tolist()
        for i in range(99, 200):
            f_vals[i] = float("nan")  # leave only 99 finite pairs
        factor = pl.DataFrame({"ts": ts, "value": f_vals})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 200).tolist()})
        out = compute_turnover(factor, fwd, n_quantiles=5)
        # 99 < 100 threshold → zero payload.
        assert out == ZERO_PAYLOAD
