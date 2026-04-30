"""High-level evaluation orchestrator (polars-native).

``Evaluator`` glues the sub-modules (``ic``, ``quantile``, ``distribution``,
``turnover``, ``robustness``, ``cost``, ``rating``) into two entry points:

* ``evaluate(factor_values, returns, config) -> EvalResult`` — fast path
  used by the explorer panel. Runs distribution → IC → quantile → turnover
  → rating. No ProcessPoolExecutor spawns.

* ``evaluate_full(factor_values, returns, config) -> EvalResult`` — full
  diagnostic including shuffle test, subsample IC, cross-symbol IC, and
  cost waterfall.

Panel → ``[ts, symbol, value]`` flattening
------------------------------------------
The declarative framework still passes factor / forward-return data as a
``Panel`` (``pl.DataFrame`` keyed by ``ts`` with N symbol columns). Legacy
sub-functions consumed flat pandas Series; the polars rewrite uses 2-col
``[ts, value]`` or 3-col ``[ts, symbol, value]`` :class:`pl.DataFrame`
instances. :func:`_to_ts_value` flattens a wide panel by *unpivoting* the
symbol columns while preserving ``symbol`` as an identity key.  Downstream
joins use ``(ts, symbol)`` when available, preventing multi-symbol panels
from cross-pairing factor values and forward returns across different
assets.

Backward compatibility
----------------------
A small number of callers (``test_evaluation.py`` integration suite, the
``orchestrator`` while ``DataLayer`` still emits pandas) feed pandas
``Series`` / ``DataFrame`` instances. We accept those too via a lightweight
duck-typed conversion in :func:`_to_ts_value` — without importing pandas at
module top — so AC-1 (zero pandas imports under
``src/tinohelm/factor/evaluation/``) is satisfied.

NaN / Infinity contract
-----------------------
``EvalResult`` must never contain non-finite floats — the project has been
burned by PostgreSQL JSON columns rejecting NaN/Inf (see ``CLAUDE.md``
Pitfalls). The ``_scrub`` helper converts non-finite floats to ``None``
before the ``EvalResult`` is returned.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np
import polars as pl

from tinohelm.factor.evaluation.cost import edge_waterfall
from tinohelm.factor.evaluation.distribution import compute_distribution
from tinohelm.factor.evaluation.ic import (
    _ensure_factor_keys_covered,
    _ensure_unique_identity_keys,
    _join_keys,
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
from tinohelm.factor.evaluation.walk_forward import WalkForwardEvaluator
from tinohelm.factor.types import EvalConfig, EvalResult, Panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS_COL: str = "ts"
_SYMBOL_COL: str = "symbol"
_VAL_COL: str = "value"


def _is_pandas_series(obj: Any) -> bool:
    """Duck-typed detection of a pandas ``Series`` (no top-level pandas import)."""
    cls = type(obj)
    return cls.__module__.startswith("pandas") and cls.__name__ == "Series"


def _is_pandas_dataframe(obj: Any) -> bool:
    cls = type(obj)
    return cls.__module__.startswith("pandas") and cls.__name__ == "DataFrame"


def _pandas_series_to_polars(series: Any) -> pl.DataFrame:
    """Convert a pandas Series into the canonical 2-col ``[ts, value]`` frame.

    Uses ``getattr`` rather than ``import pandas`` so the evaluation module
    stays free of pandas dependencies (AC-1). The conversion path is:
      * Index → ``ts`` column (any DatetimeIndex / RangeIndex / PeriodIndex
        normalised to plain :class:`datetime.datetime` via
        :meth:`Series.index.to_pydatetime` when available, otherwise the raw
        index values are wrapped untouched).
      * Series values → ``value`` column, cast to ``Float64``.
    """
    idx = getattr(series, "index", None)
    if idx is not None and hasattr(idx, "to_pydatetime"):
        ts_vals = list(idx.to_pydatetime())
    elif idx is not None:
        ts_vals = list(idx)
    else:
        ts_vals = list(range(len(series)))
    val_vals = [float(v) if v is not None else None for v in series.tolist()]
    return pl.DataFrame({_TS_COL: ts_vals, _VAL_COL: val_vals})


def _pandas_dataframe_to_polars_panel(df: Any) -> pl.DataFrame:
    """Convert a multi-column pandas ``DataFrame`` panel into a polars panel.

    Same duck-typed pattern as :func:`_pandas_series_to_polars`. The panel
    keeps its original column ordering with ``ts`` prepended.
    """
    idx = getattr(df, "index", None)
    if idx is not None and hasattr(idx, "to_pydatetime"):
        ts_vals = list(idx.to_pydatetime())
    elif idx is not None:
        ts_vals = list(idx)
    else:
        ts_vals = list(range(len(df)))

    payload: dict[str, list] = {_TS_COL: ts_vals}
    for col in df.columns:
        col_vals = df[col].tolist()
        payload[str(col)] = [float(v) if v is not None else None for v in col_vals]
    return pl.DataFrame(payload)


def _to_ts_value(values: Panel | pl.Series | Any) -> pl.DataFrame:
    """Flatten supported input into ``[ts, value]`` or ``[ts, symbol, value]``.

    Supported input types:
      * 2-col :class:`pl.DataFrame` ``[ts, value]`` — passed through.
      * 3-col :class:`pl.DataFrame` ``[ts, symbol, value]`` — passed through
        with canonical column order.
      * Multi-col :class:`pl.DataFrame` panel ``[ts, sym1, sym2, ...]`` —
        unpivoted to long form so every ``(ts, symbol)`` cell contributes one
        row while retaining the symbol identity for later joins.
      * :class:`pl.Series` — wrapped with a synthetic integer ``ts`` axis.
      * pandas ``Series`` / ``DataFrame`` (duck-typed) — converted via
        :func:`_pandas_series_to_polars` /
        :func:`_pandas_dataframe_to_polars_panel`, then re-flattened.

    Raises
    ------
    TypeError
        If ``values`` is none of the above (matches legacy contract).
    """
    if isinstance(values, pl.DataFrame):
        cols = values.columns
        if _TS_COL not in cols:
            raise ValueError(
                f"polars DataFrame missing required {_TS_COL!r} column; got {cols!r}"
            )
        non_ts = [c for c in cols if c != _TS_COL]
        if _SYMBOL_COL in cols and _VAL_COL in cols:
            return values.select([_TS_COL, _SYMBOL_COL, _VAL_COL])
        if len(non_ts) == 1 and non_ts[0] == _VAL_COL:
            return values
        if len(non_ts) == 1:
            # Single symbol column — keep the asset identity instead of
            # collapsing it away, so wide-panel callers get consistent schema
            # whether they pass one symbol or many.
            return values.select([
                pl.col(_TS_COL),
                pl.lit(non_ts[0]).alias(_SYMBOL_COL),
                pl.col(non_ts[0]).alias(_VAL_COL),
            ])
        # Multi-symbol panel — unpivot to long form so every (ts, symbol)
        # cell becomes a row without losing the symbol identity.
        return values.unpivot(
            index=[_TS_COL],
            on=non_ts,
            variable_name=_SYMBOL_COL,
            value_name=_VAL_COL,
        ).select([_TS_COL, _SYMBOL_COL, _VAL_COL])

    if isinstance(values, pl.Series):
        ts = list(range(len(values)))
        return pl.DataFrame({_TS_COL: ts, _VAL_COL: values.to_list()})

    # Pandas duck-typed fall-throughs — keeps the evaluation module pandas-free
    # while still letting orchestrator + integration tests pass pandas inputs.
    if _is_pandas_series(values):
        return _pandas_series_to_polars(values)
    if _is_pandas_dataframe(values):
        # Re-route through the panel conversion + recursive flatten.
        panel = _pandas_dataframe_to_polars_panel(values)
        return _to_ts_value(panel)

    raise TypeError(f"Unsupported input type: {type(values).__name__}")


def _finite_or_none(x: Any) -> Any:
    """Replace NaN/Inf floats with ``None``; recurse into dicts + lists.

    Mirrors ``research.analysis.sanitize_for_json`` but returns the original
    object type (list for list, dict for dict). Exposed so callers can scrub
    a whole ``EvalResult`` before serialization.
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

    Scalar float fields (``ic_mean`` etc.) become ``None`` if NaN/Inf. Dict
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
        if val is None:
            # Preserve explicit undefined metrics (e.g. insufficient-data
            # walk-forward). Do not make them look like economically valid 0s.
            setattr(result, fname, None)
            continue
        cleaned = _finite_or_none(val)
        # Historical contract: non-finite arithmetic artifacts scrub to 0.0 for
        # typed consumers. Explicit None above means "not enough data" and is
        # preserved.
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
    result.oos_ic_series = _finite_or_none(result.oos_ic_series) or []
    result.segment_results = _finite_or_none(result.segment_results) or {}
    result.neutralization_config = _finite_or_none(result.neutralization_config) or {}
    result.effective_params = _finite_or_none(result.effective_params) or {}
    result.warnings = _finite_or_none(result.warnings) or []
    result.base_eval = _finite_or_none(result.base_eval)
    result.walk_forward = _finite_or_none(result.walk_forward)

    return result


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    """Evaluate a factor against forward returns under an ``EvalConfig``.

    The class is deliberately stateless — it's safe to instantiate once and
    reuse across many factors, or to spin up a new one per call. All
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
        factor_values: Panel | pl.Series | Any,
        returns: Panel | pl.Series | Any,
        config: EvalConfig,
    ) -> EvalResult:
        """Fast evaluation — IC / quantile / turnover / distribution / rating.

        ``returns`` semantics are explicit via ``config.returns_kind``:
          * ``"close"`` — raw close prices; :func:`forward_returns` is applied.
          * ``"forward_returns"`` — already shifted returns; used directly.

        Pandas inputs are accepted via the duck-typed conversion in
        :func:`_to_ts_value`; the evaluation module itself never imports
        pandas (AC-1).
        """
        result, _factor_df, _fwd_df, _close_df = self._evaluate_core(
            factor_values, returns, config
        )
        return result

    # ------------------------------------------------------------------ #
    # evaluate_full — full diagnostic (robustness + cost)
    # ------------------------------------------------------------------ #

    def evaluate_full(
        self,
        factor_values: Panel | pl.Series | Any,
        returns: Panel | pl.Series | Any,
        config: EvalConfig,
        *,
        shuffle_iter: int = 1000,
        shuffle_workers: int = 4,
        subsample_freq: str = "ME",
        cross_symbol_args: dict | None = None,
    ) -> EvalResult:
        """Full diagnostic — adds shuffle, subsample, cross-symbol IC + cost.

        When ``config.walk_forward`` is not ``None``, delegates to
        :class:`~tinohelm.factor.evaluation.walk_forward.WalkForwardEvaluator`
        and returns its aggregated OOS result directly (robustness + cost
        are not run for walk-forward mode as they require a flat panel).

        Parameters
        ----------
        shuffle_iter : int
            Number of shuffle permutations. Pass 0 to skip shuffle test.
        shuffle_workers : int
            ProcessPool size for shuffle test.
        subsample_freq : str
            Pandas-compatible offset alias for subsample IC grouping
            (``"ME"`` = month-end). Translated to a polars duration via
            ``ic._FREQ_MAP``.
        cross_symbol_args : dict | None
            If provided, runs ``cross_symbol_ic`` with these kwargs.
        """
        # --- Walk-forward branch -------------------------------------------
        if config.walk_forward is not None:
            wf_evaluator = WalkForwardEvaluator(config.walk_forward)
            fwd_df, _close_df = self._prepare_returns(returns, config)
            fwd_config = dataclasses.replace(config, returns_kind="forward_returns")
            base_result, _, _, _ = self._evaluate_core(factor_values, fwd_df, fwd_config)

            def _eval_fn(panel: Panel, fwd: Panel) -> EvalResult:
                result, _, _, _ = self._evaluate_core(panel, fwd, fwd_config)
                return result

            wf_result = wf_evaluator.evaluate(factor_values, fwd_df, eval_fn=_eval_fn)
            wf_result.base_eval = dataclasses.asdict(base_result)
            return _scrub_result(wf_result)

        result, factor_df, fwd_df, _close_df = self._evaluate_core(
            factor_values, returns, config
        )

        robustness: dict[str, Any] = {}

        # --- Shuffle test ------------------------------------------------
        if shuffle_iter and shuffle_iter > 0:
            try:
                robustness["shuffle"] = shuffle_test(
                    factor_df, fwd_df, n_iter=shuffle_iter, max_workers=shuffle_workers,
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
        robustness["subsample"] = subsample_ic(factor_df, fwd_df, freq=subsample_freq)

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
        returns: Panel | pl.Series | Any,
        config: EvalConfig,
    ) -> tuple[pl.DataFrame, pl.DataFrame | None]:
        """Translate ``returns`` into a forward-return evaluation frame.

        ``config.returns_kind`` is the only source of truth.  Do not infer
        semantics from values: a sliced forward-return fold can be strictly
        positive and have no tail null marker, making it indistinguishable
        from a close-price slice by shape alone.

        Returns
        -------
        tuple[pl.DataFrame, pl.DataFrame | None]
            ``(forward_return_frame, close_frame_or_none)`` — frames use
            ``[ts, value]`` or ``[ts, symbol, value]``.  The close
            frame is ``None`` when the input was already pre-shifted (so
            :func:`compute_ic_decay` can be skipped in the caller).
        """
        as_frame = _to_ts_value(returns)
        if config.returns_kind == "forward_returns":
            return as_frame, None
        if config.returns_kind == "close":
            fwd = forward_returns(as_frame, config.forward_period, log_ret=config.log_ret)
            return fwd, as_frame
        raise ValueError(
            "unknown EvalConfig.returns_kind="
            f"{config.returns_kind!r}; expected 'close' or 'forward_returns'"
        )

    def _evaluate_core(
        self,
        factor_values: Panel | pl.Series | Any,
        returns: Panel | pl.Series | Any,
        config: EvalConfig,
    ) -> tuple[EvalResult, pl.DataFrame, pl.DataFrame, pl.DataFrame | None]:
        """Shared implementation — returns ``(result, factor_df, fwd_df, close_df)``.

        Both :meth:`evaluate` and :meth:`evaluate_full` delegate here so
        flattening + ``_prepare_returns`` happen exactly once.
        """
        factor_df = _to_ts_value(factor_values)
        fwd_df, close_df = self._prepare_returns(returns, config)

        # Defensive: align by identity keys so stacked panels share the same
        # asset/time axis (mirrors pandas ``align(join="inner")`` without
        # producing the symbol² row explosion caused by joining on ``ts`` only).
        key_cols = _join_keys(factor_df, fwd_df)
        _ensure_unique_identity_keys(factor_df, key_cols, "factor")
        _ensure_unique_identity_keys(fwd_df, key_cols, "fwd_ret")
        _ensure_factor_keys_covered(factor_df, fwd_df, key_cols)
        common_keys = factor_df.select(key_cols).join(
            fwd_df.select(key_cols), on=key_cols, how="inner",
        )
        factor_df = factor_df.join(common_keys, on=key_cols, how="inner")
        fwd_df = fwd_df.join(common_keys, on=key_cols, how="inner")

        result = EvalResult()

        # 1. Distribution — factor-only analysis, cheap.
        dist = compute_distribution(factor_df)
        result.distribution_stats = dist.get("stats", {})
        result.distribution_histogram = dist.get("histogram", [])

        # 2. IC series + summary.
        ic_series_df = compute_ic_series(factor_df, fwd_df, freq=config.ic_freq)
        summary = compute_ic_summary(ic_series_df)
        result.ic_mean = float(summary["ic_mean"])
        result.ic_std = float(summary["ic_std"])
        result.ir = float(summary["ir"])
        result.ic_tstat = float(summary["ic_tstat"])
        result.ic_positive_pct = float(summary["ic_positive_pct"])
        result.ic_max_abs = float(summary["ic_max_abs"])
        result.ic_series = (
            ic_series_df.to_dicts() if ic_series_df.height > 0 else []
        )

        # 3. Decay + half-life — needs the close price, not forward returns.
        if close_df is not None:
            decay = compute_ic_decay(factor_df, close_df)
            result.ic_decay = decay
            result.half_life = compute_half_life(decay)

        # 4. Quantile analysis.
        q = compute_quantile_returns(factor_df, fwd_df, n_quantiles=config.quantiles)
        result.quantile_pnl = q.get("avg_returns", {})
        result.quantile_cum_returns = q.get("cum_returns", {})
        result.is_monotonic = bool(q.get("is_monotonic", False))

        # 5. Turnover — uses the cost_bps config for fee drag.
        fee_rate = config.cost_bps / 10000.0 / 2.0  # bps → per-side decimal
        turn = compute_turnover(factor_df, fwd_df, n_quantiles=config.quantiles, fee_rate=fee_rate)
        result.turnover = float(turn["daily"])
        result.turnover_annualized = float(turn["annualized"])
        result.fee_drag_monthly = float(turn["fee_drag_monthly"])

        # 6. Rating.
        result.rating = compute_rating({
            "ir": result.ir,
            "ic_positive_pct": result.ic_positive_pct,
        })

        return _scrub_result(result), factor_df, fwd_df, close_df


__all__ = ["Evaluator"]
