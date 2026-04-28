"""Quantile PnL — polars-native (post pandas migration).

Split by factor-value quantile (rank-based bucketing equivalent to
``pd.qcut(..., labels=False)``), compute the average per-period return
per quantile plus a sampled cumulative-return series. The monotonicity
flag mirrors legacy ``is_monotonic`` semantics (``Q1 ≥ Q2 ≥ … ≥ QN``).

Inputs follow the same ``[ts, value]`` 2-col DataFrame convention as
:mod:`tinohelm.factor.evaluation.ic`.

Why we don't use bare ``pl.Series.qcut``
----------------------------------------
:meth:`pl.Series.qcut` returns labels in *bin-creation order*, which is
not necessarily ascending by value (different from
:func:`pandas.qcut(..., labels=False)`). Downstream consumers expect
``Q1 = lowest factor values``, so we run ``qcut`` then remap raw labels
to a canonical ascending ordering via per-bin min lookup.
"""
from __future__ import annotations

import polars as pl

from tinohelm.factor.evaluation.ic import _build_paired


_EMPTY_OUTPUT = {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}


def _bucketize_canonical(
    paired: pl.DataFrame, n_quantiles: int
) -> pl.DataFrame | None:
    """Assign each paired row a canonical 0..n_quantiles-1 bucket label.

    The label ordering is value-ascending (``q=0`` → lowest factor values,
    ``q=n_quantiles-1`` → highest), matching the legacy
    :func:`pandas.qcut(..., labels=False)` semantics that the rest of the
    evaluation pipeline assumes.

    Returns ``None`` when bucketization fails (degenerate factor → fewer
    than 2 unique buckets, or polars qcut raises).
    """
    int_labels = [str(i) for i in range(n_quantiles)]
    try:
        with_q_raw = paired.with_columns(
            pl.col("factor")
            .qcut(n_quantiles, labels=int_labels, allow_duplicates=True)
            .cast(pl.Int8)
            .alias("q_raw")
        ).drop_nulls(subset=["q_raw"])
    except (pl.exceptions.ComputeError, ValueError):
        return None

    if with_q_raw.height == 0:
        return None

    # Build a raw-label → canonical-label remap by sorting raw labels on
    # their per-bin minimum factor value (lowest min → q=0).
    remap = (
        with_q_raw.group_by("q_raw")
        .agg(pl.col("factor").min().alias("__min"))
        .sort("__min")
        .with_row_index("q_canon")
        .select([
            pl.col("q_raw"),
            pl.col("q_canon").cast(pl.Int8).alias("q"),
        ])
    )
    if remap.height < 2:
        return None

    return with_q_raw.join(remap, on="q_raw", how="left").drop("q_raw")


def compute_quantile_returns(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    n_quantiles: int = 5,
) -> dict:
    """Quantile analysis: split by factor value, compute per-quantile returns.

    Returns
    -------
    dict with keys:
        ``avg_returns`` — ``{Q1: float, ...}`` average per-period return per quantile
        ``cum_returns`` — ``{Q1: [{date, cum_ret}], ...}`` sampled cumulative return series
        ``is_monotonic`` — bool, ``True`` if Q1 ≥ Q2 ≥ … ≥ QN

    Guards:
      * Returns empty output if fewer than ``n_quantiles * 20`` paired obs.
      * Handles degenerate factors (constant values) — :meth:`pl.Series.qcut`
        with ``allow_duplicates=True`` collapses bins to a single label;
        downstream filtering keeps the empty payload contract.
    """
    paired = _build_paired(factor, fwd_ret)

    if paired.height < n_quantiles * 20:
        return {**_EMPTY_OUTPUT, "avg_returns": {}, "cum_returns": {}}

    bucketed = _bucketize_canonical(paired, n_quantiles)
    if bucketed is None:
        return {**_EMPTY_OUTPUT, "avg_returns": {}, "cum_returns": {}}

    avg_returns: dict[str, float] = {}
    cum_returns: dict[str, list[dict]] = {}

    unique_qs = sorted(bucketed["q"].unique().to_list())
    for q in unique_qs:
        label = f"Q{int(q) + 1}"
        # Maintain chronological order so cum returns make sense.
        sort_cols = ["ts", "symbol"] if "symbol" in bucketed.columns else ["ts"]
        group = bucketed.filter(pl.col("q") == q).sort(sort_cols)
        avg_returns[label] = round(float(group["fwd_ret"].mean()), 8)

        # Sample cumulative-return series down to ≤ ~100 points (legacy contract
        # — keeps wire payloads small without losing curve shape).
        cum = ((1 + group["fwd_ret"]).cum_prod() - 1).to_list()
        ts_iso = group["ts"].to_list()
        n = len(cum)
        step = max(1, n // 100)
        sampled_pairs = list(zip(ts_iso[::step], cum[::step]))
        cum_returns[label] = [
            {
                "date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                "cum_ret": round(float(v), 6),
            }
            for idx, v in sampled_pairs
        ]

    # Monotonicity check — Q1 ≥ Q2 ≥ … ≥ QN (≥, not strict >).
    vals = list(avg_returns.values())
    is_mono = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))

    return {"avg_returns": avg_returns, "cum_returns": cum_returns, "is_monotonic": is_mono}


__all__ = ["compute_quantile_returns"]
