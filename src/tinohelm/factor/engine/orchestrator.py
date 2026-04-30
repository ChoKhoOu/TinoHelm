"""Orchestrator for the declarative factor framework.

Integrates every sub-system built in s3-s10 into two entry points:

* :meth:`Orchestrator.run` — single-factor end-to-end diagnostic (cache lookup
  → data load → kernel exec → evaluate → cache store → observer summary).
* :meth:`Orchestrator.batch_run` — multi-factor batch variant that shares a
  single data load across all factors and executes within-layer kernels in
  parallel via :class:`~tinohelm.factor.engine.scheduler.Scheduler`.

Design notes
------------
* **No new abstractions.** This module composes already-built pieces:
  :class:`Registry`, :class:`DataLayer`, :class:`Planner`,
  :class:`Scheduler`, :class:`AbstractBackend`, :class:`Evaluator`,
  :class:`FactorCache`, :class:`Observer`.  The "orchestration" itself is
  just a fixed sequence of method calls.
* **Kernel call convention** is delegated to the *same* helpers the
  :class:`Scheduler` uses (``_select_inputs`` + ``_call_kernel``) — so
  single-factor ``run()`` and batch ``batch_run()`` share identical kernel
  invocation semantics.
* **Cache key** is ``FactorCache.build_key(name, code_hash, config,
  data_range)``.  ``data_range`` is ``(config.start, config.end)``.
* **Observer spans**: every run emits ``orchestrator.run`` (outer) with
  nested ``data_load``, ``kernel_exec``, ``evaluate`` spans.  When the cache
  is fully hit, only the outer span is emitted, tagged ``cache_hit=True``.
* **Failure isolation** in batch: a single factor raising an exception does
  not stop others — its slot in the returned dict is ``None`` (not missing)
  so callers can distinguish "failed" from "unknown".

Frequency handling
------------------
The underlying :class:`DataLayer` expects **short-form** frequency strings
(``"1m"``, ``"5m"``, ``"1h"``, …) as used by ``catalog_helpers.INTERVAL_MAP``.
Callers pass the interval via the ``interval`` argument (default ``"1m"``);
it's threaded into the ``DataRequest`` objects this module generates.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable

from joblib import Parallel, delayed

from tinohelm.factor.backend.base import AbstractBackend
from tinohelm.factor.cache import FactorCache
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.engine.scheduler import Scheduler
from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.observer import Observer
from tinohelm.factor.registry import Registry
from tinohelm.factor.types import (
    DataRequest,
    EvalConfig,
    EvalResult,
    FactorSpec,
    Panel,
)

logger = logging.getLogger(__name__)

#: Default frequency passed to :class:`DataLayer` when the caller does not
#: supply one via the ``interval`` argument.  Uses the short-form convention
#: from :data:`tinohelm.data.catalog_helpers.INTERVAL_MAP`.
_DEFAULT_INTERVAL: str = "1m"


# ---------------------------------------------------------------------------
# Internal: DataRequest construction
# ---------------------------------------------------------------------------

def _build_data_requests(
    specs: list[FactorSpec],
    universe: tuple[str, ...],
    interval: str,
) -> list[DataRequest]:
    """Build deduplicated ``DataRequest`` objects from a list of factor specs.

    Grouping rule: ``(symbol, field_name, frequency, source)``.  Within
    each group the max ``spec.lookback`` wins (lookback closure — same
    semantics as :meth:`Planner.merge_data_requests`, but inlined here so
    we can pass a short-form frequency directly to :class:`DataLayer`).
    """
    # Import the planner's source inference to stay in lock-step with it.
    from tinohelm.factor.engine.planner import _infer_source

    merged: dict[tuple[str, str, str, str], int] = {}
    for spec in specs:
        for inp in spec.input_specs:
            resolved_freq = inp.frequency if inp.frequency is not None else interval
            source = _infer_source(inp.field_name)
            for sym in universe:
                key = (sym, inp.field_name, resolved_freq, source)
                existing = merged.get(key, 0)
                merged[key] = max(existing, spec.lookback)

    return [
        DataRequest(
            symbol=sym,
            field_name=field_name,
            frequency=freq,
            lookback=lookback,
            source=source,
        )
        for (sym, field_name, freq, source), lookback in sorted(merged.items())
    ]


def _extract_close_panel(data: dict[str, Panel]) -> Panel | None:
    """Return the ``close`` Panel from a loaded data dict, if present."""
    return data.get("close")


def _ensure_close_requests(
    requests: list[DataRequest],
    universe: tuple[str, ...],
    interval: str,
) -> list[DataRequest]:
    """Ensure evaluation close prices are included in the shared data load."""
    existing = {
        (r.symbol, r.field_name, r.frequency, r.source)
        for r in requests
    }
    out = list(requests)
    for sym in universe:
        key = (sym, "close", interval, "bar")
        if key not in existing:
            out.append(
                DataRequest(
                    symbol=sym,
                    field_name="close",
                    frequency=interval,
                    lookback=0,
                    source="bar",
                )
            )
    return out


def _serialize_segment_results(segment_results: dict[str, dict]) -> dict[str, dict]:
    """Convert nested EvalResult objects returned by segmentation to dicts."""
    out: dict[str, dict] = {}
    for provider, segments in segment_results.items():
        out[provider] = {}
        for label, value in segments.items():
            if dataclasses.is_dataclass(value):
                out[provider][label] = dataclasses.asdict(value)
            else:
                out[provider][label] = value
    return out


def _merge_effective_params(spec: FactorSpec, config: EvalConfig, params: dict | None = None) -> dict:
    """Merge factor defaults with config/request params for execution."""
    merged = dict(spec.params or {})
    merged.update(dict(config.params or {}))
    if params:
        merged.update(dict(params))
    return merged


def _attach_run_metadata(
    result: EvalResult,
    *,
    spec: FactorSpec,
    cache_key: str,
    cache_hit: bool,
    effective_params: dict,
) -> EvalResult:
    """Attach source/cache metadata to an EvalResult for LLM diagnostics."""
    result.effective_params = dict(effective_params)
    result.cache_key = cache_key or None
    result.cache_hit = cache_hit
    result.factor_code_hash = spec.code_hash
    result.factor_source_file = spec.source_file
    result.factor_module_path = spec.module_path
    return result


def _select_btc_or_first_symbol(panel: Panel, universe: tuple[str, ...]) -> str | None:
    """Choose a close/funding column for BTC-based segmentation providers."""
    candidate_cols = [c for c in panel.columns if c != "ts"]
    for sym in universe:
        if sym in candidate_cols and sym.upper().startswith("BTC"):
            return sym
    for col in candidate_cols:
        if col.upper().startswith("BTC"):
            return col
    return candidate_cols[0] if candidate_cols else None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """End-to-end coordinator for single-factor and batch factor runs.

    Parameters
    ----------
    registry:
        :class:`~tinohelm.factor.registry.Registry` for spec + kernel lookup.
    data_layer:
        :class:`~tinohelm.factor.data_layer.DataLayer` for parquet / JSON
        loading.  Must already be configured with a :class:`Universe`.
    backend:
        :class:`~tinohelm.factor.backend.base.AbstractBackend` implementation
        (typically :class:`~tinohelm.factor.backend.polars_backend.PolarsBackend`).
    evaluator:
        :class:`~tinohelm.factor.evaluation.evaluator.Evaluator` instance.
    cache:
        Optional :class:`~tinohelm.factor.cache.FactorCache`.  When ``None``,
        no caching is performed (always recompute).
    observer:
        Optional :class:`~tinohelm.factor.observer.Observer`.  When ``None``,
        a stateless no-op observer is created internally so downstream code
        can always call ``observer.start_span`` / ``record_output_stats``.
    """

    def __init__(
        self,
        registry: Registry,
        data_layer: DataLayer,
        backend: AbstractBackend,
        evaluator: Evaluator,
        cache: FactorCache | None = None,
        observer: Observer | None = None,
    ) -> None:
        self._registry = registry
        self._data_layer = data_layer
        self._backend = backend
        self._evaluator = evaluator
        self._cache = cache
        self._observer = observer if observer is not None else Observer()

    # ------------------------------------------------------------------
    # Public API — single factor run
    # ------------------------------------------------------------------

    def run(
        self,
        factor_name: str,
        config: EvalConfig,
        *,
        params: dict | None = None,
        run_id: str | None = None,
        full: bool = False,
        interval: str = _DEFAULT_INTERVAL,
    ) -> EvalResult:
        """Run the full diagnostic pipeline for a single factor.

        Parameters
        ----------
        factor_name:
            Factor identifier as registered in :class:`Registry`.
        config:
            :class:`EvalConfig` describing universe, date range, IC freq, etc.
        params:
            Per-run parameter overrides. These are merged with
            ``FactorSpec.params`` and ``config.params`` and passed to kernels
            that accept a ``params`` keyword.
        run_id:
            Optional run identifier forwarded to the observer span tag.  When
            ``None``, the observer's auto-generated UUID is used.
        full:
            When ``True``, uses :meth:`Evaluator.evaluate_full` (adds
            robustness + cost waterfall).  Default ``False`` (fast path).
        interval:
            Short-form bar frequency string (e.g. ``"1m"``, ``"5m"``).  Passed
            to :class:`DataLayer` for OHLCV loads.  Default ``"1m"``.

        Returns
        -------
        EvalResult
            Complete evaluation outcome with IC / quantile / turnover /
            distribution / (optional) robustness + cost.

        Raises
        ------
        KeyError
            If ``factor_name`` is not registered.
        """
        spec = self._registry.get_spec(factor_name)
        if spec is None:
            raise KeyError(
                f"Factor {factor_name!r} not found in registry. "
                "Did you call Registry.scan()?"
            )

        effective_params = _merge_effective_params(spec, config, params)
        if effective_params != dict(config.params or {}):
            config = dataclasses.replace(config, params=effective_params)

        data_range = (config.start, config.end)
        cache_key = (
            FactorCache.build_key(factor_name, spec.code_hash, config, data_range)
            if self._cache is not None
            else ""
        )

        # -- Outer span tags ----------------------------------------------
        run_tags: dict[str, Any] = {"factor": factor_name}
        if run_id is not None:
            run_tags["run_id"] = run_id

        with self._observer.start_span("orchestrator.run", **run_tags) as outer_ctx:
            # 1. Cache lookup --------------------------------------------
            if self._cache is not None:
                hit = self._cache.lookup(cache_key)
                if hit is not None and hit.factor_values_hit and hit.eval_hit:
                    outer_ctx.tags["cache_hit"] = True
                    assert hit.eval_result is not None  # mypy happiness
                    return _attach_run_metadata(
                        hit.eval_result,
                        spec=spec,
                        cache_key=cache_key,
                        cache_hit=True,
                        effective_params=effective_params,
                    )
                # Partial hit — we may short-circuit compute when only the
                # factor values are cached (still need to evaluate).
                cached_values: Panel | None = (
                    hit.factor_values if hit is not None and hit.factor_values_hit else None
                )
            else:
                cached_values = None

            outer_ctx.tags["cache_hit"] = False

            # 2. Data load -----------------------------------------------
            if cached_values is None:
                requests = _ensure_close_requests(
                    _build_data_requests(
                        [spec], config.universe, interval=interval
                    ),
                    config.universe,
                    interval,
                )
                with self._observer.start_span("data_load", factor=factor_name):
                    data = self._data_layer.load(
                        requests, start=config.start, end=config.end
                    )
            else:
                data = {}  # will be reconstructed for evaluate below

            # 3. Compute (kernel exec) -----------------------------------
            if cached_values is None:
                kernel = self._registry.get_kernel(factor_name)
                with self._observer.start_span("kernel_exec", factor=factor_name):
                    factor_values = _call_kernel(
                        spec, kernel, data, self._backend, params=effective_params
                    )
            else:
                factor_values = cached_values

            if config.neutralize:
                with self._observer.start_span(
                    "neutralize",
                    factor=factor_name,
                    providers=list(config.neutralize),
                ):
                    factor_values = self._neutralize_factor_values(factor_values, config)

            self._observer.record_output_stats(factor_name, factor_values)

            # 4. Evaluate ------------------------------------------------
            # The evaluator needs ``close`` price to compute forward returns
            # and IC decay.  Ensure close is available:
            # - If data was loaded from scratch, close may already be present.
            # - If we hit a partial cache (cached_values != None), close was
            #   skipped during data_load and must be fetched separately.
            # - If the factor does not use close directly, we still need it for
            #   evaluation (forward return computation).  Load it on demand.
            if "close" not in data:
                close_requests = [
                    DataRequest(
                        symbol=sym,
                        field_name="close",
                        frequency=interval,
                        lookback=spec.lookback,
                        source="bar",
                    )
                    for sym in config.universe
                ]
                reason = "partial_cache" if cached_values is not None else "close_not_in_data"
                with self._observer.start_span("data_load", factor=factor_name,
                                               reason=reason):
                    close_data = self._data_layer.load(
                        close_requests, start=config.start, end=config.end
                    )
                    data = {**data, **close_data}

            close_panel = _extract_close_panel(data)
            if close_panel is None:
                raise RuntimeError(
                    f"Factor {factor_name!r}: 'close' Panel could not be loaded; "
                    "cannot compute forward returns for evaluation."
                )

            with self._observer.start_span("evaluate", factor=factor_name, full=full):
                if full:
                    eval_result = self._evaluator.evaluate_full(
                        factor_values, close_panel, config
                    )
                else:
                    eval_result = self._evaluator.evaluate(
                        factor_values, close_panel, config
                    )

            if config.neutralize:
                eval_result.neutralization_config = {
                    "providers": list(config.neutralize),
                    "method": "ols",
                }

            _attach_run_metadata(
                eval_result,
                spec=spec,
                cache_key=cache_key,
                cache_hit=False,
                effective_params=effective_params,
            )

            if config.segments:
                with self._observer.start_span(
                    "segment_evaluate",
                    factor=factor_name,
                    providers=list(config.segments),
                ):
                    eval_result.segment_results = self._evaluate_segments(
                        factor_values=factor_values,
                        close_panel=close_panel,
                        data=data,
                        config=config,
                    )

            # 5. Cache store ---------------------------------------------
            if self._cache is not None:
                self._cache.store(
                    cache_key,
                    factor_name=factor_name,
                    code_hash=spec.code_hash,
                    factor_values=factor_values,
                    eval_result=eval_result,
                )

            return eval_result

    def _neutralize_factor_values(self, factor_values: Panel, config: EvalConfig) -> Panel:
        """Apply configured Universe PIT masking and OLS neutralization."""
        from tinohelm.aligner.aligner import Aligner
        from tinohelm.factor.universe import Universe

        universe_obj = getattr(self._data_layer, "_universe", None)
        if universe_obj is None:
            universe_obj = Universe.from_symbols(config.universe)
        aligner = Aligner(universe_obj, neutralize=list(config.neutralize))
        return aligner.align(factor_values)

    def _evaluate_segments(
        self,
        *,
        factor_values: Panel,
        close_panel: Panel,
        data: dict[str, Panel],
        config: EvalConfig,
    ) -> dict[str, dict]:
        """Run requested segmentation providers against the production panel."""
        from tinohelm.factor.evaluation.segmentation import segment_evaluate

        requested = set(config.segments)
        supported = {"btc_trend", "vol_regime", "funding_level"}
        unknown = sorted(requested - supported)
        if unknown:
            raise ValueError(f"Unknown factor segment provider(s): {unknown}")

        symbol = _select_btc_or_first_symbol(close_panel, config.universe)
        btc_close_series = close_panel[symbol] if symbol else None
        btc_vol_series = close_panel[symbol] if symbol else None

        funding_series = None
        if "funding_level" in requested:
            funding_panel = data.get("funding_rate")
            if funding_panel is None:
                funding_requests = [
                    DataRequest(
                        symbol=sym,
                        field_name="funding_rate",
                        frequency="8h",
                        lookback=0,
                        source="funding_rate",
                    )
                    for sym in config.universe
                ]
                funding_data = self._data_layer.load(
                    funding_requests, start=config.start, end=config.end
                )
                funding_panel = funding_data.get("funding_rate")
            if funding_panel is not None:
                funding_symbol = _select_btc_or_first_symbol(funding_panel, config.universe)
                funding_series = funding_panel[funding_symbol] if funding_symbol else None
            if funding_series is None:
                raise RuntimeError("funding_level segmentation requires a funding_rate panel")

        # Avoid computing unrequested BTC-derived providers by withholding the
        # corresponding input series.
        raw = segment_evaluate(
            factor_values,
            self._evaluator._prepare_returns(close_panel, config)[0],
            btc_close_series=(btc_close_series if "btc_trend" in requested else None),
            btc_vol_series=(btc_vol_series if "vol_regime" in requested else None),
            funding_series=funding_series,
            eval_config=dataclasses.replace(config, returns_kind="forward_returns"),
        )
        return _serialize_segment_results({k: v for k, v in raw.items() if k in requested})

    # ------------------------------------------------------------------
    # Public API — batch factor run
    # ------------------------------------------------------------------

    def batch_run(
        self,
        factor_names: list[str],
        config: EvalConfig,
        *,
        params_map: dict[str, dict] | None = None,
        run_id: str | None = None,
        full: bool = False,
        interval: str = _DEFAULT_INTERVAL,
        max_workers: int | None = None,
    ) -> dict[str, EvalResult | None]:
        """Run multiple factors as a batch — one shared data load + parallel kernels.

        Parameters
        ----------
        factor_names:
            List of factor identifiers as registered in :class:`Registry`.
        config:
            :class:`EvalConfig` used for every factor in the batch.
        params_map:
            Optional ``{factor_name: params_dict}`` override map.  Currently
            threaded through for forward-compatibility; kernels consume
            scalars from their own closures.
        run_id:
            Optional shared identifier for the whole batch observer span.
        full:
            When ``True`` uses :meth:`Evaluator.evaluate_full` per factor.
        interval:
            Short-form bar frequency string used for OHLCV loads.
        max_workers:
            :class:`Scheduler` worker cap (``None`` = one thread per factor).

        Returns
        -------
        dict[str, EvalResult | None]
            Mapping from factor name to :class:`EvalResult`.  A factor that
            failed (missing spec, kernel raised, evaluator raised) is mapped
            to ``None`` — the key is still present so callers can distinguish
            "failed" from "unknown".
        """
        params_map = params_map or {}

        batch_tags: dict[str, Any] = {"factors": list(factor_names), "full": full}
        if run_id is not None:
            batch_tags["run_id"] = run_id

        with self._observer.start_span("orchestrator.batch_run", **batch_tags):
            # 1. Resolve specs -------------------------------------------
            specs: list[FactorSpec] = []
            results: dict[str, EvalResult | None] = {}
            for name in factor_names:
                spec = self._registry.get_spec(name)
                if spec is None:
                    logger.warning(
                        "batch_run: factor %r not in registry — recording failure",
                        name,
                    )
                    results[name] = None
                    continue
                specs.append(spec)

            if not specs:
                return results

            # 2. Cache lookup (per-factor) --------------------------------
            # Any factor with a full hit is filled immediately and dropped
            # from the compute set.
            data_range = (config.start, config.end)
            cache_keys: dict[str, str] = {}
            specs_to_compute: list[FactorSpec] = []
            cached_values_map: dict[str, Panel] = {}

            for spec in specs:
                if self._cache is None:
                    effective = _merge_effective_params(spec, config, params_map.get(spec.name))
                    if effective != dict(config.params or {}):
                        spec_config = dataclasses.replace(config, params=effective)
                    else:
                        spec_config = config
                    cache_keys[spec.name] = FactorCache.build_key(
                        spec.name, spec.code_hash, spec_config, data_range
                    )
                    specs_to_compute.append(spec)
                    continue
                effective = _merge_effective_params(spec, config, params_map.get(spec.name))
                spec_config = dataclasses.replace(config, params=effective)
                cache_keys[spec.name] = FactorCache.build_key(
                    spec.name, spec.code_hash, spec_config, data_range
                )
                hit = self._cache.lookup(cache_keys[spec.name])
                if hit is not None and hit.factor_values_hit and hit.eval_hit:
                    assert hit.eval_result is not None
                    results[spec.name] = _attach_run_metadata(
                        hit.eval_result,
                        spec=spec,
                        cache_key=cache_keys[spec.name],
                        cache_hit=True,
                        effective_params=effective,
                    )
                    continue
                if hit is not None and hit.factor_values_hit:
                    cached_values_map[spec.name] = hit.factor_values  # type: ignore[assignment]
                specs_to_compute.append(spec)

            if not specs_to_compute:
                return results

            # 3. Single merged data load ----------------------------------
            requests = _ensure_close_requests(
                _build_data_requests(
                    specs_to_compute, config.universe, interval=interval
                ),
                config.universe,
                interval,
            )
            with self._observer.start_span("data_load", n_factors=len(specs_to_compute)):
                data = self._data_layer.load(
                    requests, start=config.start, end=config.end
                )

            close_panel = _extract_close_panel(data)
            if close_panel is None:
                # No close → every compute-target factor fails
                for spec in specs_to_compute:
                    if spec.name in cached_values_map:
                        # We have cached values but no close → still can't evaluate
                        logger.warning(
                            "batch_run: factor %r has cached values but 'close' "
                            "Panel is missing — marking failed",
                            spec.name,
                        )
                    results[spec.name] = None
                return results

            # 4. Parallel kernel execution --------------------------------
            factor_values_map: dict[str, Panel] = dict(cached_values_map)  # start with cached
            errors_map: dict[str, str] = {}

            specs_needing_compute = [
                s for s in specs_to_compute if s.name not in cached_values_map
            ]

            if specs_needing_compute:
                # Route each spec's kernel through the Registry. Use the
                # Scheduler's _call_kernel convention for uniformity.
                kernel_map: dict[str, Callable] = {}
                for spec in specs_needing_compute:
                    try:
                        kernel_map[spec.name] = self._registry.get_kernel(spec.name)
                    except KeyError:
                        errors_map[spec.name] = (
                            f"No kernel registered for {spec.name!r}"
                        )

                specs_with_kernels = [
                    s for s in specs_needing_compute if s.name in kernel_map
                ]

                # Fan out via joblib.Parallel (threading backend keeps thread
                # semantics identical to the old ThreadPoolExecutor while
                # enabling joblib's smarter load-balancing and progress hooks).
                # We use backend="threading" rather than "loky" because factor
                # kernels may be local closures (e.g. in tests) which are not
                # picklable across a process boundary.
                n_workers = (
                    max_workers if max_workers is not None else max(1, len(specs_with_kernels))
                )

                # Record kernel_exec spans; actual execution is concurrent.
                # ``_run_one_kernel`` returns ``BaseException`` instead of
                # raising so that one factor failing does not abort the
                # whole ``Parallel`` batch (joblib 1.5 dropped the
                # ``return_exceptions`` kwarg — failure isolation is
                # implemented in the worker function instead).
                with self._observer.start_span("kernel_exec", n_factors=len(specs_with_kernels)):
                    raw_results = Parallel(
                        n_jobs=n_workers,
                        backend="threading",
                    )(
                        delayed(_run_one_kernel)(
                            spec,
                            kernel_map[spec.name],
                            data,
                            self._backend,
                            _merge_effective_params(spec, config, params_map.get(spec.name)),
                        )
                        for spec in specs_with_kernels
                    )

                    for spec, outcome in zip(specs_with_kernels, raw_results):
                        if isinstance(outcome, BaseException):
                            errors_map[spec.name] = (
                                f"{type(outcome).__name__}: {outcome}"
                            )
                            logger.warning(
                                "batch_run: factor %r raised %s",
                                spec.name,
                                errors_map[spec.name],
                            )
                        else:
                            factor_values_map[spec.name] = outcome

            # 5. Evaluate each successful factor + cache store ------------
            for spec in specs_to_compute:
                if spec.name in errors_map:
                    results[spec.name] = None
                    continue

                factor_values = factor_values_map.get(spec.name)
                if factor_values is None:
                    # Shouldn't happen with our bookkeeping, but guard anyway.
                    results[spec.name] = None
                    continue

                if config.neutralize:
                    with self._observer.start_span(
                        "neutralize",
                        factor=spec.name,
                        providers=list(config.neutralize),
                    ):
                        factor_values = self._neutralize_factor_values(factor_values, config)

                self._observer.record_output_stats(spec.name, factor_values)

                try:
                    effective_params = _merge_effective_params(
                        spec, config, params_map.get(spec.name)
                    )
                    eval_config = dataclasses.replace(config, params=effective_params)
                    with self._observer.start_span("evaluate", factor=spec.name, full=full):
                        if full:
                            eval_result = self._evaluator.evaluate_full(
                                factor_values, close_panel, eval_config
                            )
                        else:
                            eval_result = self._evaluator.evaluate(
                                factor_values, close_panel, eval_config
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "batch_run: evaluator failed for %r: %s", spec.name, exc
                    )
                    results[spec.name] = None
                    continue

                if config.neutralize:
                    eval_result.neutralization_config = {
                        "providers": list(config.neutralize),
                        "method": "ols",
                    }
                _attach_run_metadata(
                    eval_result,
                    spec=spec,
                    cache_key=cache_keys.get(spec.name, ""),
                    cache_hit=False,
                    effective_params=effective_params,
                )
                if config.segments:
                    eval_result.segment_results = self._evaluate_segments(
                        factor_values=factor_values,
                        close_panel=close_panel,
                        data=data,
                        config=config,
                    )

                results[spec.name] = eval_result

                # Cache store per factor
                if self._cache is not None:
                    key = cache_keys.get(spec.name)
                    if key is None:
                        continue
                    self._cache.store(
                        key,
                        factor_name=spec.name,
                        code_hash=spec.code_hash,
                        factor_values=factor_values,
                        eval_result=eval_result,
                    )

            return results


# ---------------------------------------------------------------------------
# Helpers shared between single-run and batch-run
# ---------------------------------------------------------------------------

def _call_kernel(
    spec: FactorSpec,
    kernel: Callable,
    data: dict[str, Panel],
    backend: AbstractBackend,
    params: dict | None = None,
) -> Panel:
    """Invoke *kernel* with the right calling convention and return its Panel.

    Reuses :meth:`Scheduler._select_inputs` + :meth:`Scheduler._call_kernel`
    so this orchestrator's single-factor path produces identical semantics
    to the batch scheduler's.
    """
    factor_data = Scheduler._select_inputs(spec, data)
    return Scheduler._call_kernel(spec, kernel, factor_data, backend, params=params)


def _run_one_kernel(
    spec: FactorSpec,
    kernel: Callable,
    data: dict[str, Panel],
    backend: AbstractBackend,
    params: dict | None = None,
) -> Panel | BaseException:
    """Worker-thread entry that pairs _select_inputs + _call_kernel.

    Kept at module level so it pickles cleanly for any future
    :class:`ProcessPoolExecutor` migration.

    Failure isolation contract
    --------------------------
    Returns a :class:`BaseException` instance instead of raising when the
    kernel fails.  joblib 1.5 dropped support for the ``return_exceptions``
    kwarg, so exceptions raised inside ``Parallel`` workers now propagate
    through ``Parallel.__call__`` and abort the whole batch.  Catching here
    keeps the behaviour the call-site in :meth:`Orchestrator.batch_run`
    already expects (it inspects each outcome with
    ``isinstance(outcome, BaseException)``) and preserves the design's
    failure-isolation guarantee.
    """
    try:
        return _call_kernel(spec, kernel, data, backend, params=params)
    except BaseException as exc:  # noqa: BLE001 — by design: isolate per-factor failures
        return exc


__all__ = ["Orchestrator"]
