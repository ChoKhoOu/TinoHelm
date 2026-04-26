"""Multi-factor composition — equal / IC-weighted / IR-weighted / Ledoit-Wolf.

Combines N factor :data:`Panel`s into a single composite panel with the
same wide ``[ts, sym₁, …, sym_N]`` layout. The four supported weighting
schemes (AC-2.5.1):

* ``equal`` — simple per-cell average.

* ``ic_weighted`` — weights proportional to each factor's IC mean,
  normalised to sum to 1 in absolute value.  Negative-IC factors flip
  sign so they contribute positively to the composite.

* ``ir_weighted`` — weights proportional to each factor's IR (mean / std
  of IC).  Falls back to a uniform weight vector when every IR is zero.

* ``ledoit_wolf`` — optimal weights ``w ∝ Σ⁻¹ μ`` where Σ is the
  Ledoit-Wolf shrinkage estimator on the IC time-series (one column per
  factor). Requires :func:`sklearn.covariance.ledoit_wolf` which is in
  the project dependency set since the factor-framework rebuild.

Empty / degenerate input contract (AC-2.5.1)
--------------------------------------------
* All-zero panels — the composite is all-zero (no exception).
* All-NaN panels — the composite is all-NaN (no exception).
* Mixed sign — IC-weighted handles this by absolute-value normalisation.
* Identical IC means — IC-weighted produces a uniform weight vector
  (each factor contributes equally).
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import polars as pl


_CompositionMethod = Literal["equal", "ic_weighted", "ir_weighted", "ledoit_wolf"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_panels(panels: dict[str, pl.DataFrame]) -> tuple[list[str], list[str], pl.Series | None]:
    """Validate panel dictionary and extract common metadata.

    Returns ``(factor_names, sym_cols, ts_series)``. ``ts_series`` is
    ``None`` if no panel has a ``ts`` column. Raises :class:`ValueError`
    when factor names / symbol columns / timestamps disagree across
    panels.
    """
    if not panels:
        raise ValueError("combine_factors requires at least one panel")

    factor_names = list(panels.keys())
    first = panels[factor_names[0]]
    has_ts = "ts" in first.columns
    if has_ts:
        sym_cols = [c for c in first.columns if c != "ts"]
        ts_series = first["ts"]
    else:
        sym_cols = list(first.columns)
        ts_series = None

    for name in factor_names[1:]:
        p = panels[name]
        if has_ts:
            if "ts" not in p.columns:
                raise ValueError(
                    f"panel {name!r} missing 'ts' column (other panels include one)"
                )
            other_sym = [c for c in p.columns if c != "ts"]
        else:
            if "ts" in p.columns:
                raise ValueError(
                    f"panel {name!r} has 'ts' column but the first panel does not"
                )
            other_sym = list(p.columns)
        if other_sym != sym_cols:
            raise ValueError(
                f"panel {name!r} has symbol columns {other_sym!r}; "
                f"expected {sym_cols!r} (matching the first panel)"
            )
        if has_ts and not p["ts"].equals(ts_series):
            raise ValueError(
                f"panel {name!r} has a different 'ts' axis from the first panel"
            )

    return factor_names, sym_cols, ts_series


def _stack_values(panels: dict[str, pl.DataFrame], sym_cols: list[str]) -> np.ndarray:
    """Stack panels into a single ``(F, T, N)`` float64 numpy buffer."""
    arrs = []
    for name, p in panels.items():
        sub = p.select(sym_cols)
        arrs.append(sub.to_numpy().astype(np.float64, copy=False))
    return np.stack(arrs, axis=0)


def _build_output(
    composite: np.ndarray,
    sym_cols: list[str],
    ts_series: pl.Series | None,
) -> pl.DataFrame:
    """Wrap a ``(T, N)`` float matrix into the canonical wide panel."""
    data: dict[str, list | pl.Series] = {}
    if ts_series is not None:
        data["ts"] = ts_series
    for j, c in enumerate(sym_cols):
        data[c] = composite[:, j].tolist()
    return pl.DataFrame(data)


def _normalize_weights_abs(weights: np.ndarray) -> np.ndarray:
    """Normalise weights so their absolute values sum to 1.

    Returns a uniform vector when every weight is zero (or non-finite),
    matching the AC-2.5.1 "all-zero input → all-zero output, no
    exception" contract — combined with the upstream all-zero panel
    short-circuit, this never produces a divide-by-zero blowup.
    """
    weights = np.asarray(weights, dtype=np.float64)
    finite = np.where(np.isfinite(weights), weights, 0.0)
    abs_sum = float(np.sum(np.abs(finite)))
    if abs_sum <= 0.0:
        # Uniform fallback — each factor contributes equally.
        F = len(weights)
        return np.full(F, 1.0 / F, dtype=np.float64) if F > 0 else weights
    return finite / abs_sum


# ---------------------------------------------------------------------------
# combine_factors — main entry point
# ---------------------------------------------------------------------------


def combine_factors(
    panels: dict[str, pl.DataFrame],
    method: _CompositionMethod = "equal",
    *,
    ic_means: dict[str, float] | None = None,
    ic_irs: dict[str, float] | None = None,
    ic_time_series: dict[str, np.ndarray | pl.Series] | None = None,
) -> pl.DataFrame:
    """Compose ``panels`` into a single weight-aware composite panel.

    Parameters
    ----------
    panels:
        Mapping ``{factor_name: panel}``. All panels must share the same
        symbol columns and (if present) ``ts`` axis.
    method:
        Composition method — see module docstring.
    ic_means:
        Required for ``method="ic_weighted"``. Mapping
        ``{factor_name: ic_mean}``.
    ic_irs:
        Required for ``method="ir_weighted"``. Mapping
        ``{factor_name: ic_ir}``.
    ic_time_series:
        Required for ``method="ledoit_wolf"``. Mapping
        ``{factor_name: 1-D array of IC values}`` — every series must
        have the same length so the covariance estimator is well-defined.

    Returns
    -------
    pl.DataFrame
        Composite panel with the same shape as each input panel.
    """
    factor_names, sym_cols, ts_series = _validate_panels(panels)
    stacked = _stack_values(panels, sym_cols)  # (F, T, N)
    F, T, N = stacked.shape

    if F == 0 or N == 0:
        return _build_output(np.zeros((T, N), dtype=np.float64), sym_cols, ts_series)

    # All-zero input short-circuit (AC-2.5.1) — keeps NaN propagation only
    # for the legitimately all-NaN case below.
    if np.all(stacked == 0.0):
        return _build_output(np.zeros((T, N), dtype=np.float64), sym_cols, ts_series)

    # All-NaN input short-circuit — return all-NaN composite directly.
    if np.all(np.isnan(stacked)):
        return _build_output(np.full((T, N), np.nan, dtype=np.float64), sym_cols, ts_series)

    if method == "equal":
        weights = np.full(F, 1.0 / F, dtype=np.float64)
    elif method == "ic_weighted":
        if ic_means is None:
            raise ValueError("method='ic_weighted' requires ``ic_means`` mapping")
        raw = np.array(
            [float(ic_means.get(name, 0.0)) for name in factor_names],
            dtype=np.float64,
        )
        weights = _normalize_weights_abs(raw)
    elif method == "ir_weighted":
        if ic_irs is None:
            raise ValueError("method='ir_weighted' requires ``ic_irs`` mapping")
        raw = np.array(
            [float(ic_irs.get(name, 0.0)) for name in factor_names],
            dtype=np.float64,
        )
        weights = _normalize_weights_abs(raw)
    elif method == "ledoit_wolf":
        if ic_time_series is None:
            raise ValueError(
                "method='ledoit_wolf' requires ``ic_time_series`` mapping"
            )
        weights = _ledoit_wolf_weights(factor_names, ic_time_series)
    else:
        raise ValueError(
            f"unknown composition method {method!r}; expected one of "
            "'equal', 'ic_weighted', 'ir_weighted', 'ledoit_wolf'"
        )

    # composite[t, n] = Σ_f w_f * stacked[f, t, n]
    # — reshape to (F, 1, 1) so it broadcasts against (F, T, N).
    weighted = stacked * weights.reshape(F, 1, 1)
    composite = np.nansum(weighted, axis=0)

    # Preserve all-NaN rows: nansum returns 0 if every input is NaN. Detect
    # such cells and write back NaN so callers can distinguish "no signal"
    # from "zero composite".
    all_nan_mask = np.all(np.isnan(stacked), axis=0)
    composite[all_nan_mask] = np.nan

    return _build_output(composite, sym_cols, ts_series)


# ---------------------------------------------------------------------------
# Ledoit-Wolf — sklearn shrinkage estimator wrapper
# ---------------------------------------------------------------------------


def _ledoit_wolf_weights(
    factor_names: list[str],
    ic_time_series: dict[str, np.ndarray | pl.Series],
) -> np.ndarray:
    """Compute Ledoit-Wolf-shrunk weights ``w ∝ Σ⁻¹ μ``.

    The IC time-series is stacked into a ``(T, F)`` matrix and passed to
    :func:`sklearn.covariance.ledoit_wolf` for the shrunk Σ. Mean IC ``μ``
    is the column-wise mean. The weight vector is normalised so its
    absolute values sum to 1 — matching the IC/IR weighted schemes.

    Falls back to uniform weights when:

    * the covariance is singular (``np.linalg.LinAlgError`` from
      ``np.linalg.solve``);
    * every column has zero mean and zero variance (``μ`` is all-zero);
    * any IC series contains a non-finite entry (drops to
      :func:`np.linalg.lstsq` which still returns a finite solution).
    """
    from sklearn.covariance import ledoit_wolf  # local import — heavy module

    F = len(factor_names)
    if F == 0:
        return np.array([], dtype=np.float64)

    arrays = []
    for name in factor_names:
        v = ic_time_series.get(name)
        if v is None:
            raise KeyError(
                f"ic_time_series missing factor {name!r}; got keys {list(ic_time_series)!r}"
            )
        if isinstance(v, pl.Series):
            arr = v.to_numpy()
        else:
            arr = np.asarray(v, dtype=float)
        arrays.append(arr.astype(np.float64, copy=False))

    lengths = {len(a) for a in arrays}
    if len(lengths) != 1:
        raise ValueError(
            f"all IC series must have the same length; got lengths {lengths}"
        )
    T = lengths.pop()
    if T < 2:
        # Not enough samples to estimate covariance — uniform fallback.
        return np.full(F, 1.0 / F, dtype=np.float64)

    matrix = np.column_stack(arrays)  # (T, F)
    # Replace non-finite cells with column means so ledoit_wolf is well-defined.
    col_means = np.nanmean(matrix, axis=0)
    col_means = np.where(np.isfinite(col_means), col_means, 0.0)
    nan_mask = ~np.isfinite(matrix)
    if nan_mask.any():
        for j in range(F):
            matrix[nan_mask[:, j], j] = col_means[j]

    if np.allclose(col_means, 0.0):
        return np.full(F, 1.0 / F, dtype=np.float64)

    cov, _shrinkage = ledoit_wolf(matrix)
    try:
        raw_weights = np.linalg.solve(cov, col_means)
    except np.linalg.LinAlgError:
        return np.full(F, 1.0 / F, dtype=np.float64)
    if not np.all(np.isfinite(raw_weights)):
        return np.full(F, 1.0 / F, dtype=np.float64)

    return _normalize_weights_abs(raw_weights)


__all__ = ["combine_factors"]
