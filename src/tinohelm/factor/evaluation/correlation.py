"""Cross-factor correlation matrices — polars-native.

Two correlation modes (AC-2.4.1):

* **cross_section** — for each timestamp, compute pairwise Spearman
  correlation across symbols, then average across timestamps. Captures
  whether two factors agree on *which symbols are favoured at any given
  point in time*. The default mode used by US-2.4.

* **ic_time_series** — for each factor, build an IC time-series (Spearman
  IC vs ``forward_returns`` per timestamp), then compute pairwise
  Pearson correlation across the IC series. Captures whether two
  factors' alpha *moves together over time* — a different question from
  whether they currently rank symbols similarly.

Both modes return a wide ``(F, F+1)`` :class:`polars.DataFrame` with a
``factor_name`` column followed by F factor-named float columns. The
diagonal is exactly ``1.0`` and the matrix is symmetric. Self-correlation
of constant or degenerate input is forced to ``1.0`` (matches scipy
``spearmanr`` semantics with ``nan_policy='propagate'`` plus our
post-fill).

Determinism contract (AC-2.4.1)
-------------------------------
Same input → byte-identical output across two consecutive calls. We
sort the input panels' columns and timestamps before computation and
use deterministic ``round(..., 12)`` to absorb 1-bit float residue.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from tinohelm.factor.evaluation.ic import forward_returns


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CorrelationMethod = Literal["cross_section", "ic_time_series"]

# Float tolerance used to round per-pair correlations into the matrix. This
# absorbs the IEEE-754 last-bit residue that two equivalent code paths can
# produce on different machines / library versions, so the determinism check
# (AC-2.4.1) remains stable.
_DETERMINISM_ROUND: int = 12


def _stack_panel_values(panel: pl.DataFrame) -> tuple[list[str], np.ndarray]:
    """Return ``(symbol_columns, values_TxN)`` from a panel.

    The panel is expected to be a wide ``[ts, sym₁, …, sym_N]`` DataFrame
    (the canonical :data:`tinohelm.factor.types.Panel` layout). Symbol
    columns are returned in their original order. The ``ts`` column is
    dropped; if missing, all columns are treated as symbol columns.
    """
    cols = list(panel.columns)
    if "ts" in cols:
        cols = [c for c in cols if c != "ts"]
        return cols, panel.select(cols).to_numpy()
    return cols, panel.to_numpy()


def _sort_panel(panel: pl.DataFrame) -> pl.DataFrame:
    """Sort panel rows by ``ts`` (if present) for deterministic input order."""
    if "ts" in panel.columns:
        return panel.sort("ts")
    return panel


def _spearman_safe(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman correlation that returns ``nan`` for degenerate input.

    Wraps :func:`scipy.stats.spearmanr` and:
    * drops paired non-finite rows (``np.isfinite`` on both columns);
    * returns ``nan`` if fewer than 3 valid pairs remain or if either
      column is constant after the drop;
    * never raises — ``nan`` is the explicit "no signal" sentinel.
    """
    if x.size != y.size:
        raise ValueError("Spearman inputs must have the same length")
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan")
    xv = x[mask]
    yv = y[mask]
    if np.all(xv == xv[0]) or np.all(yv == yv[0]):
        return float("nan")
    rho, _ = spearmanr(xv, yv)
    return float(rho) if np.isfinite(rho) else float("nan")


def _pearson_safe(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation with the same safety contract as :func:`_spearman_safe`."""
    if x.size != y.size:
        raise ValueError("Pearson inputs must have the same length")
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan")
    xv = x[mask]
    yv = y[mask]
    if np.all(xv == xv[0]) or np.all(yv == yv[0]):
        return float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = np.corrcoef(xv, yv)
    rho = cov[0, 1]
    return float(rho) if np.isfinite(rho) else float("nan")


# ---------------------------------------------------------------------------
# correlation_matrix_cross_section
# ---------------------------------------------------------------------------


def correlation_matrix_cross_section(
    panels: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    """Pairwise Spearman correlation, averaged across timestamps.

    For each ``ts`` (intersection of all panels' timestamps after
    inner-aligning), compute Spearman ρ across symbols for every pair of
    factors, then average over timestamps. Per-timestamp ``nan`` (caused
    by constant slices or < 3 valid symbols) is dropped from the mean.

    Parameters
    ----------
    panels:
        Mapping ``{factor_name: panel}``. Each panel is a wide
        ``[ts, sym₁, …, sym_N]`` polars DataFrame. The factor names are
        used as both the ``factor_name`` column values and the matrix
        column headers.

    Returns
    -------
    pl.DataFrame
        Wide ``(F, F+1)`` matrix with column ``factor_name`` plus F
        float64 columns (one per factor). Diagonal entries are exactly
        ``1.0``; ``nan`` propagates only if every per-timestamp ρ for a
        pair was ``nan`` — in that case the cell is ``0.0`` (matches
        legacy ``analysis.py`` behaviour for "no signal").
    """
    factor_names = list(panels.keys())
    F = len(factor_names)
    if F == 0:
        return pl.DataFrame(schema={"factor_name": pl.Utf8})

    # Inner-align panels on ``ts`` so every factor sees the same sample of
    # timestamps. We sort rows for deterministic enumeration.
    sorted_panels = {name: _sort_panel(p) for name, p in panels.items()}

    # Collect (T, N) numpy slices for every factor against the common ts axis.
    # If any panel lacks a ``ts`` column we assume row-aligned input.
    has_ts = all("ts" in p.columns for p in sorted_panels.values())
    if has_ts:
        # Inner-join on ts to get the common timestamp set, then re-extract
        # each factor's per-row values for those timestamps.
        common_ts: pl.Series | None = None
        for p in sorted_panels.values():
            ts_col = p["ts"]
            common_ts = ts_col if common_ts is None else (
                pl.DataFrame({"ts": common_ts})
                .join(pl.DataFrame({"ts": ts_col}), on="ts", how="inner")
                ["ts"]
            )
        if common_ts is None or len(common_ts) == 0:
            return _empty_corr_matrix(factor_names)

        ts_df = pl.DataFrame({"ts": common_ts})
        aligned: dict[str, np.ndarray] = {}
        sym_count: dict[str, int] = {}
        for name, p in sorted_panels.items():
            sym_cols = [c for c in p.columns if c != "ts"]
            sym_count[name] = len(sym_cols)
            joined = ts_df.join(p, on="ts", how="left").select(sym_cols)
            aligned[name] = joined.to_numpy()
        T = len(common_ts)
    else:
        aligned = {}
        sym_count = {}
        T_set = {p.height for p in sorted_panels.values()}
        if len(T_set) != 1:
            raise ValueError(
                "panels without a ``ts`` column must have identical row counts"
            )
        T = next(iter(T_set))
        for name, p in sorted_panels.items():
            aligned[name] = p.to_numpy()
            sym_count[name] = p.width

    # Pairwise Spearman per timestamp, averaged across timestamps.
    matrix = np.zeros((F, F), dtype=np.float64)
    for i in range(F):
        for j in range(i, F):
            if i == j:
                matrix[i, j] = 1.0
                continue
            ai = aligned[factor_names[i]]
            aj = aligned[factor_names[j]]
            # Per-timestamp Spearman — collect non-NaN and average.
            per_ts: list[float] = []
            for t in range(T):
                rho = _spearman_safe(ai[t], aj[t])
                if np.isfinite(rho):
                    per_ts.append(rho)
            mean_rho = float(np.mean(per_ts)) if per_ts else 0.0
            mean_rho = round(mean_rho, _DETERMINISM_ROUND)
            matrix[i, j] = mean_rho
            matrix[j, i] = mean_rho

    return _matrix_to_dataframe(matrix, factor_names)


# ---------------------------------------------------------------------------
# correlation_matrix_ic_time_series
# ---------------------------------------------------------------------------


def correlation_matrix_ic_time_series(
    ic_series: dict[str, pl.Series | np.ndarray],
) -> pl.DataFrame:
    """Pairwise Pearson correlation across factors' IC time-series.

    Parameters
    ----------
    ic_series:
        Mapping ``{factor_name: ic_per_timestamp}``. Each value is a
        :class:`polars.Series` or 1-D numpy array of floats — the per-
        timestamp IC values produced by, e.g., a horizon-by-horizon
        Spearman IC. The arrays are aligned by index (i.e. position),
        not by ``ts`` — callers are expected to compute the IC series
        on the same forward-return grid.
    """
    factor_names = list(ic_series.keys())
    F = len(factor_names)
    if F == 0:
        return pl.DataFrame(schema={"factor_name": pl.Utf8})

    arrays: list[np.ndarray] = []
    lengths: set[int] = set()
    for name in factor_names:
        v = ic_series[name]
        if isinstance(v, pl.Series):
            arr = v.to_numpy()
        else:
            arr = np.asarray(v, dtype=float)
        arrays.append(arr)
        lengths.add(len(arr))
    if len(lengths) != 1:
        raise ValueError(
            f"IC series must all have the same length; got lengths {lengths}"
        )

    matrix = np.zeros((F, F), dtype=np.float64)
    for i in range(F):
        for j in range(i, F):
            if i == j:
                matrix[i, j] = 1.0
                continue
            rho = _pearson_safe(arrays[i], arrays[j])
            rho = 0.0 if not np.isfinite(rho) else rho
            rho = round(rho, _DETERMINISM_ROUND)
            matrix[i, j] = rho
            matrix[j, i] = rho

    return _matrix_to_dataframe(matrix, factor_names)


# ---------------------------------------------------------------------------
# correlation_matrix — convenience entry point dispatching to the two modes
# ---------------------------------------------------------------------------


def correlation_matrix(
    panels: dict[str, pl.DataFrame],
    method: _CorrelationMethod = "cross_section",
    *,
    forward_returns_panel: pl.DataFrame | None = None,
    forward_period: int = 5,
    close_panel: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """High-level entry point — dispatch to cross-section or IC-time-series.

    For ``method="cross_section"`` we delegate directly to
    :func:`correlation_matrix_cross_section`.

    For ``method="ic_time_series"`` we first compute a per-timestamp IC
    series for each factor by Spearman-correlating its panel against
    ``forward_returns_panel`` row-by-row, then run
    :func:`correlation_matrix_ic_time_series`. Either provide
    ``forward_returns_panel`` directly (preferred — same shape as
    ``panels`` values) or ``close_panel`` plus ``forward_period`` so we
    can compute it via :func:`tinohelm.factor.evaluation.ic.forward_returns`.
    """
    if method == "cross_section":
        return correlation_matrix_cross_section(panels)
    if method != "ic_time_series":
        raise ValueError(
            f"Unknown correlation method {method!r}; expected one of "
            "'cross_section' / 'ic_time_series'."
        )

    fwd_panel = forward_returns_panel
    if fwd_panel is None:
        if close_panel is None:
            raise ValueError(
                "method='ic_time_series' requires either ``forward_returns_panel`` "
                "or ``close_panel`` (so a forward-return panel can be derived)."
            )
        fwd_panel = _close_panel_to_forward_panel(close_panel, forward_period)

    ic_series_map: dict[str, np.ndarray] = {}
    fwd_sym_cols, fwd_values = _stack_panel_values(_sort_panel(fwd_panel))
    fwd_ts = fwd_panel.sort("ts")["ts"] if "ts" in fwd_panel.columns else None

    for name, panel in panels.items():
        sorted_panel = _sort_panel(panel)
        sym_cols, values = _stack_panel_values(sorted_panel)
        if sym_cols != fwd_sym_cols:
            # Best-effort: align symbol columns by name.
            common = [c for c in sym_cols if c in fwd_sym_cols]
            if not common:
                raise ValueError(
                    f"factor {name!r} shares no symbol columns with forward returns"
                )
            sub_idx = [sym_cols.index(c) for c in common]
            fwd_idx = [fwd_sym_cols.index(c) for c in common]
            values = values[:, sub_idx]
            fwd_aligned = fwd_values[:, fwd_idx]
        else:
            fwd_aligned = fwd_values

        T = min(values.shape[0], fwd_aligned.shape[0])
        per_ts = np.full(T, np.nan, dtype=np.float64)
        for t in range(T):
            rho = _spearman_safe(values[t], fwd_aligned[t])
            per_ts[t] = rho if np.isfinite(rho) else np.nan
        ic_series_map[name] = per_ts

    return correlation_matrix_ic_time_series(ic_series_map)


# ---------------------------------------------------------------------------
# Internal helpers — DataFrame construction
# ---------------------------------------------------------------------------


def _matrix_to_dataframe(matrix: np.ndarray, factor_names: list[str]) -> pl.DataFrame:
    """Wrap an ``(F, F)`` float matrix into a polars wide-frame."""
    data: dict[str, list] = {"factor_name": factor_names}
    for j, name in enumerate(factor_names):
        data[name] = matrix[:, j].tolist()
    return pl.DataFrame(data)


def _empty_corr_matrix(factor_names: list[str]) -> pl.DataFrame:
    """Return an all-zero correlation matrix with diagonal = 1.0."""
    F = len(factor_names)
    matrix = np.zeros((F, F), dtype=np.float64)
    for i in range(F):
        matrix[i, i] = 1.0
    return _matrix_to_dataframe(matrix, factor_names)


def _close_panel_to_forward_panel(
    close_panel: pl.DataFrame, forward_period: int
) -> pl.DataFrame:
    """Convert a wide ``[ts, sym₁, …]`` close panel into forward returns."""
    if "ts" not in close_panel.columns:
        raise ValueError("close_panel must include a 'ts' column for forward returns")
    sym_cols = [c for c in close_panel.columns if c != "ts"]
    out = {"ts": close_panel["ts"]}
    for c in sym_cols:
        sub = close_panel.select([pl.col("ts"), pl.col(c).alias("value")])
        fwd = forward_returns(sub, forward_period)
        out[c] = fwd["value"]
    return pl.DataFrame(out)


__all__ = [
    "correlation_matrix",
    "correlation_matrix_cross_section",
    "correlation_matrix_ic_time_series",
]
