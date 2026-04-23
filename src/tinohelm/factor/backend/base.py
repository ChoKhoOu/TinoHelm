"""Abstract backend interface for panel factor computation.

All operators accept and return ``Panel`` (``pd.DataFrame`` with a
``DatetimeIndex`` as index and symbol strings as columns).

Axis convention (project-wide)
------------------------------
- ``axis=0`` — **cross-sectional** operation: same timestamp, across symbols.
  This is the standard quant convention for "ranking within the universe at
  time *t*".  Internally maps to ``DataFrame.rank(axis=1, ...)`` because
  pandas ``axis=1`` operates row-wise (i.e., across columns = symbols).
- ``axis=1`` — **time-series** operation: same symbol, across time.
  Maps to ``DataFrame.rank(axis=0, ...)``.

NaN handling
------------
Each operator documents its NaN propagation policy.  The default is to
delegate to pandas, which propagates NaN conservatively (NaN in → NaN out
for most rolling/shift ops).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from tinohelm.factor.types import Panel


class AbstractBackend(ABC):
    """Minimal operator set for declarative factor computation.

    All methods are pure (no side effects) and return a new ``Panel``.
    Implementations must not mutate the input ``panel``.
    """

    # ------------------------------------------------------------------
    # Time-series operators
    # ------------------------------------------------------------------

    @abstractmethod
    def shift(self, panel: Panel, n: int) -> Panel:
        """Shift all values by ``n`` periods along the time axis.

        Equivalent to ``panel.shift(n)`` — positive *n* shifts values
        forward (introduces NaN at the start), negative *n* shifts
        backward (introduces NaN at the end).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        n:
            Number of periods to shift.  May be negative.

        Returns
        -------
        Panel
            Shifted panel with NaN in the first/last ``abs(n)`` rows.
        """

    @abstractmethod
    def rolling(
        self,
        panel: Panel,
        window: int,
        op: Literal["mean", "sum", "std", "min", "max"],
        min_periods: int | None = None,
    ) -> Panel:
        """Apply a rolling aggregation along the time axis.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        window:
            Rolling window size in bars.  Must be >= 1.
        op:
            Aggregation function: ``"mean"``, ``"sum"``, ``"std"``,
            ``"min"``, or ``"max"``.
        min_periods:
            Minimum number of non-NaN observations required to produce a
            result.  If ``None``, defaults to ``window`` (pandas default).
            Pass ``1`` to allow results with a single observation.

        Returns
        -------
        Panel
            Rolling-aggregated panel.  First ``window - 1`` rows are NaN
            when ``min_periods`` is ``None``.
        """

    @abstractmethod
    def diff(self, panel: Panel, n: int = 1) -> Panel:
        """Compute the first discrete difference along the time axis.

        Equivalent to ``panel.diff(n)``.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        n:
            Periods to shift for computing the difference.  Default 1.

        Returns
        -------
        Panel
            Differenced panel.
        """

    @abstractmethod
    def pct_change(self, panel: Panel, n: int = 1) -> Panel:
        """Compute percentage change along the time axis.

        Equivalent to ``panel.pct_change(n)``.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        n:
            Periods to shift for computing percentage change.  Default 1.

        Returns
        -------
        Panel
            Percentage-change panel.
        """

    @abstractmethod
    def ewm(
        self,
        panel: Panel,
        span: int | None = None,
        alpha: float | None = None,
        op: Literal["mean", "std"] = "mean",
    ) -> Panel:
        """Exponentially weighted moving aggregation along the time axis.

        Exactly one of ``span`` or ``alpha`` must be provided.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        span:
            Specify decay in terms of span (*com = (span - 1) / 2*).
        alpha:
            Specify smoothing factor directly (0 < alpha <= 1).
        op:
            Aggregation: ``"mean"`` or ``"std"``.

        Returns
        -------
        Panel
            EWM-aggregated panel.
        """

    # ------------------------------------------------------------------
    # Cross-sectional / element-wise operators
    # ------------------------------------------------------------------

    @abstractmethod
    def rank(
        self,
        panel: Panel,
        axis: int = 0,
        pct: bool = True,
    ) -> Panel:
        """Rank values with optional percentage normalization.

        Project axis convention
        ~~~~~~~~~~~~~~~~~~~~~~~
        - ``axis=0`` (default) — **cross-sectional rank**: ranks across
          symbols at each timestamp.  For a universe of *N* symbols,
          the rank vector at each row sums to *N* (or lies in ``[0, 1]``
          when ``pct=True``).  Pandas equivalent: ``df.rank(axis=1)``.
        - ``axis=1`` — **time-series rank**: ranks across time for each
          symbol independently.  Pandas equivalent: ``df.rank(axis=0)``.

        NaN handling: NaN values are excluded from ranking (``na_option
        = "keep"`` in pandas, which assigns NaN to NaN inputs).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        axis:
            0 = cross-sectional (across symbols), 1 = time-series
            (across time).
        pct:
            If ``True``, return rank percentile in ``[0, 1]``.

        Returns
        -------
        Panel
            Ranked panel.  Values in ``[0, 1]`` when ``pct=True``.
        """

    @abstractmethod
    def clip(
        self,
        panel: Panel,
        low: float | None,
        high: float | None,
    ) -> Panel:
        """Clip values to the ``[low, high]`` interval.

        Values below ``low`` are set to ``low``; values above ``high``
        are set to ``high``.  Either bound may be ``None`` (unbounded).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        low:
            Lower bound (inclusive).  ``None`` = no lower bound.
        high:
            Upper bound (inclusive).  ``None`` = no upper bound.

        Returns
        -------
        Panel
            Clipped panel.
        """

    @abstractmethod
    def zscore(self, panel: Panel, axis: int = 0) -> Panel:
        """Standardize values to zero-mean, unit-variance.

        Applies the same axis convention as :meth:`rank`:
        - ``axis=0`` — cross-sectional z-score (standardize across
          symbols at each timestamp).
        - ``axis=1`` — time-series z-score (standardize across time for
          each symbol).

        NaN values are excluded from mean/std computation.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).
        axis:
            0 = cross-sectional, 1 = time-series.

        Returns
        -------
        Panel
            Z-scored panel.
        """

    @abstractmethod
    def log(self, panel: Panel) -> Panel:
        """Element-wise natural logarithm.

        NaN propagation: ``log(x)`` for ``x <= 0`` produces NaN (numpy
        default behaviour).

        Parameters
        ----------
        panel:
            Input panel (time × symbol).  Values should be positive.

        Returns
        -------
        Panel
            Log-transformed panel.
        """

    @abstractmethod
    def abs(self, panel: Panel) -> Panel:
        """Element-wise absolute value.

        Parameters
        ----------
        panel:
            Input panel (time × symbol).

        Returns
        -------
        Panel
            Absolute-value panel.
        """
