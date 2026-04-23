"""Pandas-native implementation of :class:`AbstractBackend`.

All operators delegate to ``pd.DataFrame`` / ``np.`` APIs directly.
No copies are made beyond what pandas itself performs.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from tinohelm.factor.backend.base import AbstractBackend
from tinohelm.factor.types import Panel

# Mapping from op name to the corresponding Rolling method name.
_ROLLING_OPS: dict[str, str] = {
    "mean": "mean",
    "sum": "sum",
    "std": "std",
    "min": "min",
    "max": "max",
}


class PandasBackend(AbstractBackend):
    """Pandas-native backend for panel factor operators.

    All methods return a new ``Panel`` (``pd.DataFrame``); the input
    is never mutated.

    Axis convention (mirrors :class:`AbstractBackend`)
    --------------------------------------------------
    The project uses a **quant-centric** axis numbering where ``axis=0``
    means "cross-sectional" (same timestamp, across symbols).  This is
    the **opposite** of pandas convention, where ``axis=0`` iterates
    over the row (time) index.  The translation is applied internally:

    - project ``axis=0`` → pandas ``axis=1`` (operate across columns)
    - project ``axis=1`` → pandas ``axis=0`` (operate across rows)

    Callers always use the project convention; the translation is
    transparent.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pandas_axis(project_axis: int) -> int:
        """Convert project axis to pandas axis.

        Project 0 (cross-sectional, across symbols) → pandas 1 (across columns).
        Project 1 (time-series, across time)         → pandas 0 (across rows).
        """
        if project_axis == 0:
            return 1
        if project_axis == 1:
            return 0
        raise ValueError(f"axis must be 0 or 1, got {project_axis!r}")

    # ------------------------------------------------------------------
    # Time-series operators
    # ------------------------------------------------------------------

    def shift(self, panel: Panel, n: int) -> Panel:
        """Shift panel by *n* periods along the time axis.

        Delegates to ``DataFrame.shift(n)``.  Positive *n* lags the
        data (introduces NaN at the beginning); negative *n* leads it.
        """
        return panel.shift(n)

    def rolling(
        self,
        panel: Panel,
        window: int,
        op: Literal["mean", "sum", "std", "min", "max"],
        min_periods: int | None = None,
    ) -> Panel:
        """Rolling aggregation along the time axis.

        Delegates to ``DataFrame.rolling(window, min_periods=min_periods).<op>()``.

        NaN propagation: any window containing fewer than ``min_periods``
        (defaults to ``window``) non-NaN observations produces NaN.
        """
        if op not in _ROLLING_OPS:
            raise ValueError(
                f"Unsupported rolling op {op!r}. "
                f"Choose from: {list(_ROLLING_OPS)}"
            )
        roller = panel.rolling(window, min_periods=min_periods)
        return getattr(roller, _ROLLING_OPS[op])()

    def diff(self, panel: Panel, n: int = 1) -> Panel:
        """First discrete difference along the time axis.

        Delegates to ``DataFrame.diff(n)``.  First *n* rows are NaN.
        """
        return panel.diff(n)

    def pct_change(self, panel: Panel, n: int = 1) -> Panel:
        """Percentage change along the time axis.

        Delegates to ``DataFrame.pct_change(n)``.  First *n* rows are NaN.
        Division-by-zero (zero base) produces ``inf`` (pandas default).
        """
        return panel.pct_change(n)

    def ewm(
        self,
        panel: Panel,
        span: int | None = None,
        alpha: float | None = None,
        op: Literal["mean", "std"] = "mean",
    ) -> Panel:
        """Exponentially weighted moving aggregation.

        Exactly one of ``span`` or ``alpha`` must be provided.
        Delegates to ``DataFrame.ewm(span=..., alpha=...).<op>()``.
        """
        if (span is None) == (alpha is None):
            raise ValueError(
                "Exactly one of 'span' or 'alpha' must be provided."
            )
        if op not in ("mean", "std"):
            raise ValueError(f"Unsupported ewm op {op!r}. Choose 'mean' or 'std'.")
        ewm_obj = panel.ewm(span=span, alpha=alpha)
        return getattr(ewm_obj, op)()

    # ------------------------------------------------------------------
    # Cross-sectional / element-wise operators
    # ------------------------------------------------------------------

    def rank(
        self,
        panel: Panel,
        axis: int = 0,
        pct: bool = True,
    ) -> Panel:
        """Rank panel values with optional percentile normalization.

        Project axis convention (quant-centric)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        - ``axis=0`` (default) — cross-sectional rank: ranks across
          symbols at each timestamp independently.  At each row, the
          symbol with the smallest value gets rank 1 (or ~0.0 in pct
          mode); the largest gets rank *N* (or ~1.0).
          Pandas equivalent: ``df.rank(axis=1, pct=pct, na_option='keep')``.
        - ``axis=1`` — time-series rank: ranks the time-history of each
          symbol independently.
          Pandas equivalent: ``df.rank(axis=0, pct=pct, na_option='keep')``.

        NaN values receive NaN in the output (``na_option='keep'``).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        axis:
            0 = cross-sectional (across symbols), 1 = time-series.
        pct:
            Return percentile rank in ``[0, 1]`` if ``True``.
        """
        pandas_axis = self._to_pandas_axis(axis)
        return panel.rank(axis=pandas_axis, pct=pct, na_option="keep")

    def clip(
        self,
        panel: Panel,
        low: float | None,
        high: float | None,
    ) -> Panel:
        """Clip values to ``[low, high]``.

        Delegates to ``DataFrame.clip(lower=low, upper=high)``.
        NaN values pass through unchanged.
        """
        return panel.clip(lower=low, upper=high)

    def zscore(self, panel: Panel, axis: int = 0) -> Panel:
        """Cross-sectional or time-series z-score standardization.

        For ``axis=0`` (cross-sectional): at each timestamp, subtract
        the mean across symbols and divide by the std across symbols.

        For ``axis=1`` (time-series): for each symbol, subtract its
        historical mean and divide by its historical std.

        NaN values are excluded from mean/std computation (``skipna=True``).
        A column/row with all-NaN produces NaN output; a column/row with
        zero std produces NaN (not inf).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        axis:
            0 = cross-sectional, 1 = time-series.
        """
        pandas_axis = self._to_pandas_axis(axis)
        mean = panel.mean(axis=pandas_axis)
        std = panel.std(axis=pandas_axis)
        # Replace zero std with NaN to avoid inf; broadcast back to panel shape.
        std = std.replace(0.0, float("nan"))
        result = panel.sub(mean, axis=1 - pandas_axis)
        result = result.div(std, axis=1 - pandas_axis)
        return result

    def log(self, panel: Panel) -> Panel:
        """Element-wise natural logarithm via ``np.log``.

        Inputs <= 0 produce NaN (``np.log`` of non-positive returns NaN
        or -inf; pandas wraps this so that -inf becomes NaN through
        numpy warning suppression not applied here — callers should
        ensure positive inputs).
        """
        return pd.DataFrame(
            np.log(panel.values),
            index=panel.index,
            columns=panel.columns,
        )

    def abs(self, panel: Panel) -> Panel:
        """Element-wise absolute value.

        Delegates to ``DataFrame.abs()``.  NaN passes through unchanged.
        """
        return panel.abs()
