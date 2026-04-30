"""Vectorized matrix IC evaluation."""
from __future__ import annotations

from typing import Literal

import numpy as np
import polars as pl

from tinohelm.factor.research.panel import MatrixPanel


_FREQ_MAP: dict[str, str] = {
    "D": "1d",
    "W": "1w",
    "ME": "1mo",
    "M": "1mo",
    "H": "1h",
    "h": "1h",
    "T": "1m",
    "min": "1m",
}


def _to_polars_freq(freq: str) -> str:
    return _FREQ_MAP.get(freq, freq)


Method = Literal["spearman", "pearson"]


def _rank_1d(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    sorted_ranks = np.empty(len(sorted_values), dtype=np.float64)
    start = 0
    while start < len(sorted_values):
        end = start + 1
        while end < len(sorted_values) and sorted_values[end] == sorted_values[start]:
            end += 1
        sorted_ranks[start:end] = (start + end - 1) / 2.0 + 1.0
        start = end
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = sorted_ranks
    return ranks


def rank_rows(values: np.ndarray) -> np.ndarray:
    """Rank each row independently, preserving NaNs."""

    arr = np.asarray(values, dtype=np.float64)
    ranks = np.full(arr.shape, np.nan, dtype=np.float64)
    for row_idx, row in enumerate(arr):
        valid = np.isfinite(row)
        if not np.any(valid):
            continue
        ranks[row_idx, valid] = _rank_1d(row[valid])
    return ranks


def _corr_1d(x_row: np.ndarray, y_row: np.ndarray) -> float:
    x_centered = x_row - x_row.mean()
    y_centered = y_row - y_row.mean()
    denom = np.sqrt(np.sum(x_centered * x_centered) * np.sum(y_centered * y_centered))
    if denom <= 0:
        return np.nan
    return float(np.sum(x_centered * y_centered) / denom)


def rowwise_corr(x: np.ndarray, y: np.ndarray, min_valid: int = 20) -> np.ndarray:
    """Compute row-wise Pearson correlation after dropping invalid pairs."""

    if x.shape != y.shape:
        raise ValueError(f"shape mismatch: {x.shape!r} != {y.shape!r}")
    if min_valid <= 0:
        raise ValueError("min_valid must be > 0")
    x_arr = np.asarray(x, dtype=np.float64)
    y_arr = np.asarray(y, dtype=np.float64)
    out = np.full(x_arr.shape[0], np.nan, dtype=np.float64)
    for idx in range(x_arr.shape[0]):
        valid = np.isfinite(x_arr[idx]) & np.isfinite(y_arr[idx])
        if int(valid.sum()) < min_valid:
            continue
        x_row = x_arr[idx, valid]
        y_row = y_arr[idx, valid]
        out[idx] = _corr_1d(x_row, y_row)
    return out


def _rowwise_spearman_corr(x: np.ndarray, y: np.ndarray, min_valid: int = 20) -> np.ndarray:
    if x.shape != y.shape:
        raise ValueError(f"shape mismatch: {x.shape!r} != {y.shape!r}")
    if min_valid <= 0:
        raise ValueError("min_valid must be > 0")
    x_arr = np.asarray(x, dtype=np.float64)
    y_arr = np.asarray(y, dtype=np.float64)
    out = np.full(x_arr.shape[0], np.nan, dtype=np.float64)
    for idx in range(x_arr.shape[0]):
        valid = np.isfinite(x_arr[idx]) & np.isfinite(y_arr[idx])
        if int(valid.sum()) < min_valid:
            continue
        x_rank = _rank_1d(x_arr[idx, valid])
        y_rank = _rank_1d(y_arr[idx, valid])
        out[idx] = _corr_1d(x_rank, y_rank)
    return out


def _rowwise_ic(x: np.ndarray, y: np.ndarray, method: Method, min_valid: int) -> np.ndarray:
    if method == "spearman":
        return _rowwise_spearman_corr(x, y, min_valid=min_valid)
    return rowwise_corr(x, y, min_valid=min_valid)


def _matrix_pairs(factor: MatrixPanel, forward_returns: MatrixPanel) -> pl.DataFrame:
    ts = np.repeat(factor.normalized_ts(), len(factor.symbols))
    paired = pl.DataFrame({
        "ts": ts,
        "factor": factor.values.reshape(-1).astype(np.float64, copy=False),
        "fwd_ret": forward_returns.values.reshape(-1).astype(np.float64, copy=False),
    })
    return paired.filter(pl.col("factor").is_finite() & pl.col("fwd_ret").is_finite())


def compute_ic_matrix(
    factor: MatrixPanel,
    forward_returns: MatrixPanel,
    method: Method = "spearman",
    min_valid: int = 20,
    freq: str | None = "D",
    min_total_pairs: int = 30,
) -> pl.DataFrame:
    """Compute IC series from aligned factor/return matrices.

    With ``freq`` set, this mirrors the production evaluator contract:
    valid pairs are flattened across symbols, bucketed by ``ic_freq``, and each
    bucket emits one IC when both global and per-bucket sample thresholds pass.
    Passing ``freq=None`` keeps the low-level row-wise matrix primitive for
    tests/debugging.
    """

    factor.validate()
    forward_returns.validate()
    if method not in ("spearman", "pearson"):
        raise ValueError("method must be 'spearman' or 'pearson'")
    if min_valid <= 0:
        raise ValueError("min_valid must be > 0")
    if min_total_pairs < 0:
        raise ValueError("min_total_pairs must be >= 0")
    if factor.symbols != forward_returns.symbols:
        raise ValueError("factor and forward_returns symbols must match")
    if factor.values.shape != forward_returns.values.shape or not np.array_equal(
        factor.normalized_ts(), forward_returns.normalized_ts()
    ):
        raise ValueError("factor and forward_returns axes must match")

    if freq is None:
        ic = _rowwise_ic(factor.values, forward_returns.values, method, min_valid)
        mask = np.isfinite(ic)
        if not np.any(mask):
            return pl.DataFrame(schema={"date": pl.Utf8, "ic": pl.Float64})
        dates = [np.datetime_as_string(ts, unit="ns") for ts in factor.normalized_ts()[mask]]
        return pl.DataFrame({"date": dates, "ic": np.round(ic[mask], 6)})

    pairs = _matrix_pairs(factor, forward_returns)
    empty_schema = {"date": pl.Utf8, "ic": pl.Float64}
    if pairs.height < min_total_pairs:
        return pl.DataFrame(schema=empty_schema)

    bucketed = pairs.with_columns(pl.col("ts").dt.truncate(_to_polars_freq(freq)).alias("bucket"))
    results: list[dict[str, object]] = []
    for (bucket_dt,), group in bucketed.group_by(["bucket"], maintain_order=True):
        if group.height < min_valid:
            continue
        ic_val = group.select(
            pl.corr(pl.col("factor"), pl.col("fwd_ret"), method=method).alias("ic")
        ).item()
        if ic_val is None or not np.isfinite(ic_val):
            continue
        results.append({
            "date": bucket_dt.isoformat() if hasattr(bucket_dt, "isoformat") else str(bucket_dt),
            "ic": round(float(ic_val), 6),
        })
    if not results:
        return pl.DataFrame(schema=empty_schema)
    return pl.DataFrame(results, schema=empty_schema)


def summarize_ic_matrix(ic_series: pl.DataFrame) -> dict[str, float]:
    """Summarize matrix IC using the existing project rounding contract."""

    empty = {
        "ic_mean": 0,
        "ic_std": 0,
        "ir": 0,
        "ic_positive_pct": 0,
        "ic_max_abs": 0,
        "ic_tstat": 0,
    }
    if ic_series.height == 0 or "ic" not in ic_series.columns:
        return empty

    ics = ic_series["ic"].to_numpy()
    if len(ics) == 0:
        return empty

    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics))
    std_eff = std_ic if std_ic > 1e-12 else 0.0
    ir = mean_ic / std_eff if std_eff > 0 else 0
    ic_tstat = mean_ic / (std_eff / np.sqrt(len(ics))) if std_eff > 0 else 0
    pct_pos = float(np.mean(ics > 0))
    max_abs = float(np.max(np.abs(ics)))

    return {
        "ic_mean": round(mean_ic, 6),
        "ic_std": round(std_ic, 6),
        "ir": round(ir, 4),
        "ic_tstat": round(ic_tstat, 2),
        "ic_positive_pct": round(pct_pos, 4),
        "ic_max_abs": round(max_abs, 6),
    }
