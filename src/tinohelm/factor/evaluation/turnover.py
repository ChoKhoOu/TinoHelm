"""Turnover stats for factor-based rebalancing — polars-native.

Migrated from ``research.analysis.compute_turnover`` (legacy pandas).
Produces ``{daily, annualized, fee_drag_monthly}`` with the same math
and guards (AC-13.2 — drift ≤ 1e-6).

Design notes
------------
* Assumes daily rebalancing — annualised with 252 trading days.
* Monthly fee-drag assumes 21 trading days/month and a 2-sided fill
  (``daily_turn * 2 * fee_rate * 21``).
* Degenerate factors (constant values) yield :meth:`pl.Series.qcut`
  collapsed bins; we drop those rows so the output is the canonical
  ``{0, 0, 0}`` payload (avoids the "false 100 % turnover" pre-fix
  regression that pandas ``NaN != NaN`` ``.mean()`` produced).
* Quantile labels are remapped to the canonical ascending ordering
  (``q=0`` → lowest factor values) — see
  :func:`tinohelm.factor.evaluation.quantile._bucketize_canonical`.
  Although the per-row turnover math is invariant under label
  permutation (we only care whether the *label* changed between
  consecutive days), reusing the same helper keeps the two modules
  algorithmically aligned.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from tinohelm.factor.evaluation.ic import _build_paired
from tinohelm.factor.evaluation.quantile import _bucketize_canonical


_EMPTY_OUTPUT: dict[str, float] = {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


def compute_turnover(
    factor: pl.DataFrame,
    fwd_ret: pl.DataFrame,
    n_quantiles: int = 5,
    fee_rate: float = 0.0004,
) -> dict[str, float]:
    """Compute turnover stats for factor-based rebalancing.

    Returns
    -------
    dict
        ``{"daily", "annualized", "fee_drag_monthly"}`` — same shape as the
        legacy pandas implementation. Short input (< ``n_quantiles * 20``
        pairs) or degenerate factors return the zero payload.
    """
    paired = _build_paired(factor, fwd_ret)

    if paired.height < n_quantiles * 20:
        return dict(_EMPTY_OUTPUT)

    bucketed = _bucketize_canonical(paired, n_quantiles)
    if bucketed is None:
        return dict(_EMPTY_OUTPUT)

    # Bucket each row into its calendar day, then walk consecutive days
    # comparing quantile labels.  For multi-symbol panels, preserve symbol
    # identity and compare BTC_t with BTC_t+1 rather than using row position
    # (which would silently compare against a different asset when the
    # universe order changes).  For legacy flat series, keep the historical
    # per-row alignment within each day.
    bucketed = bucketed.with_columns(
        pl.col("ts").dt.truncate("1d").alias("day"),
    )
    has_symbol = "symbol" in bucketed.columns
    if has_symbol:
        bucketed = bucketed.sort(["day", "symbol", "ts"]).with_columns(
            pl.col("ts")
            .cum_count()
            .over(["day", "symbol"])
            .cast(pl.Int64)
            .alias("row_in_symbol_day")
        )
        join_cols = ["symbol", "row_in_symbol_day"]
    else:
        # ``row_in_day`` enumerates rows within each day in chronological order.
        bucketed = bucketed.sort(["day", "ts"]).with_columns(
            pl.col("ts").cum_count().over("day").cast(pl.Int64).alias("row_in_day")
        )
        join_cols = ["row_in_day"]

    daily_groups = bucketed.partition_by("day", as_dict=True, maintain_order=True)
    turnovers: list[float] = []
    prev_q: pl.DataFrame | None = None
    for _key, group in daily_groups.items():
        if group.height == 0:
            continue
        curr_q = group.select([*join_cols, "q"])
        if prev_q is not None:
            joined = prev_q.rename({"q": "q_prev"}).join(
                curr_q.rename({"q": "q_curr"}),
                on=join_cols,
                how="inner",
            )
            if joined.height > 0:
                changed_arr = joined.select(
                    (pl.col("q_curr") != pl.col("q_prev")).cast(pl.Float64).alias("changed")
                )["changed"].to_numpy()
                turnovers.append(float(changed_arr.mean()))
        prev_q = curr_q

    daily_turn = float(np.mean(turnovers)) if turnovers else 0
    annual_turn = daily_turn * 252
    fee_drag = daily_turn * 2 * fee_rate * 21  # monthly, 2-sided

    return {
        "daily": round(daily_turn, 4),
        "annualized": round(annual_turn, 1),
        "fee_drag_monthly": round(fee_drag, 4),
    }


__all__ = ["compute_turnover"]
