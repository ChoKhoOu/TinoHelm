"""Factor value distribution stats + histogram — polars-native.

Migrated from ``research.analysis.compute_distribution`` (pandas) — produces:
  * A ``n_bins``-entry histogram (``[{bin_start, bin_end, count}, ...]``).
  * Summary stats: ``mean``, ``std``, ``skew``, ``kurtosis``, ``min``, ``max``,
    ``zero_pct``, ``autocorr_1``, ``autocorr_5``.

NaN / ±Inf are filtered before the histogram is built so the computation
never poisons downstream JSON.

Statistics conventions (preserved from legacy regression contract)
-------------------------------------------------------------------
* ``np.std`` / ``pl.std(ddof=0)`` — population std.
* ``skew`` / ``kurtosis`` — pandas / scipy *adjusted* (sample, ``bias=False``,
  ``fisher=True``). Polars ``Series.skew(bias=False)`` and
  ``Series.kurtosis(bias=False, fisher=True)`` produce the same output to
  ``1e-12`` precision.
* ``autocorr_k`` — sample correlation between ``arr`` and ``arr.shift(k)``,
  matching :meth:`pandas.Series.autocorr` semantics. Polars has no native
  ``autocorr`` so we delegate to :func:`numpy.corrcoef` on the shifted slice.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def _autocorr(arr: np.ndarray, lag: int) -> float:
    """Sample autocorrelation at ``lag``.

    Mirrors :meth:`pandas.Series.autocorr`: drops NaN pairs (already filtered
    upstream), then computes ``np.corrcoef`` between ``arr[:-lag]`` and
    ``arr[lag:]``. Returns ``np.nan`` for constant input — the caller coerces
    that to ``0`` via ``np.isfinite``.
    """
    if lag <= 0 or len(arr) <= lag:
        return float("nan")
    a = arr[:-lag]
    b = arr[lag:]
    # ``np.corrcoef`` returns the 2x2 correlation matrix; we want the off-diag.
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.corrcoef(a, b)
    return float(m[0, 1])


def compute_distribution(factor: pl.DataFrame | pl.Series, n_bins: int = 50) -> dict:
    """Factor value distribution stats + histogram.

    Parameters
    ----------
    factor:
        Either a 2-col :class:`pl.DataFrame` ``[ts, value]`` (the canonical
        post-polars-migration shape) or a 1-col :class:`pl.Series` of factor
        values. The ``ts`` axis is ignored — distribution stats only consume
        the values themselves.
    n_bins:
        Histogram bin count. Default 50 (legacy contract).

    Returns
    -------
    dict
        ``{"histogram": [...], "stats": {...}}`` — same shape as the legacy
        pandas implementation. Short input (< 10 finite values) returns
        ``{"histogram": [], "stats": {}}``.
    """
    if isinstance(factor, pl.DataFrame):
        if "value" in factor.columns:
            series = factor["value"]
        elif factor.width >= 1:
            # Tolerate {ts, factor_name} variants by selecting the first
            # non-``ts`` column. Keeps the API forgiving for bench callers.
            non_ts_cols = [c for c in factor.columns if c != "ts"]
            if not non_ts_cols:
                return {"histogram": [], "stats": {}}
            series = factor[non_ts_cols[0]]
        else:
            return {"histogram": [], "stats": {}}
    else:
        series = factor

    # Drop nulls and non-finite values up front — the histogram/stat math
    # both require a clean numpy buffer.
    clean = series.drop_nulls()
    arr = clean.to_numpy()
    arr = arr[np.isfinite(arr)]

    if len(arr) < 10:
        return {"histogram": [], "stats": {}}

    counts, bin_edges = np.histogram(arr, bins=n_bins)
    histogram = [
        {
            "bin_start": round(float(bin_edges[i]), 6),
            "bin_end": round(float(bin_edges[i + 1]), 6),
            "count": int(counts[i]),
        }
        for i in range(len(counts))
    ]

    # ``pl.Series.skew`` / ``kurtosis`` match pandas/scipy *adjusted* outputs
    # when called with ``bias=False`` (and ``fisher=True`` for kurtosis).
    pl_arr = pl.Series("v", arr)
    skew = pl_arr.skew(bias=False)
    kurt = pl_arr.kurtosis(bias=False, fisher=True)
    skew_val = float(skew) if skew is not None and np.isfinite(skew) else 0.0
    kurt_val = float(kurt) if kurt is not None and np.isfinite(kurt) else 0.0

    acf_1 = _autocorr(arr, 1) if len(arr) > 1 else 0
    acf_5 = _autocorr(arr, 5) if len(arr) > 5 else 0

    stats = {
        "mean": round(float(np.mean(arr)), 6),
        "std": round(float(np.std(arr)), 6),
        "skew": round(skew_val, 4),
        "kurtosis": round(kurt_val, 4),
        "min": round(float(np.min(arr)), 6),
        "max": round(float(np.max(arr)), 6),
        "zero_pct": round(float(np.mean(arr == 0)), 4),
        "autocorr_1": round(acf_1, 4) if np.isfinite(acf_1) else 0,
        "autocorr_5": round(acf_5, 4) if np.isfinite(acf_5) else 0,
    }

    return {"histogram": histogram, "stats": stats}


__all__ = ["compute_distribution"]
