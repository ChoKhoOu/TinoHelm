"""Cross-section z-score / clip signal kernel.

For each timestamp, compute the cross-section z-score of the factor
panel (mean-centred, std-scaled across the universe), clip to
``[-clip, +clip]``, then divide by ``clip`` so values land in
``[-1, +1]``.  This produces a continuous-valued weight before the
final normalisation step.

Parameters (``params``)
-----------------------
clip:
    Maximum absolute z-score.  Default ``3.0``.

Notes
-----
* Sample standard deviation uses ``ddof=1`` (Bessel-corrected) to match
  most quantitative-research conventions.
* Rows with fewer than 2 valid (non-NaN) cells, or with zero variance,
  pass through as all-zero weights.  Constraint normalisation maps zero
  to zero, so these rows produce no positions.
* NaN positions are preserved across the transform.
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


def zscore_clip(
    factor_panel: pl.DataFrame,
    params: dict[str, Any],
    constraints: dict[str, float],
) -> pl.DataFrame:
    """Cross-section z-score, clipped and rescaled to ``[-1, +1]``.

    Parameters
    ----------
    factor_panel:
        ``"ts"`` + N symbol columns.
    params:
        Recognised keys: ``"clip"`` (default ``3.0``).
    constraints:
        Position-constraint dict.

    Returns
    -------
    pl.DataFrame
        Weight panel with the same layout as ``factor_panel``.
    """
    clip_val = float(params.get("clip", 3.0))
    if clip_val <= 0:
        raise ValueError(f"zscore_clip clip must be > 0, got {clip_val!r}")

    ts_col, factor_arr, sym_cols = split_factor_panel(factor_panel)
    T, _ = factor_arr.shape
    weights = np.zeros_like(factor_arr)

    for t in range(T):
        row = factor_arr[t, :]
        valid_mask = np.isfinite(row)
        if int(valid_mask.sum()) < 2:
            # Need at least 2 observations for ddof=1 std.
            weights[t, :] = np.where(valid_mask, 0.0, np.nan)
            continue
        valid = row[valid_mask]
        mu = float(np.mean(valid))
        sd = float(np.std(valid, ddof=1))
        if sd == 0.0 or not np.isfinite(sd):
            weights[t, :] = np.where(valid_mask, 0.0, np.nan)
            continue
        z = (valid - mu) / sd
        z = np.clip(z, -clip_val, clip_val) / clip_val
        # Insert into the right cells; NaN cells stay NaN.
        new_row = np.full_like(row, np.nan)
        new_row[valid_mask] = z
        weights[t, :] = new_row

    weights = normalize_to_constraints(weights, **constraints)
    return build_weight_panel(ts_col, weights, sym_cols)
