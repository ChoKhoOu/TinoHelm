"""High-level evaluation orchestrator.

``Evaluator`` glues the sub-modules (``ic``, ``quantile``, ``distribution``,
``turnover``, ``robustness``, ``cost``, ``rating``) into two entry points:

* ``evaluate(factor_values, returns, config) -> EvalResult`` — fast path
  used by the explorer panel.  Runs distribution → IC → quantile → turnover
  → rating.  No ProcessPoolExecutor spawns.

* ``evaluate_full(factor_values, returns, config) -> EvalResult`` — full
  diagnostic including shuffle test, subsample IC, cross-symbol IC, and
  cost waterfall.

Panel → series flattening
-------------------------
The declarative framework passes factor values and forward returns as a
``Panel`` (``DatetimeIndex × symbol``).  All legacy sub-functions take flat
``pd.Series``.  The evaluator flattens the Panel into a long-form Series by
stacking symbols (preserving the DatetimeIndex so ``pd.Grouper(freq="D")``
still works on the time dimension).

If the input is already a ``pd.Series`` (single-symbol factor), we pass it
through unchanged — this keeps the explorer panel's existing behaviour
intact during the migration window.

NaN / Infinity contract
-----------------------
``EvalResult`` must never contain non-finite floats — the project has been
burned by PostgreSQL JSON columns rejecting NaN/Inf (see ``CLAUDE.md``
Pitfalls).  The ``_scrub`` helper converts non-finite floats to ``None``
before the ``EvalResult`` is returned.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tinohelm.factor.evaluation.cost import edge_waterfall
from tinohelm.factor.evaluation.distribution import compute_distribution
from tinohelm.factor.evaluation.ic import (
    compute_ic_decay,
    compute_ic_series,
    compute_ic_summary,
    compute_half_life,
    forward_returns,
)
from tinohelm.factor.evaluation.quantile import compute_quantile_returns
from tinohelm.factor.evaluation.rating import compute_rating
from tinohelm.factor.evaluation.robustness import (
    cross_symbol_ic,
    shuffle_test,
    subsample_ic,
)
from tinohelm.factor.evaluation.turnover import compute_turnover
from tinohelm.factor.types import EvalConfig, EvalResult, Panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_series(values: Panel | pd.Series) -> pd.Series:
    """Flatten a Panel (time × symbol) into a long-form Series keyed by time.

    * ``pd.Series`` → returned as-is (explorer panel's single-symbol path).
    * ``pd.DataFrame`` with a single column → squeezed to Series.
    * ``pd.DataFrame`` with multiple columns → ``stack()`` produces a
      MultiIndex; we collapse to a flat DatetimeIndex by stacking symbols
      *below* time (so pd.Grouper(freq="D") still works).

    The underlying IC / quantile / turnover functions all call ``dropna()``
    first so the duplicated time-index stemming from multi-symbol stacking
    is fine — same time may appear multiple times (once per symbol).
    """
    if isinstance(values, pd.Series):
        return values
    if isinstance(values, pd.DataFrame):
        if values.shape[1] == 1:
            return values.iloc[:, 0]
        # Multi-symbol panel: stack() yields a Series with (time, symbol)
        # MultiIndex. We drop the symbol level to keep a flat DatetimeIndex
        # while duplicating timestamps across symbols. ``pd.Grouper`` still
        # buckets by time because the outer level is time.
        stacked = values.stack(future_stack=True).sort_index()
        # ``stacked.index`` has 2 levels [time, symbol]. Reset the symbol
        # level to drop it, keeping timestamps as the only index.
        stacked.index = stacked.index.get_level_values(0)
        return stacked
    raise TypeError(f"Unsupported input type: {type(values).__name__}")


def _finite_or_none(x: Any) -> Any:
    """Replace NaN/Inf floats with ``None``; recurse into dicts + lists.

    Mirrors ``research.analysis.sanitize_for_json`` but returns the original
    object type (list for list, dict for dict).  Exposed so callers can
    scrub a whole ``EvalResult`` before serialization.
    """
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    # numpy scalars (float64("nan")) are also floats
    if isinstance(x, np.floating):
        if np.isnan(x) or np.isinf(x):
            return None
        return float(x)
    if isinstance(x, dict):
        return {k: _finite_or_none(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite_or_none(i) for i in x]
    return x


def _scrub_result(result: EvalResult) -> EvalResult:
    """Replace any non-finite float inside ``EvalResult`` with ``None``.

    Scalar float fields (``ic_mean`` etc.) become ``None`` if NaN/Inf.  Dict
    / list fields (``quantile_pnl``, ``ic_series`` …) are recursively
    scrubbed via ``_finite_or_none``.
    """
    # Scalar numeric fields
    for fname in (
        "ic_mean",
        "ic_std",
        "ir",
        "ic_tstat",
        "ic_positive_pct",
        "ic_max_abs",
        "turnover",
        "turnover_annualized",
        "fee_drag_monthly",
    ):
        val = getattr(result, fname)
        cleaned = _finite_or_none(val)
        # Preserve the numeric field as 0.0 when scrubbed to None so
        # downstream typed consumers don't choke. Fields that legitimately
        # can be None (e.g. half_life) are handled separately.
        setattr(result, fname, cleaned if cleaned is not None else 0.0)

    # Collections
    result.quantile_pnl = _finite_or_none(result.quantile_pnl) or {}
    result.quantile_cum_returns = _finite_or_none(result.quantile_cum_returns) or {}
    result.distribution_stats = _finite_or_none(result.distribution_stats) or {}
    result.distribution_histogram = _finite_or_none(result.distribution_histogram) or []
    result.ic_series = _finite_or_none(result.ic_series) or []
    result.ic_decay = _finite_or_none(result.ic_decay) or []
    result.robustness = _finite_or_none(result.robustness) or {}
    result.cost = _finite_or_none(result.cost) or {}

    return result


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    """Evaluate a factor against forward returns under an ``EvalConfig``.

    The class is deliberately stateless — it's safe to instantiate once and
    reuse across many factors, or to spin up a new one per call.  All
    long-lived state lives in the arguments.

    Example
    -------
    >>> from tinohelm.factor.types import EvalConfig
    >>> evaluator = Evaluator()
    >>> result = evaluator.evaluate(factor_values, returns, config)
    """

    # ------------------------------------------------------------------ #
    # evaluate — fast path (no process pool)
    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        factor_values: Panel | pd.Series,
        returns: Panel | pd.Series,
        config: EvalConfig,
    ) -> EvalResult:
        """Fast evaluation — IC / quantile / turnover / distribution / rating.

        ``returns`` may be either:
          * Forward returns (already shifted) — used directly.
          * Raw close prices — ``forward_returns(..., config.forward_period)``
            is applied if the input looks price-like (all positive, no NaN
            tail).  This matches the explorer panel's calling convention.

        The ``Evaluator`` is conservative and *does not* heuristically detect
        "price vs forward-return"; callers must pass forward returns
        explicitly.  ``_prepare_returns()`` is the single integration point
        if a heuristic is added later.
        """
        result, _factor_s, _fwd_s, _close_s = self._evaluate_core(
            factor_values, returns, config
        )
        return result

    # ------------------------------------------------------------------ #
    # evaluate_full — full diagnostic (robustness + cost)
    # ------------------------------------------------------------------ #

    def evaluate_full(
        self,
        factor_values: Panel | pd.Series,
        returns: Panel | pd.Series,
        config: EvalConfig,
        *,
        shuffle_iter: int = 1000,
        shuffle_workers: int = 4,
        subsample_freq: str = "ME",
        cross_symbol_args: dict | None = None,
    ) -> EvalResult:
        """Full diagnostic — adds shuffle, subsample, cross-symbol IC + cost.

        Parameters
        ----------
        shuffle_iter : int
            Number of shuffle permutations. Pass 0 to skip shuffle test.
        shuffle_workers : int
            ProcessPool size for shuffle test.
        subsample_freq : str
            Pandas offset string for subsample IC grouping (``"ME"`` = month-end).
        cross_symbol_args : dict | None
            If provided, runs ``cross_symbol_ic`` with these kwargs. Shape:
            ``{"factor_name": str, "factor_params": dict, "symbols": list,
              "interval": str, "start": str, "end": str,
              "catalog_path": str | None, "max_workers": int}``.
        """
        # Reuse the core evaluation (no redundant _prepare_returns call).
        result, factor_s, fwd_s, _close_s = self._evaluate_core(
            factor_values, returns, config
        )

        robustness: dict[str, Any] = {}

        # --- Shuffle test ------------------------------------------------
        if shuffle_iter and shuffle_iter > 0:
            try:
                robustness["shuffle"] = shuffle_test(
                    factor_s, fwd_s, n_iter=shuffle_iter, max_workers=shuffle_workers,
                )
            except Exception as exc:  # pragma: no cover — defensive
                robustness["shuffle"] = {
                    "real_ic": 0,
                    "shuffle_distribution": [],
                    "p_value": 1.0,
                    "significant": False,
                    "error": str(exc),
                }

        # --- Subsample IC ------------------------------------------------
        robustness["subsample"] = subsample_ic(factor_s, fwd_s, freq=subsample_freq)

        # --- Cross-symbol IC -------------------------------------------
        if cross_symbol_args:
            try:
                robustness["cross_symbol"] = cross_symbol_ic(**cross_symbol_args)
            except Exception as exc:  # pragma: no cover — defensive
                robustness["cross_symbol"] = [{"error": str(exc)}]

        result.robustness = robustness

        # --- Cost waterfall -------------------------------------------
        result.cost = edge_waterfall(
            ic_mean=result.ic_mean,
            turnover_daily=result.turnover,
            fee_rate=config.cost_bps / 10000.0 / 2.0,
            slippage_bps=1.0,
        )

        return _scrub_result(result)

    # ------------------------------------------------------------------ #
    # _evaluate_core — shared implementation for evaluate / evaluate_full
    # ------------------------------------------------------------------ #

    @staticmethod
    def _prepare_returns(
        returns: Panel | pd.Series,
        config: EvalConfig,
    ) -> tuple[pd.Series, pd.Series | None]:
        """Translate ``returns`` into a forward-return series.

        Heuristic: if the input already contains explicit NaNs in the tail
        (consistent with ``forward_returns(..., period)``), treat it as
        pre-shifted forward returns.  Otherwise treat it as close prices
        and apply ``forward_returns``.

        Returns
        -------
        tuple[pd.Series, pd.Series | None]
            ``(forward_return_series, close_series_or_none)``.
            ``close_series_or_none`` is the flattened price series when the
            input was detected as prices (used for IC decay).  ``None``
            when the input was already pre-shifted forward returns.
        """
        as_series = _to_series(returns)

        # Tail NaN heuristic — if the last ``forward_period`` entries are
        # all NaN, assume it's already a forward-return series.
        tail = as_series.iloc[-config.forward_period:]
        if len(tail) == config.forward_period and tail.isna().all():
            return as_series, None

        # Treat as close price; compute forward returns.
        fwd = forward_returns(as_series, config.forward_period, log_ret=config.log_ret)
        return fwd, as_series

    def _evaluate_core(
        self,
        factor_values: Panel | pd.Series,
        returns: Panel | pd.Series,
        config: EvalConfig,
    ) -> tuple[EvalResult, pd.Series, pd.Series, pd.Series | None]:
        """Shared implementation returning ``(result, factor_s, fwd_s, close_s)``.

        Both ``evaluate()`` and ``evaluate_full()`` delegate here so that
        flattening and ``_prepare_returns`` happen exactly once.
        """
        factor_s = _to_series(factor_values)
        fwd_s, close_s = self._prepare_returns(returns, config)

        # Defensive: align indices so stacked panels share the same time axis.
        factor_s, fwd_s = factor_s.align(fwd_s, join="inner")

        result = EvalResult()

        # 1. Distribution — factor-only analysis, cheap.
        dist = compute_distribution(factor_s)
        result.distribution_stats = dist.get("stats", {})
        result.distribution_histogram = dist.get("histogram", [])

        # 2. IC series + summary.
        ic_series_df = compute_ic_series(factor_s, fwd_s, freq=config.ic_freq)
        summary = compute_ic_summary(ic_series_df)
        result.ic_mean = float(summary["ic_mean"])
        result.ic_std = float(summary["ic_std"])
        result.ir = float(summary["ir"])
        result.ic_tstat = float(summary["ic_tstat"])
        result.ic_positive_pct = float(summary["ic_positive_pct"])
        result.ic_max_abs = float(summary["ic_max_abs"])
        result.ic_series = (
            ic_series_df.to_dict("records") if len(ic_series_df) > 0 else []
        )

        # 3. Decay + half-life — needs the close price, not forward returns.
        if close_s is not None:
            decay = compute_ic_decay(factor_s, close_s)
            result.ic_decay = decay
            result.half_life = compute_half_life(decay)

        # 4. Quantile analysis.
        q = compute_quantile_returns(factor_s, fwd_s, n_quantiles=config.quantiles)
        result.quantile_pnl = q.get("avg_returns", {})
        result.quantile_cum_returns = q.get("cum_returns", {})
        result.is_monotonic = bool(q.get("is_monotonic", False))

        # 5. Turnover — uses the cost_bps config for fee drag.
        fee_rate = config.cost_bps / 10000.0 / 2.0  # bps → per-side decimal
        turn = compute_turnover(factor_s, fwd_s, n_quantiles=config.quantiles, fee_rate=fee_rate)
        result.turnover = float(turn["daily"])
        result.turnover_annualized = float(turn["annualized"])
        result.fee_drag_monthly = float(turn["fee_drag_monthly"])

        # 6. Rating.
        result.rating = compute_rating({
            "ir": result.ir,
            "ic_positive_pct": result.ic_positive_pct,
        })

        return _scrub_result(result), factor_s, fwd_s, close_s


__all__ = ["Evaluator"]
