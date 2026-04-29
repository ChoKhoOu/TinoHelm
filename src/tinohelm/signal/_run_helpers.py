"""Shared helpers for the signal run/export/worker pipeline.

This module consolidates the **universe resolution** + **bar-type template**
construction logic that previously lived only in the ``SignalDrivenStrategy``
config plumbing, so ``POST /api/signal/run`` can normalize a request into a
reproducible ``signal_runs.config`` snapshot at enqueue time.

The two concerns bundled here are:

1. **Universe resolution** — turn a ``universe_id`` or ``universe_ref``
   (plus an anchor timestamp) into a concrete list of symbol strings
   (TinoHelm format, e.g. ``"BTCUSDT-PERP"``) and the NT-format
   ``instrument_ids`` (with ``.BINANCE`` venue suffix appended).  This
   uses :class:`tinohelm.factor.universe.Universe.from_db_row` for PIT
   filtering, so symbols that were not tradeable at the anchor time are
   excluded with the same semantics as factor evaluation.

2. **Bar-type template construction** — convert the ``SignalSpec.rebalance_freq``
   string (e.g. ``"1H"`` / ``"1D"``) into an NT-compatible bar-type
   template (e.g. ``"{instrument_id}-1-HOUR-LAST-EXTERNAL"``) that
   :class:`SignalDrivenStrategy.on_start` can format per instrument and
   feed to :func:`nautilus_trader.model.data.BarType.from_str`.

Both helpers are pure Python (no DB / Redis / NT runtime deps) and are
deliberately import-light so the API route + worker + tests can exercise
them without spinning up the full factor engine.
"""
from __future__ import annotations

import re
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from tinohelm.strategy.loader_helpers import parse_interval

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Venue convention
# ---------------------------------------------------------------------------

#: Venue suffix used by every TinoHelm instrument_id.  Mirrors
#: :func:`tinohelm.strategy.loader_helpers.normalize_symbol` — the whole
#: project is single-venue (Binance Futures) so this is a constant rather
#: than a lookup.  If multi-venue support lands the lookup here + in
#: ``loader_helpers`` must be upgraded together.
_DEFAULT_VENUE_SUFFIX: str = ".BINANCE"
_REBALANCE_FREQ_RE = re.compile(r"^([1-9]\d*)([smhd])$")
_REBALANCE_UNITS_NS: dict[str, int] = {
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
    "d": 86_400_000_000_000,
}


def _nt_instrument_id(symbol: str) -> str:
    """Return the NT-format instrument id for a TinoHelm symbol.

    Idempotent — a symbol that already carries ``.BINANCE`` is returned
    unchanged.  Empty / whitespace symbols are rejected with a clear
    ``ValueError`` so universe rows with bad data fail fast at the
    resolution boundary rather than producing a broken
    ``SignalDrivenStrategyConfig``.
    """
    stripped = symbol.strip()
    if not stripped:
        raise ValueError(f"Empty symbol in universe: {symbol!r}")
    if stripped.endswith(_DEFAULT_VENUE_SUFFIX):
        return stripped
    return f"{stripped}{_DEFAULT_VENUE_SUFFIX}"


# ---------------------------------------------------------------------------
# Universe resolution — DB → (pit_symbols, instrument_ids)
# ---------------------------------------------------------------------------

async def resolve_universe_to_instrument_ids(
    *,
    universe_id: int | None,
    universe_ref: str | None,
    anchor_ts: datetime | None,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
    db: "AsyncSession",
) -> tuple[int, str, list[str], list[str], dict]:
    """Resolve a universe reference to concrete PIT symbols + instrument ids.

    Resolution order:

    1. ``universe_id`` (direct PK lookup) takes priority when supplied.
    2. Otherwise fall back to ``universe_ref`` (``universes.name`` column,
       which carries a ``UNIQUE`` constraint — see
       :class:`tinohelm.db.models.Universe`).
    3. If neither locates a row → :class:`ValueError` (the route layer
       translates this to HTTP 422).

    Once a row is found, :class:`tinohelm.factor.universe.Universe.from_db_row`
    rebuilds the PIT tracker.  Historical runs with a finite ``start_ts`` or
    ``end_ts`` persist the union of all symbols active at any point in that
    window, so the worker can load delisted-in-window names before downstream
    PIT masks null ineligible timestamps.  Runs without a window keep the
    current/live behavior and use ``get_symbols_at(anchor_ts)``.

    Parameters
    ----------
    universe_id:
        Preferred lookup key — ``universes.id`` (integer PK).
    universe_ref:
        Fallback lookup key — ``universes.name`` (UNIQUE).
    anchor_ts:
        PIT anchor timestamp.  ``None`` → ``datetime.utcnow()``.
    start_ts / end_ts:
        Optional historical evaluation window.  If either side is supplied,
        the returned symbol list is ``Universe.get_symbols_between(start, end)``
        rather than end-anchor constituents.
    db:
        Async SQLAlchemy session (caller owns commit / rollback).

    Returns
    -------
    (universe_id, universe_name, pit_symbols, instrument_ids, pit_rules_json)
        All five are derived atomically from the same DB row so callers
        can persist them into ``signal_runs.config`` + ``signal_runs.universe_id``
        without risking cross-field drift.  ``pit_rules_json`` preserves the
        original listing/delisting boundaries for the worker's historical
        :class:`DataLayer` PIT mask; ``pit_symbols`` is the data-loading set
        (historical active-window union when a window is supplied, otherwise
        the anchor-time live set).

    Raises
    ------
    ValueError
        Neither ``universe_id`` nor ``universe_ref`` is supplied; or the
        lookup yields no row; or the PIT filter returns an empty symbol
        list (an enqueued run against an empty universe would never
        trade).
    """
    if universe_id is None and not universe_ref:
        raise ValueError(
            "Cannot resolve universe: neither universe_id nor universe_ref "
            "was supplied.  The signal run requires a concrete symbol list "
            "to populate instrument_ids at enqueue time."
        )

    # Local import — keeps this module import-light for tests that don't
    # touch the DB layer.
    from sqlalchemy import select
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    row = None
    if universe_id is not None:
        row = (
            await db.execute(
                select(UniverseORM).where(UniverseORM.id == universe_id)
            )
        ).scalar_one_or_none()

    if row is None and universe_ref:
        row = (
            await db.execute(
                select(UniverseORM).where(UniverseORM.name == universe_ref)
            )
        ).scalar_one_or_none()

    if row is None:
        raise ValueError(
            f"Cannot resolve universe: lookup by "
            f"universe_id={universe_id!r} / universe_ref={universe_ref!r} "
            f"returned no row.  Sync the CSV via "
            f"POST /api/factor/universes/sync first."
        )

    if anchor_ts is None:
        # Use naive UTC to match the project-wide datetime convention
        # (see CLAUDE.md "DB DateTime columns are TIMESTAMP WITHOUT TIME ZONE").
        anchor_ts = datetime.now(UTC).replace(tzinfo=None)

    universe = Universe.from_db_row(row)
    if start_ts is not None or end_ts is not None:
        pit_symbols = universe.get_symbols_between(start_ts, end_ts or anchor_ts)
    else:
        pit_symbols = universe.get_symbols_at(anchor_ts)
    if not pit_symbols:
        scope = (
            f"window={start_ts.isoformat() if start_ts else None}.."
            f"{(end_ts or anchor_ts).isoformat() if (end_ts or anchor_ts) else None}"
            if start_ts is not None or end_ts is not None
            else f"anchor_ts={anchor_ts.isoformat()}"
        )
        raise ValueError(
            f"Universe {row.name!r} (id={row.id}) resolved to an empty "
            f"symbol list at {scope}.  "
            "All members are pre-listing, post-delisting, or inside the "
            "7-day new-coin isolation window — refusing to enqueue a run "
            "that would never trade."
        )

    instrument_ids = [_nt_instrument_id(sym) for sym in pit_symbols]
    return (
        row.id,
        row.name,
        list(pit_symbols),
        instrument_ids,
        dict(row.pit_rules_json or {}),
    )


# ---------------------------------------------------------------------------
# Bar-type template — rebalance_freq → NT interval segment
# ---------------------------------------------------------------------------

def normalize_rebalance_freq(rebalance_freq: str) -> str:
    """Return a canonical short-form rebalance frequency or raise.

    The strategy loader's :func:`parse_interval` intentionally falls back to
    ``"1-MINUTE"`` for invalid strings to keep legacy portfolio loading
    permissive.  Signal run/export/worker paths need a stricter contract: bar
    cadence and rebalance gate must be derived from the exact same validated
    frequency, otherwise live configs can subscribe to one cadence but gate on
    another.  Accepted values are ``<positive integer><s|m|h|d>`` and are
    case-insensitive (``"1H"`` → ``"1h"``).
    """
    freq = (rebalance_freq or "").strip().lower()
    if not _REBALANCE_FREQ_RE.match(freq):
        raise ValueError(
            "invalid rebalance_freq: expected '<positive integer><s|m|h|d>', "
            f"got {rebalance_freq!r}"
        )
    return freq


def rebalance_freq_to_ns(rebalance_freq: str) -> int:
    """Convert a validated short-form cadence (``1h``/``30m``/...) to ns."""
    freq = normalize_rebalance_freq(rebalance_freq)
    match = _REBALANCE_FREQ_RE.match(freq)
    assert match is not None  # guaranteed by normalize_rebalance_freq
    return int(match.group(1)) * _REBALANCE_UNITS_NS[match.group(2)]


def build_bar_type_template(rebalance_freq: str) -> str:
    """Build the ``{instrument_id}``-placeholder bar-type template.

    The rebalance cadence on the research side uses the short-form
    convention (``"1H"`` / ``"4h"`` / ``"1D"``) which is case-insensitive
    per :mod:`tinohelm.strategy.loader_helpers` (``parse_interval`` lowercases
    before matching).  NT's :meth:`BarType.from_str` wants the NT-formatted
    segment (``"1-HOUR"`` / ``"1-DAY"``).

    We delegate the parsing to the shared helper so CLI loader, backtest
    runner, and signal framework all use the same INTERVAL_MAP.  This is
    deliberately a *template* (with ``{instrument_id}`` placeholder) rather
    than a fully-resolved bar-type string because the signal driven
    strategy formats it per-instrument at :meth:`on_start` time (see
    ``SignalDrivenStrategy._bar_type_template``).

    Examples
    --------
    >>> build_bar_type_template("1H")
    '{instrument_id}-1-HOUR-LAST-EXTERNAL'
    >>> build_bar_type_template("4h")
    '{instrument_id}-4-HOUR-LAST-EXTERNAL'
    >>> build_bar_type_template("1D")
    '{instrument_id}-1-DAY-LAST-EXTERNAL'

    Parameters
    ----------
    rebalance_freq:
        Short-form cadence string.  Empty / unparseable values raise
        ``ValueError``.  Signal configs must not inherit
        :func:`parse_interval`'s legacy ``1-MINUTE`` fallback because export
        also derives ``rebalance_freq_ns`` from this same value.

    Returns
    -------
    str
        Bar-type template with ``{instrument_id}`` placeholder — ready to
        be persisted into ``signal_runs.config["bar_type_template"]`` and
        consumed by :class:`SignalDrivenStrategy`.
    """
    # ``parse_interval`` lowercases its argument internally via ``.lower()``
    # on the dynamic-regex path, so "1H" and "1h" both resolve to
    # ``"1-HOUR"`` — matching the user-facing contract in signal.md §2.1.
    interval_part = parse_interval(normalize_rebalance_freq(rebalance_freq))
    return f"{{instrument_id}}-{interval_part}-LAST-EXTERNAL"


__all__ = [
    "resolve_universe_to_instrument_ids",
    "normalize_rebalance_freq",
    "rebalance_freq_to_ns",
    "build_bar_type_template",
]
