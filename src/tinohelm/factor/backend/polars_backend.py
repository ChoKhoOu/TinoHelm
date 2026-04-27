"""Polars-native implementation of the :class:`AbstractBackend` Protocol.

The wide-table contract for every operator is:

- Input: ``polars.DataFrame`` with column ``ts`` (Datetime) plus N symbol
  columns (Float64).  The ``ts`` column is treated as the time axis;
  symbol columns are the cross-sectional dimension.
- Output: same shape (same ``ts`` column, same symbol columns in the
  same order); only the values of the symbol columns change.

All methods are pure — the input panel is never mutated and the result is
always a freshly-built :class:`polars.DataFrame`.
"""
from __future__ import annotations

from typing import Literal

import polars as pl

from tinohelm.factor.types import Panel


# Column name used for the time axis in every panel.
_TS_COL: str = "ts"

# Mapping from rolling op name to the polars expression factory.
_ROLLING_FACTORIES: dict[
    str,
    "callable[[str, int, int | None], pl.Expr]",
] = {
    "mean": lambda c, w, mp: pl.col(c).rolling_mean(window_size=w, min_samples=mp),
    "sum": lambda c, w, mp: pl.col(c).rolling_sum(window_size=w, min_samples=mp),
    "std": lambda c, w, mp: pl.col(c).rolling_std(window_size=w, min_samples=mp),
    "min": lambda c, w, mp: pl.col(c).rolling_min(window_size=w, min_samples=mp),
    "max": lambda c, w, mp: pl.col(c).rolling_max(window_size=w, min_samples=mp),
}


def _mask_nonfinite_to_null(col: pl.Expr) -> pl.Expr:
    """Return *col* with NaN / ±inf replaced by ``null``.

    DataLayer uses ``float('nan')`` to mark "symbol not in PIT universe" or
    rolling warmup rows that have not yet matured.  Polars treats NaN as a
    valid ``Float64`` value — ``is_not_null()`` does **not** exclude it, so
    cross-sectional aggregations (rank, zscore) that use ``over("ts")`` would
    include NaN cells and contaminate the result.

    This helper is called once at the start of each cross-sectional branch
    to turn every non-finite cell into a proper ``null`` before any
    ``.over()`` aggregation runs.  The contract "null in → null out" is
    preserved because the NaN stays null through the round-trip.
    """
    return pl.when(col.is_finite()).then(col).otherwise(None)


class PolarsBackend:
    """Polars backend for panel factor operators.

    Implements the structural contract defined by
    :class:`~tinohelm.factor.backend.base.AbstractBackend`.  Does not
    inherit from it — duck-typing via :class:`typing.Protocol` is enough.

    Axis convention (mirrors :class:`AbstractBackend`)
    --------------------------------------------------
    The project uses a **quant-centric** axis numbering where ``axis=0``
    means "cross-sectional" (same timestamp, across symbols).  In the
    polars wide-table layout this corresponds to operating *across* the
    symbol columns of a given row.  ``axis=1`` is the time-series axis,
    operating *down* a single symbol column.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _value_cols(panel: Panel) -> list[str]:
        """Return the list of symbol columns (everything except ``ts``)."""
        return [c for c in panel.columns if c != _TS_COL]

    @staticmethod
    def _ensure_ts_column(panel: Panel) -> None:
        """Validate that the panel has a ``ts`` column."""
        if _TS_COL not in panel.columns:
            raise ValueError(
                f"PolarsBackend expects a column named {_TS_COL!r}; "
                f"got columns: {panel.columns!r}"
            )

    # ------------------------------------------------------------------
    # Time-series operators
    # ------------------------------------------------------------------

    def shift(self, panel: Panel, n: int) -> Panel:
        """Shift symbol columns by *n* periods along the time axis.

        Positive *n* lags the data (introduces ``null`` at the beginning);
        negative *n* leads it (``null`` at the end).  The ``ts`` column is
        left untouched.
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns([pl.col(c).shift(n).alias(c) for c in cols])

    def rolling(
        self,
        panel: Panel,
        window: int,
        op: Literal["mean", "sum", "std", "min", "max"],
        min_periods: int | None = None,
    ) -> Panel:
        """Rolling aggregation along the time axis.

        Delegates to the corresponding ``pl.col(c).rolling_<op>`` polars
        expression.  ``min_periods`` is forwarded as polars'
        ``min_samples`` argument; ``None`` defaults to ``window``.

        Raises
        ------
        ValueError
            If ``op`` is not one of the supported aggregations or
            ``window`` is below 1.
        """
        if op not in _ROLLING_FACTORIES:
            raise ValueError(
                f"Unsupported rolling op {op!r}. "
                f"Choose from: {list(_ROLLING_FACTORIES)}"
            )
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        factory = _ROLLING_FACTORIES[op]
        return panel.with_columns([factory(c, window, min_periods).alias(c) for c in cols])

    def diff(self, panel: Panel, n: int = 1) -> Panel:
        """First (or *n*-th) discrete difference along the time axis.

        Delegates to ``pl.col(c).diff(n)``; first *n* rows are ``null``.
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns([pl.col(c).diff(n).alias(c) for c in cols])

    def pct_change(self, panel: Panel, n: int = 1) -> Panel:
        """Percentage change along the time axis.

        Delegates to ``pl.col(c).pct_change(n)``; first *n* rows are
        ``null``.  Division-by-zero produces ``inf`` (numpy default).
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns([pl.col(c).pct_change(n).alias(c) for c in cols])

    def ewm(
        self,
        panel: Panel,
        span: int | None = None,
        alpha: float | None = None,
        op: Literal["mean", "std"] = "mean",
    ) -> Panel:
        """Exponentially weighted moving aggregation along the time axis.

        Exactly one of ``span`` or ``alpha`` must be provided.  Delegates
        to ``pl.col(c).ewm_<op>(span=...) / ewm_<op>(alpha=...)``.

        Raises
        ------
        ValueError
            If both / neither of ``span`` and ``alpha`` are given, or
            ``op`` is not ``"mean"`` / ``"std"``.
        """
        if (span is None) == (alpha is None):
            raise ValueError(
                "Exactly one of 'span' or 'alpha' must be provided."
            )
        if op not in ("mean", "std"):
            raise ValueError(f"Unsupported ewm op {op!r}. Choose 'mean' or 'std'.")
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        if op == "mean":
            exprs = [pl.col(c).ewm_mean(span=span, alpha=alpha).alias(c) for c in cols]
        else:
            exprs = [pl.col(c).ewm_std(span=span, alpha=alpha).alias(c) for c in cols]
        return panel.with_columns(exprs)

    # ------------------------------------------------------------------
    # Cross-sectional / element-wise operators
    # ------------------------------------------------------------------

    def rank(
        self,
        panel: Panel,
        axis: int = 0,
        pct: bool = True,
    ) -> Panel:
        """Rank values with optional percentile normalization.

        Project axis convention (quant-centric)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        - ``axis=0`` (default) — **cross-sectional rank**: ranks across
          symbols at each timestamp independently.  Implemented by
          unpivoting the wide table to long form, ranking ``over("ts")``,
          and pivoting back.  When ``pct=True`` the rank is divided by
          the per-row count of non-null values, giving values in
          ``(0, 1]`` for valid cells (``null`` for null inputs).
        - ``axis=1`` — **time-series rank**: ranks the time-history of
          each symbol independently.  Uses the per-column polars
          :py:meth:`pl.Expr.rank` directly.

        ``null`` inputs always produce ``null`` outputs.

        Raises
        ------
        ValueError
            If ``axis`` is not 0 or 1.
        """
        if axis not in (0, 1):
            raise ValueError(f"axis must be 0 or 1, got {axis!r}")
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()

        if axis == 1:
            # Time-series rank — operate down each column independently.
            # Polars ``Expr.rank`` propagates ``null`` automatically and the
            # method ``"average"`` matches pandas' default rank semantics.
            # Mask NaN/inf → null before ranking so that non-finite warmup
            # values are excluded from the per-column rank and the pct
            # denominator (is_finite counts only valid floats, not NaN).
            method = "average"
            # First mask non-finite values to null in the input panel so
            # that rank propagates null correctly for NaN cells.
            masked = panel.with_columns(
                [_mask_nonfinite_to_null(pl.col(c)).alias(c) for c in cols]
            )
            exprs = [
                pl.col(c).rank(method=method, descending=False).cast(pl.Float64).alias(c)
                for c in cols
            ]
            ranked = masked.with_columns(exprs)
            if not pct:
                return ranked
            # Convert to percentile by dividing by the per-column finite count
            # from the *original masked* panel (after null-masking, is_not_null
            # correctly counts only finite values).
            pct_exprs: list[pl.Expr] = []
            for c in cols:
                cnt_expr = pl.col(c).is_not_null().sum().cast(pl.Float64)
                pct_exprs.append((pl.col(c) / cnt_expr).alias(c))
            return ranked.with_columns(pct_exprs)

        # Cross-sectional rank (axis=0): unpivot → rank.over("ts") → pivot back.
        # Mask NaN/inf → null before unpivoting so that non-finite cells are
        # excluded from the cross-sectional rank and pct denominator.
        masked_panel = panel.with_columns(
            [_mask_nonfinite_to_null(pl.col(c)).alias(c) for c in cols]
        )
        long = masked_panel.unpivot(
            index=[_TS_COL],
            on=cols,
            variable_name="__symbol",
            value_name="__v",
        )
        rank_expr = pl.col("__v").rank(method="average", descending=False).over(_TS_COL)
        long = long.with_columns(rank_expr.cast(pl.Float64).alias("__r"))
        if pct:
            cnt_expr = pl.col("__v").is_not_null().sum().over(_TS_COL).cast(pl.Float64)
            long = long.with_columns((pl.col("__r") / cnt_expr).alias("__r"))
        wide = long.select([_TS_COL, "__symbol", "__r"]).pivot(
            index=_TS_COL,
            on="__symbol",
            values="__r",
        )
        # Restore original symbol-column ordering (pivot does not preserve it).
        return wide.select([_TS_COL, *cols])

    def clip(
        self,
        panel: Panel,
        low: float | None,
        high: float | None,
    ) -> Panel:
        """Clip symbol-column values to ``[low, high]``.

        Either bound may be ``None`` (unbounded).  ``null`` values pass
        through unchanged.
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        if low is None and high is None:
            return panel.clone()
        return panel.with_columns(
            [pl.col(c).clip(lower_bound=low, upper_bound=high).alias(c) for c in cols]
        )

    def zscore(self, panel: Panel, axis: int = 0) -> Panel:
        """Z-score standardisation.

        For ``axis=0`` (cross-sectional): at each timestamp, subtract
        the mean across symbols and divide by the std across symbols.
        Implemented via unpivot/pivot with ``over("ts")``.

        For ``axis=1`` (time-series): for each symbol, subtract its
        historical mean and divide by its historical std.

        ``null`` values are excluded from mean/std.  A row/column with
        zero std produces ``inf`` / ``NaN`` according to numpy
        semantics — callers that need NaN-safe output should mask
        afterwards.

        Raises
        ------
        ValueError
            If ``axis`` is not 0 or 1.
        """
        if axis not in (0, 1):
            raise ValueError(f"axis must be 0 or 1, got {axis!r}")
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()

        if axis == 1:
            # Mask NaN/inf → null before computing per-column mean/std so that
            # a single NaN warmup cell does not contaminate the entire column's
            # statistics.  Align with the axis=0 path (lines 348-349) and the
            # axis=1 rank path (lines 245-246) which both apply _mask_nonfinite_to_null
            # for the same reason.
            masked = panel.with_columns(
                [_mask_nonfinite_to_null(pl.col(c)).alias(c) for c in cols]
            )
            exprs = [
                ((pl.col(c) - pl.col(c).mean()) / pl.col(c).std()).alias(c)
                for c in cols
            ]
            return masked.with_columns(exprs)

        # Cross-sectional zscore via unpivot/pivot with ``over("ts")``.
        # Mask NaN/inf → null before unpivoting so that non-finite cells are
        # excluded from mean/std computation and produce null output (not
        # NaN-contaminated values across the entire cross-section).
        masked_panel = panel.with_columns(
            [_mask_nonfinite_to_null(pl.col(c)).alias(c) for c in cols]
        )
        long = masked_panel.unpivot(
            index=[_TS_COL],
            on=cols,
            variable_name="__symbol",
            value_name="__v",
        )
        z_expr = (
            (pl.col("__v") - pl.col("__v").mean().over(_TS_COL))
            / pl.col("__v").std().over(_TS_COL)
        ).alias("__z")
        long = long.with_columns(z_expr)
        wide = long.select([_TS_COL, "__symbol", "__z"]).pivot(
            index=_TS_COL,
            on="__symbol",
            values="__z",
        )
        return wide.select([_TS_COL, *cols])

    def log(self, panel: Panel) -> Panel:
        """Element-wise natural logarithm.

        Inputs ``<= 0`` produce ``-inf`` / ``NaN`` per numpy semantics.
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns([pl.col(c).log().alias(c) for c in cols])

    def abs(self, panel: Panel) -> Panel:
        """Element-wise absolute value.  ``null`` passes through."""
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns([pl.col(c).abs().alias(c) for c in cols])

    # ------------------------------------------------------------------
    # New algos required by the task spec (acceptance criterion #3 lists
    # ``fillna`` among the 10 algorithms; not part of the AbstractBackend
    # Protocol but exposed here for the s06 builtins migration).
    # ------------------------------------------------------------------

    def fillna(self, panel: Panel, value: float = 0.0) -> Panel:
        """Replace ``null`` **and** ``NaN`` values in symbol columns with ``value``.

        Polars distinguishes ``null`` (missing value sentinel) from ``NaN``
        (IEEE-754 not-a-number, a valid ``Float64`` value).  The DataLayer
        uses ``NaN`` to mark "asset not in PIT universe" and rolling warmup
        rows; callers that invoke ``fillna`` expect both to be replaced.

        Implementation: ``fill_null(value)`` handles ``null``; chaining
        ``fill_nan(value)`` then handles ``NaN``.  Order does not matter
        because the two sets are disjoint in Polars ``Float64`` columns.

        Not part of the :class:`AbstractBackend` Protocol — kept here as a
        backend-specific helper.
        """
        self._ensure_ts_column(panel)
        cols = self._value_cols(panel)
        if not cols:
            return panel.clone()
        return panel.with_columns(
            [pl.col(c).fill_null(value).fill_nan(value).alias(c) for c in cols]
        )
