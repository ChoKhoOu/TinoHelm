"""Parameter scanning — 1D sweep and 2D heatmap with parallel execution."""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def _sweep_worker(args: tuple) -> dict:
    """Worker for single parameter evaluation (top-level for pickling)."""
    factor_name, df_bytes, param_name, param_value, base_params, forward_period = args
    try:
        import pickle
        from tinohelm.research.factors import compute_factor
        from tinohelm.research.analysis import forward_returns

        df = pickle.loads(df_bytes)
        params = dict(base_params)
        params[param_name] = param_value

        sig = compute_factor(factor_name, df, params)
        fwd = forward_returns(df["close"], forward_period)

        paired = pd.DataFrame({"f": sig, "r": fwd}).dropna()
        paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]

        if len(paired) < 30:
            return {"value": param_value, "ic": 0}

        ic, _ = spearmanr(paired["f"], paired["r"])
        return {"value": param_value, "ic": round(float(ic), 6) if np.isfinite(ic) else 0}
    except Exception as exc:
        return {"value": param_value, "ic": 0, "error": str(exc)}


def sweep_1d(
    factor_name: str,
    df: pd.DataFrame,
    param_name: str,
    values: list[int | float],
    base_params: dict,
    forward_period: int = 5,
    max_workers: int = 4,
) -> list[dict]:
    """Single parameter sweep: IC for each parameter value."""
    import pickle
    df_bytes = pickle.dumps(df)

    args_list = [
        (factor_name, df_bytes, param_name, v, base_params, forward_period)
        for v in values
    ]

    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_sweep_worker, a): a[3] for a in args_list}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda x: x["value"])
    return results


def _heatmap_worker(args: tuple) -> dict:
    """Worker for 2D parameter evaluation."""
    factor_name, df_bytes, p1_name, p1_val, p2_name, p2_val, base_params, forward_period = args
    try:
        import pickle
        from tinohelm.research.factors import compute_factor
        from tinohelm.research.analysis import forward_returns

        df = pickle.loads(df_bytes)
        params = dict(base_params)
        params[p1_name] = p1_val
        params[p2_name] = p2_val

        sig = compute_factor(factor_name, df, params)
        fwd = forward_returns(df["close"], forward_period)

        paired = pd.DataFrame({"f": sig, "r": fwd}).dropna()
        paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]

        if len(paired) < 30:
            return {"p1": p1_val, "p2": p2_val, "ic": 0}

        ic, _ = spearmanr(paired["f"], paired["r"])
        return {"p1": p1_val, "p2": p2_val, "ic": round(float(ic), 6) if np.isfinite(ic) else 0}
    except Exception as exc:
        return {"p1": p1_val, "p2": p2_val, "ic": 0, "error": str(exc)}


def build_ic_matrix(
    results: list[dict],
    param1_values: list[int | float],
    param2_values: list[int | float],
) -> list[list[float]]:
    """Pivot heatmap worker results into a row-major IC matrix.

    ``results`` may arrive in any order (futures.as_completed). We index by
    ``(p1, p2)`` and emit a ``len(param1_values) × len(param2_values)`` matrix,
    filling missing cells with 0.0 so downstream Plotly heatmaps don't choke
    on holes (a worker may have failed and dropped a cell).

    Pure function; no IO. Lives at module scope so it's testable independently
    of the ProcessPoolExecutor that produces ``results``.
    """
    # O(n) lookup, not O(n²) per cell.
    by_pair: dict[tuple, float] = {}
    for r in results:
        if "p1" in r and "p2" in r:
            by_pair[(r["p1"], r["p2"])] = float(r.get("ic", 0))

    return [
        [by_pair.get((p1, p2), 0.0) for p2 in param2_values]
        for p1 in param1_values
    ]


def sweep_2d(
    factor_name: str,
    df: pd.DataFrame,
    param1_name: str,
    param1_values: list[int | float],
    param2_name: str,
    param2_values: list[int | float],
    base_params: dict,
    forward_period: int = 5,
    max_workers: int = 4,
) -> dict:
    """2D parameter heatmap: IC for each (param1, param2) combination."""
    import pickle
    df_bytes = pickle.dumps(df)

    args_list = [
        (factor_name, df_bytes, param1_name, p1, param2_name, p2, base_params, forward_period)
        for p1 in param1_values
        for p2 in param2_values
    ]

    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_heatmap_worker, a) for a in args_list]
        for fut in as_completed(futures):
            results.append(fut.result())

    return {
        "param1": param1_name,
        "param2": param2_name,
        "values1": param1_values,
        "values2": param2_values,
        "ic_matrix": build_ic_matrix(results, param1_values, param2_values),
    }
