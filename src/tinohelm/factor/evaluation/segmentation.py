"""Segmentation evaluation: split factor panels by market regime, evaluate each slice.

Public API
----------
``segment_evaluate(panel, forward_returns, *, btc_close_series, btc_vol_series,
                   funding_series, eval_config) -> dict[str, dict[str, EvalResult]]``

Supported segmentation providers
---------------------------------
btc_trend
    Split timestamps by sign of the 20-bar BTC close percent change:
    ``"up"`` (pct_change_20 > 0) / ``"down"`` (pct_change_20 ≤ 0).

vol_regime
    Split timestamps by BTC realised volatility vs. its median:
    ``"high"`` (vol > median) / ``"low"`` (vol ≤ median).
    Volatility is the rolling 20-bar standard deviation of log returns.

funding_level
    Split timestamps by sign of the funding-rate series:
    ``"positive"`` (funding > 0) / ``"negative"`` (funding ≤ 0).

For each regime, the factor panel and forward returns are filtered to the matching
timestamps; ``Evaluator._evaluate_core`` is called on the slice.  If a slice has
fewer than 30 rows the evaluator short-circuits to a zero ``EvalResult`` — we
return that unchanged so callers always get a well-typed object (never None or a
missing key).

No pandas imports at module top (AC-1 contract of the evaluation package).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

from tinohelm.factor.evaluation.evaluator import Evaluator, _to_ts_value
from tinohelm.factor.types import EvalConfig, EvalResult


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TS_COL = "ts"


def _empty_eval_result() -> EvalResult:
    """Return a zero-initialised EvalResult for empty or short segments."""
    return EvalResult()


def _filter_panel_by_ts(panel: pl.DataFrame, ts_set: set) -> pl.DataFrame:
    """Filter ``panel`` rows whose ``ts`` value is in ``ts_set``.

    Works for any polars-native hashable ts type (Datetime, Date, Int64, etc.).
    Returns an empty frame with the same schema when no rows match.
    """
    if not ts_set:
        return panel.filter(pl.lit(False))
    return panel.filter(pl.col(_TS_COL).is_in(list(ts_set)))


def _filter_ts_value_by_ts(df: pl.DataFrame, ts_set: set) -> pl.DataFrame:
    """Filter a 2-col ``[ts, value]`` frame to matching timestamps."""
    if not ts_set:
        return df.filter(pl.lit(False))
    return df.filter(pl.col(_TS_COL).is_in(list(ts_set)))


def _evaluate_slice(
    panel: pl.DataFrame,
    fwd_df: pl.DataFrame,
    eval_config: EvalConfig,
) -> EvalResult:
    """Run ``Evaluator._evaluate_core`` on a (panel, fwd_df) slice.

    Returns ``EvalResult()`` (zero) when the slice is empty rather than raising.
    The evaluator's own short-circuit handles slices with < 30 paired observations.
    """
    if panel.height == 0 or fwd_df.height == 0:
        return _empty_eval_result()
    try:
        evaluator = Evaluator()
        result, _, _, _ = evaluator._evaluate_core(panel, fwd_df, eval_config)
        return result
    except Exception:
        return _empty_eval_result()


def _series_to_aligned_frame(series: pl.Series, ref_ts: pl.Series) -> pl.DataFrame:
    """Wrap a pl.Series into a ``[ts, value]`` frame aligned to ``ref_ts``.

    When ``series`` has the same length as ``ref_ts`` we assume positional
    alignment (common in test fixtures and data-layer outputs).  The caller is
    responsible for ensuring lengths match.
    """
    if len(series) != len(ref_ts):
        raise ValueError(
            f"series length {len(series)} does not match ref_ts length {len(ref_ts)}"
        )
    return pl.DataFrame({_TS_COL: ref_ts, "value": series})


# ---------------------------------------------------------------------------
# Segmentation providers
# ---------------------------------------------------------------------------

def _btc_trend_masks(
    btc_close_series: pl.Series,
    ref_ts: pl.Series,
) -> tuple[set, set]:
    """Return (up_ts_set, down_ts_set) using 20-bar pct_change on BTC close.

    ``up`` = pct_change_20 > 0, ``down`` = pct_change_20 ≤ 0 (including NaN rows
    which fall into down to avoid silent exclusion of leading warmup bars).
    """
    close_arr = btc_close_series.to_numpy()
    n = len(close_arr)
    # pct_change(20): ret[t] = (close[t] - close[t-20]) / close[t-20]
    ret = np.full(n, np.nan)
    for i in range(20, n):
        prev = close_arr[i - 20]
        if prev != 0 and math.isfinite(prev) and math.isfinite(close_arr[i]):
            ret[i] = (close_arr[i] - prev) / prev

    ts_list = ref_ts.to_list()
    up_set: set = set()
    down_set: set = set()
    for i, ts_val in enumerate(ts_list):
        r = ret[i]
        if math.isfinite(r) and r > 0:
            up_set.add(ts_val)
        else:
            down_set.add(ts_val)
    return up_set, down_set


def _vol_regime_masks(
    btc_vol_series: pl.Series,
    ref_ts: pl.Series,
) -> tuple[set, set]:
    """Return (high_ts_set, low_ts_set) using rolling 20-bar std of log returns.

    Computes realised vol from ``btc_vol_series`` (treated as close prices).
    Median split: ``high`` = vol > median, ``low`` = vol ≤ median.
    NaN vol rows are put in ``low`` to keep all timestamps covered.
    """
    close_arr = btc_vol_series.to_numpy()
    n = len(close_arr)
    vol = np.full(n, np.nan)
    for i in range(1, n):
        window_start = max(0, i - 19)
        window = close_arr[window_start : i + 1]
        # log returns within the window
        log_rets = []
        for j in range(1, len(window)):
            p_prev = window[j - 1]
            p_cur = window[j]
            if p_prev > 0 and p_cur > 0 and math.isfinite(p_prev) and math.isfinite(p_cur):
                log_rets.append(math.log(p_cur / p_prev))
        if len(log_rets) >= 2:
            vol[i] = float(np.std(log_rets))

    finite_vols = vol[np.isfinite(vol)]
    median_vol = float(np.median(finite_vols)) if len(finite_vols) > 0 else 0.0

    ts_list = ref_ts.to_list()
    high_set: set = set()
    low_set: set = set()
    for i, ts_val in enumerate(ts_list):
        v = vol[i]
        if math.isfinite(v) and v > median_vol:
            high_set.add(ts_val)
        else:
            low_set.add(ts_val)
    return high_set, low_set


def _funding_level_masks(
    funding_series: pl.Series,
    ref_ts: pl.Series,
) -> tuple[set, set]:
    """Return (positive_ts_set, negative_ts_set) by sign of the funding-rate series.

    ``positive`` = funding > 0, ``negative`` = funding ≤ 0 (includes zero and NaN).
    """
    vals = funding_series.to_numpy()
    ts_list = ref_ts.to_list()
    pos_set: set = set()
    neg_set: set = set()
    for i, ts_val in enumerate(ts_list):
        v = vals[i]
        if math.isfinite(float(v)) and float(v) > 0:
            pos_set.add(ts_val)
        else:
            neg_set.add(ts_val)
    return pos_set, neg_set


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def segment_evaluate(
    panel: pl.DataFrame,
    forward_returns_df: pl.DataFrame,
    *,
    btc_close_series: pl.Series | None = None,
    btc_vol_series: pl.Series | None = None,
    funding_series: pl.Series | None = None,
    eval_config: EvalConfig | None = None,
) -> dict[str, dict[str, EvalResult]]:
    """Evaluate a factor panel across market-regime segments.

    Produces per-regime IC / IR summaries by slicing ``panel`` and
    ``forward_returns_df`` to the timestamps belonging to each regime label.

    Parameters
    ----------
    panel:
        Factor panel — ``pl.DataFrame`` with a ``ts`` column and N symbol columns
        (wide format) or a 2-col ``[ts, value]`` frame (single-symbol).
    forward_returns_df:
        Pre-built forward-return panel compatible with ``Evaluator._evaluate_core``.
    btc_close_series:
        BTC close-price series positionally aligned to ``panel``'s ``ts`` column.
        When provided, activates the ``btc_trend`` segmentation.
    btc_vol_series:
        BTC close-price series for volatility estimation, positionally aligned to
        ``panel``'s ``ts`` column.  When provided, activates ``vol_regime``.
    funding_series:
        Funding-rate series positionally aligned to ``panel``'s ``ts`` column.
        When provided, activates ``funding_level``.
    eval_config:
        ``EvalConfig`` forwarded to ``Evaluator._evaluate_core``.  Defaults to a
        minimal config when ``None`` (daily IC freq, 5-bar forward period).

    Returns
    -------
    dict[str, dict[str, EvalResult]]
        A dict keyed by active provider names.  Each value is a dict mapping
        regime labels to ``EvalResult`` objects::

            {
                "btc_trend": {"up": EvalResult, "down": EvalResult},
                "vol_regime": {"high": EvalResult, "low": EvalResult},
                "funding_level": {"positive": EvalResult, "negative": EvalResult},
            }

        Only providers whose input series was supplied appear in the output.
        Empty slices return a zero-initialised ``EvalResult`` (never ``None``).
    """
    if eval_config is None:
        eval_config = EvalConfig(universe=(), start="", end="")

    # Extract the ts column from the panel for mask computation.
    if _TS_COL not in panel.columns:
        raise ValueError(f"panel is missing required '{_TS_COL}' column")
    ref_ts: pl.Series = panel[_TS_COL]

    # Flatten forward_returns_df to [ts, value] so it can be filtered by ts set.
    fwd_flat = _to_ts_value(forward_returns_df)

    out: dict[str, dict[str, EvalResult]] = {}

    # --- btc_trend ---
    if btc_close_series is not None:
        up_set, down_set = _btc_trend_masks(btc_close_series, ref_ts)
        up_panel = _filter_panel_by_ts(panel, up_set)
        down_panel = _filter_panel_by_ts(panel, down_set)
        up_fwd = _filter_ts_value_by_ts(fwd_flat, up_set)
        down_fwd = _filter_ts_value_by_ts(fwd_flat, down_set)
        out["btc_trend"] = {
            "up": _evaluate_slice(up_panel, up_fwd, eval_config),
            "down": _evaluate_slice(down_panel, down_fwd, eval_config),
        }

    # --- vol_regime ---
    if btc_vol_series is not None:
        high_set, low_set = _vol_regime_masks(btc_vol_series, ref_ts)
        high_panel = _filter_panel_by_ts(panel, high_set)
        low_panel = _filter_panel_by_ts(panel, low_set)
        high_fwd = _filter_ts_value_by_ts(fwd_flat, high_set)
        low_fwd = _filter_ts_value_by_ts(fwd_flat, low_set)
        out["vol_regime"] = {
            "high": _evaluate_slice(high_panel, high_fwd, eval_config),
            "low": _evaluate_slice(low_panel, low_fwd, eval_config),
        }

    # --- funding_level ---
    if funding_series is not None:
        pos_set, neg_set = _funding_level_masks(funding_series, ref_ts)
        pos_panel = _filter_panel_by_ts(panel, pos_set)
        neg_panel = _filter_panel_by_ts(panel, neg_set)
        pos_fwd = _filter_ts_value_by_ts(fwd_flat, pos_set)
        neg_fwd = _filter_ts_value_by_ts(fwd_flat, neg_set)
        out["funding_level"] = {
            "positive": _evaluate_slice(pos_panel, pos_fwd, eval_config),
            "negative": _evaluate_slice(neg_panel, neg_fwd, eval_config),
        }

    return out


__all__ = ["segment_evaluate"]
