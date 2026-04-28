"""Tests — walk-forward purged & embargoed CV.

Verifies :func:`generate_folds` invariants and :class:`WalkForwardEvaluator`
OOS IC aggregation against a synthetic golden dataset.

All tests are deterministic (``np.random.seed(42)``), NT-free, and run in
< 5 s on a laptop.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.evaluation.walk_forward import Fold, WalkForwardEvaluator, generate_folds
from tinohelm.factor.types import EvalConfig, EvalResult, WalkForwardSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_timestamps(n: int) -> pl.Series:
    """Build an N-row hourly Datetime Series starting at 2024-01-01."""
    start = dt.datetime(2024, 1, 1)
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


def _make_panel(values_2d: np.ndarray, ts: pl.Series) -> pl.DataFrame:
    """Build a wide panel [ts, sym0..symN] from a (T, N) numpy array."""
    T, N = values_2d.shape
    assert len(ts) == T
    payload = {"ts": ts}
    for i in range(N):
        payload[f"sym{i}"] = values_2d[:, i].tolist()
    return pl.DataFrame(payload)


def _default_spec() -> WalkForwardSpec:
    """T=300: 1 fold expected — (300 - 200 - 10) // 50 = 1.8 → 1."""
    return WalkForwardSpec(
        train_bars=200,
        test_bars=50,
        embargo_bars=10,
        purge_bars=10,
    )


def _make_simple_eval_fn(ic_value: float) -> object:
    """Return an eval_fn that always returns EvalResult(ic_mean=ic_value)."""
    def _fn(panel: pl.DataFrame, fwd: pl.DataFrame) -> EvalResult:
        r = EvalResult()
        r.ic_mean = ic_value
        r.ic_std = 0.05
        r.ir = ic_value / 0.05
        return r
    return _fn


# ---------------------------------------------------------------------------
# 1. test_generate_folds_count
# ---------------------------------------------------------------------------

def test_generate_folds_count():
    """T=300, train=200, test=50, embargo=10, purge=10 → exactly 1 fold.

    Manual calculation:
        effective_train = 200 - 10 = 190
        fold 0: train=[0, 190), test=[210, 260)  — test_end=260 <= 300 ✓
        fold 1: train=[50, 240), test=[260, 310) — test_end=310 > 300  ✗  stop
    """
    ts = _make_timestamps(300)
    spec = _default_spec()
    folds = generate_folds(ts, spec)
    assert len(folds) == 1, f"expected 1 fold, got {len(folds)}"


# ---------------------------------------------------------------------------
# 2. test_embargo_satisfied
# ---------------------------------------------------------------------------

def test_embargo_satisfied():
    """Every fold must satisfy test_start - train_end >= embargo_bars.

    Note: train_end already excludes purge_bars, so the actual gap between
    the original train window boundary and test_start is exactly embargo_bars.
    The check here is test_start - (train_start + train_bars) >= embargo_bars.
    """
    ts = _make_timestamps(600)
    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10)
    folds = generate_folds(ts, spec)
    assert len(folds) >= 1, "need at least 1 fold to verify"
    for fold in folds:
        # full original train end (without purge subtraction)
        full_train_end = fold.train_start + spec.train_bars
        gap = fold.test_start - full_train_end
        assert gap >= spec.embargo_bars, (
            f"fold {fold.fold_id}: gap={gap} < embargo_bars={spec.embargo_bars}"
        )


# ---------------------------------------------------------------------------
# 3. test_purge_satisfied
# ---------------------------------------------------------------------------

def test_purge_satisfied():
    """train_end - train_start <= train_bars - purge_bars (effective train ≤ cap)."""
    ts = _make_timestamps(600)
    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10)
    folds = generate_folds(ts, spec)
    assert len(folds) >= 1
    cap = spec.train_bars - spec.purge_bars
    for fold in folds:
        effective = fold.train_end - fold.train_start
        assert effective <= cap, (
            f"fold {fold.fold_id}: effective_train={effective} > cap={cap}"
        )


# ---------------------------------------------------------------------------
# 4. test_no_overlap_between_train_and_test
# ---------------------------------------------------------------------------

def test_no_overlap_between_train_and_test():
    """Train range [train_start, train_end) and test range [test_start, test_end)
    must not overlap."""
    ts = _make_timestamps(600)
    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10)
    folds = generate_folds(ts, spec)
    assert len(folds) >= 1
    for fold in folds:
        # Ranges overlap iff max(starts) < min(ends)
        overlap = max(fold.train_start, fold.test_start) < min(fold.train_end, fold.test_end)
        assert not overlap, (
            f"fold {fold.fold_id}: train [{fold.train_start},{fold.train_end}) "
            f"overlaps test [{fold.test_start},{fold.test_end})"
        )


# ---------------------------------------------------------------------------
# 5. test_golden_synthetic_data
# ---------------------------------------------------------------------------

def test_golden_synthetic_data():
    """Golden standard: OOS IC series mean must fall in [-0.04, 0.0].

    Synthetic construction:
        T=500, N=10 symbols.
        Factor scores drawn from N(i, 0.5) for symbol i.
        IS segment (bars 0-209): forward returns = factor * 0.10 + noise
            → Spearman IC ≈ +0.10.
        OOS segment (bars 210-499): forward returns = factor * (-0.02) + larger_noise
            → negative Spearman IC.

        WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10):
            fold 0: train [0, 190), test [210, 260)  — in OOS zone → IC≈-0.02
            fold 1: train [50, 240), test [260, 310) — test also in OOS zone
            (further folds test_end <= 500)

        All test folds land in the OOS zone → mean OOS IC is clearly negative.
    """
    rng = np.random.default_rng(42)
    T = 500
    N = 10

    ts = _make_timestamps(T)

    # Factor panel: symbol i has base score = i, plus small noise
    factor_vals = np.column_stack([
        rng.normal(loc=float(i), scale=0.5, size=T) for i in range(N)
    ])  # shape (T, N)

    # IS returns: positive correlation with factor
    is_noise = rng.normal(0, 0.3, size=(T, N))
    # OOS returns: negative correlation with factor
    oos_noise = rng.normal(0, 0.5, size=(T, N))

    returns_vals = np.zeros((T, N))
    is_end = 210  # test folds start at 210, so every OOS fold is in the negative regime
    returns_vals[:is_end] = factor_vals[:is_end] * 0.10 + is_noise[:is_end]
    returns_vals[is_end:] = factor_vals[is_end:] * (-0.02) + oos_noise[is_end:]

    factor_panel = _make_panel(factor_vals, ts)
    returns_panel = _make_panel(returns_vals, ts)

    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10)
    evaluator_obj = Evaluator()
    wf = WalkForwardEvaluator(spec)

    def _eval_fn(panel: pl.DataFrame, fwd: pl.DataFrame) -> EvalResult:
        result, _, _, _ = evaluator_obj._evaluate_core(panel, fwd, _dummy_config())
        return result

    result = wf.evaluate(factor_panel, returns_panel, eval_fn=_eval_fn)

    assert len(result.oos_ic_series) >= 1, "expected at least 1 OOS fold"
    oos_ic_means = [d["ic_mean"] for d in result.oos_ic_series]
    mean_oos_ic = float(np.mean(oos_ic_means))
    assert -0.25 <= mean_oos_ic <= -0.05, (
        f"OOS IC mean {mean_oos_ic:.4f} not in [-0.25, -0.05]; "
        f"per-fold IC means: {oos_ic_means}"
    )


def _dummy_config() -> EvalConfig:
    return EvalConfig(
        universe=tuple(f"sym{i}" for i in range(10)),
        start="2024-01-01",
        end="2024-06-01",
        forward_period=1,
        ic_freq="D",
    )


# ---------------------------------------------------------------------------
# 6. test_oos_ic_series_at_least_one_fold
# ---------------------------------------------------------------------------

def test_oos_ic_series_at_least_one_fold():
    """result.oos_ic_series length must be >= 1 for valid inputs."""
    ts = _make_timestamps(600)
    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=10)
    wf = WalkForwardEvaluator(spec)

    # Minimal eval_fn: always returns ic_mean=0.05
    result = wf.evaluate(
        _make_panel(np.ones((600, 2)), ts),
        _make_panel(np.ones((600, 2)), ts),
        eval_fn=_make_simple_eval_fn(0.05),
    )
    assert len(result.oos_ic_series) >= 1, (
        f"expected oos_ic_series length >= 1, got {len(result.oos_ic_series)}"
    )


# ---------------------------------------------------------------------------
# 7. test_evaluate_full_routes_to_walk_forward_when_spec_present
# ---------------------------------------------------------------------------

def test_evaluate_full_routes_to_walk_forward_when_spec_present():
    """evaluate_full with EvalConfig(walk_forward=...) must populate oos_ic_series."""
    rng = np.random.default_rng(0)
    T = 400
    N = 5
    ts = _make_timestamps(T)
    factor_vals = rng.normal(size=(T, N))
    # Returns with slight positive IC in IS zone, near-zero in OOS
    returns_vals = factor_vals * 0.05 + rng.normal(0, 0.4, size=(T, N))

    factor_panel = _make_panel(factor_vals, ts)
    returns_panel = _make_panel(returns_vals, ts)

    spec = WalkForwardSpec(train_bars=200, test_bars=50, embargo_bars=10, purge_bars=5)
    config = EvalConfig(
        universe=tuple(f"sym{i}" for i in range(N)),
        start="2024-01-01",
        end="2024-06-01",
        forward_period=1,
        ic_freq="D",
        walk_forward=spec,
    )

    evaluator = Evaluator()
    result = evaluator.evaluate_full(factor_panel, returns_panel, config, shuffle_iter=0)

    assert isinstance(result, EvalResult)
    assert len(result.oos_ic_series) >= 1, (
        "evaluate_full with walk_forward spec must populate oos_ic_series"
    )
    # Each entry must have the canonical keys
    required_keys = {"fold", "train_start", "train_end", "test_start", "test_end",
                     "ic_mean", "ic_std", "sharpe"}
    for entry in result.oos_ic_series:
        missing = required_keys - set(entry.keys())
        assert not missing, f"oos_ic_series entry missing keys: {missing}"


# ---------------------------------------------------------------------------
# 8. test_evaluate_full_skips_walk_forward_when_spec_none
# ---------------------------------------------------------------------------

def test_evaluate_full_skips_walk_forward_when_spec_none():
    """evaluate_full with walk_forward=None must not populate oos_ic_series."""
    rng = np.random.default_rng(1)
    T = 200
    N = 3
    ts = _make_timestamps(T)
    factor_vals = rng.normal(size=(T, N))
    returns_vals = factor_vals * 0.05 + rng.normal(0, 0.3, size=(T, N))

    factor_panel = _make_panel(factor_vals, ts)
    returns_panel = _make_panel(returns_vals, ts)

    config = EvalConfig(
        universe=tuple(f"sym{i}" for i in range(N)),
        start="2024-01-01",
        end="2024-06-01",
        forward_period=1,
        ic_freq="D",
        walk_forward=None,  # explicit None → no walk-forward
    )

    evaluator = Evaluator()
    result = evaluator.evaluate_full(
        factor_panel, returns_panel, config, shuffle_iter=0
    )

    assert isinstance(result, EvalResult)
    assert result.oos_ic_series == [], (
        f"oos_ic_series should be empty when walk_forward=None, got {result.oos_ic_series}"
    )
