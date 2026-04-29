"""Params-grid search: cartesian product + joblib parallel + IR rank + corr-filter top-K.

Public API
----------
``params_grid(factor_fn, base_panel_data, grid, forward_returns, *, top_k, corr_filter,
              n_jobs, eval_config) -> list[dict]``

Each returned dict contains::

    {
        "params": dict,          # the param combination
        "ic_mean": float | None,
        "ir": float | None,
        "ic_series": list[dict], # used internally for corr-filter, also exposed
        "panel": pl.DataFrame,   # the raw factor panel produced by factor_fn
    }

Algorithm
---------
1. Generate all (grid) Cartesian-product combinations.
2. Parallel-evaluate each via ``Evaluator._evaluate_core`` — produces IC / IR.
3. Rank candidates by IR descending (None / NaN treated as 0).
4. Iterative top-K selection with Pearson corr-filter on ic_series values:
   skip candidate if |corr(ic_series_candidate, ic_series_selected)| > corr_filter.
5. Return up to ``top_k`` non-correlated candidates.

No pandas imports at module top (AC-1 contract of the evaluation package).
"""
from __future__ import annotations

import dataclasses
import math
from itertools import product
from typing import Any, Callable

import numpy as np
import polars as pl
from joblib import Parallel, delayed

from tinohelm.factor.evaluation.evaluator import Evaluator, _to_ts_value
from tinohelm.factor.types import EvalConfig, EvalResult


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_ir(result: EvalResult) -> float:
    """Return IR as a finite float; fall back to 0.0 for None / NaN / Inf."""
    val = result.ir
    if val is None:
        return 0.0
    try:
        f = float(val)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _ic_series_to_array(ic_series: list[dict]) -> np.ndarray:
    """Extract IC values from the canonical ``[{"date": ..., "ic": float}]`` list.

    Returns a 1-D float64 ndarray with NaN dropped (same length as valid rows).
    Returns an empty array when ic_series is empty or contains no finite values.
    """
    if not ic_series:
        return np.array([], dtype=np.float64)
    vals = []
    for row in ic_series:
        v = row.get("ic")
        if v is not None and math.isfinite(v):
            vals.append(float(v))
    return np.array(vals, dtype=np.float64)


def _pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation between two 1-D arrays; 0.0 when any array is empty or constant.

    Aligns arrays by truncating to the shorter length before computing.
    """
    min_len = min(len(a), len(b))
    if min_len < 2:
        return 0.0
    a = a[:min_len]
    b = b[:min_len]
    std_a = float(np.std(a))
    std_b = float(np.std(b))
    if std_a < 1e-12 or std_b < 1e-12:
        return 0.0
    corr = float(np.corrcoef(a, b)[0, 1])
    return corr if math.isfinite(corr) else 0.0


# ---------------------------------------------------------------------------
# Worker — must be picklable (top-level function, not a lambda)
# ---------------------------------------------------------------------------

def _eval_one(
    factor_fn: Callable[..., pl.DataFrame],
    base_panel_data: dict[str, Any],
    params: dict[str, Any],
    forward_returns_df: pl.DataFrame,
    eval_config: EvalConfig,
) -> dict:
    """Evaluate one parameter combination.

    Calls ``factor_fn(**base_panel_data, **params)`` to get a factor panel,
    then runs ``Evaluator._evaluate_core`` to compute IC / IR.

    Returns a dict::

        {
            "params": dict,
            "ic_mean": float,
            "ir": float,
            "ic_series": list[dict],
            "panel": pl.DataFrame,
        }
    """
    panel: pl.DataFrame = factor_fn(**base_panel_data, **params)
    evaluator = Evaluator()
    result, _, _, _ = evaluator._evaluate_core(panel, forward_returns_df, eval_config)
    return {
        "params": params,
        "ic_mean": result.ic_mean,
        "ir": _safe_ir(result),
        "ic_series": result.ic_series,
        "panel": panel,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def params_grid(
    factor_fn: Callable[..., pl.DataFrame],
    base_panel_data: dict[str, Any],
    grid: dict[str, list[Any]],
    forward_returns_df: pl.DataFrame,
    *,
    top_k: int = 3,
    corr_filter: float = 0.7,
    n_jobs: int = -1,
    eval_config: EvalConfig | None = None,
) -> list[dict]:
    """Cartesian-product grid search with parallel evaluation, IR ranking, and corr-filter top-K.

    Parameters
    ----------
    factor_fn:
        Callable that accepts ``**base_panel_data`` merged with one param combination
        and returns a factor panel (``pl.DataFrame`` with a ``ts`` column).
    base_panel_data:
        Fixed keyword arguments forwarded to ``factor_fn`` on every call
        (e.g. close-price panel, volume panel).
    grid:
        Mapping of parameter name → list of candidate values.  The full Cartesian
        product of all value lists is evaluated.  Example::

            {"lookback": [5, 10, 20], "smooth": [1, 3]}  # → 6 combinations

    forward_returns_df:
        Pre-built forward-return panel passed to ``Evaluator._evaluate_core``.
        Must be a ``pl.DataFrame`` compatible with ``_to_ts_value``.
    top_k:
        Maximum number of candidates to return (after corr-filter).
    corr_filter:
        Maximum allowed Pearson correlation between ic_series of any two selected
        candidates.  Set ``> 1.0`` (e.g. 1.1) to disable filtering.
    n_jobs:
        Joblib ``n_jobs``.  ``-1`` = all logical cores, ``1`` = serial (useful for
        debugging or small grids where fork overhead exceeds eval time).
    eval_config:
        ``EvalConfig`` forwarded to ``Evaluator._evaluate_core``.  Defaults to a
        minimal config when ``None`` (daily IC freq, 5-bar forward period, empty
        universe — suitable for unit tests).

    Returns
    -------
    list[dict]
        Up to ``top_k`` dicts sorted by IR descending, each containing
        ``params``, ``ic_mean``, ``ir``, ``ic_series``, and ``panel``.
    """
    if eval_config is None:
        eval_config = EvalConfig(universe=(), start="", end="")
    eval_config = dataclasses.replace(eval_config, returns_kind="forward_returns")

    # --- Build Cartesian product ---
    keys = list(grid.keys())
    combos: list[dict[str, Any]] = []
    if keys:
        value_lists = [grid[k] for k in keys]
        combos = [dict(zip(keys, vs)) for vs in product(*value_lists)]
    else:
        combos = [{}]

    if not combos:
        return []

    # --- Parallel evaluation ---
    results: list[dict] = Parallel(n_jobs=n_jobs, backend="loky")(
        delayed(_eval_one)(factor_fn, base_panel_data, combo, forward_returns_df, eval_config)
        for combo in combos
    )

    # --- Rank by IR descending ---
    ranked = sorted(results, key=lambda r: -(r.get("ir") or 0.0))

    # --- Iterative top-K with corr-filter ---
    selected: list[dict] = []
    for cand in ranked:
        if len(selected) >= top_k:
            break
        cand_arr = _ic_series_to_array(cand.get("ic_series") or [])
        is_correlated = False
        for sel in selected:
            sel_arr = _ic_series_to_array(sel.get("ic_series") or [])
            corr = _pearson_corr(cand_arr, sel_arr)
            if abs(corr) > corr_filter:
                is_correlated = True
                break
        if not is_correlated:
            selected.append(cand)

    return selected


__all__ = ["params_grid"]
