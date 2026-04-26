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
