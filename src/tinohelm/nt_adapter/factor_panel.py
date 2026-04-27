"""Live-path factor panel construction — NT cache → polars wide panels.

This module bridges the NT in-memory bar cache to the polars wide-table
:data:`tinohelm.factor.types.Panel` contract that built-in factor kernels
expect.  It exists so :class:`tinohelm.nt_adapter.signal_driven_strategy.
SignalDrivenStrategy` can run real factor kernels in live / sandbox /
backtest mode without rescanning the Parquet catalog or rerunning the
research-time :class:`tinohelm.factor.data_layer.DataLayer`.

Scope and limitations
---------------------
- **Supported inputs**: pure OHLCV-based factors — kernel parameters that
  alias to one of ``close``, ``open``, ``high``, ``low``, ``volume``.
- **Unsupported inputs**: factors whose ``input_specs`` reference
  ``funding_rate``, ``open_interest``, ``orderbook_imbalance``,
  ``trade_qty``, ``trade_side`` or any other non-OHLCV PIT data.  Those
  cannot be materialised from ``cache.bars`` alone and require either an
  Actor that writes the data into NT's cache or a custom factor wired to
  a different data source.  Such factors must be rejected at the
  :func:`tinohelm.api.routes.signal.export_run` boundary so a misconfigured
  signal does not silently produce zero target weights.

Design notes
------------
- Bars in :meth:`nautilus_trader.cache.cache.Cache.bars` are stored
  newest-first (``deque.appendleft``).  We therefore reverse the slice
  before constructing the polars frame so chronological order matches
  the research-time panel contract used by :mod:`tinohelm.factor.builtins`.
- The wide panel layout ``[ts, sym1, sym2, ...]`` mirrors
  :data:`tinohelm.factor.types.Panel` exactly — symbol columns are
  Float64, ``ts`` is ``Datetime("ns")``.
- ``ts`` axes across symbols are aligned by inner-joining their per-symbol
  series.  If a symbol has fewer than ``warmup_bars`` history, we return
  ``None`` so the strategy skips this rebalance window cleanly.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

import polars as pl

from tinohelm.factor.alias import resolve_alias
from tinohelm.factor.types import FactorSpec, Panel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public field set we know how to materialise from a Bar object
# ---------------------------------------------------------------------------

#: OHLCV fields readable directly off an NT :class:`Bar` instance.  Mapping
#: from canonical field name → Bar attribute name.
_BAR_OHLCV_ATTR: dict[str, str] = {
    "close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
}


def supported_bar_fields() -> frozenset[str]:
    """Return the set of canonical field names this module can satisfy.

    Used by :func:`tinohelm.api.routes.signal.export_run` to validate that
    a factor's input requirements are compatible with the live-path
    :class:`SignalDrivenStrategy` before exporting a portfolio.yaml.
    """
    return frozenset(_BAR_OHLCV_ATTR.keys())


def factor_uses_only_bar_fields(factor_spec: FactorSpec) -> bool:
    """Return ``True`` iff every input the factor needs is in :func:`supported_bar_fields`.

    Empty ``input_specs`` is treated as compatible (covers e.g. tests that
    skip the @factor decorator's automatic input detection).
    """
    if not factor_spec.input_specs:
        return True
    supported = supported_bar_fields()
    return all(spec.field_name in supported for spec in factor_spec.input_specs)


# ---------------------------------------------------------------------------
# Cache → wide panel
# ---------------------------------------------------------------------------


def _series_from_bars(
    bars: list,
    field_name: str,
    symbol: str,
) -> pl.DataFrame:
    """Build a 2-col ``[ts, <symbol>]`` frame from an NT ``bars`` list.

    ``bars`` is the value returned by ``self.cache.bars(bar_type)`` — newest
    bar at index ``[0]``, oldest at the tail.  We reverse the list to get
    chronological order before building the frame.
    """
    attr = _BAR_OHLCV_ATTR[field_name]

    # Reverse to chronological order (cache stores newest-first via appendleft).
    chrono = list(reversed(bars))
    ts_list: list[int] = []
    val_list: list[float] = []
    for bar in chrono:
        # ``bar.ts_init`` is ns int (close-aligned per project convention).
        ts_list.append(int(bar.ts_init))
        raw = getattr(bar, attr)
        # NT Bar prices/volume are Price/Quantity value objects with
        # ``as_double``; floats / ints fall through unchanged.
        as_double = getattr(raw, "as_double", None)
        val_list.append(float(as_double()) if callable(as_double) else float(raw))

    if not ts_list:
        return pl.DataFrame(
            {"ts": [], symbol: []},
            schema={"ts": pl.Datetime("ns"), symbol: pl.Float64},
        )

    return pl.DataFrame(
        {"ts": ts_list, symbol: val_list},
        schema={"ts": pl.Datetime("ns"), symbol: pl.Float64},
    )


def build_wide_panel(
    field_name: str,
    bars_by_symbol: Mapping[str, list],
    min_history: int,
) -> Panel | None:
    """Construct the wide panel ``[ts, sym1, sym2, ...]`` for one field.

    Parameters
    ----------
    field_name:
        Canonical OHLCV field (``"close"`` / ``"high"`` / ...). Must be in
        :func:`supported_bar_fields`.
    bars_by_symbol:
        Mapping ``symbol_short → list[Bar]`` (newest-first).  Typically
        obtained by iterating ``self._bar_types`` and calling
        ``self.cache.bars(bar_type)`` per entry.
    min_history:
        Minimum number of bars required per symbol.  When any symbol has
        fewer bars, the function returns ``None`` so the caller skips this
        rebalance window cleanly.

    Returns
    -------
    Panel | None
        The wide :data:`Panel` or ``None`` when at least one symbol has
        insufficient history.
    """
    if field_name not in _BAR_OHLCV_ATTR:
        raise KeyError(
            f"build_wide_panel: field {field_name!r} is not an OHLCV field "
            f"({sorted(_BAR_OHLCV_ATTR)}); use the research-time DataLayer instead."
        )

    if not bars_by_symbol:
        return None

    series_frames: list[pl.DataFrame] = []
    for symbol, bars in bars_by_symbol.items():
        if not bars or len(bars) < min_history:
            logger.debug(
                "build_wide_panel: symbol %s has %d bars < min_history %d; skipping cross-section",
                symbol,
                len(bars) if bars else 0,
                min_history,
            )
            return None
        series_frames.append(_series_from_bars(bars, field_name, symbol))

    # Inner-join all per-symbol frames on ``ts``.  Inner-join is correct here
    # because the BarSynchronizer guarantees we only get to this point once
    # all symbols have published a bar for the same ts; missing bars in the
    # mid-stream are dropped by the inner join (which is what we want — any
    # symbol with a hole at ts T can't contribute a valid factor row at T).
    panel = series_frames[0]
    for frame in series_frames[1:]:
        panel = panel.join(frame, on="ts", how="inner")

    if panel.is_empty():
        return None
    return panel.sort("ts")


# ---------------------------------------------------------------------------
# Top-level helper used by SignalDrivenStrategy
# ---------------------------------------------------------------------------


def compute_latest_factor_panel(
    factor_kernel: Any,
    factor_spec: FactorSpec,
    bars_by_symbol: Mapping[str, list],
    min_history: int,
    extra_kernel_params: dict[str, Any] | None = None,
) -> Panel | None:
    """Run a factor kernel against live NT cache data; return the latest panel.

    This is the production hook for
    :meth:`tinohelm.nt_adapter.signal_driven_strategy.SignalDrivenStrategy._compute_factor_panel`.

    Parameters
    ----------
    factor_kernel:
        The factor function (decorated with ``@factor``) — typically obtained
        via ``Registry().get_kernel(factor_name)``.
    factor_spec:
        The associated :class:`FactorSpec`.  Drives input field discovery.
    bars_by_symbol:
        ``{symbol_short: list[Bar]}`` — values come from
        ``self.cache.bars(bar_type)`` (newest-first lists).
    min_history:
        Minimum bars per symbol; matches ``effective_warmup`` in the
        strategy.  When any symbol falls short, returns ``None``.
    extra_kernel_params:
        Forwarded as the ``params`` kwarg to the kernel.  The factor's
        :class:`FactorSpec.params` defaults are merged underneath.

    Returns
    -------
    Panel | None
        The factor output panel (full history; the caller picks the last
        row).  ``None`` when history is short / the kernel has no input
        fields (defensive).

    Raises
    ------
    KeyError
        When :class:`FactorSpec` references a non-OHLCV field that this
        module cannot resolve from ``cache.bars`` alone.  Caller should
        have validated this with :func:`factor_uses_only_bar_fields`
        before constructing the strategy.
    """
    # Determine which OHLCV panels the kernel needs.
    if factor_spec.input_specs:
        field_names = [spec.field_name for spec in factor_spec.input_specs]
    else:
        # Conservative default — most kernels need close.  Should be rare.
        field_names = ["close"]

    factor_data: dict[str, Panel] = {}
    for field_name in field_names:
        canonical = resolve_alias(field_name)
        if canonical not in _BAR_OHLCV_ATTR:
            raise KeyError(
                f"compute_latest_factor_panel: factor {factor_spec.name!r} "
                f"requires field {canonical!r} which is not OHLCV — this "
                f"path cannot satisfy non-bar inputs.  Reject at export time "
                f"with factor_uses_only_bar_fields()."
            )
        panel = build_wide_panel(
            field_name=canonical,
            bars_by_symbol=bars_by_symbol,
            min_history=min_history,
        )
        if panel is None:
            return None
        factor_data[canonical] = panel

    # Merge per-call params on top of the spec defaults.  The kernel
    # signature accepts ``params=None`` and treats it as ``{}``.
    merged_params: dict[str, Any] = dict(factor_spec.params or {})
    if extra_kernel_params:
        merged_params.update(extra_kernel_params)

    # Builtin kernels accept positional Panel args in the order declared
    # in their signature, plus a final ``params`` keyword argument.  Use
    # the same calling convention as :func:`tinohelm.factor.engine.scheduler.
    # Scheduler._call_kernel` (kw-named per InputSpec.field_name).
    return factor_kernel(**factor_data, params=merged_params)
