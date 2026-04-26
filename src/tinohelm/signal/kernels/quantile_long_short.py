"""Quantile long-short signal kernel.

Each timestamp partitions the universe into ``quantiles`` buckets by
factor value.  Symbols in ``long_q`` (default = top quantile, i.e.
``quantiles - 1``) are bought with equal weight; symbols in ``short_q``
(default = bottom quantile, i.e. ``0``) are sold with equal weight.

Equivalent to a generalised top-K kernel — when ``quantiles`` divides
``N`` evenly, ``top_k_long_short(k=N // quantiles)`` produces matching
signs on the same symbols.  Quantile bucket boundaries are computed via
rank-based binning (same convention as ``pandas.qcut``) so ties are
broken stably.

Parameters (``params``)
-----------------------
quantiles:
    Number of buckets.  Default ``5``.
long_q:
    Bucket index to buy.  ``0`` is the lowest, ``quantiles - 1`` is the
    highest.  Default = ``quantiles - 1`` (top bucket).
short_q:
    Bucket index to sell.  Default ``0`` (bottom bucket).

Notes
-----
The number of valid (non-NaN) symbols at each timestamp must be
≥ ``quantiles``; otherwise the row produces zero weights.
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


def quantile_long_short(
    factor_panel: pl.DataFrame,
    params: dict[str, Any],
    constraints: dict[str, float],
) -> pl.DataFrame:
    """Long top-quantile, short bottom-quantile, equal-weight within each.

    Parameters
    ----------
    factor_panel:
        ``"ts"`` + N symbol columns.
    params:
        Recognised keys:

        * ``"quantiles"`` — bucket count.  Default ``5``.
        * ``"long_q"`` — bucket to buy.  Default = top.
        * ``"short_q"`` — bucket to sell.  Default ``0``.
    constraints:
        Position-constraint dict.

    Returns
    -------
    pl.DataFrame
        Weight panel with the same layout as ``factor_panel``.
    """
    n_quantiles = int(params.get("quantiles", 5))
    if n_quantiles < 2:
        raise ValueError(
            f"quantile_long_short quantiles must be >= 2, got {n_quantiles!r}"
        )
    long_q = int(params.get("long_q", n_quantiles - 1))
    short_q = int(params.get("short_q", 0))
    if not (0 <= long_q < n_quantiles):
        raise ValueError(
            f"long_q must be in [0, {n_quantiles}), got {long_q!r}"
        )
    if not (0 <= short_q < n_quantiles):
        raise ValueError(
            f"short_q must be in [0, {n_quantiles}), got {short_q!r}"
        )

    ts_col, factor_arr, sym_cols = split_factor_panel(factor_panel)
    T, N = factor_arr.shape
    weights = np.zeros_like(factor_arr)

    for t in range(T):
        row = factor_arr[t, :]
        valid_mask = np.isfinite(row)
        n_valid = int(valid_mask.sum())
        if n_valid < n_quantiles:
            continue
        valid_indices = np.where(valid_mask)[0]
        valid = row[valid_indices]
        # Rank-based bucket assignment: argsort gives ascending rank.
        # Convert ranks into [0, n_quantiles - 1] bucket indices.
        ranks = np.argsort(np.argsort(valid, kind="stable"), kind="stable")
        bins = (ranks * n_quantiles // n_valid).astype(int)
        bins = np.clip(bins, 0, n_quantiles - 1)

        long_local = bins == long_q
        short_local = bins == short_q
        long_indices = valid_indices[long_local]
        short_indices = valid_indices[short_local]
        if long_indices.size:
            weights[t, long_indices] = 1.0 / long_indices.size
        if short_indices.size:
            weights[t, short_indices] = -1.0 / short_indices.size

    weights = normalize_to_constraints(weights, **constraints)
    return build_weight_panel(ts_col, weights, sym_cols)
