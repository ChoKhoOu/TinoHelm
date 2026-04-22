"""Scheduler for the declarative factor framework.

Responsibilities
----------------
- Execute a :class:`~tinohelm.factor.engine.planner.Plan` by invoking each
  factor's kernel function in parallel within each topological layer.
- Isolate failures: a single factor raising an exception does not block the
  rest of the layer.  Errors are collected and surfaced in the result dict
  under the ``"errors"`` key.
- Supply each factor kernel with only the data panels it declares in its
  ``input_specs``, filtered from the global ``data`` dict passed by the caller.

Parallelism
-----------
Uses :class:`concurrent.futures.ThreadPoolExecutor` because factor kernels
are typically CPU-bound pandas operations.  The GIL limits true CPU
parallelism for pure-Python work, but for I/O-heavy kernels (rare in factor
research) or when NumPy/pandas releases the GIL internally, this still
provides real concurrency.

``max_workers`` defaults to the number of factors in the group so that each
factor gets its own thread, capped at the system thread limit implicitly
enforced by ``ThreadPoolExecutor``.

Result format
-------------
The ``execute`` method returns a ``dict[str, Panel]`` mapping factor name →
output Panel.  An additional special key ``"errors"`` (if non-empty) holds
a ``dict[str, str]`` mapping factor name → error message string.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from tinohelm.factor.backend.base import AbstractBackend
from tinohelm.factor.engine.planner import Plan
from tinohelm.factor.types import FactorSpec, Panel

logger = logging.getLogger(__name__)


class Scheduler:
    """Executes a :class:`~tinohelm.factor.engine.planner.Plan`.

    Parameters
    ----------
    max_workers:
        Maximum number of threads per layer.  ``None`` means "one thread per
        factor in the group" (no shared cap).
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: Plan,
        data: dict[str, Panel],
        backend: AbstractBackend,
        kernel_map: dict[str, Any] | None = None,
    ) -> dict[str, Panel]:
        """Execute all layers of *plan* and return factor output panels.

        Parameters
        ----------
        plan:
            Plan produced by :class:`~tinohelm.factor.engine.planner.Planner`.
        data:
            Pre-loaded data dict.  Keys are ``field_name`` strings (e.g.
            ``"close"``, ``"funding_rate"``); values are :data:`Panel`
            DataFrames (time × symbol).
        backend:
            :class:`~tinohelm.factor.backend.base.AbstractBackend` instance
            passed to factor kernels.
        kernel_map:
            Optional mapping of factor name → callable.  When provided,
            this callable is used instead of looking up the function via
            ``spec.__factor_func__`` or a registry.  Intended for testing
            and for callers that manage their own function references.

        Returns
        -------
        dict[str, Panel]
            ``{factor_name: output_panel, ..., "errors": {factor_name: msg}}``
            The ``"errors"`` key is always present; it maps to an empty dict
            when all factors succeed.
        """
        results: dict[str, Panel] = {}
        errors: dict[str, str] = {}

        for layer in plan.layers:
            layer_results, layer_errors = self._parallel_group(
                layer, data=data, backend=backend, kernel_map=kernel_map
            )
            results.update(layer_results)
            errors.update(layer_errors)

        results["errors"] = errors  # type: ignore[assignment]
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parallel_group(
        self,
        group: list[FactorSpec],
        *,
        data: dict[str, Panel],
        backend: AbstractBackend,
        kernel_map: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Panel], dict[str, str]]:
        """Execute all specs in *group* in parallel threads.

        Parameters
        ----------
        group:
            Specs to execute concurrently (they are in the same topological
            layer and have no inter-dependencies within this group).
        data:
            Full data dict (same as passed to :meth:`execute`).
        backend:
            Backend instance forwarded to each kernel.
        kernel_map:
            Optional name → callable override.

        Returns
        -------
        tuple[dict[str, Panel], dict[str, str]]
            ``(results, errors)`` — both indexed by factor name.
        """
        if not group:
            return {}, {}

        n_workers = self._max_workers if self._max_workers is not None else len(group)
        results: dict[str, Panel] = {}
        errors: dict[str, str] = {}

        # Map Future → spec so we can attribute errors
        future_to_spec: dict[Future[Panel], FactorSpec] = {}

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            for spec in group:
                kernel = self._resolve_kernel(spec, kernel_map)
                if kernel is None:
                    errors[spec.name] = (
                        f"No kernel found for factor '{spec.name}'. "
                        "Pass a kernel_map or attach __factor_func__ to the spec."
                    )
                    continue

                # _select_inputs may raise KeyError for missing required inputs.
                # Wrap submission so the error is captured per-factor rather
                # than propagating to the caller.
                future = executor.submit(
                    self._run_single, spec, kernel, data, backend
                )
                future_to_spec[future] = spec

            for future in as_completed(future_to_spec):
                spec = future_to_spec[future]
                try:
                    panel = future.result()
                    results[spec.name] = panel
                except Exception as exc:  # noqa: BLE001
                    msg = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "Scheduler: factor '%s' raised %s", spec.name, msg
                    )
                    errors[spec.name] = msg

        return results, errors

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_single(
        spec: FactorSpec,
        kernel: Any,
        data: dict[str, Panel],
        backend: AbstractBackend,
    ) -> Panel:
        """Select inputs and call kernel — all inside the worker thread.

        Keeping both steps in the thread ensures any ``KeyError`` from
        ``_select_inputs`` is captured by the Future rather than raised in the
        main thread during submission.
        """
        factor_data = Scheduler._select_inputs(spec, data)
        return Scheduler._call_kernel(spec, kernel, factor_data, backend)

    @staticmethod
    def _resolve_kernel(
        spec: FactorSpec,
        kernel_map: dict[str, Any] | None,
    ) -> Any:
        """Return the callable for *spec*, or ``None`` if not found."""
        if kernel_map and spec.name in kernel_map:
            return kernel_map[spec.name]
        # Fallback: spec may carry a reference to the original function via a
        # non-frozen attribute set by the decorator (not part of FactorSpec
        # dataclass fields — attached dynamically to the spec object).
        return getattr(spec, "__factor_func__", None)

    @staticmethod
    def _select_inputs(
        spec: FactorSpec,
        data: dict[str, Panel],
    ) -> dict[str, Panel]:
        """Build the subset of *data* that *spec* needs.

        Returns a dict keyed by ``field_name``.  Missing required fields
        produce a ``KeyError`` that will be caught by the caller and recorded
        as an error for the factor.
        """
        selected: dict[str, Panel] = {}
        for inp in spec.input_specs:
            if inp.field_name in data:
                selected[inp.field_name] = data[inp.field_name]
            elif inp.required:
                raise KeyError(
                    f"Required input '{inp.field_name}' not found in data dict "
                    f"for factor '{spec.name}'. Available keys: {list(data.keys())}"
                )
            # Optional missing inputs are simply omitted
        return selected

    @staticmethod
    def _call_kernel(
        spec: FactorSpec,
        kernel: Any,
        factor_data: dict[str, Panel],
        backend: AbstractBackend,
    ) -> Panel:
        """Invoke *kernel* with the appropriate calling convention.

        Two conventions are supported:

        1. **Keyword-only data** — ``kernel(**factor_data)`` — for factor
           functions decorated with ``@factor`` whose signature uses named
           Panel parameters (the primary convention in this framework).

        2. **Backend-first** — ``kernel(backend, **factor_data)`` — for
           kernels that need the backend to be passed explicitly.  Detected by
           checking for a ``backend`` parameter in the signature.

        Returns
        -------
        Panel
            The output DataFrame produced by the kernel.
        """
        import inspect

        sig = inspect.signature(kernel)
        param_names = list(sig.parameters.keys())

        if param_names and param_names[0] == "backend":
            result = kernel(backend, **factor_data)
        else:
            result = kernel(**factor_data)

        if not isinstance(result, pd.DataFrame):
            raise TypeError(
                f"Kernel for factor '{spec.name}' must return a pd.DataFrame "
                f"(Panel), got {type(result).__name__}"
            )
        return result
