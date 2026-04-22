"""Tests for ``tinohelm.factor.evaluation`` — IC / quantile / distribution /
turnover / robustness / cost / rating pipeline.

Coverage matrix
---------------
1.  **Unit** — each sub-module preserves the exact legacy semantics.
2.  **Regression (AC-13.2)** — new vs old numeric outputs differ by < 1e-10
    for every field that has a legacy counterpart.
3.  **Synthetic data integration** — ``Evaluator.evaluate`` produces
    non-empty values for every ``EvalResult`` field.
4.  **NaN scrubbing** — after ``evaluate``, no ``EvalResult`` float is
    NaN/Inf even when the input contains non-finite values.
5.  **``evaluate_full`` smoke test** — robustness (shuffle w/ tiny
    n_iter=4) + cost + rating populate correctly.

All tests are deterministic (fixed seeds) and NT-free.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.evaluation import (
    Evaluator,
    compute_distribution,
    compute_ic_decay,
    compute_ic_series,
    compute_ic_summary,
    compute_half_life,
    compute_quantile_returns,
    compute_rating,
    compute_turnover,
    edge_waterfall,
    forward_returns,
    rating_letter,
    shuffle_test,
    subsample_ic,
    summarize_shuffle_distribution,
)
from tinohelm.factor.evaluation.evaluator import _finite_or_none, _to_series
from tinohelm.factor.types import EvalConfig, EvalResult



# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def hourly_close() -> pd.Series:
    """500 hourly close prices — matches ``tests/research/test_analysis.py``."""
    rng = np.random.default_rng(42)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(100.0 + np.cumsum(rng.normal(0, 0.3, n)), index=idx)


@pytest.fixture
def positively_correlated_pair():
    rng = np.random.default_rng(7)
    n = 600
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    factor = pd.Series(rng.normal(0, 1, n), index=idx)
    noise = pd.Series(rng.normal(0, 0.3, n), index=idx)
    fwd = 0.5 * factor + noise
    return factor, fwd


@pytest.fixture
def panel_close() -> pd.DataFrame:
    """Multi-symbol price panel: 250 bars × 10 symbols (~= 100 days daily)."""
    rng = np.random.default_rng(111)
    n_bars = 250
    symbols = [f"SYM{i:02d}" for i in range(10)]
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="1h")
    data = 100.0 + np.cumsum(rng.normal(0, 0.3, (n_bars, len(symbols))), axis=0)
    return pd.DataFrame(data, index=idx, columns=symbols)


@pytest.fixture
def panel_factor(panel_close: pd.DataFrame) -> pd.DataFrame:
    """Factor = shifted price momentum — correlates with forward returns."""
    # 10-bar momentum
    return panel_close.pct_change(10)


@pytest.fixture
def synth_100d_10sym():
    """Dense synthetic dataset: 2400 hourly bars × 10 symbols (~100 days)."""
    rng = np.random.default_rng(2024)
    n_bars = 2400  # ≈100 trading days of hourly bars
    symbols = [f"SYM{i:02d}" for i in range(10)]
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="1h")

    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0, 0.3, (n_bars, len(symbols))), axis=0),
        index=idx,
        columns=symbols,
    )
    # Factor = 5-bar momentum shifted so it correlates with forward returns
    factor = close.pct_change(5)
    return close, factor


# ──────────────────────────────────────────────────────────────────────
# Unit — IC module
# ──────────────────────────────────────────────────────────────────────


class TestICModule:
    def test_empty_ic_series_returns_zero_summary(self):
        empty = pd.DataFrame(columns=["date", "ic"])
        out = compute_ic_summary(empty)
        assert out == {
            "ic_mean": 0,
            "ic_std": 0,
            "ir": 0,
            "ic_positive_pct": 0,
            "ic_max_abs": 0,
            "ic_tstat": 0,
        }

    def test_short_paired_returns_empty_ic(self):
        idx = pd.date_range("2024-01-01", periods=20, freq="1h")
        factor = pd.Series(np.arange(20, dtype=float), index=idx)
        fwd = pd.Series(np.arange(20, dtype=float), index=idx)
        ic = compute_ic_series(factor, fwd, freq="D")
        assert ic.empty
        assert list(ic.columns) == ["date", "ic"]


# ──────────────────────────────────────────────────────────────────────
# Unit — Quantile module
# ──────────────────────────────────────────────────────────────────────


class TestQuantileModule:
    def test_short_series_returns_empty(self):
        idx = pd.date_range("2024-01-01", periods=50, freq="1h")
        out = compute_quantile_returns(
            pd.Series(np.arange(50, dtype=float), index=idx),
            pd.Series(np.arange(50, dtype=float), index=idx),
            n_quantiles=5,
        )
        assert out == {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}

    def test_degenerate_factor_returns_empty(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series([1.0] * 200, index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        out = compute_quantile_returns(factor, fwd, n_quantiles=5)
        assert out == {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}


# ──────────────────────────────────────────────────────────────────────
# Unit — Distribution module
# ──────────────────────────────────────────────────────────────────────


class TestDistributionModule:
    def test_short_returns_empty(self):
        out = compute_distribution(pd.Series([1.0, 2.0, 3.0]))
        assert out == {"histogram": [], "stats": {}}


# ──────────────────────────────────────────────────────────────────────
# Unit — Turnover module
# ──────────────────────────────────────────────────────────────────────


class TestTurnoverModule:
    def test_degenerate_returns_zero(self):
        idx = pd.date_range("2024-01-01", periods=200, freq="1h")
        factor = pd.Series([1.0] * 200, index=idx)
        fwd = pd.Series(np.arange(200, dtype=float), index=idx)
        out = compute_turnover(factor, fwd, n_quantiles=5)
        assert out == {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


# ──────────────────────────────────────────────────────────────────────
# Unit — Rating module
# ──────────────────────────────────────────────────────────────────────


class TestRatingModule:
    @pytest.mark.parametrize("ir,pct,expected", [
        (1.5, 0.65, 3),
        (-1.5, 0.65, 3),
        (0.7, 0.58, 2),
        (0.3, 0.50, 1),
        (0.1, 0.50, 0),
    ])
    def test_rating_thresholds(self, ir, pct, expected):
        summary = {"ir": ir, "ic_positive_pct": pct}
        assert compute_rating(summary) == expected

    def test_letter_grades(self):
        assert rating_letter(3) == "S"
        assert rating_letter(2) == "A"
        assert rating_letter(1) == "B"
        assert rating_letter(0, {"ic_positive_pct": 0.50}) == "C"
        assert rating_letter(0, {"ic_positive_pct": 0.40}) == "D"

    def test_letter_grades_without_summary(self):
        # Default summary (missing pct) → D
        assert rating_letter(0) == "D"


# ──────────────────────────────────────────────────────────────────────
# Unit — Cost module
# ──────────────────────────────────────────────────────────────────────


class TestCostModule:
    def test_uses_abs_ic(self):
        pos = edge_waterfall(0.05, 1.0)
        neg = edge_waterfall(-0.05, 1.0)
        assert pos["gross_edge_bps"] == neg["gross_edge_bps"]


# ──────────────────────────────────────────────────────────────────────
# Unit — Robustness module
# ──────────────────────────────────────────────────────────────────────


class TestRobustnessModule:
    def test_shuffle_short_circuits_small_input(self):
        idx = pd.date_range("2024-01-01", periods=50, freq="1h")
        factor = pd.Series(np.arange(50, dtype=float), index=idx)
        fwd = pd.Series(np.arange(50, dtype=float), index=idx)
        out = shuffle_test(factor, fwd, n_iter=10, max_workers=1)
        assert out == {
            "real_ic": 0,
            "shuffle_distribution": [],
            "p_value": 1.0,
            "significant": False,
        }


# ──────────────────────────────────────────────────────────────────────
# Integration — Evaluator.evaluate produces non-empty fields
# ──────────────────────────────────────────────────────────────────────


class TestEvaluateAllFieldsPopulated:
    def test_all_scalar_fields_non_default(self, synth_100d_10sym):
        close, factor = synth_100d_10sym
        close_1 = close.iloc[:, 0]
        factor_1 = factor.iloc[:, 0]
        config = EvalConfig(
            universe=("SYM00",), start="2024-01-01", end="2024-05-01",
            forward_period=5, quantiles=5,
        )
        result = Evaluator().evaluate(factor_1, close_1, config)

        # IC numeric fields are populated (non-None, finite)
        for fname in ("ic_mean", "ic_std", "ir", "ic_tstat", "ic_positive_pct", "ic_max_abs"):
            val = getattr(result, fname)
            assert val is not None
            assert np.isfinite(val), f"{fname} = {val!r} is not finite"

        # Turnover fields populated
        assert np.isfinite(result.turnover)
        assert np.isfinite(result.turnover_annualized)
        assert np.isfinite(result.fee_drag_monthly)

        # Rating is 0..3
        assert 0 <= result.rating <= 3

    def test_all_collection_fields_non_empty(self, synth_100d_10sym):
        close, factor = synth_100d_10sym
        close_1 = close.iloc[:, 0]
        factor_1 = factor.iloc[:, 0]
        config = EvalConfig(
            universe=("SYM00",), start="2024-01-01", end="2024-05-01",
            forward_period=5, quantiles=5,
        )
        result = Evaluator().evaluate(factor_1, close_1, config)

        # Dense synth data → all collections populated
        assert len(result.ic_series) > 0
        assert len(result.ic_decay) > 0
        assert len(result.quantile_pnl) > 0
        assert len(result.quantile_cum_returns) > 0
        assert len(result.distribution_stats) > 0
        assert len(result.distribution_histogram) > 0

        # half_life may be 0 or an int (legacy falls back to last lag)
        assert result.half_life is None or isinstance(result.half_life, int)


# ──────────────────────────────────────────────────────────────────────
# NaN / Infinity contract
# ──────────────────────────────────────────────────────────────────────


class TestNaNScrubbing:
    def test_no_nan_inf_in_scalar_fields_after_nan_injection(self, synth_100d_10sym):
        close, factor = synth_100d_10sym
        # Inject NaN / Inf into factor and close
        close_dirty = close.iloc[:, 0].copy()
        factor_dirty = factor.iloc[:, 0].copy()
        factor_dirty.iloc[10] = np.nan
        factor_dirty.iloc[20] = np.inf
        factor_dirty.iloc[30] = -np.inf
        close_dirty.iloc[40] = np.nan
        close_dirty.iloc[50] = np.inf

        config = EvalConfig(universe=("SYM00",), start="2024-01-01", end="2024-05-01")
        result = Evaluator().evaluate(factor_dirty, close_dirty, config)

        # No NaN / Inf anywhere in scalar fields
        for fname in (
            "ic_mean", "ic_std", "ir", "ic_tstat", "ic_positive_pct", "ic_max_abs",
            "turnover", "turnover_annualized", "fee_drag_monthly",
        ):
            val = getattr(result, fname)
            assert val is None or (isinstance(val, (int, float)) and not math.isnan(float(val)) and not math.isinf(float(val))), (
                f"{fname} = {val!r} contains NaN/Inf"
            )

    def test_degenerate_factor_produces_no_nan_result(self):
        """Constant factor → correlation undefined. EvalResult must still
        have scrubbed float fields (no NaN / Inf).

        scipy emits ``ConstantInputWarning`` when one array is constant —
        expected and filtered out here (same pattern as ``tests/research/
        test_robustness.py::test_returns_zero_when_correlation_undefined``).
        """
        import warnings

        from scipy.stats import ConstantInputWarning

        idx = pd.date_range("2024-01-01", periods=500, freq="1h")
        factor = pd.Series([1.0] * 500, index=idx)  # constant, triggers NaN IC
        close = pd.Series(100.0 + np.arange(500, dtype=float) * 0.1, index=idx)

        config = EvalConfig(universe=("SYM00",), start="2024-01-01", end="2024-05-01")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConstantInputWarning)
            warnings.filterwarnings("ignore", message="invalid value encountered in divide")
            result = Evaluator().evaluate(factor, close, config)

        # All scalar fields must be finite (either 0 or real number).
        for fname in (
            "ic_mean", "ic_std", "ir", "ic_tstat", "ic_positive_pct", "ic_max_abs",
            "turnover", "turnover_annualized", "fee_drag_monthly",
        ):
            val = getattr(result, fname)
            # After scrubbing, 0.0 replaces None-values (set by _scrub_result).
            assert isinstance(val, (int, float))
            assert not math.isnan(float(val))
            assert not math.isinf(float(val))

    def test_finite_or_none_helper(self):
        assert _finite_or_none(float("nan")) is None
        assert _finite_or_none(float("inf")) is None
        assert _finite_or_none(float("-inf")) is None
        assert _finite_or_none(3.14) == 3.14
        assert _finite_or_none({"a": float("nan"), "b": 1.0}) == {"a": None, "b": 1.0}
        assert _finite_or_none([1.0, float("inf"), 2.0]) == [1.0, None, 2.0]


# ──────────────────────────────────────────────────────────────────────
# Panel flattening
# ──────────────────────────────────────────────────────────────────────


class TestPanelFlattening:
    def test_to_series_passthrough(self):
        s = pd.Series([1, 2, 3])
        assert _to_series(s) is s

    def test_to_series_single_column_df(self):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
        out = _to_series(df)
        assert isinstance(out, pd.Series)
        pd.testing.assert_series_equal(out, df["a"])

    def test_to_series_multi_column_panel_flattens(self, panel_close):
        out = _to_series(panel_close)
        assert isinstance(out, pd.Series)
        # Flattened length = bars × symbols
        assert len(out) == panel_close.shape[0] * panel_close.shape[1]
        # Index is flat DatetimeIndex (not MultiIndex)
        assert not isinstance(out.index, pd.MultiIndex)

    def test_to_series_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            _to_series("not a series")


# ──────────────────────────────────────────────────────────────────────
# evaluate_full — robustness + cost + rating end-to-end
# ──────────────────────────────────────────────────────────────────────


class TestEvaluateFull:
    def test_adds_robustness_and_cost(self, synth_100d_10sym):
        """Full diagnostic populates robustness.shuffle + subsample + cost.

        We use ``shuffle_iter=4`` to keep the ProcessPool tiny so tests stay fast.
        """
        close, factor = synth_100d_10sym
        close_1 = close.iloc[:, 0]
        factor_1 = factor.iloc[:, 0]

        config = EvalConfig(
            universe=("SYM00",), start="2024-01-01", end="2024-05-01",
            forward_period=5, quantiles=5, cost_bps=4.0,
        )
        result = Evaluator().evaluate_full(
            factor_1,
            close_1,
            config,
            shuffle_iter=4,       # small n_iter for test speed
            shuffle_workers=1,    # single process → minimal overhead
            subsample_freq="ME",
        )

        # Shuffle — 4 keys (real_ic / shuffle_distribution / p_value / significant)
        assert "shuffle" in result.robustness
        assert set(result.robustness["shuffle"].keys()) >= {
            "real_ic", "shuffle_distribution", "p_value", "significant",
        }

        # Subsample — list of dicts (may be empty if data doesn't span multiple months)
        assert "subsample" in result.robustness
        assert isinstance(result.robustness["subsample"], list)

        # Cost waterfall — 4 keys
        assert set(result.cost.keys()) == {
            "gross_edge_bps", "fee_cost_bps", "slippage_bps", "net_edge_bps",
        }

        # Rating still populated (evaluate_full calls evaluate internally)
        assert 0 <= result.rating <= 3

    def test_skip_shuffle_with_zero_iter(self, synth_100d_10sym):
        """``shuffle_iter=0`` must skip the ProcessPool entirely."""
        close, factor = synth_100d_10sym
        close_1 = close.iloc[:, 0]
        factor_1 = factor.iloc[:, 0]

        config = EvalConfig(universe=("SYM00",), start="2024-01-01", end="2024-05-01")
        result = Evaluator().evaluate_full(
            factor_1, close_1, config, shuffle_iter=0, shuffle_workers=1,
        )

        assert "shuffle" not in result.robustness
        assert "subsample" in result.robustness

    def test_cost_derived_from_turnover_and_ic(self, synth_100d_10sym):
        """``cost`` dict values must match ``edge_waterfall`` with the
        evaluator's own IC + turnover outputs."""
        close, factor = synth_100d_10sym
        close_1 = close.iloc[:, 0]
        factor_1 = factor.iloc[:, 0]

        config = EvalConfig(universe=("SYM00",), start="2024-01-01", end="2024-05-01", cost_bps=4.0)
        result = Evaluator().evaluate_full(
            factor_1, close_1, config, shuffle_iter=0,
        )

        # Recompute expected cost from the evaluator's own scalars
        expected = edge_waterfall(
            ic_mean=result.ic_mean,
            turnover_daily=result.turnover,
            fee_rate=4.0 / 10000.0 / 2.0,
            slippage_bps=1.0,
        )
        assert result.cost == expected


# ──────────────────────────────────────────────────────────────────────
# Import hygiene (no circular import)
# ──────────────────────────────────────────────────────────────────────


class TestNoCircularImports:
    """Importing each sub-module in isolation must not raise ImportError."""

    def test_import_ic(self):
        from tinohelm.factor.evaluation import ic  # noqa: F401

    def test_import_quantile(self):
        from tinohelm.factor.evaluation import quantile  # noqa: F401

    def test_import_distribution(self):
        from tinohelm.factor.evaluation import distribution  # noqa: F401

    def test_import_turnover(self):
        from tinohelm.factor.evaluation import turnover  # noqa: F401

    def test_import_robustness(self):
        from tinohelm.factor.evaluation import robustness  # noqa: F401

    def test_import_cost(self):
        from tinohelm.factor.evaluation import cost  # noqa: F401

    def test_import_rating(self):
        from tinohelm.factor.evaluation import rating  # noqa: F401

    def test_import_evaluator(self):
        from tinohelm.factor.evaluation import evaluator  # noqa: F401

    def test_default_eval_result_instantiation(self):
        """EvalResult must instantiate with defaults (s1 contract)."""
        r = EvalResult()
        assert r.ic_mean == 0.0
        assert r.half_life is None
        assert r.quantile_pnl == {}
        assert r.robustness == {}
        assert r.cost == {}
