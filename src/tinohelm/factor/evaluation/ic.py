"""IC / RankIC / IR / t-stat / decay — polars-native (post pandas migration).

Numerical contract
------------------
Output values (``ic_mean``, ``ic_std``, ``ir``, ``ic_tstat``,
``ic_positive_pct``, ``ic_max_abs``) match the legacy pandas
``research.analysis.compute_ic_summary`` implementation up to the same
``round(..., N)`` rounding (AC-13.2 — drift ≤ 1e-6).

Input contract (post-polars)
----------------------------
``factor`` / ``fwd_ret`` / ``close`` are :class:`polars.DataFrame`
instances with columns ``[ts, value]`` or ``[ts, symbol, value]``:

* ``ts``    — :class:`polars.Datetime` (nanos / micros) — paired by inner
  join across factor / fwd. Multi-symbol stacked panels may carry
  duplicate ``ts`` rows (one row per symbol per timestamp); duplicates
  flow through ``group_by`` correctly (the daily bucket math doesn't care
  whether a bucket comes from many symbols or one).
* ``symbol`` — optional asset key.  When present on both frames, all joins
  and shifts use ``(ts, symbol)`` so multi-symbol panels cannot cross-pair
  BTC factor values with ETH forward returns.
* ``value`` — :class:`polars.Float64` — factor score, forward return, or
  close price.

The previous :func:`pd.Grouper(freq=...)` is mapped onto polars'
:meth:`pl.col.dt.truncate` + :meth:`group_by` via :data:`_FREQ_MAP`.
"""
from __future__ import annotations

import numpy as np
import polars as pl


# ---------------------------------------------------------------------------
# Internal helpers — schema validation + frequency translation
# ---------------------------------------------------------------------------

_TS_COL: str = "ts"
_SYMBOL_COL: str = "symbol"
_VAL_COL: str = "value"

# Map pandas-compatible offset aliases (used historically across the project)
# onto polars duration strings consumed by ``dt.truncate``. Anything not in the
# map falls back to the raw alias (which lets advanced callers pass ``"1h"`` /
# ``"5m"`` etc. directly).
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
    """Translate a pandas-compatible offset alias into a polars duration."""
    return _FREQ_MAP.get(freq, freq)


def _ensure_ts_value_frame(df: pl.DataFrame, name: str) -> pl.DataFrame:
    """Validate input has ``[ts, value]`` plus optional ``symbol``.

    Returns the frame unchanged. Kept as a lightweight guard so the
    sub-functions surface clear errors when callers pass malformed inputs.
    """
    if _TS_COL not in df.columns or _VAL_COL not in df.columns:
        raise ValueError(
            f"{name!r} expects columns [{_TS_COL!r}, {_VAL_COL!r}]; "
            f"got: {df.columns!r}"
        )
    return df


def _cast_temporal_ts_to_ns(df: pl.DataFrame) -> pl.DataFrame:
    """Cast temporal ``ts`` columns to a common nanosecond unit for joins."""
    if _TS_COL not in df.columns:
        return df
    ts_dtype = df.schema[_TS_COL]
    if ts_dtype.is_temporal():
        return df.with_columns(pl.col(_TS_COL).cast(pl.Datetime("ns")))
    return df


def _join_keys(left: pl.DataFrame, right: pl.DataFrame) -> list[str]:
    """Return identity-preserving join keys for evaluation frames.

    Symbol identity is intentionally fail-closed: either both frames carry a
    ``symbol`` column, or neither does.  Falling back to ``ts`` when only one
    side is multi-symbol silently broadcasts/cross-pairs returns across assets.
    """
    left_has_symbol = _SYMBOL_COL in left.columns
    right_has_symbol = _SYMBOL_COL in right.columns
    if left_has_symbol != right_has_symbol:
        raise ValueError(
            "factor and fwd_ret must both include 'symbol' or both omit it; "
            f"got left columns={left.columns!r}, right columns={right.columns!r}"
        )
    keys = [_TS_COL]
    if left_has_symbol:
        keys.append(_SYMBOL_COL)
    return keys


def _ensure_unique_identity_keys(df: pl.DataFrame, keys: list[str], name: str) -> None:
    """Reject duplicate join keys before an inner join can cartesian-expand."""
    if df.height <= 1:
        return
    if bool(df.select(keys).is_duplicated().any()):
        raise ValueError(
            f"{name} has duplicate identity rows for {keys!r}; "
            "dedupe or aggregate before IC evaluation"
        )


def _ensure_factor_keys_covered(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    keys: list[str],
) -> None:
    """Reject finite factor cells that would be silently dropped by inner join."""
    if factor.height == 0:
        return

    factor_keys = (
        factor
        .filter(pl.col(_VAL_COL).is_not_null() & pl.col(_VAL_COL).is_finite())
        .select(keys)
        .unique(maintain_order=True)
    )
    if factor_keys.height == 0:
        return

    if fwd_ret.height == 0:
        raise ValueError(
            "fwd_ret missing identity keys required by factor; "
            f"keys={keys!r}, missing_sample={factor_keys.head(5).to_dicts()!r}"
        )

    fwd_keys = fwd_ret.select(keys).unique(maintain_order=True)
    missing = factor_keys.join(fwd_keys, on=keys, how="anti")
    if missing.height > 0:
        raise ValueError(
            "fwd_ret missing identity keys required by factor; "
            f"keys={keys!r}, missing_sample={missing.head(5).to_dicts()!r}"
        )


# ---------------------------------------------------------------------------
# forward_returns — shared helper (used by ic.py + quantile.py + turnover.py)
# ---------------------------------------------------------------------------

def forward_returns(
    close: pl.DataFrame,
    period: int,
    log_ret: bool = False,
) -> pl.DataFrame:
    """Forward-return series.

    ``fwd[t] = close[t+period] / close[t] - 1`` (or log variant). The last
    ``period`` rows are ``null`` because the future bar isn't available.

    Returns a fresh :class:`pl.DataFrame` with columns ``[ts, value]`` or
    ``[ts, symbol, value]``.  Multi-symbol frames are shifted independently
    within each symbol.
    The caller's frame is **not** mutated.
    """
    _ensure_ts_value_frame(close, "forward_returns(close)")
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period!r}")
    if _SYMBOL_COL in close.columns:
        ordered = close.sort([_SYMBOL_COL, _TS_COL])
        future = pl.col(_VAL_COL).shift(-period).over(_SYMBOL_COL)
    else:
        ordered = close.sort(_TS_COL)
        future = pl.col(_VAL_COL).shift(-period)

    current = pl.col(_VAL_COL)
    valid_pair = future.is_finite() & current.is_finite() & (current != 0)
    ratio = future / current
    if log_ret:
        valid_pair = valid_pair & (future > 0) & (current > 0)
        expr = pl.when(valid_pair).then(ratio.log()).otherwise(None)
    else:
        expr = pl.when(valid_pair).then(ratio - 1).otherwise(None)

    select_cols = [pl.col(_TS_COL)]
    if _SYMBOL_COL in ordered.columns:
        select_cols.append(pl.col(_SYMBOL_COL))
    select_cols.append(expr.alias(_VAL_COL))
    return ordered.select(select_cols)


# ---------------------------------------------------------------------------
# Internal: build the joined-and-cleaned (ts, factor, fwd_ret) frame
# ---------------------------------------------------------------------------

def _build_paired(factor: pl.DataFrame, fwd_ret: pl.DataFrame) -> pl.DataFrame:
    """Inner-join on identity keys and drop non-finite rows.

    Output schema: ``[ts, factor, fwd_ret]`` for single-series input, or
    ``[ts, symbol, factor, fwd_ret]`` for multi-symbol input.
    Equivalent of ``pandas DataFrame({"factor": ..., "fwd_ret": ...}).dropna()``
    plus the historical ``np.isfinite`` guard.
    """
    _ensure_ts_value_frame(factor, "factor")
    _ensure_ts_value_frame(fwd_ret, "fwd_ret")
    factor = _cast_temporal_ts_to_ns(factor)
    fwd_ret = _cast_temporal_ts_to_ns(fwd_ret)
    keys = _join_keys(factor, fwd_ret)
    _ensure_unique_identity_keys(factor, keys, "factor")
    _ensure_unique_identity_keys(fwd_ret, keys, "fwd_ret")
    _ensure_factor_keys_covered(factor, fwd_ret, keys)

    paired = (
        factor.rename({_VAL_COL: "factor"})
        .join(fwd_ret.rename({_VAL_COL: "fwd_ret"}), on=keys, how="inner")
        .drop_nulls()
        .filter(pl.col("factor").is_finite() & pl.col("fwd_ret").is_finite())
    )
    return paired


# ---------------------------------------------------------------------------
# compute_ic_series — per-period (daily / weekly) rank IC
# ---------------------------------------------------------------------------

def compute_ic_series(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    method: str = "spearman",
    freq: str = "D",
) -> pl.DataFrame:
    """Per-period Rank IC (Spearman by default).

    Returns a 2-col DataFrame with columns ``[date (Utf8), ic (Float64)]``.
    Groups with < 20 observations or a non-finite IC are dropped. If fewer
    than 30 valid pairs exist overall, returns an empty frame (with the
    canonical column order) so :func:`compute_ic_summary` short-circuits to
    the zero summary.
    """
    if method not in {"spearman", "pearson"}:
        raise ValueError("method must be 'spearman' or 'pearson'")
    _ensure_ts_value_frame(factor, "factor")
    _ensure_ts_value_frame(fwd_ret, "fwd_ret")
    for frame_name, frame in (("factor", factor), ("fwd_ret", fwd_ret)):
        ts_dtype = frame.schema[_TS_COL]
        if not ts_dtype.is_temporal():
            raise ValueError(
                f"{frame_name}.{_TS_COL!r} must be a datetime/date column for IC "
                f"frequency bucketing; got {ts_dtype}"
            )

    paired = _build_paired(factor, fwd_ret)
    empty_schema = {"date": pl.Utf8, "ic": pl.Float64}
    if paired.height < 30:
        return pl.DataFrame(schema=empty_schema)

    bucket = pl.col(_TS_COL).dt.truncate(_to_polars_freq(freq)).alias("bucket")
    grouped = (
        paired.with_columns(bucket)
        .group_by("bucket", maintain_order=True)
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col("factor"), pl.col("fwd_ret"), method=method).alias("ic"),
        )
        .filter((pl.col("n") >= 20) & pl.col("ic").is_not_null() & pl.col("ic").is_finite())
    )
    if grouped.height == 0:
        return pl.DataFrame(schema=empty_schema)

    return grouped.select(
        pl.col("bucket").map_elements(
            lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x),
            return_dtype=pl.Utf8,
        ).alias("date"),
        pl.col("ic").round(6).alias("ic"),
    )


# ---------------------------------------------------------------------------
# compute_ic_summary — IR / t-stat / positive-pct aggregation
# ---------------------------------------------------------------------------

_EMPTY_SUMMARY: dict[str, float] = {
    "ic_mean": 0,
    "ic_std": 0,
    "ir": 0,
    "ic_positive_pct": 0,
    "ic_max_abs": 0,
    "ic_tstat": 0,
}


def compute_ic_summary(ic_series: pl.DataFrame) -> dict[str, float]:
    """Aggregate per-period IC values into a 6-key summary.

    Contract preserved from the legacy pandas implementation:
        * ``np.std`` uses ``ddof=0`` (population standard deviation).
        * ``ic_positive_pct`` counts strict ``ic > 0`` (zeros do not count).
        * ``ir`` / ``ic_tstat`` collapse to ``0`` when the std is below a
          ``1e-12`` IEEE-noise floor (otherwise constant-value IC arrays
          would emit 10^15-scale residue from the float representation of
          ``0.1`` / ``0.3`` / ...).
    """
    if ic_series.height == 0 or "ic" not in ic_series.columns:
        return dict(_EMPTY_SUMMARY)

    ics = np.asarray(ic_series["ic"].to_numpy(), dtype=float)
    ics = ics[np.isfinite(ics)]
    if len(ics) == 0:
        return dict(_EMPTY_SUMMARY)

    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics))
    # IEEE noise floor — see legacy module comment for rationale.
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


# ---------------------------------------------------------------------------
# compute_ic_decay — IC at multiple forward horizons
# ---------------------------------------------------------------------------

# Fibonacci-ish default lag grid — matches legacy behaviour.
_DEFAULT_LAGS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89)


def compute_ic_decay(
    factor: pl.DataFrame,
    close: pl.DataFrame,
    lags: list[int] | None = None,
) -> list[dict]:
    """IC at multiple forward horizons (decay curve).

    Output shape: ``[{"lag": int, "ic": float}, ...]`` — same as legacy.
    Lag groups with fewer than 30 paired observations emit ``ic=0``.
    """
    _ensure_ts_value_frame(factor, "factor")
    _ensure_ts_value_frame(close, "close")
    if lags is None:
        lags = list(_DEFAULT_LAGS)

    results: list[dict] = []
    for lag in lags:
        fwd_df = forward_returns(close, lag)
        paired = _build_paired(factor, fwd_df)
        if paired.height < 30:
            results.append({"lag": lag, "ic": 0})
            continue
        ic_arr = paired.select(
            pl.corr(pl.col("factor"), pl.col("fwd_ret"), method="spearman").alias("ic")
        )
        ic_val = ic_arr.item()
        results.append({
            "lag": lag,
            "ic": round(float(ic_val), 6) if ic_val is not None and np.isfinite(ic_val) else 0,
        })

    return results


# ---------------------------------------------------------------------------
# compute_half_life — first lag where |IC| drops to ≤ half of max |IC|
# ---------------------------------------------------------------------------

def compute_half_life(decay: list[dict]) -> int | None:
    """Find half-life from a decay curve.

    Returns ``None`` if the curve is empty or the peak ``|IC|`` is below the
    noise-floor threshold (< 0.001). Otherwise returns the first lag whose
    ``|IC|`` is ≤ half of the peak; falls back to the last lag when no such
    drop is found. Pure Python — works on the same plain-dict output shape
    produced by :func:`compute_ic_decay`.
    """
    if not decay:
        return None
    max_ic = max(abs(d["ic"]) for d in decay)
    if max_ic < 0.001:
        return None
    half = max_ic / 2
    for d in decay:
        if abs(d["ic"]) <= half:
            return d["lag"]
    return decay[-1]["lag"]


__all__ = [
    "compute_ic_decay",
    "compute_ic_series",
    "compute_ic_summary",
    "compute_half_life",
    "forward_returns",
]
