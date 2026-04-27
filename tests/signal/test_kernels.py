"""Unit tests — built-in signal kernels and ``normalize_to_constraints``.

Coverage
--------
- AC-3.2.1: every kernel emits a weight panel where each row obeys::

    Σ |wᵢ| ≤ gross_exposure         (gross-exposure cap)
    |Σ wᵢ| ≤ net_exposure           (net-exposure cap)
    |wᵢ|   ≤ max_position           (per-asset cap)

  with a 1e-6 numerical tolerance.
- Output panel shape and column layout match the input ``(T, N+1)`` and
  the ``"ts"`` first column is preserved verbatim.
- :func:`normalize_to_constraints` enforces all three caps under direct
  unit tests with edge cases (NaN preservation, zero-row pass-through,
  per-asset clipping, and the saturation+shift interaction that broke
  the original single-pass algorithm).
- Kernel parameter validation (``k > 0``, ``power > 0``,
  ``lower <= upper``, ``clip > 0``).
- Cross-kernel fuzzing — 1000 random rows per kernel must satisfy all
  three caps to ensure no kernel parametrization slips past the
  projection.
- Convergence-warning behaviour — if the projection's iteration cap is
  exhausted, a warning is emitted via :mod:`logging` (no exception).
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import polars as pl
import pytest

from tinohelm.signal import (
    normalize_to_constraints,
    quantile_long_short,
    rank_to_weight,
    threshold_signed,
    top_k_long_short,
    zscore_clip,
)
from tinohelm.signal import kernel as _kernel_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default constraint dict used in most kernel tests.  Market-neutral.
_DEFAULT_CONSTRAINTS: dict[str, float] = {
    "gross_exposure": 1.0,
    "net_exposure": 0.0,
    "max_position": 0.5,
}

# Larger constraint allowing observations with different gross totals.
_LOOSE_CONSTRAINTS: dict[str, float] = {
    "gross_exposure": 2.0,
    "net_exposure": 1.0,
    "max_position": 0.5,
}


def _hourly_ts(n: int) -> pl.Series:
    start = dt.datetime(2024, 1, 1)
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


def _sample_panel(rng_seed: int = 42, T: int = 20, N: int = 6) -> pl.DataFrame:
    """Build a deterministic factor panel for kernel tests."""
    rng = np.random.default_rng(rng_seed)
    data: dict[str, object] = {"ts": _hourly_ts(T)}
    for i in range(N):
        data[f"S{i:02d}"] = rng.standard_normal(T).tolist()
    return pl.DataFrame(data)


def _assert_constraints(weight_panel: pl.DataFrame, constraints: dict[str, float]) -> None:
    """Assert each row of ``weight_panel`` satisfies the three bounds."""
    sym_cols = [c for c in weight_panel.columns if c != "ts"]
    arr = weight_panel.select(sym_cols).to_numpy()
    tol = 1e-6
    for t in range(arr.shape[0]):
        row = arr[t, :]
        valid = row[np.isfinite(row)]
        if valid.size == 0:
            continue
        gross = float(np.sum(np.abs(valid)))
        net = float(np.sum(valid))
        max_abs = float(np.max(np.abs(valid)))
        assert gross <= constraints["gross_exposure"] + tol, (
            f"row {t}: gross={gross} > cap={constraints['gross_exposure']}"
        )
        assert abs(net) <= constraints["net_exposure"] + tol, (
            f"row {t}: |net|={abs(net)} > cap={constraints['net_exposure']}"
        )
        assert max_abs <= constraints["max_position"] + tol, (
            f"row {t}: max|w|={max_abs} > cap={constraints['max_position']}"
        )


def _assert_panel_shape(weight_panel: pl.DataFrame, factor_panel: pl.DataFrame) -> None:
    """Assert weight panel has the same shape and ts column as factor."""
    assert weight_panel.shape == factor_panel.shape
    assert weight_panel.columns == factor_panel.columns
    # ts column preserved verbatim
    assert weight_panel["ts"].to_list() == factor_panel["ts"].to_list()


# ---------------------------------------------------------------------------
# AC-3.2.1: top_k_long_short
# ---------------------------------------------------------------------------


class TestTopKLongShort:
    def test_constraints_satisfied(self):
        panel = _sample_panel(rng_seed=1, T=20, N=8)
        out = top_k_long_short(
            panel, params={"k": 2}, constraints=_DEFAULT_CONSTRAINTS
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_long_short_split_correct(self):
        """Top factor → +1/k, bottom factor → -1/k (before normalization)."""
        # k=1, gross=1, max=1 so normalization shouldn't trim.
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(2),
                "A": [0.10, 0.30],
                "B": [0.20, 0.10],
                "C": [-0.10, -0.20],
                "D": [0.30, 0.50],
            }
        )
        constraints = {
            "gross_exposure": 2.0,
            "net_exposure": 1.0,
            "max_position": 1.0,
        }
        out = top_k_long_short(
            panel, params={"k": 1}, constraints=constraints
        )
        # Row 0: D=0.30 highest → long; C=-0.10 lowest → short
        assert out["D"][0] == pytest.approx(1.0)
        assert out["C"][0] == pytest.approx(-1.0)
        # Row 1: D=0.50 highest → long; C=-0.20 lowest → short
        assert out["D"][1] == pytest.approx(1.0)
        assert out["C"][1] == pytest.approx(-1.0)

    def test_too_few_valid_returns_zero_row(self):
        """When < 2k valid values, row is left at 0."""
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [0.10],
                "B": [None],
                "C": [None],
                "D": [None],
            }
        )
        out = top_k_long_short(
            panel, params={"k": 1}, constraints=_DEFAULT_CONSTRAINTS
        )
        # Need k=1 long + k=1 short = 2 valid; only 1 valid → all zero.
        assert all(out[c][0] == 0.0 for c in ["A", "B", "C", "D"])

    def test_invalid_k_raises(self):
        panel = _sample_panel(T=2, N=4)
        with pytest.raises(ValueError, match="k must be > 0"):
            top_k_long_short(panel, params={"k": 0}, constraints=_DEFAULT_CONSTRAINTS)


# ---------------------------------------------------------------------------
# AC-3.2.1: quantile_long_short
# ---------------------------------------------------------------------------


class TestQuantileLongShort:
    def test_constraints_satisfied(self):
        panel = _sample_panel(rng_seed=2, T=20, N=10)
        out = quantile_long_short(
            panel,
            params={"quantiles": 5, "long_q": 4, "short_q": 0},
            constraints=_DEFAULT_CONSTRAINTS,
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_default_long_q_is_top(self):
        """When long_q omitted, default is quantiles - 1."""
        panel = _sample_panel(rng_seed=3, T=10, N=10)
        out_explicit = quantile_long_short(
            panel,
            params={"quantiles": 5, "long_q": 4, "short_q": 0},
            constraints=_DEFAULT_CONSTRAINTS,
        )
        out_default = quantile_long_short(
            panel,
            params={"quantiles": 5},
            constraints=_DEFAULT_CONSTRAINTS,
        )
        _assert_constraints(out_default, _DEFAULT_CONSTRAINTS)
        # Both should produce the same weights with default settings.
        for c in out_explicit.columns:
            if c == "ts":
                continue
            assert out_explicit[c].to_list() == pytest.approx(
                out_default[c].to_list()
            )

    def test_invalid_quantiles_raises(self):
        panel = _sample_panel(T=5, N=4)
        with pytest.raises(ValueError, match="quantiles must be >= 2"):
            quantile_long_short(
                panel, params={"quantiles": 1}, constraints=_DEFAULT_CONSTRAINTS
            )

    def test_invalid_long_q_raises(self):
        panel = _sample_panel(T=5, N=10)
        with pytest.raises(ValueError, match="long_q"):
            quantile_long_short(
                panel,
                params={"quantiles": 5, "long_q": 99},
                constraints=_DEFAULT_CONSTRAINTS,
            )

    def test_too_few_valid_returns_zero_row(self):
        """When valid count < quantiles, row stays zero."""
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [0.10],
                "B": [0.20],
                "C": [None],
                "D": [None],
            }
        )
        out = quantile_long_short(
            panel,
            params={"quantiles": 5},
            constraints=_DEFAULT_CONSTRAINTS,
        )
        for c in ["A", "B", "C", "D"]:
            assert out[c][0] == 0.0


# ---------------------------------------------------------------------------
# AC-3.2.1: threshold_signed
# ---------------------------------------------------------------------------


class TestThresholdSigned:
    def test_constraints_satisfied(self):
        panel = _sample_panel(rng_seed=4, T=20, N=6)
        out = threshold_signed(
            panel,
            params={
                "upper": 0.5,
                "lower": -0.5,
                "long_weight": 0.5,
                "short_weight": -0.5,
            },
            constraints=_DEFAULT_CONSTRAINTS,
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_threshold_logic(self):
        """Above upper → long_weight, below lower → short_weight, else 0."""
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [1.0],   # > upper
                "B": [-1.0],  # < lower
                "C": [0.0],   # in band → 0
                "D": [0.6],   # > upper
            }
        )
        # Choose constraints loose enough so no rescaling happens.
        constraints = {
            "gross_exposure": 5.0,
            "net_exposure": 5.0,
            "max_position": 1.0,
        }
        out = threshold_signed(
            panel,
            params={
                "upper": 0.5,
                "lower": -0.5,
                "long_weight": 0.5,
                "short_weight": -0.5,
            },
            constraints=constraints,
        )
        assert out["A"][0] == pytest.approx(0.5)
        assert out["B"][0] == pytest.approx(-0.5)
        assert out["C"][0] == pytest.approx(0.0)
        assert out["D"][0] == pytest.approx(0.5)

    def test_lower_above_upper_raises(self):
        panel = _sample_panel(T=2, N=4)
        with pytest.raises(ValueError, match="lower"):
            threshold_signed(
                panel,
                params={"upper": 0.0, "lower": 0.5},
                constraints=_DEFAULT_CONSTRAINTS,
            )


# ---------------------------------------------------------------------------
# AC-3.2.1: zscore_clip
# ---------------------------------------------------------------------------


class TestZScoreClip:
    def test_constraints_satisfied(self):
        panel = _sample_panel(rng_seed=5, T=20, N=8)
        out = zscore_clip(
            panel, params={"clip": 3.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_zero_variance_row_zero_weights(self):
        """Constant row → std=0 → zero weights (skip rescale)."""
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [1.0],
                "B": [1.0],
                "C": [1.0],
                "D": [1.0],
            }
        )
        out = zscore_clip(
            panel, params={"clip": 3.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        for c in ["A", "B", "C", "D"]:
            assert out[c][0] == pytest.approx(0.0)

    def test_too_few_valid_returns_zero_row(self):
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [1.0],
                "B": [None],
                "C": [None],
            }
        )
        out = zscore_clip(
            panel, params={"clip": 3.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        # When fewer than 2 valid values, every cell is unusable —
        # the kernel writes NaN to the originally-NaN cells and 0 to the
        # valid cell (so normalize_to_constraints sees a zero-only row).
        # After normalization NaN propagates through unchanged, and the
        # zero cells stay zero.  No constraint is violated either way.
        # We assert "no positive position" rather than a specific value.
        sym_cols = ["A", "B", "C"]
        arr = out.select(sym_cols).to_numpy()[0]
        valid = arr[np.isfinite(arr)]
        assert valid.size == 0 or np.allclose(valid, 0.0)

    def test_invalid_clip_raises(self):
        panel = _sample_panel(T=5, N=6)
        with pytest.raises(ValueError, match="clip"):
            zscore_clip(
                panel, params={"clip": 0.0}, constraints=_DEFAULT_CONSTRAINTS
            )


# ---------------------------------------------------------------------------
# AC-3.2.1: rank_to_weight
# ---------------------------------------------------------------------------


class TestRankToWeight:
    def test_constraints_satisfied_linear(self):
        panel = _sample_panel(rng_seed=6, T=20, N=10)
        out = rank_to_weight(
            panel, params={"power": 1.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_constraints_satisfied_quadratic(self):
        panel = _sample_panel(rng_seed=7, T=20, N=10)
        out = rank_to_weight(
            panel, params={"power": 2.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        _assert_panel_shape(out, panel)
        _assert_constraints(out, _DEFAULT_CONSTRAINTS)

    def test_top_rank_positive_weight(self):
        """Highest factor → positive weight, lowest → negative weight."""
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "low": [0.0],
                "mid": [0.5],
                "high": [1.0],
            }
        )
        out = rank_to_weight(
            panel, params={"power": 1.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        # high must be positive, low must be negative.
        assert out["high"][0] > 0
        assert out["low"][0] < 0

    def test_invalid_power_raises(self):
        panel = _sample_panel(T=5, N=6)
        with pytest.raises(ValueError, match="power"):
            rank_to_weight(
                panel, params={"power": 0.0}, constraints=_DEFAULT_CONSTRAINTS
            )

    def test_too_few_valid_returns_zero_row(self):
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [1.0],
                "B": [None],
                "C": [None],
            }
        )
        out = rank_to_weight(
            panel, params={"power": 1.0}, constraints=_DEFAULT_CONSTRAINTS
        )
        # Mirrors zscore_clip: NaN cells stay NaN, valid cells go to 0.
        sym_cols = ["A", "B", "C"]
        arr = out.select(sym_cols).to_numpy()[0]
        valid = arr[np.isfinite(arr)]
        assert valid.size == 0 or np.allclose(valid, 0.0)


# ---------------------------------------------------------------------------
# normalize_to_constraints — direct unit tests
# ---------------------------------------------------------------------------


class TestNormalizeToConstraints:
    def test_clip_to_max_position(self):
        weights = np.array([[0.8, -0.6, 0.3]])
        out = normalize_to_constraints(
            weights, gross_exposure=10.0, net_exposure=10.0, max_position=0.5
        )
        assert np.all(np.abs(out) <= 0.5 + 1e-9)
        # 0.8 → 0.5; -0.6 → -0.5; 0.3 stays.
        np.testing.assert_allclose(out[0], [0.5, -0.5, 0.3])

    def test_gross_scale_down(self):
        weights = np.array([[0.5, -0.5, 0.5, -0.5]])
        # gross before = 2.0; cap = 1.0 → scale by 0.5
        out = normalize_to_constraints(
            weights, gross_exposure=1.0, net_exposure=10.0, max_position=1.0
        )
        np.testing.assert_allclose(out[0], [0.25, -0.25, 0.25, -0.25])

    def test_net_shift_to_target(self):
        """When |net| > cap, uniform shift moves net toward ±cap."""
        # Σw = +0.6; cap = 0.0 → shift each by +0.6/3 = 0.2 (subtract)
        weights = np.array([[0.4, 0.3, -0.1]])  # net = 0.6
        out = normalize_to_constraints(
            weights, gross_exposure=10.0, net_exposure=0.0, max_position=10.0
        )
        # After shift: [0.2, 0.1, -0.3] → net ≈ 0
        assert abs(float(np.sum(out[0]))) < 1e-9

    def test_nan_preserved(self):
        weights = np.array([[0.3, np.nan, -0.4]])
        out = normalize_to_constraints(
            weights, gross_exposure=2.0, net_exposure=1.0, max_position=0.5
        )
        assert np.isnan(out[0, 1])
        # The other values were already within bounds.
        assert abs(out[0, 0] - 0.3) < 1e-9
        assert abs(out[0, 2] - (-0.4)) < 1e-9

    def test_all_nan_row_passes_through(self):
        weights = np.array([[np.nan, np.nan, np.nan]])
        out = normalize_to_constraints(
            weights, gross_exposure=1.0, net_exposure=0.0, max_position=0.5
        )
        assert np.all(np.isnan(out[0]))

    def test_zero_weights_pass_through(self):
        weights = np.zeros((3, 4))
        out = normalize_to_constraints(
            weights, gross_exposure=1.0, net_exposure=0.0, max_position=0.5
        )
        assert np.all(out == 0.0)

    def test_invalid_gross_raises(self):
        weights = np.zeros((1, 3))
        with pytest.raises(ValueError, match="gross_exposure"):
            normalize_to_constraints(
                weights,
                gross_exposure=0.0,
                net_exposure=0.0,
                max_position=0.5,
            )

    def test_invalid_max_position_raises(self):
        weights = np.zeros((1, 3))
        with pytest.raises(ValueError, match="max_position"):
            normalize_to_constraints(
                weights,
                gross_exposure=1.0,
                net_exposure=0.0,
                max_position=0.0,
            )

    def test_invalid_net_raises(self):
        weights = np.zeros((1, 3))
        with pytest.raises(ValueError, match="net_exposure"):
            normalize_to_constraints(
                weights,
                gross_exposure=1.0,
                net_exposure=-0.1,
                max_position=0.5,
            )

    def test_non_2d_raises(self):
        weights = np.array([0.3, 0.4, 0.5])  # 1-D
        with pytest.raises(ValueError, match="2-D"):
            normalize_to_constraints(
                weights,
                gross_exposure=1.0,
                net_exposure=0.0,
                max_position=0.5,
            )


# ---------------------------------------------------------------------------
# Saturation + shift regression — the original single-pass algorithm
# silently violated |Σw| ≤ net_exposure when step-1 clip saturated cells
# on one side; the smart-shift cyclic projection fixes this.
# ---------------------------------------------------------------------------


class TestNormalizeSaturationShift:
    def test_saturation_upper_side_net_zero(self):
        """``[2,2,2,-1] + max=0.5 + net=0`` was the canonical bug repro.

        Naive single-sweep produced ``[+0.25,+0.25,+0.25,-0.5]`` (net=+0.25).
        Smart-shift projects onto the feasible set: cell 3 is already
        saturated at -max_position, so it's excluded from the shift; the
        remaining 1.0 of net excess is distributed across the 3 upper-side
        cells → ``[+1/6, +1/6, +1/6, -0.5]`` with net = 0.
        """
        weights = np.array([[2.0, 2.0, 2.0, -1.0]])
        out = normalize_to_constraints(
            weights, gross_exposure=10.0, net_exposure=0.0, max_position=0.5
        )
        assert abs(float(np.sum(out[0]))) <= 1e-6
        assert float(np.max(np.abs(out[0]))) <= 0.5 + 1e-9
        assert float(np.sum(np.abs(out[0]))) <= 10.0 + 1e-6

    def test_saturation_lower_side_net_zero(self):
        """Mirror of the upper-side case — net excess pushes weights up."""
        weights = np.array([[-2.0, -2.0, -2.0, 1.0]])
        out = normalize_to_constraints(
            weights, gross_exposure=10.0, net_exposure=0.0, max_position=0.5
        )
        assert abs(float(np.sum(out[0]))) <= 1e-6
        assert float(np.max(np.abs(out[0]))) <= 0.5 + 1e-9
        assert float(np.sum(np.abs(out[0]))) <= 10.0 + 1e-6

    def test_threshold_signed_imbalanced_long_short_count(self):
        """End-to-end: ``threshold_signed`` with imbalanced long/short
        counts must still satisfy all three caps after normalization.

        With ``long_weight=1.0, short_weight=-1.0`` and 4-of-5 cells
        crossing the upper threshold (1 short), the raw weights are
        ``[1, 1, 1, 1, -1]`` (net=+3, gross=5).  The original algorithm
        clipped to ``[0.5, 0.5, 0.5, 0.5, -0.5]`` (net=+1.5), shifted
        uniformly by 1.5/5=0.3 → ``[0.2, 0.2, 0.2, 0.2, -0.8]``, then the
        final clip became ``[0.2, 0.2, 0.2, 0.2, -0.5]`` (net=+0.3, BUG).
        Smart-shift converges to a feasible point.
        """
        panel = pl.DataFrame(
            {
                "ts": _hourly_ts(1),
                "A": [1.0],   # > upper
                "B": [1.0],   # > upper
                "C": [1.0],   # > upper
                "D": [1.0],   # > upper
                "E": [-1.0],  # < lower
            }
        )
        constraints = {
            "gross_exposure": 5.0,
            "net_exposure": 0.0,  # market-neutral
            "max_position": 0.5,
        }
        out = threshold_signed(
            panel,
            params={
                "upper": 0.5,
                "lower": -0.5,
                "long_weight": 1.0,
                "short_weight": -1.0,
            },
            constraints=constraints,
        )
        _assert_constraints(out, constraints)

    def test_well_formed_input_unchanged(self):
        """Rows already satisfying all three caps must pass through
        unchanged (smart-shift's stationary point equals the identity)."""
        weights = np.array([[0.3, -0.2, 0.1, 0.0]])  # net=+0.2, gross=0.6
        out = normalize_to_constraints(
            weights, gross_exposure=1.0, net_exposure=0.5, max_position=0.5
        )
        np.testing.assert_allclose(out[0], weights[0])


# ---------------------------------------------------------------------------
# Fuzz: 1000 random rows × 5 kernels.  Every output row must satisfy
# Σ|w|≤gross, |Σw|≤net, |wᵢ|≤max within 1e-6 tolerance.
# ---------------------------------------------------------------------------


class TestKernelFuzz:
    """1000-row fuzz across each kernel — the projector must never let a
    constraint violation through, regardless of input distribution or
    kernel parameter."""

    @pytest.mark.parametrize("seed", [101, 202, 303, 404, 505])
    def test_threshold_signed_fuzz(self, seed: int):
        rng = np.random.default_rng(seed)
        rows = 200  # 5 seeds × 200 = 1000 rows
        N = int(rng.integers(2, 31))
        panel_data: dict[str, object] = {"ts": _hourly_ts(rows)}
        for i in range(N):
            panel_data[f"S{i:02d}"] = rng.normal(
                loc=rng.uniform(-1.0, 1.0),
                scale=rng.uniform(0.1, 2.0),
                size=rows,
            ).tolist()
        panel = pl.DataFrame(panel_data)
        constraints = {
            "gross_exposure": float(rng.uniform(0.5, 5.0)),
            "net_exposure": float(rng.uniform(0.0, 1.0)),
            "max_position": float(rng.uniform(0.1, 1.0)),
        }
        out = threshold_signed(
            panel,
            params={
                "upper": float(rng.uniform(-0.2, 0.8)),
                "lower": float(rng.uniform(-0.8, -0.2)),
                "long_weight": float(rng.uniform(0.1, 2.0)),
                "short_weight": -float(rng.uniform(0.1, 2.0)),
            },
            constraints=constraints,
        )
        _assert_constraints(out, constraints)

    @pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
    def test_top_k_long_short_fuzz(self, seed: int):
        rng = np.random.default_rng(seed)
        rows = 200
        N = int(rng.integers(4, 31))
        panel_data: dict[str, object] = {"ts": _hourly_ts(rows)}
        for i in range(N):
            panel_data[f"S{i:02d}"] = rng.standard_normal(rows).tolist()
        panel = pl.DataFrame(panel_data)
        constraints = {
            "gross_exposure": float(rng.uniform(0.5, 5.0)),
            "net_exposure": float(rng.uniform(0.0, 1.0)),
            "max_position": float(rng.uniform(0.1, 1.0)),
        }
        k = int(rng.integers(1, max(2, N // 2 + 1)))
        out = top_k_long_short(panel, params={"k": k}, constraints=constraints)
        _assert_constraints(out, constraints)

    @pytest.mark.parametrize("seed", [111, 222, 333, 444, 555])
    def test_quantile_long_short_fuzz(self, seed: int):
        rng = np.random.default_rng(seed)
        rows = 200
        N = int(rng.integers(5, 31))
        panel_data: dict[str, object] = {"ts": _hourly_ts(rows)}
        for i in range(N):
            panel_data[f"S{i:02d}"] = rng.standard_normal(rows).tolist()
        panel = pl.DataFrame(panel_data)
        constraints = {
            "gross_exposure": float(rng.uniform(0.5, 5.0)),
            "net_exposure": float(rng.uniform(0.0, 1.0)),
            "max_position": float(rng.uniform(0.1, 1.0)),
        }
        quantiles = int(rng.integers(2, 6))
        out = quantile_long_short(
            panel,
            params={"quantiles": quantiles},
            constraints=constraints,
        )
        _assert_constraints(out, constraints)

    @pytest.mark.parametrize("seed", [777, 888, 999, 1010, 1111])
    def test_zscore_clip_fuzz(self, seed: int):
        rng = np.random.default_rng(seed)
        rows = 200
        N = int(rng.integers(2, 31))
        panel_data: dict[str, object] = {"ts": _hourly_ts(rows)}
        for i in range(N):
            # Heavy-tailed → zscore clip will saturate, exercising the
            # projection's saturation handling.
            panel_data[f"S{i:02d}"] = (
                rng.standard_normal(rows) * 5.0
            ).tolist()
        panel = pl.DataFrame(panel_data)
        constraints = {
            "gross_exposure": float(rng.uniform(0.5, 5.0)),
            "net_exposure": float(rng.uniform(0.0, 1.0)),
            "max_position": float(rng.uniform(0.1, 1.0)),
        }
        out = zscore_clip(
            panel,
            params={"clip": float(rng.uniform(1.0, 5.0))},
            constraints=constraints,
        )
        _assert_constraints(out, constraints)

    @pytest.mark.parametrize("seed", [13, 26, 39, 52, 65])
    def test_rank_to_weight_fuzz(self, seed: int):
        rng = np.random.default_rng(seed)
        rows = 200
        N = int(rng.integers(2, 31))
        panel_data: dict[str, object] = {"ts": _hourly_ts(rows)}
        for i in range(N):
            panel_data[f"S{i:02d}"] = rng.standard_normal(rows).tolist()
        panel = pl.DataFrame(panel_data)
        constraints = {
            "gross_exposure": float(rng.uniform(0.5, 5.0)),
            "net_exposure": float(rng.uniform(0.0, 1.0)),
            "max_position": float(rng.uniform(0.1, 1.0)),
        }
        out = rank_to_weight(
            panel,
            params={"power": float(rng.uniform(0.5, 3.0))},
            constraints=constraints,
        )
        _assert_constraints(out, constraints)


# ---------------------------------------------------------------------------
# Convergence warning — if the projection cap is artificially small the
# loop logs a warning rather than raising; downstream live-trading paths
# depend on this non-throwing contract.
# ---------------------------------------------------------------------------


class TestNormalizeConvergenceWarning:
    def test_warns_when_iteration_cap_exhausted(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ):
        """Force a non-converging case by capping the projector at 0
        iterations.  The cyclic loop never runs; the hard-guard fallback
        applies, and a warning is logged for each violating row."""
        # max_iter=0 means the loop body never executes → converged stays
        # False even on trivially-feasible inputs that *would* satisfy
        # the caps after one pass.  This is the cleanest way to exercise
        # the warn-and-fallback code path without writing unrealistic
        # zero-headroom weights.
        monkeypatch.setattr(_kernel_module, "_PROJECTION_MAX_ITER", 0)

        weights = np.array([[2.0, 2.0, 2.0, -1.0]])
        with caplog.at_level(logging.WARNING, logger="tinohelm.signal.kernel"):
            out = normalize_to_constraints(
                weights,
                gross_exposure=10.0,
                net_exposure=0.0,
                max_position=0.5,
            )

        # Warning fired (no raise).
        assert any(
            "failed to converge" in r.message for r in caplog.records
        ), f"expected convergence warning, got: {[r.message for r in caplog.records]!r}"

        # Hard-guard still enforced box and gross caps.
        assert float(np.max(np.abs(out[0]))) <= 0.5 + 1e-9
        assert float(np.sum(np.abs(out[0]))) <= 10.0 + 1e-6

    def test_no_warning_on_well_formed_input(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Inputs satisfying all three caps must converge in a single
        loop iteration (no warning)."""
        weights = np.array([[0.3, -0.2, 0.1, 0.0]])
        with caplog.at_level(logging.WARNING, logger="tinohelm.signal.kernel"):
            normalize_to_constraints(
                weights,
                gross_exposure=1.0,
                net_exposure=0.5,
                max_position=0.5,
            )
        assert not any(
            "failed to converge" in r.message for r in caplog.records
        )
