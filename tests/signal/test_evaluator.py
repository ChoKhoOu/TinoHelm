"""Unit tests — SignalEvaluator + SignalEvalResult.

Coverage
--------
- AC-3.3.1: evaluate(weight_panel, future_returns, cost_model) → SignalEvalResult
- AC-3.3.2: all-zero weights → sharpe=0 / mdd=0 / turnover=0
- Red-line 5: evaluator.py must not import nautilus_trader
- Synthetic known-value assertions for PnL, MDD, capacity, tail-loss, cost drag
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import polars as pl
import pytest

from tinohelm.signal.evaluator import SignalEvalResult, SignalEvaluator
from tinohelm.signal.types import CostModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zero_cost() -> CostModel:
    """CostModel with all costs set to zero (for isolated metric testing)."""
    return CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)


@pytest.fixture()
def sample_weight_panel() -> pl.DataFrame:
    np.random.seed(42)
    T, N = 50, 5
    weights_arr = np.random.randn(T, N)
    # Normalize so gross exposure ≈ 1.
    weights_arr /= np.abs(weights_arr).sum(axis=1, keepdims=True)
    return pl.DataFrame(
        {
            "ts": list(range(T)),
            **{f"s{i}": weights_arr[:, i].tolist() for i in range(N)},
        }
    )


@pytest.fixture()
def sample_future_returns() -> pl.DataFrame:
    np.random.seed(123)
    T, N = 50, 5
    returns_arr = np.random.randn(T, N) * 0.01
    return pl.DataFrame(
        {
            "ts": list(range(T)),
            **{f"s{i}": returns_arr[:, i].tolist() for i in range(N)},
        }
    )


# ---------------------------------------------------------------------------
# Test 1 — basic evaluate returns a well-formed SignalEvalResult
# ---------------------------------------------------------------------------


def test_basic_evaluate(
    sample_weight_panel: pl.DataFrame,
    sample_future_returns: pl.DataFrame,
) -> None:
    evaluator = SignalEvaluator(periods_per_year=252)
    cost = CostModel(name="custom", fee_bps_per_side=5.0, slippage_bps_per_side=3.0)
    result = evaluator.evaluate(sample_weight_panel, sample_future_returns, cost)

    assert isinstance(result, SignalEvalResult)
    assert result.n_periods > 0
    assert isinstance(result.net_pnl_curve, list)
    assert len(result.net_pnl_curve) == result.n_periods
    assert isinstance(result.gross_pnl_curve, list)
    assert len(result.gross_pnl_curve) == result.n_periods
    # total_return must equal last element of net_pnl_curve
    assert abs(result.total_return - result.net_pnl_curve[-1]) < 1e-12
    # All fields are float scalars
    assert isinstance(result.sharpe, float)
    assert isinstance(result.mdd, float)
    assert isinstance(result.turnover_annualized, float)
    assert isinstance(result.capacity_score, float)
    assert isinstance(result.tail_loss_p99, float)
    assert isinstance(result.cost_drag, float)
    # Basic sanity: MDD and capacity_score are non-negative
    assert result.mdd >= 0.0
    assert 0.0 <= result.capacity_score <= 1.0


# ---------------------------------------------------------------------------
# Test 2 — all-zero weights → sharpe=0 / mdd=0 / turnover=0
# ---------------------------------------------------------------------------


def test_zero_weights_zero_metrics() -> None:
    """全 0 weight → sharpe=0 / mdd=0 / turnover=0."""
    T, N = 50, 5
    ts = list(range(T))
    weight_panel = pl.DataFrame(
        {
            "ts": ts,
            **{f"s{i}": [0.0] * T for i in range(N)},
        }
    )
    np.random.seed(7)
    returns_panel = pl.DataFrame(
        {
            "ts": ts,
            **{f"s{i}": np.random.randn(T).tolist() for i in range(N)},
        }
    )
    cost = CostModel(name="custom", fee_bps_per_side=5.0)
    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)

    assert result.sharpe == 0.0
    assert result.mdd == 0.0
    assert result.turnover_annualized == 0.0
    assert result.total_return == 0.0


# ---------------------------------------------------------------------------
# Test 3 — synthetic data with known analytical solution
# ---------------------------------------------------------------------------


def test_known_pnl_synthetic() -> None:
    """Constant weights + constant returns → verifiable cumulative PnL."""
    T, N = 10, 2
    weights = np.array([[0.5, -0.5]] * T)
    returns = np.array([[0.01, -0.01]] * T)

    weight_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            "s1": weights[:, 0].tolist(),
            "s2": weights[:, 1].tolist(),
        }
    )
    returns_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            "s1": returns[:, 0].tolist(),
            "s2": returns[:, 1].tolist(),
        }
    )

    cost = CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)
    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)

    # Each period gross = 0.5*0.01 + (-0.5)*(-0.01) = 0.01
    # No cost → net = gross
    # cum_net[t] = (t+1) * 0.01
    assert abs(result.total_return - 0.1) < 1e-10
    for t in range(T):
        assert abs(result.net_pnl_curve[t] - (t + 1) * 0.01) < 1e-10, (
            f"period {t}: expected {(t+1)*0.01:.6f}, got {result.net_pnl_curve[t]:.6f}"
        )


# ---------------------------------------------------------------------------
# Test 4 — cost drag reduces net vs gross; higher cost → more drag
# ---------------------------------------------------------------------------


def test_cost_drag_reduces_net() -> None:
    """Non-zero cost → net < gross; higher cost → greater drag."""
    T, N = 20, 3
    weights = np.tile(np.array([1 / 3, -1 / 3, 0.0]), (T, 1))
    # Introduce a flip at t=5 so turnover is non-trivial.
    weights[5:, 0] = -1 / 3
    weights[5:, 1] = 1 / 3

    rng = np.random.RandomState(42)
    returns = rng.randn(T, N) * 0.01

    weight_panel = pl.DataFrame(
        {"ts": list(range(T)), **{f"s{i}": weights[:, i].tolist() for i in range(N)}}
    )
    returns_panel = pl.DataFrame(
        {"ts": list(range(T)), **{f"s{i}": returns[:, i].tolist() for i in range(N)}}
    )

    high_cost = CostModel(name="custom", fee_bps_per_side=100.0, slippage_bps_per_side=0.0)
    low_cost = CostModel(name="custom", fee_bps_per_side=1.0, slippage_bps_per_side=0.0)

    result_high = SignalEvaluator().evaluate(weight_panel, returns_panel, high_cost)
    result_low = SignalEvaluator().evaluate(weight_panel, returns_panel, low_cost)

    assert result_high.cost_drag > result_low.cost_drag
    assert result_high.total_return < result_low.total_return


# ---------------------------------------------------------------------------
# Test 5 — no nautilus_trader import (red-line 5)
# ---------------------------------------------------------------------------


def test_no_nautilus_import() -> None:
    """evaluator.py must not pull in nautilus_trader at import time."""
    nt_modules_before = {k for k in sys.modules if k.startswith("nautilus")}

    # Force a fresh import of the evaluator module.
    if "tinohelm.signal.evaluator" in sys.modules:
        del sys.modules["tinohelm.signal.evaluator"]

    import tinohelm.signal.evaluator  # noqa: F401

    nt_modules_after = {k for k in sys.modules if k.startswith("nautilus")}

    added = nt_modules_after - nt_modules_before
    assert not added, f"nautilus_trader modules added by evaluator import: {added}"


def test_no_nautilus_import_via_grep() -> None:
    """evaluator.py source code must not have import statements for nautilus_trader."""
    src_path = pathlib.Path(__file__).parent.parent.parent / "src/tinohelm/signal/evaluator.py"
    src = src_path.read_text()
    # Check line-by-line: no executable import of nautilus_trader.
    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith(("import nautilus", "from nautilus"))
    ]
    assert not import_lines, f"Found nautilus import lines: {import_lines}"


# ---------------------------------------------------------------------------
# Test 6 — MDD correctness on a hand-crafted PnL curve
# ---------------------------------------------------------------------------


def test_mdd_correctness() -> None:
    """Net PnL = [0.1, 0.2, 0.05, 0.15] → max drawdown = 0.15."""
    # net period returns that produce the desired cum PnL increments
    # cum_net[0]=0.1, cum_net[1]=0.2, cum_net[2]=0.05, cum_net[3]=0.15
    T = 4
    weight_panel = pl.DataFrame({"ts": list(range(T)), "s1": [1.0] * T})
    returns_panel = pl.DataFrame(
        {"ts": list(range(T)), "s1": [0.1, 0.1, -0.15, 0.1]}
    )
    cost = CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)

    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)

    # cum_net = [0.1, 0.2, 0.05, 0.15]
    # running_max = [0.1, 0.2, 0.2, 0.2]
    # drawdown = [0, 0, -0.15, -0.05] → mdd = 0.15
    assert abs(result.mdd - 0.15) < 1e-9


def test_mdd_counts_loss_from_flat_baseline() -> None:
    """Losing from the first period is drawdown from the initial flat baseline."""
    result = SignalEvaluator().evaluate(
        pl.DataFrame({"ts": [0, 1], "s1": [1.0, 1.0]}),
        pl.DataFrame({"ts": [0, 1], "s1": [-0.10, 0.02]}),
        CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0),
    )

    # cum_net = [-0.10, -0.08]; with the implicit starting equity/PnL of 0,
    # max drawdown is 0.10.  The previous implementation incorrectly used
    # -0.10 as the first peak and returned 0.0.
    assert result.mdd == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Test 7 — capacity_score: concentrated vs diversified
# ---------------------------------------------------------------------------


def test_capacity_score_concentrated_low() -> None:
    """Single asset holds 100% weight → capacity_score = 0."""
    T, N = 5, 4
    weight_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            "s1": [1.0] * T,
            "s2": [0.0] * T,
            "s3": [0.0] * T,
            "s4": [0.0] * T,
        }
    )
    returns_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            **{f"s{i}": [0.01] * T for i in range(1, 5)},
        }
    )
    cost = CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)
    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)
    # top1=1.0, total=1.0 → concentration=1.0 → capacity=0.0
    assert result.capacity_score == 0.0


def test_capacity_score_diversified_high() -> None:
    """Four equal 25% weights → capacity_score = 0.75."""
    T, N = 5, 4
    weight_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            "s1": [0.25] * T,
            "s2": [0.25] * T,
            "s3": [0.25] * T,
            "s4": [0.25] * T,
        }
    )
    returns_panel = pl.DataFrame(
        {
            "ts": list(range(T)),
            **{f"s{i}": [0.01] * T for i in range(1, 5)},
        }
    )
    cost = CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)
    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)
    # top1=0.25, total=1.0 → concentration=0.25 → capacity=0.75
    assert abs(result.capacity_score - 0.75) < 1e-9


# ---------------------------------------------------------------------------
# Test 8 — tail_loss_p99 is the worst-1% net period return
# ---------------------------------------------------------------------------


def test_tail_loss_p99_extreme_negative() -> None:
    """1st-percentile net return over 100 random periods should be negative."""
    T, N = 100, 1
    np.random.seed(42)
    rets = np.random.randn(T) * 0.01
    weight_panel = pl.DataFrame({"ts": list(range(T)), "s1": [1.0] * T})
    returns_panel = pl.DataFrame({"ts": list(range(T)), "s1": rets.tolist()})
    cost = CostModel(name="custom", fee_bps_per_side=0.0, slippage_bps_per_side=0.0)
    result = SignalEvaluator().evaluate(weight_panel, returns_panel, cost)
    assert result.tail_loss_p99 < 0


# ---------------------------------------------------------------------------
# Test 9 — rebate reduces effective cost
# ---------------------------------------------------------------------------


def test_rebate_reduces_cost() -> None:
    """Maker rebate should lower cost_drag compared to no-rebate model."""
    T, N = 20, 2
    weights = np.tile(np.array([0.5, -0.5]), (T, 1))
    returns = np.zeros((T, N))

    weight_panel = pl.DataFrame(
        {"ts": list(range(T)), "s1": weights[:, 0].tolist(), "s2": weights[:, 1].tolist()}
    )
    returns_panel = pl.DataFrame(
        {"ts": list(range(T)), "s1": returns[:, 0].tolist(), "s2": returns[:, 1].tolist()}
    )

    no_rebate = CostModel(name="custom", fee_bps_per_side=5.0, slippage_bps_per_side=0.0, rebate_bps_per_side=0.0)
    with_rebate = CostModel(name="custom", fee_bps_per_side=5.0, slippage_bps_per_side=0.0, rebate_bps_per_side=2.0)

    result_no = SignalEvaluator().evaluate(weight_panel, returns_panel, no_rebate)
    result_rb = SignalEvaluator().evaluate(weight_panel, returns_panel, with_rebate)

    assert result_rb.cost_drag < result_no.cost_drag
    # Cost drag should be proportional: rebate reduces per-side cost by 2bps
    expected_ratio = (5.0 - 2.0) / 5.0
    assert abs(result_rb.cost_drag / result_no.cost_drag - expected_ratio) < 1e-9


# ---------------------------------------------------------------------------
# Test 10 — trailing all-NaN return rows are excluded from T/turnover/cost
# ---------------------------------------------------------------------------


def test_trailing_nan_return_rows_excluded() -> None:
    """尾部全 NaN 的 returns 行不应该计入 T/turnover/cost.

    Scenario: T=5 rows; row index 4 has all-NaN returns (forward-return
    panel's natural last row from close.shift(-1)/close-1).

    Expected behaviour after the fix
    ---------------------------------
    - n_periods == 4  (the NaN row is trimmed before any calculation)
    - Weights, turnover, and cost are computed only over rows 0-3.
    - cost_drag equals the cost computed from the 4 valid rows only.
    """
    T_full = 5
    T_valid = 4
    N = 2
    ts = list(range(T_full))

    # Constant weights: s1=0.6, s2=0.4 for all 5 rows.
    weight_panel = pl.DataFrame(
        {"ts": ts, "s1": [0.6] * T_full, "s2": [0.4] * T_full}
    )

    # Rows 0-3 have valid returns; row 4 is all NaN (forward-return tail).
    s1_rets = [0.01, -0.005, 0.008, 0.003, float("nan")]
    s2_rets = [0.005, 0.002, -0.003, 0.007, float("nan")]
    returns_panel = pl.DataFrame({"ts": ts, "s1": s1_rets, "s2": s2_rets})

    cost = CostModel(name="custom", fee_bps_per_side=5.0, slippage_bps_per_side=2.0)

    result = SignalEvaluator(periods_per_year=252).evaluate(
        weight_panel, returns_panel, cost
    )

    # n_periods must equal T_valid (trailing NaN row stripped).
    assert result.n_periods == T_valid, (
        f"expected n_periods={T_valid}, got {result.n_periods}"
    )

    # PnL curves have exactly T_valid elements.
    assert len(result.net_pnl_curve) == T_valid
    assert len(result.gross_pnl_curve) == T_valid

    # Compute expected cost_drag manually over 4 valid rows.
    # Constant weights → zero delta at t=1,2,3; entry at t=0 = Σ|w[0]|.
    # turnover_per_period = [1.0, 0.0, 0.0, 0.0]
    # cost_rate = (5 + 2 - 0) / 10_000 = 0.0007
    cost_rate = (5.0 + 2.0) / 10_000.0
    expected_cost_drag = (0.6 + 0.4) * cost_rate  # only the entry turnover = 1.0
    assert abs(result.cost_drag - expected_cost_drag) < 1e-12, (
        f"cost_drag mismatch: expected {expected_cost_drag}, got {result.cost_drag}"
    )

    # Gross PnL: 4 valid periods.
    expected_gross = [
        0.6 * 0.01 + 0.4 * 0.005,    # 0.006 + 0.002 = 0.008
        0.6 * -0.005 + 0.4 * 0.002,  # -0.003 + 0.0008 = -0.0022
        0.6 * 0.008 + 0.4 * -0.003,  # 0.0048 - 0.0012 = 0.0036
        0.6 * 0.003 + 0.4 * 0.007,   # 0.0018 + 0.0028 = 0.0046
    ]
    cum_gross_expected = float(sum(expected_gross))
    assert abs(result.gross_pnl_curve[-1] - cum_gross_expected) < 1e-12, (
        f"gross_pnl_curve[-1] mismatch: expected {cum_gross_expected}, got {result.gross_pnl_curve[-1]}"
    )
