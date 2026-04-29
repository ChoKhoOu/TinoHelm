"""Rank-percentile-to-weight signal kernel.

Each timestamp converts factor values to centered rank percentiles in
``[-1, +1]``, optionally sharpened by a power.  Common settings:

* ``power=1.0`` — linear ramp (concentrated around top/bottom only at the
  extremes).
* ``power=2.0`` — quadratic, more concentrated at extreme ranks.
* ``power=0.5`` — square-root, flatter / more diversified.

After raising to the power the row is normalised so Σ|wᵢ| = 1 (when
non-zero), then handed to :func:`normalize_to_constraints` for final
shaping.  This per-row pre-normalisation ensures the gross-exposure cap
is what ultimately drives portfolio leverage rather than the kernel's
internal scale, which depends on N and the ``power``.

Parameters (``params``)
-----------------------
power:
    Concentration exponent.  Must be > 0.  Default ``1.0``.
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


def rank_to_weight(
    factor_panel: pl.DataFrame,
    params: dict[str, Any],
    constraints: dict[str, float],
) -> pl.DataFrame:
    """Map cross-section rank percentile to a signed weight.

    Parameters
    ----------
    factor_panel:
        ``"ts"`` + N symbol columns.
    params:
        Recognised keys: ``"power"`` (default ``1.0``).
    constraints:
        Position-constraint dict.

    Returns
    -------
    pl.DataFrame
        Weight panel with the same layout as ``factor_panel``.
    """
    power = float(params.get("power", 1.0))
    if power <= 0:
        raise ValueError(f"rank_to_weight power must be > 0, got {power!r}")

    ts_col, factor_arr, sym_cols = split_factor_panel(factor_panel)
    T, _ = factor_arr.shape
    weights = np.zeros_like(factor_arr)

    for t in range(T):
        row = factor_arr[t, :]
        valid_mask = np.isfinite(row)
        n_valid = int(valid_mask.sum())
        if n_valid < 2:
            weights[t, :] = np.where(valid_mask, 0.0, np.nan)
            continue
        valid_indices = np.where(valid_mask)[0]
        # Centred rank percentile in [-1, +1].
        ranks = np.argsort(np.argsort(row[valid_indices], kind="stable"), kind="stable")
        rank_pct = (ranks - (n_valid - 1) / 2.0) * 2.0 / (n_valid - 1)
        signed = np.sign(rank_pct) * np.abs(rank_pct) ** power

        # Pre-normalise to Σ|w| = 1 so the gross_exposure cap drives
        # portfolio leverage independently of N / power.
        gross = float(np.sum(np.abs(signed)))
        if gross > 0:
            signed = signed / gross

        new_row = np.full_like(row, np.nan)
        new_row[valid_indices] = signed
        weights[t, :] = new_row

    weights = normalize_to_constraints(weights, **constraints)
    return build_weight_panel(ts_col, weights, sym_cols)
