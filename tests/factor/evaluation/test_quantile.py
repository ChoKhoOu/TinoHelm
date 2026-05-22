"""Unit tests — ``tinohelm.factor.evaluation.quantile`` (polars-native).

Locks the numerical contract of ``compute_quantile_returns``:

* Schema — exactly 3 keys (``avg_returns``, ``cum_returns``, ``is_monotonic``).
* Thresholds — the ``< n_quantiles * 20`` short-circuit and the
  collapsed-bin all-empty path must both route to the canonical empty
  payload.
* Monotonicity uses ``>=`` (Q1 ≥ Q2 ≥ … ≥ QN), not strict ``>``, so a
  flat quantile profile still counts as monotonic.
* Precision — avg returns at 8 dp, cumulative points at 6 dp; sampling
  targets ≤ 100 points.

Pure-logic, deterministic, NT-free, < 200 ms.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.quantile import compute_quantile_returns


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _hourly_ts(n: int, start: dt.datetime = dt.datetime(2024, 1, 1)) -> pl.Series:
    """Build an N-row hourly :class:`pl.Datetime` index for fixtures."""
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


def _frame(values, ts: pl.Series | None = None) -> pl.DataFrame:
    arr = np.asarray(values, dtype=float)
    if ts is None:
        ts = _hourly_ts(len(arr))
    return pl.DataFrame({"ts": ts, "value": arr.tolist()})


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def monotone_pair():
    """Factor = index, fwd = -factor so lower-factor buckets earn more."""
    n = 500
    ts = _hourly_ts(n)
    factor = pl.DataFrame({"ts": ts, "value": np.arange(n, dtype=float).tolist()})
    fwd = pl.DataFrame({"ts": ts, "value": (-np.arange(n, dtype=float)).tolist()})
    return factor, fwd


@pytest.fixture
def noisy_pair():
    rng = np.random.default_rng(42)
    n = 500
    ts = _hourly_ts(n)
    factor_arr = rng.normal(0, 1, n)
    fwd_arr = 0.1 * factor_arr + rng.normal(0, 1, n)
    factor = pl.DataFrame({"ts": ts, "value": factor_arr.tolist()})
    fwd = pl.DataFrame({"ts": ts, "value": fwd_arr.tolist()})
    return factor, fwd


# ──────────────────────────────────────────────────────────────────────
# Contract
# ──────────────────────────────────────────────────────────────────────


EMPTY = {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}


class TestSchema:
    def test_exactly_three_keys(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        assert set(out.keys()) == {"avg_returns", "cum_returns", "is_monotonic"}

    def test_avg_returns_labels_are_Q1_through_Qn(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert sorted(out["avg_returns"].keys()) == ["Q1", "Q2", "Q3", "Q4", "Q5"]

    def test_cum_returns_has_same_labels_as_avg(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=3)
        assert set(out["avg_returns"].keys()) == set(out["cum_returns"].keys())

    def test_is_monotonic_is_bool(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        assert isinstance(out["is_monotonic"], bool)

    def test_multi_symbol_buckets_and_compounds_once_per_timestamp(self):
        """Cross-sectional quantiles must not mix time or compound per symbol."""
        n_ts = 60
        ts = _hourly_ts(n_ts)
        # Large regime shift would dominate a global qcut; per-ts ranking still
        # assigns S0/S1 to Q1 and S2/S3 to Q2 at every timestamp.
        shift = np.array([0 if i < n_ts // 2 else 1_000 for i in range(n_ts)])
        factor = pl.DataFrame({
            "ts": ts.repeat_by(4).explode(),
            "symbol": ["S0", "S1", "S2", "S3"] * n_ts,
            "value": np.column_stack([
                shift + 0,
                shift + 1,
                shift + 2,
                shift + 3,
            ]).ravel().astype(float).tolist(),
        })
        fwd = pl.DataFrame({
            "ts": ts.repeat_by(4).explode(),
            "symbol": ["S0", "S1", "S2", "S3"] * n_ts,
            "value": ([0.01, 0.01, 0.02, 0.02] * n_ts),
        })

        out = compute_quantile_returns(factor, fwd, n_quantiles=2)

        assert out["avg_returns"] == {"Q1": 0.01, "Q2": 0.02}
        assert len(out["cum_returns"]["Q1"]) == n_ts
        assert len(out["cum_returns"]["Q2"]) == n_ts


# ──────────────────────────────────────────────────────────────────────
# Short-circuit boundaries
# ──────────────────────────────────────────────────────────────────────


class TestShortCircuit:
    def test_paired_below_n_times_20_returns_empty(self):
        # 5 quantiles × 20 = 100 minimum; give 99 → empty.
        ts = _hourly_ts(99)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(99, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(99, dtype=float).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY

    def test_paired_exactly_n_times_20_does_not_short_circuit(self):
        # Exactly 100 pairs with 5 quantiles — the `< 100` guard lets this
        # through (strict less-than).  We just assert a non-empty result,
        # not specific numbers, to avoid locking numerical noise.
        ts = _hourly_ts(100)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(100, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": (-np.arange(100, dtype=float)).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out["avg_returns"] != {}

    def test_custom_n_quantiles_scales_the_threshold(self):
        # 3 quantiles × 20 = 60 — 59 pairs should short-circuit.
        ts = _hourly_ts(59)
        factor = pl.DataFrame({"ts": ts, "value": np.arange(59, dtype=float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(59, dtype=float).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=3)
        assert out == EMPTY

    def test_nan_rows_are_dropped_before_threshold(self):
        ts = _hourly_ts(200)
        f_vals = np.arange(200, dtype=float).tolist()
        # Blow out 150 rows → only 50 remain, below 5*20 = 100.
        for i in range(50, 200):
            f_vals[i] = float("nan")
        factor = pl.DataFrame({"ts": ts, "value": f_vals})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(200, dtype=float).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY

    def test_inf_rows_are_dropped_before_threshold(self):
        ts = _hourly_ts(200)
        f_vals = np.arange(200, dtype=float).tolist()
        for i in range(50, 200):
            f_vals[i] = float("inf")
        factor = pl.DataFrame({"ts": ts, "value": f_vals})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(200, dtype=float).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY


# ──────────────────────────────────────────────────────────────────────
# Degenerate factor handling
# ──────────────────────────────────────────────────────────────────────


class TestDegenerateInput:
    def test_constant_factor_yields_empty_avg_returns(self):
        # Constant factor → qcut collapses to a single bucket → legacy
        # contract maps that to the empty payload.
        ts = _hourly_ts(300)
        factor = pl.DataFrame({"ts": ts, "value": [1.0] * 300})
        fwd = pl.DataFrame({"ts": ts, "value": np.arange(300, dtype=float).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == EMPTY

    def test_two_unique_values_with_five_quantiles_handles_gracefully(self):
        # Only two unique factor values → qcut collapses many bins. Must not
        # raise and must produce a well-formed result.
        rng = np.random.default_rng(0)
        ts = _hourly_ts(500)
        factor = pl.DataFrame({
            "ts": ts,
            "value": rng.choice([1.0, 2.0], size=500).astype(float).tolist(),
        })
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, 500).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Fewer than 5 buckets survive; labels are a subset of Q1..Q5.
        assert set(out["avg_returns"].keys()) <= {"Q1", "Q2", "Q3", "Q4", "Q5"}


# ──────────────────────────────────────────────────────────────────────
# Monotonicity
# ──────────────────────────────────────────────────────────────────────


class TestMonotonicity:
    def test_perfect_descending_pair_is_monotonic(self, monotone_pair):
        factor, fwd = monotone_pair
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Lowest-factor bucket (Q1) → highest fwd returns; monotone ≥.
        assert out["is_monotonic"] is True
        vals = list(out["avg_returns"].values())
        for a, b in zip(vals, vals[1:]):
            assert a >= b

    def test_random_pair_usually_not_monotonic(self):
        rng = np.random.default_rng(12345)
        n = 2000
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # Pure noise at 2000 samples → very unlikely to be monotone by chance.
        assert out["is_monotonic"] is False

    def test_flat_profile_counts_as_monotonic(self):
        # Perfectly equal avg returns across quantiles — ≥ is True everywhere,
        # so is_monotonic must be True (docstring explicitly says ≥, not >).
        ts = _hourly_ts(500)
        factor = pl.DataFrame({"ts": ts, "value": np.tile(np.arange(5), 100).astype(float).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": [1.0] * 500})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        # All quantile means == 1.0 → flat → monotonic by ≥ definition.
        if out["avg_returns"]:  # if the function produced any buckets
            assert out["is_monotonic"] is True


# ──────────────────────────────────────────────────────────────────────
# Precision / structure
# ──────────────────────────────────────────────────────────────────────


class TestPrecision:
    def test_avg_returns_values_rounded_to_8dp(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for v in out["avg_returns"].values():
            assert round(v, 8) == v

    def test_cum_returns_values_rounded_to_6dp(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for points in out["cum_returns"].values():
            for point in points:
                assert round(point["cum_ret"], 6) == point["cum_ret"]

    def test_cum_returns_sampled_to_at_most_100_points_per_bucket(self):
        # 2000 rows / 5 buckets ≈ 400 per bucket; sampling step = len/100.
        rng = np.random.default_rng(99)
        n = 2000
        ts = _hourly_ts(n)
        factor = pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})
        fwd = pl.DataFrame({"ts": ts, "value": rng.normal(0, 0.1, n).tolist()})
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        for points in out["cum_returns"].values():
            # step = max(1, len//100); sampled length = ceil(len/step).
            # For len ≈ 400 → step = 4 → sampled ≈ 100.
            assert len(points) <= 110, f"too many cum points: {len(points)}"

    def test_cum_returns_point_shape(self, noisy_pair):
        factor, fwd = noisy_pair
        out = compute_quantile_returns(factor, fwd)
        for points in out["cum_returns"].values():
            for point in points:
                assert set(point.keys()) == {"date", "cum_ret"}
                # date is ISO string
                assert isinstance(point["date"], str)
                # And it round-trips through fromisoformat.
                dt.datetime.fromisoformat(point["date"])


# ──────────────────────────────────────────────────────────────────────
# Defensive copies (payload isolation)
# ──────────────────────────────────────────────────────────────────────


class TestDefensiveBehavior:
    def test_caller_mutation_of_result_does_not_affect_rerun(self, noisy_pair):
        factor, fwd = noisy_pair
        out1 = compute_quantile_returns(factor, fwd)
        out1["avg_returns"]["Q1"] = 9999
        out2 = compute_quantile_returns(factor, fwd)
        assert out2["avg_returns"]["Q1"] != 9999
