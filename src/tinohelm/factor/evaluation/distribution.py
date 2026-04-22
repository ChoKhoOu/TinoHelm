"""Factor value distribution stats + histogram.

Migrated from ``research.analysis.compute_distribution`` — produces:
  * A ``n_bins``-entry histogram (``[{bin_start, bin_end, count}, ...]``).
  * Summary stats: ``mean``, ``std``, ``skew``, ``kurtosis``, ``min``, ``max``,
    ``zero_pct``, ``autocorr_1``, ``autocorr_5``.

NaN / ±Inf are filtered before the histogram is built so the computation
never poisons downstream JSON.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_distribution(factor: pd.Series, n_bins: int = 50) -> dict:
    """Factor value distribution stats + histogram.

    Returns
    -------
    dict
        ``{"histogram": [...], "stats": {...}}`` — same shape as
        ``research.analysis.compute_distribution``.  Short input (<10 finite
        values) returns ``{"histogram": [], "stats": {}}``.
    """
    clean = factor.dropna()
    clean = clean[np.isfinite(clean)]

    if len(clean) < 10:
        return {"histogram": [], "stats": {}}

    counts, bin_edges = np.histogram(clean, bins=n_bins)
    histogram = [
        {
            "bin_start": round(float(bin_edges[i]), 6),
            "bin_end": round(float(bin_edges[i + 1]), 6),
            "count": int(counts[i]),
        }
        for i in range(len(counts))
    ]

    arr = clean.values
    acf_1 = float(pd.Series(arr).autocorr(lag=1)) if len(arr) > 1 else 0
    acf_5 = float(pd.Series(arr).autocorr(lag=5)) if len(arr) > 5 else 0

    stats = {
        "mean": round(float(np.mean(arr)), 6),
        "std": round(float(np.std(arr)), 6),
        "skew": round(float(pd.Series(arr).skew()), 4),
        "kurtosis": round(float(pd.Series(arr).kurtosis()), 4),
        "min": round(float(np.min(arr)), 6),
        "max": round(float(np.max(arr)), 6),
        "zero_pct": round(float(np.mean(arr == 0)), 4),
        "autocorr_1": round(acf_1, 4) if np.isfinite(acf_1) else 0,
        "autocorr_5": round(acf_5, 4) if np.isfinite(acf_5) else 0,
    }

    return {"histogram": histogram, "stats": stats}


__all__ = ["compute_distribution"]
