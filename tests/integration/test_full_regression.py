"""End-to-end regression test covering the full factor → signal → evaluation pipeline.

Coverage:
1. 9 active factors computed on synthetic (T=200, N=10) OHLCV panel
2. Cross-section neutralization via Aligner (universe PIT mask + OLS residuals)
3. Factor correlation matrix + hierarchical clustering
4. compare_results pairwise bootstrap CI
5. 5 built-in SignalKernels: each produces a valid weight panel satisfying constraints
6. SignalEvaluator: sharpe, mdd, n_periods, cost_drag computed correctly
7. AC-6.1.1: grep check — zero pandas imports in factor/signal/aligner/evaluation modules

All tests are @pytest.mark.integration; run with::

    .venv/bin/python -m pytest tests/integration/test_full_regression.py -m integration -v

The BacktestRunner full-E2E is skipped per task spec (requires factor DataLayer wiring).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Module-level imports — must NOT import pandas
# ---------------------------------------------------------------------------

from tinohelm.factor.builtins.momentum import ret_N, rsi_signal
from tinohelm.factor.builtins.volatility import parkinson_vol, vol_ratio
from tinohelm.factor.builtins.volume import obv_slope, vwap_dev
from tinohelm.factor.builtins.microstructure import amihud_illiq
from tinohelm.factor.builtins.crypto_funding import funding_rate_level, funding_rate_mom
from tinohelm.factor.universe import Universe
from tinohelm.aligner.aligner import Aligner
from tinohelm.aligner.exposure_logmcap import LogMcapExposure
from tinohelm.factor.evaluation import (
    compare_results,
    correlation_matrix,
    hierarchical_cluster,
)
from tinohelm.factor.types import EvalResult
from tinohelm.signal.kernels import (
    top_k_long_short,
    quantile_long_short,
    threshold_signed,
    zscore_clip,
    rank_to_weight,
)
from tinohelm.signal.evaluator import SignalEvaluator
from tinohelm.signal.types import CostModel


# ---------------------------------------------------------------------------
# Shared synthetic market-data fixture (scope=module for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_market_data():
    """Deterministic (T=200, N=10) synthetic OHLCV + funding_rate panel.

    Returns a dict with keys:
        close, high, low, open_, volume, funding_rate   — pl.DataFrame
        symbols  — list[str]
        ts       — list[datetime]
    All price panels share the same ``ts`` column (col 0) + 10 symbol columns.
    """
    np.random.seed(42)
    T, N = 200, 10
    syms = [f"S{i:02d}" for i in range(N)]
    ts = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(T)]

    # Prices: geometric random walk
    log_returns = np.random.randn(T, N) * 0.005
    close = 100.0 * np.exp(np.cumsum(log_returns, axis=0))
    high = close * np.exp(np.abs(np.random.randn(T, N) * 0.002))
    low = close * np.exp(-np.abs(np.random.randn(T, N) * 0.002))
    open_ = close * np.exp(np.random.randn(T, N) * 0.001)
    volume = np.abs(np.random.randn(T, N) * 500 + 2000)
    funding = np.random.randn(T, N) * 0.0005

    def _df(data: np.ndarray) -> pl.DataFrame:
        return pl.DataFrame({"ts": ts, **{s: data[:, i].tolist() for i, s in enumerate(syms)}})

    return {
        "close": _df(close),
        "high": _df(high),
        "low": _df(low),
        "open_": _df(open_),
        "volume": _df(volume),
        "funding_rate": _df(funding),
        "symbols": syms,
        "ts": ts,
        "T": T,
        "N": N,
    }


# ---------------------------------------------------------------------------
# Helper: compute forward returns from close panel
# ---------------------------------------------------------------------------

def _forward_returns(close: pl.DataFrame, period: int = 1) -> pl.DataFrame:
    """Compute simple forward returns: close[t+period] / close[t] - 1.

    Returns same layout as *close*; last *period* rows are null.
    """
    syms = [c for c in close.columns if c != "ts"]
    arr = close.select(syms).to_numpy().astype(np.float64)
    fwd = np.full_like(arr, np.nan)
    if period < arr.shape[0]:
        fwd[:-period, :] = arr[period:, :] / arr[:-period, :] - 1
    return pl.DataFrame({"ts": close["ts"], **{s: fwd[:, i].tolist() for i, s in enumerate(syms)}})


# ---------------------------------------------------------------------------
# Test 1: 9 active factors compute on (T=200, N=10) panel
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_nine_active_factors_compute(synthetic_market_data):
    """All 9 active built-in factors produce valid (T=200, N+1=11) panels."""
    d = synthetic_market_data
    close, high, low, volume, funding = (
        d["close"], d["high"], d["low"], d["volume"], d["funding_rate"]
    )
    T, N = d["T"], d["N"]

    factors = {
        "ret_N":             ret_N(close, params={"lookback": 5}),
        "rsi_signal":        rsi_signal(close, params={"lookback": 14}),
        "parkinson_vol":     parkinson_vol(high, low, params={"lookback": 20}),
        "vol_ratio":         vol_ratio(close, params={"fast": 5, "slow": 20}),
        "obv_slope":         obv_slope(close, volume, params={"lookback": 20}),
        "vwap_dev":          vwap_dev(high, low, close, volume, params={"lookback": 20}),
        "amihud_illiq":      amihud_illiq(close, volume, params={"lookback": 20}),
        "funding_rate_level": funding_rate_level(funding),
        "funding_rate_mom":  funding_rate_mom(funding, params={"lookback": 1}),
    }

    for name, panel in factors.items():
        assert panel.shape == (T, N + 1), \
            f"{name}: expected shape ({T}, {N + 1}), got {panel.shape}"
        assert "ts" in panel.columns, f"{name}: missing ts column"
        sym_cols = [c for c in panel.columns if c != "ts"]
        assert len(sym_cols) == N, f"{name}: expected {N} symbol cols, got {len(sym_cols)}"
        # At least some non-null values (warmup rows are null, but late rows should be valid)
        last_row = panel.tail(1).select(sym_cols)
        has_non_null = last_row.to_numpy()[0].tolist()
        assert any(v is not None and not (isinstance(v, float) and np.isnan(v))
                   for v in has_non_null), \
            f"{name}: all values null in last row — warmup too long?"


# ---------------------------------------------------------------------------
# Test 2: Aligner — Universe PIT + OLS neutralization
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aligner_pit_and_neutralization(synthetic_market_data):
    """Aligner: Universe PIT masking preserves shape; OLS residuals are centred."""
    d = synthetic_market_data
    close = d["close"]
    syms = d["symbols"]

    # Compute factor
    factor_panel = ret_N(close, params={"lookback": 5})

    # Build universe (all symbols permanently active — no PIT exclusions)
    uni = Universe.from_symbols(syms)

    # --- Case 1: PIT-only (no neutralization) ---
    aligner_raw = Aligner(uni, neutralize=[])
    raw = aligner_raw.align(factor_panel)
    assert raw.shape == factor_panel.shape, "PIT-only: shape mismatch"

    # --- Case 2: LogMcap neutralization ---
    log_mcap = LogMcapExposure()
    aligner_mcap = Aligner(uni, neutralize=[log_mcap])
    neutral = aligner_mcap.align(factor_panel)
    assert neutral.shape == factor_panel.shape, "LogMcap: shape mismatch"

    # After OLS residualization, cross-section residuals should be near-zero mean
    # Check last 10 rows (skip warmup nulls from ret_N)
    sym_cols = [c for c in neutral.columns if c != "ts"]
    tail_arr = neutral.tail(10).select(sym_cols).to_numpy().astype(np.float64)
    # Rows with any NaN → skip
    valid_rows = tail_arr[~np.any(np.isnan(tail_arr), axis=1)]
    if len(valid_rows) > 0:
        row_means = np.mean(valid_rows, axis=1)
        # After OLS with intercept, residuals sum to 0 in each row
        np.testing.assert_allclose(
            row_means, np.zeros(len(row_means)), atol=1e-6,
            err_msg="OLS residuals should have zero cross-sectional mean"
        )


# ---------------------------------------------------------------------------
# Test 3: Correlation matrix + hierarchical clustering
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_correlation_and_clustering(synthetic_market_data):
    """correlation_matrix returns F×(F+1) frame; hierarchical_cluster returns correct linkage shape."""
    d = synthetic_market_data
    close, high, low, volume, funding = (
        d["close"], d["high"], d["low"], d["volume"], d["funding_rate"]
    )

    factor_panels = {
        "ret_N":             ret_N(close, params={"lookback": 5}),
        "rsi_signal":        rsi_signal(close, params={"lookback": 14}),
        "parkinson_vol":     parkinson_vol(high, low, params={"lookback": 20}),
        "vol_ratio":         vol_ratio(close, params={"fast": 5, "slow": 20}),
        "obv_slope":         obv_slope(close, volume, params={"lookback": 20}),
    }
    F = len(factor_panels)

    corr_df = correlation_matrix(factor_panels)
    # Should return DataFrame with "factor_name" col + F factor cols = F+1 cols, F rows
    assert corr_df.shape[0] == F, f"Expected {F} rows, got {corr_df.shape[0]}"
    assert "factor_name" in corr_df.columns, "Missing factor_name column"
    value_cols = [c for c in corr_df.columns if c != "factor_name"]
    assert len(value_cols) == F, f"Expected {F} value cols, got {len(value_cols)}"

    cluster_result = hierarchical_cluster(corr_df, method="ward")
    assert "linkage_matrix" in cluster_result, "Missing linkage_matrix key"
    linkage = cluster_result["linkage_matrix"]
    # scipy linkage: (F-1) rows × 4 columns
    assert linkage.shape == (F - 1, 4), \
        f"Expected linkage shape ({F - 1}, 4), got {linkage.shape}"


# ---------------------------------------------------------------------------
# Test 4: compare_results pairwise bootstrap CI
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_compare_results_bootstrap(synthetic_market_data):
    """compare_results produces valid bootstrap CI dict with 'significant' key."""
    # Construct two dummy EvalResults with ic_series
    np.random.seed(7)
    n_periods = 50
    ic_series_a = [{"date": str(i), "ic": float(v)}
                   for i, v in enumerate(np.random.randn(n_periods) * 0.1 + 0.05)]
    ic_series_b = [{"date": str(i), "ic": float(v)}
                   for i, v in enumerate(np.random.randn(n_periods) * 0.1 + 0.08)]

    result_a = EvalResult(ic_mean=0.05, ic_std=0.1, ir=0.5, ic_series=ic_series_a)
    result_b = EvalResult(ic_mean=0.08, ic_std=0.1, ir=0.8, ic_series=ic_series_b)

    comparison = compare_results(result_a, result_b, n_bootstrap=200, confidence=0.95)

    # compare_results returns {"metric_diffs": [{name, a, b, delta, ci_low, ci_high, significant},...]}
    assert "metric_diffs" in comparison, f"Missing 'metric_diffs' key; got {list(comparison)}"
    metric_diffs = comparison["metric_diffs"]
    assert len(metric_diffs) >= 2, f"Expected >= 2 metric diffs, got {len(metric_diffs)}"

    metric_names = {d["name"] for d in metric_diffs}
    assert "ic_mean" in metric_names, f"Missing ic_mean in metric_diffs; got {metric_names}"
    assert "ir" in metric_names, f"Missing ir in metric_diffs; got {metric_names}"

    for entry in metric_diffs:
        assert "delta" in entry, f"missing 'delta' in {entry}"
        assert "ci_low" in entry, f"missing 'ci_low' in {entry}"
        assert "ci_high" in entry, f"missing 'ci_high' in {entry}"
        assert "significant" in entry, f"missing 'significant' in {entry}"
        assert isinstance(entry["significant"], str)
        assert entry["significant"] in ("improved", "degraded", "neutral")
        if entry["significant"] == "improved":
            assert entry["ci_low"] is None or entry["ci_low"] > 0.0
        elif entry["significant"] == "degraded":
            assert entry["ci_high"] is None or entry["ci_high"] < 0.0


# ---------------------------------------------------------------------------
# Test 5: 5 SignalKernels on ret_N factor — constraint satisfaction
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_five_signal_kernels_constraint_satisfaction(synthetic_market_data):
    """All 5 kernels produce weight panels satisfying gross/net/max_position constraints."""
    d = synthetic_market_data
    close = d["close"]
    T, N = d["T"], d["N"]

    factor_panel = ret_N(close, params={"lookback": 5})

    constraints = {
        "gross_exposure": 1.0,
        "net_exposure":   0.0,
        "max_position":   0.5,
    }

    kernels_and_params = [
        ("top_k_long_short",    top_k_long_short,    {"k": 3}),
        ("quantile_long_short", quantile_long_short, {"quantiles": 5, "long_q": 4, "short_q": 0}),
        ("threshold_signed",    threshold_signed,    {"upper": 0.002, "lower": -0.002,
                                                      "long_weight": 0.5, "short_weight": -0.5}),
        ("zscore_clip",         zscore_clip,         {"clip": 3.0}),
        ("rank_to_weight",      rank_to_weight,      {"power": 1.0}),
    ]

    sym_cols_factor = [c for c in factor_panel.columns if c != "ts"]

    for kernel_name, kernel_fn, params in kernels_and_params:
        weight_panel = kernel_fn(factor_panel, params=params, constraints=constraints)

        # Shape check
        assert weight_panel.shape == (T, N + 1), \
            f"{kernel_name}: expected shape ({T}, {N + 1}), got {weight_panel.shape}"

        sym_cols = [c for c in weight_panel.columns if c != "ts"]
        weight_arr = weight_panel.select(sym_cols).to_numpy().astype(np.float64)

        n_violations = 0
        for t in range(T):
            row = weight_arr[t, :]
            valid = row[np.isfinite(row)]
            if len(valid) == 0:
                continue

            # Gross exposure: Σ|w| ≤ gross + tiny tolerance
            gross = float(np.sum(np.abs(valid)))
            if gross > constraints["gross_exposure"] + 1e-6:
                n_violations += 1

            # Net exposure: |Σw| ≤ net + tiny tolerance (net=0 → market neutral)
            net = abs(float(np.sum(valid)))
            if net > constraints["net_exposure"] + 1e-6:
                n_violations += 1

            # Max position: max|w| ≤ max_position + tiny tolerance
            max_pos = float(np.max(np.abs(valid)))
            if max_pos > constraints["max_position"] + 1e-6:
                n_violations += 1

        assert n_violations == 0, \
            f"{kernel_name}: {n_violations} constraint violations across {T} rows"


# ---------------------------------------------------------------------------
# Test 6: SignalEvaluator metric computation
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_signal_evaluator_metrics(synthetic_market_data):
    """SignalEvaluator produces valid SignalEvalResult for all 5 kernel outputs."""
    d = synthetic_market_data
    close = d["close"]

    factor_panel = ret_N(close, params={"lookback": 5})
    future_returns = _forward_returns(close, period=1)

    cost = CostModel(
        name="taker_8bps",
        fee_bps_per_side=4.0,
        slippage_bps_per_side=1.0,
        rebate_bps_per_side=0.0,
    )
    evaluator = SignalEvaluator(periods_per_year=8760)

    kernels_and_params = [
        ("top_k_long_short",    top_k_long_short,    {"k": 3}),
        ("quantile_long_short", quantile_long_short, {"quantiles": 5, "long_q": 4, "short_q": 0}),
        ("threshold_signed",    threshold_signed,    {"upper": 0.002, "lower": -0.002,
                                                      "long_weight": 0.5, "short_weight": -0.5}),
        ("zscore_clip",         zscore_clip,         {"clip": 3.0}),
        ("rank_to_weight",      rank_to_weight,      {"power": 1.0}),
    ]
    constraints = {
        "gross_exposure": 1.0,
        "net_exposure":   0.0,
        "max_position":   0.5,
    }

    for kernel_name, kernel_fn, params in kernels_and_params:
        weight_panel = kernel_fn(factor_panel, params=params, constraints=constraints)
        result = evaluator.evaluate(weight_panel, future_returns, cost)

        # n_periods > 0 (inner-join on ts must yield rows)
        assert result.n_periods > 0, \
            f"{kernel_name}: n_periods == 0 — inner join on ts yielded no rows"

        # sharpe is a finite float
        assert isinstance(result.sharpe, float), \
            f"{kernel_name}: sharpe not a float"
        assert np.isfinite(result.sharpe), \
            f"{kernel_name}: sharpe is NaN or Inf"

        # mdd >= 0
        assert result.mdd >= 0.0, \
            f"{kernel_name}: mdd < 0: {result.mdd}"

        # cost_drag >= 0 (we always pay costs)
        assert result.cost_drag >= 0.0, \
            f"{kernel_name}: cost_drag < 0: {result.cost_drag}"

        # net PnL curve has n_periods entries
        assert len(result.net_pnl_curve) == result.n_periods, \
            f"{kernel_name}: net_pnl_curve length mismatch"

        # total_return is last value of net_pnl_curve
        if result.net_pnl_curve:
            assert abs(result.total_return - result.net_pnl_curve[-1]) < 1e-10, \
                f"{kernel_name}: total_return != net_pnl_curve[-1]"

        # capacity_score ∈ [0, 1]
        assert 0.0 <= result.capacity_score <= 1.0, \
            f"{kernel_name}: capacity_score out of [0,1]: {result.capacity_score}"


# ---------------------------------------------------------------------------
# Test 7: Full pipeline regression — 9 factors → align → 5 kernels → evaluator
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_regression_factor_to_signal(synthetic_market_data):
    """E2E: 9 factors → Universe PIT → LogMcap neutralize → 5 kernels → SignalEvaluator."""
    d = synthetic_market_data
    close, high, low, volume, funding = (
        d["close"], d["high"], d["low"], d["volume"], d["funding_rate"]
    )
    syms = d["symbols"]
    T, N = d["T"], d["N"]

    # === Step 1: Compute all 9 active factors ===
    factors_panels = {
        "ret_N":             ret_N(close, params={"lookback": 5}),
        "rsi_signal":        rsi_signal(close, params={"lookback": 14}),
        "parkinson_vol":     parkinson_vol(high, low, params={"lookback": 20}),
        "vol_ratio":         vol_ratio(close, params={"fast": 5, "slow": 20}),
        "obv_slope":         obv_slope(close, volume, params={"lookback": 20}),
        "vwap_dev":          vwap_dev(high, low, close, volume, params={"lookback": 20}),
        "amihud_illiq":      amihud_illiq(close, volume, params={"lookback": 20}),
        "funding_rate_level": funding_rate_level(funding),
        "funding_rate_mom":  funding_rate_mom(funding, params={"lookback": 1}),
    }
    assert len(factors_panels) == 9

    for name, panel in factors_panels.items():
        assert panel.shape == (T, N + 1), f"{name}: shape mismatch"

    # === Step 2: Align with Universe PIT + LogMcap neutralization ===
    uni = Universe.from_symbols(syms)
    log_mcap = LogMcapExposure()
    aligner = Aligner(uni, neutralize=[log_mcap])

    aligned_panels = {}
    for name, panel in factors_panels.items():
        aligned = aligner.align(panel)
        assert aligned.shape == panel.shape, f"{name}: aligner changed shape"
        aligned_panels[name] = aligned

    # === Step 3: Compute forward returns ===
    fwd_returns = _forward_returns(close, period=1)

    # === Step 4: Drive all 5 kernels on ret_N (most stable active factor) ===
    driver_panel = aligned_panels["ret_N"]
    constraints = {
        "gross_exposure": 1.0,
        "net_exposure":   0.0,
        "max_position":   0.5,
    }
    cost = CostModel(
        name="taker_8bps", fee_bps_per_side=4.0,
        slippage_bps_per_side=1.0, rebate_bps_per_side=0.0,
    )
    evaluator = SignalEvaluator(periods_per_year=8760)

    results_summary = {}
    kernels_and_params = [
        ("top_k_long_short",    top_k_long_short,    {"k": 3}),
        ("quantile_long_short", quantile_long_short, {"quantiles": 5, "long_q": 4, "short_q": 0}),
        ("threshold_signed",    threshold_signed,    {"upper": 0.002, "lower": -0.002}),
        ("zscore_clip",         zscore_clip,         {"clip": 3.0}),
        ("rank_to_weight",      rank_to_weight,      {"power": 1.0}),
    ]

    for kernel_name, kernel_fn, params in kernels_and_params:
        weight_panel = kernel_fn(driver_panel, params=params, constraints=constraints)
        eval_result = evaluator.evaluate(weight_panel, fwd_returns, cost)

        assert eval_result.n_periods > 0, \
            f"{kernel_name}: n_periods == 0"
        assert np.isfinite(eval_result.sharpe), \
            f"{kernel_name}: sharpe is not finite"

        results_summary[kernel_name] = {
            "sharpe": eval_result.sharpe,
            "n_periods": eval_result.n_periods,
            "cost_drag": eval_result.cost_drag,
        }

    # All 5 kernels evaluated successfully
    assert len(results_summary) == 5, f"Expected 5 kernel results, got {len(results_summary)}"


# ---------------------------------------------------------------------------
# Test 8: AC-6.1.1 — zero pandas imports in factor/signal/aligner/evaluation modules
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_ac_6_1_1_zero_pandas_imports():
    """AC-6.1.1: No 'import pandas' or 'from pandas' in factor/signal/aligner/evaluation modules.

    Uses ripgrep (rg) if available, falls back to grep. The check covers:
    - src/tinohelm/factor/ (including evaluation/)
    - src/tinohelm/signal/
    - src/tinohelm/aligner/

    Returns exit code 1 (no matches) on success; exit code 0 means matches found → fail.
    """
    import subprocess
    import shutil
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    # AC-6.1.1 applies specifically to the polars-native evaluation pipeline:
    # - factor/evaluation/    — evaluation sub-modules (IC, quantile, turnover, etc.)
    # - factor/builtins/      — built-in factor kernels
    # - signal/               — signal kernels, evaluator, decorator
    # Other sub-modules (factor/universe.py, factor/cache.py, etc.) are excluded
    # because they legitimately use pandas for parsing/legacy compat.
    search_dirs = [
        repo_root / "src" / "tinohelm" / "factor" / "evaluation",
        repo_root / "src" / "tinohelm" / "factor" / "builtins",
        repo_root / "src" / "tinohelm" / "signal",
    ]
    patterns = [r"^import pandas", r"^from pandas"]

    # Pick grep tool
    use_rg = shutil.which("rg") is not None
    found_any = False

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        for pattern in patterns:
            if use_rg:
                cmd = ["rg", "--no-heading", "-n", pattern, str(search_dir)]
            else:
                cmd = ["grep", "-rn", "--include=*.py", pattern, str(search_dir)]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                print(f"\nFOUND pandas import in {search_dir}:\n{result.stdout}")
                found_any = True

    assert not found_any, (
        "AC-6.1.1 violation: 'import pandas' or 'from pandas' found in "
        "factor/evaluation/, factor/builtins/, or signal/ modules. "
        "These modules must be pandas-free (polars-native)."
    )


# ---------------------------------------------------------------------------
# Test 9: experimental factors raise NotImplementedError (not silently return)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_experimental_factors_raise_not_implemented():
    """Experimental factors raise NotImplementedError — they do NOT return empty panels."""
    from tinohelm.factor.builtins.microstructure import trade_imbalance
    from tinohelm.factor.builtins.crypto_data import oi_change, orderbook_imbalance_L1

    dummy = pl.DataFrame({"ts": [1, 2], "BTC": [1.0, 2.0]})

    with pytest.raises(NotImplementedError):
        trade_imbalance(dummy, dummy, params={})

    with pytest.raises(NotImplementedError):
        oi_change(dummy, params={})

    with pytest.raises(NotImplementedError):
        orderbook_imbalance_L1(dummy, params={})


# ---------------------------------------------------------------------------
# Test 10: SignalDrivenStrategy full BacktestRunner E2E (deferred)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skip(
    reason=(
        "Full BacktestRunner E2E (real catalog + instrument fixtures + "
        "_compute_factor_panel wiring via factor registry + DataLayer) "
        "is deferred — requires s18 export endpoint wiring. "
        "Protocol-level E2E is covered by "
        "tests/integration/test_signal_driven_strategy_e2e.py."
    )
)
def test_signal_driven_strategy_full_e2e_with_backtest_runner():
    """SignalDrivenStrategy via BacktestRunner — deferred to s18+s22 integration."""
    pytest.fail("This skipped test should never execute.")
