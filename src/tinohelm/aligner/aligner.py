"""Aligner — Universe PIT mask + cross-section OLS neutralization.

Accepts a :class:`~tinohelm.factor.universe.Universe` and a list of
``neutralize`` providers (either string names or
:class:`~tinohelm.aligner.exposure.ExposureProvider` instances).

Usage
-----
::

    from tinohelm.aligner.aligner import Aligner
    from tinohelm.factor.universe import Universe

    uni = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP"])
    aligner = Aligner(uni, neutralize=["btc_beta"])
    neutralized_panel = aligner.align(panel)

Algorithm
---------
1. **Universe PIT mask**: each ``(ts, sym)`` cell where the symbol was not
   eligible at that timestamp (pre-listing isolation + post-delisting) is
   set to ``null``.

2. **Cross-section OLS residual**: for each timestamp ``t``, collect
   per-symbol factor scores ``y`` and per-provider exposure vectors, stack
   them as the design matrix ``X`` (intercept + exposures), run
   ``np.linalg.lstsq``, and replace ``y`` with the residuals
   ``y - X @ beta``.  NaN cells (from the PIT mask or missing data) are
   excluded from the regression and stay ``null`` in the output.

3. **PIT violation guard**: if any provider returns a ``ts`` that lies
   *after* the maximum ``ts`` in the input panel, a
   :class:`PITViolationError` is raised.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Sequence, Union

import numpy as np
import polars as pl

from tinohelm.aligner.exposure import ExposureProvider
from tinohelm.aligner.registry import resolve as resolve_provider

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class PITViolationError(Exception):
    """Raised when an ExposureProvider returns a future timestamp.

    A provider must only return timestamps that exist in (or are ≤ the
    maximum of) the input panel's ``ts`` column.  Returning a ``ts`` beyond
    ``max(panel["ts"])`` indicates the provider is leaking future exposure
    data, which violates the Point-In-Time constraint.
    """


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

NeutralizeArg = Union[str, ExposureProvider]


# ---------------------------------------------------------------------------
# Aligner
# ---------------------------------------------------------------------------


class Aligner:
    """Applies Universe PIT filtering and cross-section OLS neutralization.

    Parameters
    ----------
    universe:
        :class:`~tinohelm.factor.universe.Universe` instance that carries
        ``listing_date`` / ``delisting_date`` per symbol for PIT masking.
    neutralize:
        Sequence of neutralization providers.  Each element is either:

        - a **string** name that is resolved via
          :func:`~tinohelm.aligner.registry.resolve` (raises
          :class:`KeyError` for unknown names), or
        - an **ExposureProvider instance** that is used directly.

        Both forms can be mixed freely.
    allow_non_pit_exposures:
        ``False`` by default.  Providers may declare ``pit_safe = False`` when
        they rely on current/latest snapshots (for example current circulating
        supply).  Such providers are rejected for historical neutralization
        unless this flag is explicitly set.

    Raises
    ------
    KeyError
        During ``__init__`` if a string name is not registered.
    """

    def __init__(
        self,
        universe: "Universe",
        neutralize: Sequence[NeutralizeArg] = (),
        *,
        allow_non_pit_exposures: bool = False,
    ) -> None:
        self.universe = universe
        self._providers: list[ExposureProvider] = self._resolve_neutralize(neutralize)
        if not allow_non_pit_exposures:
            non_pit = [
                provider.name
                for provider in self._providers
                if getattr(provider, "pit_safe", True) is False
            ]
            if non_pit:
                raise ValueError(
                    "Non-PIT exposure providers are not allowed for historical "
                    f"neutralization by default: {non_pit}. Use a PIT-safe "
                    "historical exposure provider, or pass "
                    "allow_non_pit_exposures=True for explicit exploratory use."
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_neutralize(
        self, items: Sequence[NeutralizeArg]
    ) -> list[ExposureProvider]:
        """Resolve mixed string / instance list to concrete ExposureProvider list.

        Strings are forwarded to ``registry.resolve``; instances pass through
        unchanged.  Raises :class:`KeyError` immediately on unknown names so
        the error surfaces at construction time rather than at
        :meth:`align` time.
        """
        out: list[ExposureProvider] = []
        for item in items:
            if isinstance(item, str):
                out.append(resolve_provider(item))  # KeyError if not registered
            else:
                out.append(item)
        return out

    # ------------------------------------------------------------------
    # PIT mask helpers
    # ------------------------------------------------------------------

    def _build_universe_mask(
        self,
        panel: pl.DataFrame,
        symbols: list[str],
    ) -> dict[str, tuple[datetime | None, datetime | None]]:
        """Return per-symbol (eligible_from, delisting_date) boundaries.

        Only symbols present in *panel* columns are returned.  Symbols in
        the panel but not in the universe are rejected: an
        ``Aligner(universe=...)`` boundary must not let upstream extra
        columns leak into neutralization or output.

        Uses :meth:`~tinohelm.factor.universe.Universe.get_symbol_boundaries`
        for an O(symbols) lookup rather than calling ``get_symbols_at`` per
        timestamp.
        """
        boundaries = self.universe.get_symbol_boundaries()
        result: dict[str, tuple[datetime | None, datetime | None]] = {}
        for sym in symbols:
            if sym in boundaries:
                result[sym] = boundaries[sym]
            else:
                raise ValueError(f"symbol {sym!r} is not in aligner universe")
        return result

    def _apply_universe_mask(
        self,
        panel: pl.DataFrame,
        symbols: list[str],
        boundaries: dict[str, tuple[datetime | None, datetime | None]],
    ) -> pl.DataFrame:
        """Set cells to null where symbol was not eligible at that timestamp.

        For each symbol column, any row whose ``ts`` value falls before
        ``eligible_from`` or on/after ``delisting_date`` is set to ``null``.

        The ``ts`` column must be castable to Python datetime for comparison.
        The function handles both ``pl.Datetime`` and integer ``ts`` columns
        (integer ts is left unfiltered — no PIT semantics apply).
        """
        ts_series = panel["ts"]

        # Only apply PIT masking when ts is a Datetime column
        if ts_series.dtype not in (pl.Datetime, pl.Date):
            return panel

        ts_expr = pl.col("ts").cast(pl.Datetime)

        exprs: list[pl.Expr] = []
        for sym in symbols:
            eligible_from, delisting_date = boundaries[sym]

            if eligible_from is None and delisting_date is None:
                # No PIT info — keep as-is
                exprs.append(pl.col(sym))
                continue

            # Build the PIT mask from the ``ts`` column expression itself.
            # Avoid binding an external Series/list to the expression: PIT
            # correctness must follow the row's timestamp even if Polars
            # changes eager/lazy execution or row-order internals.
            mask_expr = pl.lit(False)
            if eligible_from is not None:
                # eligible_from is listing_date + 7 days (already computed by universe)
                mask_expr = mask_expr | (ts_expr < pl.lit(eligible_from))
            if delisting_date is not None:
                mask_expr = mask_expr | (ts_expr >= pl.lit(delisting_date))

            exprs.append(
                pl.when(mask_expr).then(None).otherwise(pl.col(sym)).alias(sym)
            )

        return panel.with_columns(exprs)

    # ------------------------------------------------------------------
    # OLS neutralization helpers
    # ------------------------------------------------------------------

    def _pit_check(
        self,
        panel: pl.DataFrame,
        symbols: list[str],
        ts_series: pl.Series,
    ) -> None:
        """Raise PITViolationError if any provider returns a future timestamp.

        Checks that all ``ts`` values in each provider's exposure frame are
        ≤ the maximum ``ts`` in the panel.  A provider whose returned ``ts``
        exceeds the panel's maximum is leaking future data.

        H3 fix: both panel ts and provider ts are normalized to tz-naive
        ``Datetime("ns")`` before comparison, so tz-aware vs tz-naive
        differences don't trigger spurious ``TypeError`` on ``>`` comparisons.
        """
        panel_ts_list = self._normalize_ts_list(ts_series)
        # Handle possible None values
        panel_ts_filtered = [t for t in panel_ts_list if t is not None]
        if not panel_ts_filtered:
            return

        max_panel_ts = max(panel_ts_filtered)

        for provider in self._providers:
            exp_df = provider.get_exposure(ts_series, symbols)
            exp_ts_list = self._normalize_ts_list(exp_df["ts"])
            exp_ts_filtered = [t for t in exp_ts_list if t is not None]

            future_ts = [t for t in exp_ts_filtered if t > max_panel_ts]
            if future_ts:
                raise PITViolationError(
                    f"Provider '{provider.name}' returned future timestamps "
                    f"not in panel: {sorted(future_ts)!r}. "
                    f"Panel max ts: {max_panel_ts!r}"
                )

    def _ols_residualize_row(
        self,
        y: np.ndarray,
        exposures: list[np.ndarray],
    ) -> np.ndarray:
        """Compute OLS residuals for a single cross-section.

        Parameters
        ----------
        y:
            Factor scores for N symbols, shape ``(N,)``.  May contain NaN.
        exposures:
            List of K exposure vectors, each shape ``(N,)``.

        Returns
        -------
        np.ndarray
            Residuals of the same shape as ``y``.  NaN positions in ``y``
            or any exposure remain NaN in the output.
        """
        N = len(y)
        residuals = np.full(N, np.nan)

        # Build union NaN mask — exclude any symbol with NaN in y or any exposure
        nan_mask = np.isnan(y)
        for exp in exposures:
            nan_mask = nan_mask | np.isnan(exp)

        valid = ~nan_mask
        n_valid = int(valid.sum())
        p = 1 + len(exposures)
        if n_valid <= p:
            # Not enough degrees of freedom to estimate intercept + exposures
            # without exact-fitting the cross-section — return all-NaN row.
            return residuals

        y_valid = y[valid]
        X_cols = [np.ones(n_valid)] + [exp[valid] for exp in exposures]
        X = np.column_stack(X_cols)  # shape (n_valid, K+1)
        if np.linalg.matrix_rank(X) < p:
            # Collinear exposures (or constant exposure with intercept) would
            # produce unstable / exact-fit residuals; skip the timestamp.
            return residuals

        beta, *_ = np.linalg.lstsq(X, y_valid, rcond=None)
        residuals[valid] = y_valid - X @ beta
        return residuals

    @staticmethod
    def _normalize_ts_list(ts_series: pl.Series) -> list[datetime]:
        """Normalize a polars Datetime ``ts`` column to a list of tz-naive datetimes.

        Both ``panel["ts"]`` and provider-returned ``exp_df["ts"]`` columns may
        carry different timezones or precisions.  We coerce both sides to
        ``Datetime("ns", time_zone=None)`` before extracting Python datetime
        objects, then strip any residual tzinfo so dict-key equality works
        deterministically.

        For non-datetime ts (e.g. integer ns), we pass values through unchanged
        — equality on identical ints is unambiguous.
        """
        if ts_series.dtype == pl.Datetime:
            ts_series = ts_series.cast(pl.Datetime("ns", time_zone=None))
        ts_list = ts_series.to_list()
        normalized: list = []
        for ts in ts_list:
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            normalized.append(ts)
        return normalized

    def _apply_ols_neutralization(
        self,
        panel: pl.DataFrame,
        symbols: list[str],
        ts_series: pl.Series,
    ) -> pl.DataFrame:
        """Apply cross-section OLS residualization for all timestamps.

        For each row (timestamp) in *panel*:
        1. Gather exposure values from all providers.
        2. Run OLS and compute residuals.
        3. Replace the row's symbol values with residuals.

        Returns a new DataFrame with the same schema as *panel*.

        Raises
        ------
        PITViolationError
            If any provider's exposure ts column fails to align with the panel
            ts column on **every** row (full mismatch).  This is a defensive
            guard against tz/precision/dtype mismatches that would otherwise
            silently produce an all-NaN exposure matrix and an all-NaN OLS
            output panel.
        """
        if not self._providers:
            return panel

        # Collect exposure DataFrames from all providers (one call per provider,
        # covering all timestamps at once — providers expected to handle bulk)
        exposure_dfs: list[pl.DataFrame] = []
        for provider in self._providers:
            exp_df = provider.get_exposure(ts_series, symbols)
            exposure_dfs.append(exp_df)

        # Convert panel symbol columns to numpy for row-wise OLS
        # panel rows correspond to timestamps in order
        panel_np: dict[str, np.ndarray] = {}
        for sym in symbols:
            col = panel[sym]
            panel_np[sym] = col.cast(pl.Float64).fill_null(float("nan")).to_numpy()

        n_rows = len(panel)

        # H3 fix — unify ts dtype + strip tz so dict-key equality is reliable
        # across panel ts and provider exposure ts (which may differ in tz or
        # precision).  Without this, equality silently fails and every cell
        # becomes NaN, producing an all-NaN OLS output without any signal.
        ts_list = self._normalize_ts_list(ts_series)
        ts_to_idx: dict = {ts: i for i, ts in enumerate(ts_list)}

        # provider_arrays[k][sym] = np.ndarray of length n_rows
        provider_arrays: list[dict[str, np.ndarray]] = []
        for provider, exp_df in zip(self._providers, exposure_dfs):
            exp_ts_list = self._normalize_ts_list(exp_df["ts"])
            sym_arrays: dict[str, np.ndarray] = {}
            any_match = False
            for sym in symbols:
                arr = np.full(n_rows, np.nan)
                if sym in exp_df.columns:
                    exp_vals = exp_df[sym].cast(pl.Float64).fill_null(float("nan")).to_numpy()
                    for j, exp_ts in enumerate(exp_ts_list):
                        if exp_ts in ts_to_idx:
                            arr[ts_to_idx[exp_ts]] = exp_vals[j]
                            any_match = True
                sym_arrays[sym] = arr
            # H3 defensive guard: if every (ts, sym) cell ended up NaN AND no
            # ts even matched any panel row, this is a ts-alignment failure
            # (likely tz/precision mismatch).  Surface it loudly rather than
            # silently producing all-NaN residuals.
            if not any_match and len(exp_ts_list) > 0 and n_rows > 0:
                logger.warning(
                    "Aligner: no ts overlap between panel and provider %r — "
                    "all OLS residuals would be NaN; raising PITViolationError",
                    provider.name,
                )
                raise PITViolationError(
                    f"Exposure ts not aligned with panel ts for provider "
                    f"{provider.name!r}.  Likely cause: tz-aware vs naive "
                    f"datetime, or differing precision (us/ns/ms)."
                )
            provider_arrays.append(sym_arrays)

        # Row-wise OLS residualization
        # result_np[sym][row_idx] = residual for that symbol at that timestamp
        result_np: dict[str, np.ndarray] = {sym: np.full(n_rows, np.nan) for sym in symbols}

        for row_idx in range(n_rows):
            y = np.array([panel_np[sym][row_idx] for sym in symbols])
            exposures = [
                np.array([p_arr[sym][row_idx] for sym in symbols])
                for p_arr in provider_arrays
            ]
            residuals = self._ols_residualize_row(y, exposures)
            for col_idx, sym in enumerate(symbols):
                result_np[sym][row_idx] = residuals[col_idx]

        # Construct output DataFrame
        new_cols: list[pl.Series] = []
        for sym in symbols:
            arr = result_np[sym]
            # Convert NaN back to null
            series = pl.Series(sym, arr, dtype=pl.Float64)
            series = series.set(pl.Series("m", np.isnan(arr)), None)
            new_cols.append(series)

        return panel.with_columns(new_cols)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def align(self, panel: pl.DataFrame) -> pl.DataFrame:
        """Apply Universe PIT mask and cross-section OLS neutralization.

        Parameters
        ----------
        panel:
            Wide-format factor panel.  Must have a ``ts`` column (Datetime
            or compatible) plus one column per symbol in the universe.

        Returns
        -------
        pl.DataFrame
            Same shape as *panel*.  Cells outside the universe PIT window
            are ``null``; remaining cells are OLS residuals (or original
            values when ``neutralize=[]``).

        Raises
        ------
        PITViolationError
            If any registered provider returns exposure data with timestamps
            beyond the panel's maximum ``ts``.
        """
        ts_series = panel["ts"]
        symbols = [c for c in panel.columns if c != "ts"]

        # Step 1: Universe PIT mask.  This also fails fast when the panel
        # contains symbols outside the configured universe.
        boundaries = self._build_universe_mask(panel, symbols)
        panel = self._apply_universe_mask(panel, symbols, boundaries)

        # Step 2: PIT violation check (fail-fast before neutralization)
        if self._providers:
            self._pit_check(panel, symbols, ts_series)

        # Step 3: Cross-section OLS residualization
        if self._providers:
            panel = self._apply_ols_neutralization(panel, symbols, ts_series)

        return panel
