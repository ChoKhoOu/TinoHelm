"""Pure funding-cost math for perpetual futures backtests — NT-free.

Everything in this module is pure Python: no NautilusTrader imports, no I/O,
no global state. It exists so the funding-cost logic used by
``_FundingCostTracker`` can be unit-tested without spinning up a Cython Actor.

The cost formula is the single source of truth for how funding fees settle at
each 8-hour (or venue-configured) funding timestamp:

    cost = notional * rate            if position side is LONG
    cost = -(notional * rate)         if position side is SHORT
    notional = quantity * mark_price

Longs with a positive rate **pay**; shorts with a positive rate **receive**.
Sign symmetry is enforced by ``compute_funding_cost``.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol

SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"


class _PositionLike(Protocol):
    """Duck-type for the subset of NT Position attributes the tracker reads.

    Captured as a Protocol (rather than importing NT) so tests can stub with
    any object exposing these three attributes.
    """

    instrument_id: Any  # str-able
    side: Any           # exposes .name → "LONG" | "SHORT"
    quantity: Any       # float-able

# Precision used by ``get_results`` — mirrors the previous inline rounding so
# the serialized ``funding_records[i]["cost"]`` shape stays byte-compatible.
RECORD_COST_PRECISION = 6
SUMMARY_TOTAL_PRECISION = 4
SUMMARY_PER_SYMBOL_PRECISION = 4


def compute_funding_cost(
    *,
    side: str,
    quantity: float,
    mark_price: float,
    rate: float,
) -> float:
    """Return the funding cost (USDT) for one position at one funding tick.

    Positive return value = cost paid by the trader; negative = credit received.

    Raises ``ValueError`` on an unknown side. Passing ``"FLAT"`` or any other
    string previously produced the SHORT-branch sign silently — now it fails
    loudly, because production code only ever passes LONG/SHORT (NT's
    ``positions_open()`` never yields a FLAT position).
    """
    if side == SIDE_LONG:
        sign = 1.0
    elif side == SIDE_SHORT:
        sign = -1.0
    else:
        raise ValueError(
            f"compute_funding_cost: unknown side {side!r} "
            f"(expected {SIDE_LONG!r} or {SIDE_SHORT!r})"
        )
    return sign * float(quantity) * float(mark_price) * float(rate)


def build_funding_record(
    *,
    timestamp_iso: str,
    symbol: str,
    side: str,
    quantity: float,
    mark_price: float,
    rate: float,
    cost: float,
) -> dict[str, Any]:
    """Build the per-position record appended to ``funding_records``.

    Keyword-only so new fields can be added without breaking positional callers
    silently. Shape is intentionally flat (no nesting) because downstream JSON
    serializers in the API / tearsheet consume it directly.
    """
    return {
        "timestamp": timestamp_iso,
        "symbol": symbol,
        "side": side,
        "quantity": float(quantity),
        "mark_price": float(mark_price),
        "funding_rate": float(rate),
        "cost": round(float(cost), RECORD_COST_PRECISION),
    }


def advance_due_events(
    events: list[dict[str, Any]],
    *,
    current_ns: int,
    next_idx: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(events_due_now, new_cursor)`` for the current bar.

    Events are expected to be ordered by ``timestamp_ns`` ascending. All events
    with ``timestamp_ns <= current_ns`` starting from ``next_idx`` are consumed
    and returned; the new cursor points at the first not-yet-due event (or
    ``len(events)`` if all are exhausted).

    Idempotent: calling twice with the same ``next_idx`` returns the same
    events. The caller is responsible for persisting the new cursor.
    """
    n = len(events)
    if next_idx >= n:
        return [], next_idx
    i = next_idx
    while i < n and events[i]["timestamp_ns"] <= current_ns:
        i += 1
    if i == next_idx:
        return [], next_idx
    return events[next_idx:i], i


def apply_funding_event(
    event: dict[str, Any],
    positions: Iterable[_PositionLike],
    *,
    total_cost: float,
    per_symbol_cost: dict[str, float],
    records: list[dict[str, Any]],
) -> tuple[float, dict[str, float], list[dict[str, Any]]]:
    """Apply one funding event to the currently-open positions.

    For every position whose ``str(instrument_id)`` matches ``event["symbol"]``,
    compute the funding cost and fold it into the running totals. Returns
    the (possibly updated) ``(total_cost, per_symbol_cost, records)`` tuple.

    This is the single logic path that used to live inline in
    ``_FundingCostTracker._apply_funding``. Pulling it out lets the tracker's
    NT-Cython Actor surface stay thin enough to skip in unit tests.

    ``per_symbol_cost`` and ``records`` are mutated in place for efficiency
    (matching the previous tracker behaviour) AND returned for convenience /
    functional-style call sites. Callers can ignore the return value if they
    prefer the in-place style.
    """
    symbol = event["symbol"]
    rate = event["rate"]
    mark_price = event["mark_price"]
    timestamp_iso = event["timestamp_iso"]

    for pos in positions:
        pos_symbol = str(pos.instrument_id)
        if pos_symbol != symbol:
            continue
        qty = float(pos.quantity)
        side = pos.side.name

        cost = compute_funding_cost(
            side=side,
            quantity=qty,
            mark_price=mark_price,
            rate=rate,
        )
        total_cost += cost
        per_symbol_cost[pos_symbol] = per_symbol_cost.get(pos_symbol, 0.0) + cost
        records.append(
            build_funding_record(
                timestamp_iso=timestamp_iso,
                symbol=pos_symbol,
                side=side,
                quantity=qty,
                mark_price=mark_price,
                rate=rate,
                cost=cost,
            )
        )

    return total_cost, per_symbol_cost, records


def summarize_funding(
    *,
    total_funding_cost: float,
    per_symbol_cost: dict[str, float],
    funding_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Shape the result dict exposed by ``_FundingCostTracker.get_results()``.

    Rounding precisions match the historical on-disk/JSON shape:
      - ``total_funding_cost``: 4 dp
      - ``per_symbol_funding[*]``: 4 dp
      - ``funding_records`` passed through unchanged (per-row ``cost`` was
        already rounded at build time by :func:`build_funding_record`).
    """
    records = list(funding_records)
    return {
        "total_funding_cost": round(float(total_funding_cost), SUMMARY_TOTAL_PRECISION),
        "funding_event_count": len(records),
        "per_symbol_funding": {
            k: round(float(v), SUMMARY_PER_SYMBOL_PRECISION)
            for k, v in per_symbol_cost.items()
        },
        "funding_records": records,
    }
