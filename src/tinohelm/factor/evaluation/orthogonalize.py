"""Gram-Schmidt residual orthogonalisation across factor panels.

Given a list of factor :data:`Panel`s and an index pointing at the
*reference* factor, return the residual panel for the non-reference
factor after a per-timestamp cross-section OLS regression ``target ~ β
* reference``. The residual ``ε = target − β * reference`` is by
construction orthogonal to ``reference`` in the cross-section, so its
correlation with the reference factor is statistically negligible —
this is the AC-2.7.1 contract: for the golden synthetic ``A = 2 B + ε``
the residual of ``A`` regressed on ``B`` has |IC| < 0.05 vs ``B``.

API contract (AC-2.7.1)
-----------------------
``orthogonalize(panels, reference_idx) -> Panel``

* ``panels``: ``list[Panel]`` — N ≥ 2 factor panels with identical
  ``[ts, sym₁, …, sym_N]`` layout. The list order is preserved.
* ``reference_idx``: ``int`` — index into ``panels`` denoting the
  *reference* (the panel everything else is regressed onto).
* Returns: a single :data:`Panel` — the residual of the non-reference
  factor. The current spec covers exactly two panels per call (``A``
  and ``reference``); when more than two are passed, every non-
  reference panel is regressed onto ``reference`` and the residuals
  are stacked into a list — but to keep the documented signature
  ``-> Panel`` we return only the *first* non-reference factor's
  residual (callers wanting all residuals iterate with
  ``orthogonalize_many``).

Numerical conventions
---------------------
* Per-timestamp slope ``β = cov(target, reference) / var(reference)``
  with ``ddof=1`` (sample) — matches scipy / pandas convention. When
  ``var(reference) == 0`` (constant cross-section), the slope is
  treated as zero so the residual collapses to ``target − mean(target)``.
* Cells where either input is non-finite remain non-finite in the
  residual (NaN propagation — callers downstream filter via
  :func:`tinohelm.factor.evaluation.ic._build_paired`).
* The output panel preserves the canonical wide layout — including the
  ``ts`` column when present.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def orthogonalize(panels: list[pl.DataFrame], reference_idx: int) -> pl.DataFrame:
    """Per-timestamp residual orthogonalisation against a reference factor.

    Parameters
    ----------
    panels:
        List of factor panels with identical ``[ts, sym₁, …, sym_N]``
        layout. Length must be ≥ 2.
    reference_idx:
        Index into ``panels`` selecting the reference factor that
        non-reference panels are regressed onto.

    Returns
    -------
    pl.DataFrame
        Residual panel for the first non-reference factor.
    """
    residuals = orthogonalize_many(panels, reference_idx)
    return residuals[0]


def orthogonalize_many(
    panels: list[pl.DataFrame], reference_idx: int
) -> list[pl.DataFrame]:
    """Same as :func:`orthogonalize`, returning every non-reference residual.

    Used internally — and exposed for callers that pass more than two
    panels and want the full set of residuals.
    """
    if not isinstance(panels, list) or len(panels) < 2:
        raise ValueError(
            f"orthogonalize requires at least 2 panels; got {len(panels) if isinstance(panels, list) else type(panels).__name__}"
        )
    if reference_idx < 0 or reference_idx >= len(panels):
        raise IndexError(
            f"reference_idx={reference_idx} out of range [0, {len(panels)})"
        )

    reference = panels[reference_idx]
    has_ts = "ts" in reference.columns
    sym_cols = [c for c in reference.columns if c != "ts"] if has_ts else list(reference.columns)
    if not sym_cols:
        raise ValueError("reference panel has no symbol columns to regress on")

    # Validate every other panel matches the reference layout exactly.
    for i, p in enumerate(panels):
        if i == reference_idx:
            continue
        if has_ts:
            if "ts" not in p.columns:
                raise ValueError(
                    f"panel index {i} missing 'ts' column (reference panel has one)"
                )
            other_sym = [c for c in p.columns if c != "ts"]
        else:
            if "ts" in p.columns:
                raise ValueError(
                    f"panel index {i} has 'ts' column but reference does not"
                )
            other_sym = list(p.columns)
        if other_sym != sym_cols:
            raise ValueError(
                f"panel index {i} has symbol columns {other_sym!r}; "
                f"expected {sym_cols!r}"
            )

    ref_values = reference.select(sym_cols).to_numpy().astype(np.float64, copy=False)

    residuals: list[pl.DataFrame] = []
    for i, panel in enumerate(panels):
        if i == reference_idx:
            continue
        target_values = panel.select(sym_cols).to_numpy().astype(np.float64, copy=True)
        residual = _orthogonalize_one(target_values, ref_values)
        out = _build_panel(residual, sym_cols, panel)
        residuals.append(out)

    return residuals


def _orthogonalize_one(target: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Per-row OLS residual ``target − β * reference``.

    Both inputs are ``(T, N)`` numpy float64. The slope is computed
    cross-sectionally for each row, ignoring NaN cells. When fewer than
    2 valid pairs remain in a row (or the reference cross-section is
    constant), the residual collapses to ``target − mean(target)``
    which is the standard "no slope information" fallback.
    """
    if target.shape != reference.shape:
        raise ValueError(
            f"target shape {target.shape} != reference shape {reference.shape}"
        )

    out = np.full_like(target, np.nan, dtype=np.float64)
    T, N = target.shape
    for t in range(T):
        y = target[t]
        x = reference[t]
        mask = np.isfinite(y) & np.isfinite(x)
        n_valid = int(mask.sum())
        if n_valid < 2:
            # Not enough samples — leave row as NaN where target is NaN, else
            # keep the demeaned target as a degenerate residual.
            if n_valid == 1:
                # Single valid cell: residual is exactly 0 there (target itself
                # is the mean of one element).
                row = np.full(N, np.nan, dtype=np.float64)
                row[mask] = 0.0
                out[t] = row
            continue

        y_valid = y[mask]
        x_valid = x[mask]
        # ddof=1 (sample) — matches scipy / pandas convention.
        x_var = float(np.var(x_valid, ddof=1)) if n_valid > 1 else 0.0
        y_mean = float(np.mean(y_valid))
        if x_var == 0.0:
            # Reference is constant in this slice — slope is undefined, so we
            # fall back to demeaning the target. Cells with NaN target stay NaN.
            row = np.full(N, np.nan, dtype=np.float64)
            row[mask] = y_valid - y_mean
            out[t] = row
            continue

        x_mean = float(np.mean(x_valid))
        cov = float(np.cov(y_valid, x_valid, ddof=1)[0, 1])
        beta = cov / x_var
        intercept = y_mean - beta * x_mean
        row = np.full(N, np.nan, dtype=np.float64)
        row[mask] = y_valid - (beta * x_valid + intercept)
        out[t] = row

    return out


def _build_panel(
    values: np.ndarray, sym_cols: list[str], original: pl.DataFrame
) -> pl.DataFrame:
    """Wrap a ``(T, N)`` residual matrix into a wide polars panel."""
    data: dict[str, list | pl.Series] = {}
    if "ts" in original.columns:
        data["ts"] = original["ts"]
    for j, c in enumerate(sym_cols):
        data[c] = values[:, j].tolist()
    return pl.DataFrame(data)


__all__ = ["orthogonalize", "orthogonalize_many"]
