"""Forward-return matrix helpers for research evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import hashlib
import numpy as np

from tinohelm.factor.research.panel import MatrixPanel


@dataclass(frozen=True)
class ForwardReturnsKey:
    close_key: str
    content_key: str
    periods: tuple[int, ...]
    log_ret: bool
    expected_step_ns: int | None = None


class ForwardReturnsStore:
    """Small in-memory cache keyed by close matrix identity and request."""

    def __init__(self) -> None:
        self._cache: dict[ForwardReturnsKey, Mapping[int, MatrixPanel]] = {}

    def get_or_compute(
        self,
        close: MatrixPanel,
        periods: Sequence[int],
        log_ret: bool = False,
        close_key: str | None = None,
        expected_step_ns: int | None = None,
    ) -> Mapping[int, MatrixPanel]:
        normalized_periods = _normalize_periods(periods)
        content_key = _matrix_content_key(close)
        normalized_expected_step_ns = _normalize_expected_step_ns(
            expected_step_ns,
            close.normalized_ts().astype(np.int64),
        )
        key = ForwardReturnsKey(
            close_key or "",
            content_key,
            normalized_periods,
            bool(log_ret),
            normalized_expected_step_ns,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        computed = {
            int(period): _readonly_panel(compute_forward_returns_matrix(
                close,
                int(period),
                log_ret=log_ret,
                expected_step_ns=normalized_expected_step_ns,
            ))
            for period in normalized_periods
        }
        result = MappingProxyType(computed)
        self._cache[key] = result
        return result


def _readonly_panel(panel: MatrixPanel) -> MatrixPanel:
    panel.ts.setflags(write=False)
    panel.values.setflags(write=False)
    return panel


def _normalize_periods(periods: Sequence[int]) -> tuple[int, ...]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in periods:
        period = int(raw)
        if period <= 0:
            raise ValueError(f"period must be > 0, got {raw!r}")
        if period not in seen:
            seen.add(period)
            normalized.append(period)
    return tuple(sorted(normalized))


def _matrix_content_key(panel: MatrixPanel) -> str:
    panel.validate()
    h = hashlib.sha256()
    ts_ns = panel.normalized_ts().astype(np.int64)
    values = np.ascontiguousarray(panel.values.astype(np.float64, copy=False))
    if np.isnan(values).any():
        values = values.copy()
        values[np.isnan(values)] = np.nan
    h.update(ts_ns.tobytes())
    h.update("\0".join(panel.symbols).encode("utf-8"))
    h.update(str(values.shape).encode("ascii"))
    h.update(values.tobytes())
    return h.hexdigest()


def _infer_step_ns(ts_ns: np.ndarray) -> int | None:
    if len(ts_ns) < 2:
        return None
    diffs = np.diff(ts_ns)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return None
    first = int(positive[0])
    if not np.all(positive == first):
        raise ValueError("expected_step_ns is required for irregular timestamps")
    return first


def _normalize_expected_step_ns(expected_step_ns: int | None, ts_ns: np.ndarray) -> int | None:
    if expected_step_ns is None:
        return _infer_step_ns(ts_ns)
    step = int(expected_step_ns)
    if step <= 0:
        raise ValueError(f"expected_step_ns must be > 0, got {expected_step_ns!r}")
    return step


def compute_forward_returns_matrix(
    close: MatrixPanel,
    period: int,
    log_ret: bool = False,
    expected_step_ns: int | None = None,
) -> MatrixPanel:
    """Compute ``close[t + period] / close[t] - 1`` aligned to ``t``."""

    close.validate()
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period!r}")
    values = close.values.astype(np.float64, copy=False)
    ts_ns = close.normalized_ts().astype(np.int64)
    step_ns = _normalize_expected_step_ns(expected_step_ns, ts_ns)
    out = np.full(values.shape, np.nan, dtype=np.float64)
    if period < values.shape[0]:
        current = values[:-period]
        future = values[period:]
        finite = np.isfinite(current) & np.isfinite(future) & (current != 0)
        if step_ns is not None:
            horizon_ok = (ts_ns[period:] - ts_ns[:-period]) == int(step_ns) * period
            finite = finite & horizon_ok[:, None]
        if log_ret:
            finite = finite & (current > 0) & (future > 0)
            computed = np.full_like(current, np.nan, dtype=np.float64)
            computed[finite] = np.log(future[finite] / current[finite])
        else:
            computed = np.full_like(current, np.nan, dtype=np.float64)
            computed[finite] = (future[finite] / current[finite]) - 1.0
        out[:-period] = computed
    return MatrixPanel(ts=close.normalized_ts().copy(), symbols=close.symbols, values=out)
