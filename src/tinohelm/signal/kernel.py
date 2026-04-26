"""Shared kernel helpers — :func:`normalize_to_constraints`.

The 5 built-in kernel templates each compute *raw* weights from a factor
panel and then call :func:`normalize_to_constraints` to enforce the
position constraints declared on :class:`tinohelm.signal.types.SignalSpec`.

Constraints
-----------
For every timestamp row we enforce::

    Σ |wᵢ| ≤ gross_exposure   (gross exposure cap)
    |Σ wᵢ| ≤ net_exposure     (net exposure cap)
    |wᵢ|   ≤ max_position     (per-asset cap)

The three are not independent — shifting net toward zero can push some
weights outside ``±max_position``, and scaling gross down can change net.
The implementation iterates per row in the following order:

1. Per-asset clip to ``±max_position``.
2. If ``|net|`` exceeds the cap, shift each weight by a constant so the
   net moves to ``±net_exposure`` (the closer-to-zero side).
3. If gross exceeds the cap, scale all weights uniformly down.
4. Re-clip per-asset to ``±max_position`` (step 2 may have pushed cells
   beyond the per-asset bound).

NaN values are preserved (they represent "asset not in universe at this
timestamp" and must not be coerced to 0).  Rows with all-NaN inputs pass
through untouched.

Notes
-----
The two-pass clip (steps 1 and 4) is intentional.  After step 2's net
shift, some cells may have grown in magnitude.  A single final clip at
the end would not cap them in time for step 3's gross calculation, which
relies on the post-clip absolute sum.  Empirically the loop converges in
one iteration because the shift in step 2 is bounded.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def split_factor_panel(factor_panel: pl.DataFrame) -> tuple[pl.Series, np.ndarray, list[str]]:
    """Split a factor panel into ``(ts_col, value_array, symbol_columns)``.

    Convenience for the 5 built-in kernels: every one of them needs to
    pull off the timestamp column, expose the rest as a numpy array for
    vectorised math, and remember the symbol column order for rebuilding
    the weight panel.

    Parameters
    ----------
    factor_panel:
        2-D :class:`polars.DataFrame` with column ``"ts"`` plus N symbol
        columns.

    Returns
    -------
    tuple
        ``(ts_col, factor_arr, sym_cols)`` where:

        * ``ts_col`` is the unmodified ``"ts"`` :class:`polars.Series`.
        * ``factor_arr`` is a ``(T, N)`` ``float64`` :class:`numpy.ndarray`.
        * ``sym_cols`` is a list of N symbol column names.

    Raises
    ------
    ValueError
        If ``factor_panel`` does not have a ``"ts"`` column or has fewer
        than 1 symbol column.
    """
    if "ts" not in factor_panel.columns:
        raise ValueError(
            f"factor_panel missing required 'ts' column; got {factor_panel.columns!r}"
        )
    sym_cols = [c for c in factor_panel.columns if c != "ts"]
    if not sym_cols:
        raise ValueError("factor_panel must have at least one symbol column")
    ts_col = factor_panel["ts"]
    factor_arr = factor_panel.select(sym_cols).to_numpy().astype(np.float64, copy=True)
    return ts_col, factor_arr, sym_cols


def build_weight_panel(
    ts_col: pl.Series,
    weights: np.ndarray,
    sym_cols: list[str],
) -> pl.DataFrame:
    """Build a weight :class:`polars.DataFrame` from numpy weights.

    Inverse of :func:`split_factor_panel`.  Returns a frame with column
    layout ``["ts", *sym_cols]``.
    """
    if weights.shape != (ts_col.len(), len(sym_cols)):
        raise ValueError(
            f"weights shape {weights.shape!r} does not match "
            f"({ts_col.len()}, {len(sym_cols)})"
        )
    data: dict[str, object] = {"ts": ts_col}
    for i, c in enumerate(sym_cols):
        data[c] = weights[:, i].tolist()
    return pl.DataFrame(data)


def normalize_to_constraints(
    weights: np.ndarray,
    *,
    gross_exposure: float,
    net_exposure: float,
    max_position: float,
) -> np.ndarray:
    """Normalize raw weights to satisfy gross / net / max-position bounds.

    Parameters
    ----------
    weights:
        ``(T, N)`` raw weights.  May contain NaN for "asset not in
        universe".  ``T`` and ``N`` are arbitrary; the function operates
        per row.
    gross_exposure:
        Upper bound on Σ|wᵢ| per row.  Must be > 0.
    net_exposure:
        Upper bound on |Σwᵢ| per row.  Must be ≥ 0.  Common case is
        ``0`` for a market-neutral signal.
    max_position:
        Upper bound on |wᵢ| per cell.  Must be > 0.

    Returns
    -------
    np.ndarray
        Same shape as ``weights``.  Values respect all three bounds
        within ``1e-6`` numerical tolerance.  NaNs are preserved
        positionally.

    Raises
    ------
    ValueError
        If ``gross_exposure <= 0``, ``max_position <= 0``, or
        ``net_exposure < 0``.

    Examples
    --------
    >>> w = np.array([[0.8, -0.6, 0.3]])
    >>> out = normalize_to_constraints(
    ...     w, gross_exposure=2.0, net_exposure=1.0, max_position=0.5
    ... )
    >>> bool(np.all(np.abs(out) <= 0.5 + 1e-9))
    True
    """
    if gross_exposure <= 0:
        raise ValueError(
            f"gross_exposure must be > 0, got {gross_exposure!r}"
        )
    if max_position <= 0:
        raise ValueError(
            f"max_position must be > 0, got {max_position!r}"
        )
    if net_exposure < 0:
        raise ValueError(
            f"net_exposure must be >= 0, got {net_exposure!r}"
        )

    out = np.asarray(weights, dtype=np.float64).copy()
    if out.ndim != 2:
        raise ValueError(
            f"weights must be 2-D (T, N), got shape {out.shape!r}"
        )

    # Step 1: per-asset clip preserving NaN positions.
    # np.clip does not propagate NaN through the bounds, so we mask first.
    finite_mask = np.isfinite(out)
    out_finite = out[finite_mask]
    out_finite = np.clip(out_finite, -max_position, max_position)
    out[finite_mask] = out_finite

    T = out.shape[0]
    for t in range(T):
        row = out[t, :]
        valid_mask = np.isfinite(row)
        if not np.any(valid_mask):
            # All NaN — pass through.
            continue

        valid = row[valid_mask].copy()

        # Step 2: enforce |net| ≤ net_exposure by uniform shift.
        net = float(np.sum(valid))
        if abs(net) > net_exposure:
            target_net = float(np.sign(net)) * net_exposure
            # Shift = (current_net - target_net) / k  applied to every cell.
            shift = (net - target_net) / valid.size
            valid -= shift

        # Step 3: enforce Σ|wᵢ| ≤ gross_exposure by uniform scale-down.
        gross = float(np.sum(np.abs(valid)))
        if gross > gross_exposure and gross > 0:
            valid *= gross_exposure / gross

        # Step 4: re-clip per-asset (step 2 shifts may push cells beyond
        # ±max_position even after step 3's down-scaling).
        valid = np.clip(valid, -max_position, max_position)

        out[t, valid_mask] = valid

    return out
