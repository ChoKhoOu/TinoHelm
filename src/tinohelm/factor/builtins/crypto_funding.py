"""Crypto funding-rate factors — funding_rate_level, funding_rate_mom.

All kernels operate on a polars wide-table :data:`Panel`
(``column "ts" + N symbol columns``) and return the same shape.

These factors had no counterpart in the legacy pandas ``research/factors.py``
module, but they are still validated against the regression oracle parquet
(:func:`tests.factor._legacy_pandas._build_oracle`) which computes the
intended results with pure numpy/pandas — the polars output must match
within ``abs <= 1e-6``.
"""
from __future__ import annotations

import polars as pl

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


_TS_COL = "ts"


def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="资金费率",
    lookback=1,
    params={"lookback": 1},
    description="资金费率水平 — raw funding rate signal",
)
def funding_rate_level(funding_rate: Panel, params=None) -> Panel:
    """Raw funding-rate level: pass-through identity.

    The DataLayer is responsible for aligning the funding-rate cadence onto
    the bar timestamp grid; this kernel just returns a fresh clone so the
    caller can mutate the result without touching the input.
    """
    return funding_rate.clone()


@factor(
    category="资金费率",
    lookback=2,
    params={"lookback": 1},
    description="资金费率动量 — funding rate momentum (shift diff)",
)
def funding_rate_mom(funding_rate: Panel, params=None) -> Panel:
    """Funding rate momentum: ``funding_rate.diff(n)``.

    ``lookback`` controls the diff period (default 1 = one-period change).
    """
    n = (params or {}).get("lookback", 1)
    cols = _value_cols(funding_rate)
    if not cols:
        return funding_rate.clone()
    return funding_rate.with_columns(
        [pl.col(c).diff(n).alias(c) for c in cols]
    )
