"""Robustness tests — shuffle test, subsample IC, cross-symbol IC.

Migrated to polars-native input handling. The deterministic / pure
helpers (``summarize_shuffle_distribution``, ``_single_shuffle_ic``) keep
their numpy-array contract since they consume already-flattened factor /
forward-return arrays. Higher-level entry points (``shuffle_test``,
``subsample_ic``) now accept the same 2-col ``[ts, value]``
:class:`pl.DataFrame` shape used elsewhere in the evaluation package.

``cross_symbol_ic`` retains its factor_name + factor_params + symbols
tuple since it reloads bar data per symbol and recomputes the factor
internally; the only polars-related change is that the kernel runs on
:class:`pl.DataFrame` panels instead of pandas.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from tinohelm.factor.evaluation.ic import _build_paired, _to_polars_freq

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level globals for ProcessPoolExecutor initializer pattern.
# Each worker process creates a Registry once in _init_worker() and reuses
# it for every task, avoiding redundant scan() calls per symbol.
# ---------------------------------------------------------------------------
_worker_registry: Any = None


def _init_worker() -> None:
    """ProcessPoolExecutor initializer — build Registry once per worker."""
    global _worker_registry  # noqa: PLW0603
    from tinohelm.factor.registry import Registry

    _worker_registry = Registry()
    _worker_registry.scan()


# Public threshold for "significant" — p-value strict-less-than this is significant.
# Kept identical to the legacy ``research.robustness`` constant so tests +
# frontend can reference the same value.
SHUFFLE_SIGNIFICANCE_THRESHOLD = 0.05

# Minimum paired observations required to compute a meaningful real IC. Below
# this we short-circuit to a "no-signal" payload rather than running the
# (expensive) ProcessPoolExecutor.
SHUFFLE_MIN_OBSERVATIONS = 100


def _single_shuffle_ic(args: tuple) -> float:
    """Worker function for shuffle test (must be top-level for pickling)."""
    factor_vals, fwd_vals, seed = args
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(factor_vals)
    ic, _ = spearmanr(shuffled, fwd_vals)
    return float(ic) if np.isfinite(ic) else 0.0


def summarize_shuffle_distribution(
    real_ic: float,
    shuffle_ics: list[float] | np.ndarray,
    bins: int = 50,
) -> dict:
    """Aggregate shuffle-test math into the wire-format payload — pure, no IO.

    Splitting this out lets us unit-test the histogram + p-value contract
    without spinning up a ProcessPoolExecutor (which is slow and brittle in
    tests).

    Returns the same dict shape as ``shuffle_test``:
        ``{real_ic, shuffle_distribution, p_value, significant}``
    """
    arr = np.asarray(list(shuffle_ics), dtype=float)
    real_ic = float(real_ic) if np.isfinite(real_ic) else 0.0

    if arr.size == 0:
        return {
            "real_ic": round(real_ic, 6),
            "shuffle_distribution": [],
            "p_value": 1.0,
            "significant": False,
        }

    p_value = float(np.mean(np.abs(arr) >= abs(real_ic)))
    counts, edges = np.histogram(arr, bins=bins)
    distribution = [
        {
            "bin_start": round(float(edges[i]), 6),
            "bin_end": round(float(edges[i + 1]), 6),
            "count": int(counts[i]),
        }
        for i in range(len(counts))
    ]

    return {
        "real_ic": round(real_ic, 6),
        "shuffle_distribution": distribution,
        "p_value": round(p_value, 4),
        "significant": p_value < SHUFFLE_SIGNIFICANCE_THRESHOLD,
    }


def shuffle_test(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    n_iter: int = 1000,
    max_workers: int = 4,
) -> dict:
    """Shuffle factor values N times, compute null IC distribution.

    Returns ``{real_ic, shuffle_distribution, p_value, significant}``. Below
    ``SHUFFLE_MIN_OBSERVATIONS`` valid pairs the function short-circuits to
    a no-signal payload — the ProcessPool is never spawned.
    """
    paired = _build_paired(factor, fwd_ret)

    if paired.height < SHUFFLE_MIN_OBSERVATIONS:
        return {"real_ic": 0, "shuffle_distribution": [], "p_value": 1.0, "significant": False}

    f_vals = paired["factor"].to_numpy()
    r_vals = paired["fwd_ret"].to_numpy()

    real_ic, _ = spearmanr(f_vals, r_vals)
    real_ic = float(real_ic) if np.isfinite(real_ic) else 0.0

    args_list = [(f_vals, r_vals, seed) for seed in range(n_iter)]
    shuffle_ics: list[float] = []

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_single_shuffle_ic, a) for a in args_list]
        for fut in as_completed(futures):
            shuffle_ics.append(fut.result())

    return summarize_shuffle_distribution(real_ic, shuffle_ics)


def subsample_ic(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    freq: str = "ME",
) -> list[dict]:
    """Compute IC per time segment (monthly by default).

    Groups with < 20 observations or non-finite IC are skipped. Returns a
    list of ``{"period": "YYYY-MM", "ic": float}`` dicts.
    """
    paired = _build_paired(factor, fwd_ret)

    if paired.height == 0:
        return []

    bucket = pl.col("ts").dt.truncate(_to_polars_freq(freq)).alias("bucket")
    paired_bucketed = paired.with_columns(bucket)

    results: list[dict] = []
    for (bucket_dt,), group in paired_bucketed.group_by(["bucket"], maintain_order=True):
        if group.height < 20:
            continue
        ic_arr = group.select(
            pl.corr(pl.col("factor"), pl.col("fwd_ret"), method="spearman").alias("ic")
        )
        ic_val = ic_arr.item()
        if ic_val is None or not np.isfinite(ic_val):
            continue
        # ``bucket_dt`` is a python datetime → strftime mirrors legacy.
        period_str = bucket_dt.strftime("%Y-%m") if hasattr(bucket_dt, "strftime") else str(bucket_dt)
        results.append({
            "period": period_str,
            "ic": round(float(ic_val), 6),
        })

    return results


def _cross_symbol_worker(args: tuple) -> dict:
    """Worker for cross-symbol IC (top-level for pickling).

    Loads bar data via DataLayer and computes the factor signal using the
    declarative factor registry. Reuses the module-level ``_worker_registry``
    created by ``_init_worker()`` to avoid rebuilding the Registry per symbol.

    The kernel is called with the same convention as
    :meth:`Scheduler._call_kernel`: ``kernel(**factor_data)`` where
    ``factor_data`` maps ``input_spec.field_name → Panel``. ``params`` is
    injected when the kernel's spec declares a ``params`` parameter.
    """
    symbol, factor_name, factor_params, interval, start, end, forward_period, catalog_path = args
    try:
        from tinohelm.factor.data_layer import DataLayer
        from tinohelm.factor.evaluation.ic import forward_returns
        from tinohelm.factor.types import DataRequest
        from tinohelm.factor.universe import Universe

        global _worker_registry  # noqa: PLW0602

        universe_obj = Universe.from_symbols([symbol])
        catalog_root = __import__("pathlib").Path(catalog_path) if catalog_path else None
        layer = DataLayer(universe_obj, catalog_root=catalog_root)
        panels = layer.load(
            [
                DataRequest(symbol=symbol, field_name=f, frequency=interval, source="bar")
                for f in ("close", "open", "high", "low", "volume")
            ],
            start=start,
            end=end,
        )

        close_panel = panels.get("close")
        if close_panel is None or len(close_panel) < 100:
            return {"symbol": symbol, "ic": 0, "n_obs": 0}

        # Resolve factor kernel + spec via the worker-level registry.
        registry = _worker_registry
        kernel = registry.get_kernel(factor_name)
        spec = registry.get_spec(factor_name)

        factor_data: dict[str, Any] = {}
        if spec is not None:
            for inp in spec.input_specs:
                if inp.field_name in panels:
                    factor_data[inp.field_name] = panels[inp.field_name]
        else:
            factor_data["close"] = close_panel

        # Inject params if the kernel accepts it (legacy convention used by
        # all built-in factors: ``def ret_N(close, params=None)``).
        import inspect
        sig = inspect.signature(kernel)
        if "params" in sig.parameters:
            factor_data["params"] = factor_params  # type: ignore[assignment]

        sig_panel = kernel(**factor_data)
        # The kernel returns a Panel with the symbol column. Extract the
        # 2-col ``[ts, value]`` view used by ``forward_returns`` /
        # ``_build_paired``.  Fallback: pick the first non-``ts`` column.
        cols = [c for c in sig_panel.columns if c != "ts"]
        if symbol in cols:
            value_col = symbol
        elif cols:
            value_col = cols[0]
        else:
            return {"symbol": symbol, "ic": 0, "n_obs": 0}
        sig_df = sig_panel.select([
            pl.col("ts"),
            pl.col(value_col).alias("value"),
        ]) if "ts" in sig_panel.columns else None
        if sig_df is None:
            return {"symbol": symbol, "ic": 0, "n_obs": 0}

        close_cols = [c for c in close_panel.columns if c != "ts"]
        close_value_col = symbol if symbol in close_cols else close_cols[0]
        close_df = close_panel.select([
            pl.col("ts"),
            pl.col(close_value_col).alias("value"),
        ])
        fwd_df = forward_returns(close_df, forward_period)

        paired = _build_paired(sig_df, fwd_df)

        if paired.height < 30:
            return {"symbol": symbol, "ic": 0, "n_obs": paired.height}

        f_vals = paired["factor"].to_numpy()
        r_vals = paired["fwd_ret"].to_numpy()
        ic, _ = spearmanr(f_vals, r_vals)
        return {
            "symbol": symbol,
            "ic": round(float(ic), 6) if np.isfinite(ic) else 0,
            "n_obs": paired.height,
        }
    except Exception as exc:
        logger.warning("Cross-symbol IC failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "ic": 0, "n_obs": 0, "error": str(exc)}


def cross_symbol_ic(
    factor_name: str,
    factor_params: dict,
    symbols: list[str],
    interval: str = "1m",
    start: str | None = None,
    end: str | None = None,
    forward_period: int = 5,
    catalog_path: str | None = None,
    max_workers: int = 4,
) -> list[dict]:
    """Compute IC for the same factor across multiple symbols in parallel.

    Results are sorted by absolute IC descending.
    """
    args_list = [
        (sym, factor_name, factor_params, interval, start, end, forward_period, catalog_path)
        for sym in symbols
    ]

    results = []
    with ProcessPoolExecutor(max_workers=max_workers, initializer=_init_worker) as pool:
        futures = {pool.submit(_cross_symbol_worker, a): a[0] for a in args_list}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda x: abs(x.get("ic", 0)), reverse=True)
    return results


__all__ = [
    "SHUFFLE_MIN_OBSERVATIONS",
    "SHUFFLE_SIGNIFICANCE_THRESHOLD",
    "_single_shuffle_ic",
    "cross_symbol_ic",
    "shuffle_test",
    "subsample_ic",
    "summarize_shuffle_distribution",
]
