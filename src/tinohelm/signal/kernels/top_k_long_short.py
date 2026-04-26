"""Top-K long / bottom-K short signal kernel.

Each timestamp picks the ``k`` symbols with the highest factor values to
buy with weight ``+1/k`` and the ``k`` symbols with the lowest factor
values to sell with weight ``-1/k``.  Asymmetric weight handling between
long and short halves is left to :func:`normalize_to_constraints` (e.g.
when ``net_exposure > 0``).

This is the canonical "rank-based" decile/centile strategy used as a
baseline in factor research.  Time complexity per row is O(N log N) —
acceptable for typical universes (≤ 200 symbols).

Parameters (``params``)
-----------------------
k:
    Number of symbols on each side.  Default 3.  ``2 * k`` symbols must
    have non-NaN factor values for the row to receive any weights;
    otherwise the row is skipped (left at 0).

Notes
-----
NaN rows or rows with insufficient valid factor values produce all-zero
weight rows — :func:`normalize_to_constraints` then maps the all-zero
input to all-zero output (the sole stable fixed point).
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


def top_k_long_short(
    factor_panel: pl.DataFrame,
    params: dict[str, Any],
    constraints: dict[str, float],
) -> pl.DataFrame:
    """Equal-weight long top-K, short bottom-K.

    Parameters
    ----------
    factor_panel:
        Input factor panel: column ``"ts"`` plus N symbol columns.
    params:
        Kernel-specific parameters.  Recognised keys:

        * ``"k"`` — top-K / bottom-K count.  Default ``3``.
    constraints:
        Position constraints with keys ``gross_exposure`` /
        ``net_exposure`` / ``max_position``.

    Returns
    -------
    pl.DataFrame
        Same layout as ``factor_panel``: ``"ts"`` + N weight columns.
    """
    k = int(params.get("k", 3))
    if k <= 0:
        raise ValueError(f"top_k_long_short k must be > 0, got {k!r}")

    ts_col, factor_arr, sym_cols = split_factor_panel(factor_panel)
    T, N = factor_arr.shape
    weights = np.zeros_like(factor_arr)

    for t in range(T):
        row = factor_arr[t, :]
        valid_mask = np.isfinite(row)
        if int(valid_mask.sum()) < 2 * k:
            continue
        valid_indices = np.where(valid_mask)[0]
        # argsort descending: row[valid] sorted desc gives top-K first
        order = np.argsort(-row[valid_indices], kind="stable")
        long_idx = valid_indices[order[:k]]
        short_idx = valid_indices[order[-k:]]
        weights[t, long_idx] = 1.0 / k
        weights[t, short_idx] = -1.0 / k

    weights = normalize_to_constraints(weights, **constraints)
    return build_weight_panel(ts_col, weights, sym_cols)
