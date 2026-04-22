"""Structured observability for the declarative factor framework.

Provides span tracing (with nesting) and per-factor output statistics.
Every ``end_span`` emits a structured JSON log line via the injected logger.
``summary()`` aggregates all spans and output stats into a single dict.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

import numpy as np
import pandas as pd

_DEFAULT_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SpanRecord — internal storage for a completed span
# ---------------------------------------------------------------------------

@dataclass
class SpanRecord:
    span_id: str
    name: str
    parent_id: str | None
    start_ts: float  # seconds since epoch (time.time())
    end_ts: float
    duration_ms: float
    tags: dict[str, Any]
    error: str | None  # error repr, or None


# ---------------------------------------------------------------------------
# SpanCtx — handle returned by start_span / used as context manager
# ---------------------------------------------------------------------------

class SpanCtx:
    """Handle for an in-progress span.

    Usage (context manager — preferred)::

        with observer.start_span("kernel_exec", factor="ret_20") as s:
            ...  # span auto-ends on exit; exceptions are recorded
    """

    def __init__(self, observer: "Observer", span_id: str, name: str,
                 parent_id: str | None, start_ts: float,
                 tags: dict[str, Any]) -> None:
        self._observer = observer
        self.span_id = span_id
        self.name = name
        self.parent_id = parent_id
        self.start_ts = start_ts
        self.tags = tags
        self._finished = False

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "SpanCtx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if not self._finished:
            self._observer.end_span(self, error=exc_val if exc_val is not None else None)
        # Do not suppress exceptions
        return False


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------

class Observer:
    """Structured observability helper for factor framework runs.

    Parameters
    ----------
    run_id:
        Identifier for this evaluation run.  Auto-generated UUID4 if not
        supplied.
    logger:
        Standard-library logger to emit structured JSON lines.  Defaults to
        the module logger (``tinohelm.factor.observer``).  Inject a custom
        logger in tests to capture output.
    """

    def __init__(self, run_id: str | None = None,
                 logger: logging.Logger | None = None) -> None:
        self.run_id: str = run_id or str(uuid.uuid4())
        self._logger: logging.Logger = logger or _DEFAULT_LOGGER
        self._spans: list[SpanRecord] = []
        self._output_stats: dict[str, dict[str, Any]] = {}
        # Stack of active span_ids for parent tracking (thread-local would be
        # more robust in concurrent use, but factor runs are sequential)
        self._active_stack: list[str] = []

    # ------------------------------------------------------------------
    # Span API
    # ------------------------------------------------------------------

    @contextmanager
    def start_span(self, name: str, **tags: Any) -> Generator[SpanCtx, None, None]:
        """Start a span as a context manager.

        Yields a :class:`SpanCtx` handle.  On exit the span is automatically
        ended; any exception is recorded in the span's ``error`` field but is
        **not** suppressed.

        Parameters
        ----------
        name:
            Human-readable span name (e.g. ``"data_load"``, ``"kernel_exec"``).
        **tags:
            Arbitrary key-value metadata attached to the span record.

        Example
        -------
        ::

            with observer.start_span("kernel_exec", factor="ret_20") as s:
                result = compute(...)
        """
        span_id = str(uuid.uuid4())
        parent_id = self._active_stack[-1] if self._active_stack else None
        start_ts = time.time()
        ctx = SpanCtx(self, span_id, name, parent_id, start_ts, tags)
        self._active_stack.append(span_id)
        try:
            yield ctx
        except Exception as exc:
            if not ctx._finished:
                self.end_span(ctx, error=exc)
            raise
        finally:
            if not ctx._finished:
                self.end_span(ctx, error=None)
            # Pop from stack (guard against double-pop if end_span called manually)
            if span_id in self._active_stack:
                self._active_stack.remove(span_id)

    def end_span(self, span: SpanCtx, error: Exception | None = None) -> None:
        """Finalize a span and emit a structured log line.

        Normally called automatically by the context manager.  May be called
        manually when not using the ``with`` form.

        Parameters
        ----------
        span:
            The :class:`SpanCtx` returned by :meth:`start_span`.
        error:
            Exception instance if the span ended with an error, else ``None``.
        """
        if span._finished:
            return
        span._finished = True

        end_ts = time.time()
        duration_ms = (end_ts - span.start_ts) * 1000.0

        record = SpanRecord(
            span_id=span.span_id,
            name=span.name,
            parent_id=span.parent_id,
            start_ts=span.start_ts,
            end_ts=end_ts,
            duration_ms=duration_ms,
            tags=span.tags,
            error=repr(error) if error is not None else None,
        )
        self._spans.append(record)

        # Remove from active stack
        if span.span_id in self._active_stack:
            self._active_stack.remove(span.span_id)

        # Emit structured JSON log line
        log_payload: dict[str, Any] = {
            "event": "span_end",
            "run_id": self.run_id,
            "span_id": record.span_id,
            "name": record.name,
            "parent_id": record.parent_id,
            "start_ts": record.start_ts,
            "end_ts": record.end_ts,
            "duration_ms": round(record.duration_ms, 3),
            "tags": record.tags,
            "error": record.error,
        }
        self._logger.info(json.dumps(log_payload))

    # ------------------------------------------------------------------
    # Output statistics
    # ------------------------------------------------------------------

    def record_output_stats(self, factor_name: str, panel: pd.DataFrame) -> None:
        """Compute and store output statistics for a factor's Panel.

        Statistics computed
        -------------------
        - ``nan_rate``: proportion of NaN values across the whole panel
          (``panel.isna().mean().mean()``)
        - ``nonzero_rate``: proportion of non-NaN, non-zero values
        - ``min``, ``max``, ``mean``, ``std``: scalar statistics (NaN-safe)
        - ``shape``: ``(rows, cols)`` tuple

        Parameters
        ----------
        factor_name:
            Identifier matching the factor's ``FactorSpec.name``.
        panel:
            Output :class:`Panel` (time × symbol DataFrame).
        """
        values = panel.values.astype(float)

        nan_rate = float(np.isnan(values).mean())

        # nonzero: count cells that are not NaN and not zero
        valid_mask = ~np.isnan(values)
        nonzero_mask = valid_mask & (values != 0.0)
        total_cells = values.size
        nonzero_rate = float(nonzero_mask.sum() / total_cells) if total_cells > 0 else 0.0

        finite_vals = values[np.isfinite(values)]
        stats: dict[str, Any] = {
            "nan_rate": nan_rate,
            "nonzero_rate": nonzero_rate,
            "min": float(np.min(finite_vals)) if finite_vals.size > 0 else None,
            "max": float(np.max(finite_vals)) if finite_vals.size > 0 else None,
            "mean": float(np.mean(finite_vals)) if finite_vals.size > 0 else None,
            "std": float(np.std(finite_vals)) if finite_vals.size > 0 else None,
            "shape": list(panel.shape),
        }
        self._output_stats[factor_name] = stats

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a structured dict aggregating all spans and output stats.

        The dict has the following top-level keys:

        - ``run_id``: the run identifier
        - ``spans``: list of span dicts (each with ``span_id``, ``name``,
          ``parent_id``, ``start_ts``, ``end_ts``, ``duration_ms``,
          ``tags``, ``error``)
        - ``output_stats``: dict mapping factor name → stats dict
        """
        spans_out = [
            {
                "span_id": r.span_id,
                "name": r.name,
                "parent_id": r.parent_id,
                "start_ts": r.start_ts,
                "end_ts": r.end_ts,
                "duration_ms": round(r.duration_ms, 3),
                "tags": r.tags,
                "error": r.error,
            }
            for r in self._spans
        ]
        return {
            "run_id": self.run_id,
            "spans": spans_out,
            "output_stats": self._output_stats,
        }
