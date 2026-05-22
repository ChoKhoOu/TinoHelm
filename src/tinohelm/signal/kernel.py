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
The implementation projects each row onto the feasible set by iterating::

    clip → smart_net_shift → gross_scale → clip

until all three constraints hold within a numerical tolerance, or the
iteration cap is reached.

The "smart" net shift is the key fix over a naive uniform shift: when the
sum needs to move toward zero, only cells that still have headroom in the
relevant direction absorb the correction.  A naive uniform shift over all
cells would push already-saturated cells outside ``±max_position``, the
following clip would re-saturate them, and the next pass would loop on
the same residual indefinitely.

Saturation example (with naive single-pass algorithm)::

    weights      = [2, 2, 2, -1]   max_position=0.5  gross=10  net=0
    after clip   = [0.5, 0.5, 0.5, -0.5]              # sum = +1
    naive shift  = +1/4 = 0.25 subtracted everywhere
    →            = [0.25, 0.25, 0.25, -0.75]          # sum = 0  but |w|=0.75>0.5
    final clip   = [0.25, 0.25, 0.25, -0.5]           # sum = +0.25  (BUG: net residual)

Smart shift only acts on cells that have room to move::

    excess > 0 (subtract to reduce net):
        eligible = {i : w[i] > -max_position}
    excess < 0 (add to raise net):
        eligible = {i : w[i] <  max_position}

NaN values are preserved (they represent "asset not in universe at this
timestamp" and must not be coerced to 0).  Rows with all-NaN inputs pass
through untouched.

Notes
-----
The projection converges within ``max_iter=20`` iterations under
smart-shift; if the loop fails to converge (very rare, only when all
remaining headroom is exactly zero), a warning is emitted via
:mod:`logging` and a final hard-guard pass guarantees the per-asset and
gross caps even though the net cap may carry a small residual.  We never
raise — production live-trading paths catch and silence
:func:`normalize_to_constraints` errors via the strategy's
``_on_cross_section_ready`` ``try/except``, and an exception here would
strand the whole strategy.
"""
from __future__ import annotations

import logging

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

_PROJECTION_MAX_ITER = 20
_PROJECTION_TOL = 1e-6


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


def _project_box_gross_net_row(
    valid: np.ndarray,
    *,
    gross_cap: float,
    net_cap: float,
    max_pos: float,
    max_iter: int | None = None,
    tol: float | None = None,
) -> tuple[np.ndarray, bool]:
    """Project a 1-D weight row onto the (box ∩ gross ∩ net) feasible set.

    The three constraints are not jointly satisfiable in a single
    closed-form pass when some weights are saturated against the box
    constraint ``±max_pos``.  We use the "smart-shift" cyclic-projection
    scheme described in the module docstring:

    1. Box-clip every cell to ``[-max_pos, +max_pos]``.
    2. If ``|sum|`` exceeds ``net_cap``, distribute the corrective shift
       only across cells that still have headroom in the relevant
       direction (cells already saturated against the bound on the side
       we'd push them further into are skipped — a uniform shift across
       all cells is what causes the original convergence bug).
    3. If ``Σ|w|`` exceeds ``gross_cap``, scale uniformly down.
    4. Box-clip again (step 2's shift may have driven non-saturated cells
       past the box; step 3's scale-down might also need follow-up).

    Iterate up to ``max_iter`` rounds.  Convergence is declared when all
    three constraints hold within ``tol`` simultaneously.

    Parameters
    ----------
    valid:
        Finite-only 1-D weights (caller has already removed NaN cells).
    gross_cap, net_cap, max_pos:
        The three constraint thresholds.
    max_iter:
        Maximum number of projection passes.
    tol:
        Numerical tolerance for the convergence check.

    Returns
    -------
    tuple[np.ndarray, bool]
        ``(projected_weights, converged)``.  When ``converged`` is
        False, the caller should emit a warning; the returned weights
        still satisfy the box and gross caps (a final hard-guard pass
        is applied) but the net cap may carry a small residual.
    """
    # Read constants fresh from the module so monkeypatch in tests works.
    if max_iter is None:
        max_iter = _PROJECTION_MAX_ITER
    if tol is None:
        tol = _PROJECTION_TOL

    w = valid.astype(np.float64, copy=True)

    converged = False
    for _ in range(max_iter):
        # Step 1 (box clip).
        w = np.clip(w, -max_pos, max_pos)

        # Step 2 (smart net shift).
        net = float(np.sum(w))
        if abs(net) > net_cap + tol:
            target_net = float(np.sign(net)) * net_cap
            excess = net - target_net  # signed: same sign as net.
            if excess > 0:
                # Need to subtract: only cells above -max_pos can absorb.
                eligible = w > -max_pos + tol
            else:
                # Need to add: only cells below +max_pos can absorb.
                eligible = w < max_pos - tol
            n_eligible = int(np.sum(eligible))
            if n_eligible > 0:
                shift = excess / n_eligible
                w[eligible] -= shift
            # else: no headroom anywhere — leave w alone, next clip is
            # idempotent, the loop will exit via the convergence check or
            # via max_iter exhaustion (the warn path).

        # Step 3 (gross scale-down).
        gross = float(np.sum(np.abs(w)))
        if gross > gross_cap + tol and gross > 0:
            w *= gross_cap / gross

        # Step 4 (box clip again — step 2 may have pushed eligible cells
        # past the bound; step 3's down-scaling occasionally lands a cell
        # marginally outside numeric precision).
        w = np.clip(w, -max_pos, max_pos)

        # Convergence check.
        new_net = float(np.sum(w))
        new_gross = float(np.sum(np.abs(w)))
        new_max = float(np.max(np.abs(w))) if w.size > 0 else 0.0
        if (
            abs(new_net) <= net_cap + tol
            and new_gross <= gross_cap + tol
            and new_max <= max_pos + tol
        ):
            converged = True
            break

    if not converged:
        # Hard guard: even if the smart-shift loop failed to drive |net|
        # below net_cap (rare — happens only when every remaining cell is
        # exactly saturated against the bound on the side we'd push it),
        # we MUST still satisfy the box and gross caps so callers like
        # OrderManager.execute_diff don't blindly send oversized orders.
        w = np.clip(w, -max_pos, max_pos)
        gross = float(np.sum(np.abs(w)))
        if gross > gross_cap and gross > 0:
            w *= gross_cap / gross
            w = np.clip(w, -max_pos, max_pos)

    return w, converged


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
        within ``1e-6`` numerical tolerance (under all realistic inputs;
        if the cyclic projection fails to converge, the per-asset and
        gross caps are still guaranteed by a final hard-guard pass and a
        warning is logged via :mod:`logging`).  NaNs are preserved
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

    T = out.shape[0]
    for t in range(T):
        row = out[t, :]
        valid_mask = np.isfinite(row)
        if not np.any(valid_mask):
            # All NaN — pass through.
            continue

        valid = row[valid_mask]
        projected, converged = _project_box_gross_net_row(
            valid,
            gross_cap=gross_exposure,
            net_cap=net_exposure,
            max_pos=max_position,
        )
        if not converged:
            logger.warning(
                "normalize_to_constraints: row %d failed to converge in "
                "%d iterations (gross=%.6g, net=%.6g, max_pos=%.6g, "
                "input_n=%d); applied hard-guard fallback (per-asset and "
                "gross caps enforced; net cap may carry residual)",
                t,
                _PROJECTION_MAX_ITER,
                gross_exposure,
                net_exposure,
                max_position,
                int(valid.size),
            )
        out[t, valid_mask] = projected

    return out
