"""Threshold-based signed signal kernel.

A simple signal that buys when the factor exceeds an upper threshold and
sells when it falls below a lower threshold; otherwise stays flat.
Useful for momentum / mean-reversion factors with a clear neutral zone.

Parameters (``params``)
-----------------------
upper:
    Upper threshold.  Factor values strictly greater receive ``long_weight``.
    Default ``0.5``.
lower:
    Lower threshold.  Factor values strictly less receive ``short_weight``.
    Default ``-0.5``.
long_weight:
    Weight assigned to "long" cells.  Default ``0.5`` (positive).
short_weight:
    Weight assigned to "short" cells.  Default ``-0.5`` (negative).

Notes
-----
NaN cells map to zero weights (np.where conditions evaluate to False).
The output is then handed to :func:`normalize_to_constraints` so the
final panel respects gross/net/per-asset bounds even when many cells
breach a threshold simultaneously.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from tinohelm.signal.kernel import (
    build_weight_panel,
    normalize_to_constraints,
    split_factor_panel,
)


def threshold_signed(
    factor_panel: pl.DataFrame,
    params: dict[str, Any],
    constraints: dict[str, float],
) -> pl.DataFrame:
    """Apply long/short weights when factor crosses thresholds.

    Parameters
    ----------
    factor_panel:
        ``"ts"`` + N symbol columns.
    params:
        Recognised keys: ``"upper"``, ``"lower"``, ``"long_weight"``,
        ``"short_weight"``.
    constraints:
        Position-constraint dict.

    Returns
    -------
    pl.DataFrame
        Weight panel with the same layout as ``factor_panel``.
    """
    upper = float(params.get("upper", 0.5))
    lower = float(params.get("lower", -0.5))
    long_w = float(params.get("long_weight", 0.5))
    short_w = float(params.get("short_weight", -0.5))
    if lower > upper:
        raise ValueError(
            f"threshold_signed lower ({lower!r}) must be <= upper ({upper!r})"
        )

    ts_col, factor_arr, sym_cols = split_factor_panel(factor_panel)

    # Mask NaN before comparison so np.where doesn't fall into the "else"
    # branch (which would assign zero, but we need to preserve NaN
    # downstream so normalize_to_constraints knows which cells are
    # missing).  Two-step: compute weights treating NaN as zero, then
    # restore NaNs.
    safe = np.where(np.isfinite(factor_arr), factor_arr, 0.0)
    weights = np.where(
        safe > upper,
        long_w,
        np.where(safe < lower, short_w, 0.0),
    )
    # Re-introduce NaNs from the original panel so the missing-asset
    # signal is preserved.
    weights = np.where(np.isfinite(factor_arr), weights, np.nan)

    weights = normalize_to_constraints(weights, **constraints)
    return build_weight_panel(ts_col, weights, sym_cols)
