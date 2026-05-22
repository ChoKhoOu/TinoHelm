"""Abstract backend interface for panel factor computation.

All operators accept and return :data:`~tinohelm.factor.types.Panel`
(``polars.DataFrame`` with column ``ts`` of dtype Datetime plus N symbol
columns of dtype Float64).

Axis convention (project-wide)
------------------------------
- ``axis=0`` — **cross-sectional** operation: same timestamp, across symbols.
  This is the standard quant convention for "ranking within the universe at
  time *t*".  In the wide-table layout this means *across columns* in a
  given row.
- ``axis=1`` — **time-series** operation: same symbol, across time.
  Operates *down* a single column.

NaN handling
------------
Each operator documents its NaN propagation policy.  The default is to
delegate to polars, which propagates ``null`` conservatively (``null`` in →
``null`` out for most rolling/shift ops).

Protocol design
---------------
:class:`AbstractBackend` is a :class:`typing.Protocol` decorated with
:func:`typing.runtime_checkable` so callers may use
``isinstance(obj, AbstractBackend)`` to verify implementations
structurally.  Existing callers that pass instances around as
``AbstractBackend`` continue to work unchanged — concrete backends
(:class:`~tinohelm.factor.backend.polars_backend.PolarsBackend`) do *not*
need to inherit from this class explicitly.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from tinohelm.factor.types import Panel


@runtime_checkable
class AbstractBackend(Protocol):
    """Minimal operator set for declarative factor computation.

    All methods are pure (no side effects) and return a new :data:`Panel`.
    Implementations must not mutate the input ``panel``.

    The class is a structural :class:`typing.Protocol` — any object exposing
    these methods with compatible signatures satisfies it.  Decorated with
    :func:`typing.runtime_checkable`, so :func:`isinstance` checks work at
    runtime (used by tests and the engine to validate backend injections).
    """

    # ------------------------------------------------------------------
    # Time-series operators
    # ------------------------------------------------------------------

    def shift(self, panel: Panel, n: int) -> Panel:
        """Shift all values by ``n`` periods along the time axis.

        Equivalent to ``panel.shift(n)`` — positive *n* shifts values
        forward (introduces ``null`` at the start), negative *n* shifts
        backward (introduces ``null`` at the end).

        Parameters
        ----------
        panel:
            Input panel (``ts`` + N symbol columns).
        n:
            Number of periods to shift.  May be negative.

        Returns
        -------
        Panel
            Shifted panel with ``null`` in the first/last ``abs(n)`` rows.
        """
        ...

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
            Input panel (``ts`` + N symbol columns).
        window:
            Rolling window size in bars.  Must be >= 1.
        op:
            Aggregation function: ``"mean"``, ``"sum"``, ``"std"``,
            ``"min"``, or ``"max"``.
        min_periods:
            Minimum number of non-null observations required to produce a
            result.  If ``None``, defaults to ``window`` (polars default).
            Pass ``1`` to allow results with a single observation.

        Returns
        -------
        Panel
            Rolling-aggregated panel.  First ``window - 1`` rows are
            ``null`` when ``min_periods`` is ``None``.
        """
        ...

    def diff(self, panel: Panel, n: int = 1) -> Panel:
        """Compute the *n*-th discrete difference along the time axis.

        Equivalent to ``panel.diff(n)``.  First *n* rows are ``null``.

        Parameters
        ----------
        panel:
            Input panel.
        n:
            Periods to shift for computing the difference.  Default 1.

        Returns
        -------
        Panel
            Differenced panel.
        """
        ...

    def pct_change(self, panel: Panel, n: int = 1) -> Panel:
        """Compute percentage change along the time axis.

        Equivalent to ``panel.pct_change(n)``.  First *n* rows are
        ``null``.

        Parameters
        ----------
        panel:
            Input panel.
        n:
            Periods to shift for computing percentage change.  Default 1.

        Returns
        -------
        Panel
            Percentage-change panel.
        """
        ...

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
            Input panel.
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
        ...

    # ------------------------------------------------------------------
    # Cross-sectional / element-wise operators
    # ------------------------------------------------------------------

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
          the rank vector at each row spans ``1..N`` (or ``[0, 1]``
          when ``pct=True``).
        - ``axis=1`` — **time-series rank**: ranks across time for each
          symbol independently.

        NaN handling: ``null`` values are excluded from ranking and remain
        ``null`` in the output.

        Parameters
        ----------
        panel:
            Input panel.
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
        ...

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
            Input panel.
        low:
            Lower bound (inclusive).  ``None`` = no lower bound.
        high:
            Upper bound (inclusive).  ``None`` = no upper bound.

        Returns
        -------
        Panel
            Clipped panel.
        """
        ...

    def zscore(self, panel: Panel, axis: int = 0) -> Panel:
        """Standardize values to zero-mean, unit-variance.

        Applies the same axis convention as :meth:`rank`:

        - ``axis=0`` — cross-sectional z-score (standardize across
          symbols at each timestamp).
        - ``axis=1`` — time-series z-score (standardize across time for
          each symbol).

        ``null`` values are excluded from mean/std computation.

        Parameters
        ----------
        panel:
            Input panel.
        axis:
            0 = cross-sectional, 1 = time-series.

        Returns
        -------
        Panel
            Z-scored panel.
        """
        ...

    def log(self, panel: Panel) -> Panel:
        """Element-wise natural logarithm.

        NaN propagation: ``log(x)`` for ``x <= 0`` produces ``null`` /
        ``NaN`` (numpy default behaviour).

        Parameters
        ----------
        panel:
            Input panel.  Values should be positive.

        Returns
        -------
        Panel
            Log-transformed panel.
        """
        ...

    def abs(self, panel: Panel) -> Panel:
        """Element-wise absolute value.

        Parameters
        ----------
        panel:
            Input panel.

        Returns
        -------
        Panel
            Absolute-value panel.
        """
        ...
