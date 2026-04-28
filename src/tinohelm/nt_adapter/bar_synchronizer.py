"""Cross-section bar synchroniser — gate on all expected symbols arriving.

Background
----------
NT delivers ``on_bar`` events one symbol at a time.  Cross-sectional signal
kernels (top-K long/short, quantile, z-score-clip…) need *all* symbols of
the universe at the same timestamp before they can compute weights.

This module provides a deterministic buffering layer that holds per-symbol
bars in a ``{ts_ns: {symbol: Bar}}`` map.  When the inner dict for a given
``ts_ns`` becomes a superset of the expected symbol set, we pop it and
fire ``on_complete(ts_ns, bars)``.

Late-arrival policy
-------------------
A real exchange may drop or delay a single symbol's bar.  The
synchroniser uses a ``max_wait_bars`` threshold counted in *number of
later timestamps that have already arrived for any symbol*.  Once a
buffered timestamp has been overtaken by ``max_wait_bars`` later
timestamps, the partial entry is evicted with a ``warning`` log line and
the cross-section for that ``ts_ns`` is *skipped* (we do **not** fire
``on_complete`` with partial bars — partial cross-section breaks the
single-row kernel contract for this project).  This is the conservative
choice over partial-firing because an asymmetric universe size at one
timestamp can silently distort top-K / quantile splits.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BarSynchronizerConfig:
    """Static configuration for :class:`BarSynchronizer`.

    Attributes
    ----------
    expected_symbols:
        The full cross-section the strategy expects to receive at every
        timestamp.  Symbol strings are matched against
        ``str(bar.bar_type.instrument_id.symbol)`` (e.g. ``"BTCUSDT-PERP"``).
    max_wait_bars:
        Number of *later* timestamps that may overtake a buffered slot
        before that slot is evicted with a warning.  Default 5.
    """

    expected_symbols: tuple[str, ...] | list[str]
    max_wait_bars: int = 5


class BarSynchronizer:
    """Hold per-symbol bars until the full cross-section arrives.

    Parameters
    ----------
    config:
        :class:`BarSynchronizerConfig` describing the expected symbol
        universe and eviction policy.
    on_complete:
        Callback invoked as ``on_complete(ts_ns, {symbol: Bar})`` once all
        ``expected_symbols`` have arrived for the same ``ts_ns``.
    """

    def __init__(
        self,
        config: BarSynchronizerConfig,
        on_complete: Callable[[int, dict[str, "object"]], None],  # noqa: F821 — Bar typed at NT layer
    ) -> None:
        self.config = config
        self._expected: set[str] = set(config.expected_symbols)
        if not self._expected:
            raise ValueError("BarSynchronizerConfig.expected_symbols is empty")
        if config.max_wait_bars < 0:
            raise ValueError(
                f"max_wait_bars must be >= 0, got {config.max_wait_bars!r}"
            )
        self._on_complete = on_complete
        # ts_ns → {symbol: Bar} (insertion-order preserved by dict, useful for tests)
        self._buffer: dict[int, dict[str, object]] = defaultdict(dict)
        # Distinct timestamps observed in the stream, including slots that
        # completed and were popped.  Stale eviction must count completed
        # later cross-sections too; otherwise an old partial slot can survive
        # forever during a healthy stream of complete later bars.
        self._seen_timestamps: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_bar(self, bar: object) -> None:
        """Ingest a single NT :class:`Bar`-like object.

        ``bar`` must expose ``ts_init: int`` (nanosecond timestamp) and
        ``bar_type.instrument_id.symbol`` (or a ``.value`` thereof).
        """
        ts_ns = self._extract_ts_ns(bar)
        symbol = self._extract_symbol(bar)
        self._seen_timestamps.add(ts_ns)

        slot = self._buffer[ts_ns]
        if symbol in slot:
            # Duplicate bar for the same (ts, symbol) — keep the latest;
            # NT can occasionally deliver corrected bars, last-writer-wins
            # mirrors the production cache semantic.
            logger.debug(
                "BarSynchronizer: duplicate bar for ts=%d symbol=%s — replacing",
                ts_ns,
                symbol,
            )
        slot[symbol] = bar

        # Fire when complete.
        if self._expected.issubset(slot.keys()):
            completed = dict(self._buffer.pop(ts_ns))
            self._on_complete(ts_ns, completed)

        # Evict overtaken slots regardless of whether this bar completed
        # its own slot — keeps the buffer bounded.
        self._evict_stale()

    def pending_timestamps(self) -> list[int]:
        """Return sorted list of buffered (incomplete) timestamps.

        Exposed for diagnostics + tests — not used in the hot path.
        """
        return sorted(self._buffer.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_ts_ns(bar: object) -> int:
        """Pull ``ts_init`` (closing-time-aligned ns) off the bar.

        We use ``ts_init`` because the project convention (see
        ``CLAUDE.md`` "Bar `ts_init` must be the closing time") is that
        ``ts_init`` is the canonical close-aligned timestamp used by the
        backtest engine for cross-section ordering.
        """
        try:
            return int(bar.ts_init)  # type: ignore[attr-defined]
        except AttributeError as exc:  # pragma: no cover — defensive
            raise TypeError(
                f"BarSynchronizer.on_bar expected an object with .ts_init; got {type(bar)!r}"
            ) from exc

    @staticmethod
    def _extract_symbol(bar: object) -> str:
        """Extract the canonical symbol string from ``bar.bar_type.instrument_id``.

        Supports both stub objects (with simple string attributes) and
        real NT objects (where ``instrument_id.symbol`` may itself be a
        :class:`Symbol` value object whose ``.value`` is the string).
        """
        instrument_id = bar.bar_type.instrument_id  # type: ignore[attr-defined]
        symbol_attr = instrument_id.symbol  # type: ignore[attr-defined]
        # Real NT: Symbol value object; stub: plain str.
        return str(getattr(symbol_attr, "value", symbol_attr))

    def _evict_stale(self) -> None:
        """Drop slots overtaken by ``max_wait_bars`` later timestamps.

        We compare ts ordering on the buffer itself (no need to know bar
        durations).  A timestamp ``t`` is "stale" if there are at least
        ``max_wait_bars + 1`` distinct *later* timestamps already
        buffered (the +1 is the latest one that just arrived).
        """
        if not self._buffer:
            self._seen_timestamps.clear()
            return
        # max_wait_bars=0 means "no tolerance" — evict any buffered ts that
        # has been surpassed by even one later ts.
        threshold = self.config.max_wait_bars
        sorted_buffer_ts = sorted(self._buffer.keys())
        sorted_seen_ts = sorted(self._seen_timestamps)
        # For each pending ts, count how many later timestamps have been seen,
        # including complete slots already popped from ``_buffer``.  If
        # > threshold → evict.  ``threshold`` counts later timestamps allowed
        # before eviction.
        for ts in sorted_buffer_ts:
            n_later = sum(1 for seen_ts in sorted_seen_ts if seen_ts > ts)
            if n_later > threshold:
                missing = self._expected - set(self._buffer[ts].keys())
                logger.warning(
                    "BarSynchronizer evicting ts=%d after %d later timestamps; "
                    "missing symbols=%s — cross-section skipped",
                    ts,
                    n_later,
                    sorted(missing),
                )
                self._buffer.pop(ts, None)
        self._prune_seen_timestamps()

    def _prune_seen_timestamps(self) -> None:
        """Keep seen-ts accounting bounded by the oldest pending slot."""
        if not self._buffer:
            self._seen_timestamps.clear()
            return
        oldest_pending = min(self._buffer.keys())
        self._seen_timestamps = {
            ts for ts in self._seen_timestamps if ts >= oldest_pending
        }
