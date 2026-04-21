"""Pure NT-free serializers for node actor event publishing & persistence.

SnapshotActor publishes JSON payloads to Redis PubSub; DbWriterActor writes SQL
bind parameters to PostgreSQL. Both actors extract the same fields from the
same NT objects (Position, OrderFilled, Bar). This module is the single source
of truth for that extraction so the two actors cannot drift.

The helpers duck-type the NT objects — they require only the attribute surface
listed in each docstring, which lets tests pass plain MagicMocks or dataclasses
and keeps this file import-clean of nautilus_trader.
"""
from __future__ import annotations

from typing import Any

from tinohelm.node.actors._utils import ts_ns_to_iso


# ---------------------------------------------------------------------------
# Position serialization
# ---------------------------------------------------------------------------

def position_db_fields(pos: Any, node_type: str) -> dict[str, Any]:
    """Return DB bind parameters for the `positions` table UPSERT.

    Duck-typed contract — ``pos`` needs: ``id``, ``strategy_id``,
    ``instrument_id``, ``side.name``, ``quantity``, ``signed_qty``,
    ``avg_px_open``, ``avg_px_close``, ``realized_pnl.as_double()``,
    ``realized_pnl.currency``, ``entry.name``, ``peak_qty``, ``ts_opened``,
    ``ts_closed``, ``duration_ns``, ``is_open``, ``event_count``.
    """
    realized = pos.realized_pnl
    realized_pnl = realized.as_double() if realized is not None else None
    currency = str(realized.currency) if realized is not None else None
    avg_close = float(pos.avg_px_close) if pos.avg_px_close else None
    ts_closed = pos.ts_closed
    duration_ns = pos.duration_ns
    return {
        "node_type": node_type,
        "position_id": str(pos.id),
        "strategy_id_tag": str(pos.strategy_id) if pos.strategy_id else "",
        "instrument_id": str(pos.instrument_id),
        "side": pos.side.name,
        "quantity": str(pos.quantity),
        "signed_qty": float(pos.signed_qty),
        "avg_px_open": float(pos.avg_px_open),
        "avg_px_close": avg_close,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": None,
        "currency": currency,
        "entry_side": pos.entry.name,
        "peak_qty": str(pos.peak_qty),
        "ts_opened": ts_ns_to_iso(pos.ts_opened),
        "ts_closed": ts_ns_to_iso(ts_closed) if ts_closed and ts_closed > 0 else None,
        "duration": str(duration_ns) if duration_ns is not None and duration_ns > 0 else None,
        "is_open": pos.is_open,
        "event_count": pos.event_count,
    }


def build_position_update(
    pos: Any, node_type: str, event_type: str, ts_event: int,
) -> dict[str, Any]:
    """Build a ``position.update`` Redis PubSub payload.

    Superset of :func:`position_db_fields`. Adds ``type``, ``event``, ``id``,
    ``strategy_id``, ``duration_ns`` (raw int), ``ts`` and replaces the DB
    ``realized_pnl`` (nullable float) with a non-null 0.0 default, matching the
    historical SnapshotActor JSON contract consumed by the frontend/TUI.
    """
    strategy_id_str = str(pos.strategy_id) if pos.strategy_id else ""
    realized = pos.realized_pnl
    realized_pnl = realized.as_double() if realized is not None else 0.0
    return {
        "type": "position.update",
        "event": event_type,
        "node_type": node_type,
        "id": 0,
        "position_id": str(pos.id),
        "strategy_id": strategy_id_str,
        "strategy_id_tag": strategy_id_str,
        "instrument_id": str(pos.instrument_id),
        "side": pos.side.name,
        "quantity": str(pos.quantity),
        "signed_qty": float(pos.signed_qty),
        "avg_px_open": float(pos.avg_px_open),
        "avg_px_close": float(pos.avg_px_close) if pos.avg_px_close else None,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": None,
        "currency": str(realized.currency) if realized is not None else None,
        "entry_side": pos.entry.name,
        "peak_qty": str(pos.peak_qty),
        "is_open": pos.is_open,
        "event_count": pos.event_count,
        "ts_opened": ts_ns_to_iso(pos.ts_opened),
        "ts_closed": ts_ns_to_iso(pos.ts_closed)
        if pos.ts_closed and pos.ts_closed > 0 else None,
        "duration": str(pos.duration_ns) if pos.duration_ns else None,
        "duration_ns": pos.duration_ns if pos.duration_ns else None,
        "ts": ts_ns_to_iso(ts_event),
    }


# ---------------------------------------------------------------------------
# Fill / OrderFilled serialization
# ---------------------------------------------------------------------------

def fill_db_fields(event: Any, node_type: str) -> dict[str, Any]:
    """Return DB bind parameters for the `fills` table INSERT ON CONFLICT.

    Duck-typed contract — ``event`` needs: ``trade_id``, ``position_id``,
    ``client_order_id``, ``venue_order_id``, ``strategy_id``, ``instrument_id``,
    ``order_side.name``, ``last_qty``, ``last_px``, ``commission.as_double()``,
    ``liquidity_side.name``, ``ts_event``.
    """
    commission = event.commission
    return {
        "node_type": node_type,
        "trade_id": str(event.trade_id),
        "position_id": str(event.position_id) if event.position_id else None,
        "client_order_id": str(event.client_order_id),
        "venue_order_id": str(event.venue_order_id) if event.venue_order_id else None,
        "strategy_id_tag": str(event.strategy_id) if event.strategy_id else None,
        "instrument_id": str(event.instrument_id),
        "order_side": event.order_side.name,
        "last_qty": str(event.last_qty),
        "last_px": str(event.last_px),
        "commission": str(commission.as_double()) if commission else None,
        "liquidity_side": str(event.liquidity_side.name) if event.liquidity_side else None,
        "ts_event": ts_ns_to_iso(event.ts_event),
    }


def build_fill_event(event: Any, node_type: str) -> dict[str, Any]:
    """Build a ``fill.new`` Redis PubSub payload.

    Near-superset of :func:`fill_db_fields` — re-keys ``ts_event`` and adds
    ``type``, ``id``, ``strategy_id``, ``ts``. The frontend reads ``strategy_id``
    (not ``_tag``) on this channel; both are provided for symmetry.
    """
    strategy_id_str = str(event.strategy_id) if event.strategy_id else None
    ts_iso = ts_ns_to_iso(event.ts_event)
    commission = event.commission
    return {
        "type": "fill.new",
        "id": 0,
        "node_type": node_type,
        "trade_id": str(event.trade_id),
        "position_id": str(event.position_id) if event.position_id else None,
        "client_order_id": str(event.client_order_id),
        "venue_order_id": str(event.venue_order_id) if event.venue_order_id else None,
        "strategy_id": strategy_id_str,
        "strategy_id_tag": strategy_id_str,
        "instrument_id": str(event.instrument_id),
        "order_side": event.order_side.name,
        "last_qty": str(event.last_qty),
        "last_px": str(event.last_px),
        "commission": str(commission.as_double()) if commission else None,
        "liquidity_side": str(event.liquidity_side.name) if event.liquidity_side else None,
        "ts_event": ts_iso,
        "ts": ts_iso,
    }


# ---------------------------------------------------------------------------
# Non-fill order lifecycle event serialization
# ---------------------------------------------------------------------------

def build_order_lifecycle_event(event: Any, kind: str) -> dict[str, Any]:
    """Build an order lifecycle event payload (accepted/rejected/canceled/expired).

    ``kind`` is one of ``order_accepted`` / ``order_rejected`` /
    ``order_canceled`` / ``order_expired``. When ``kind == "order_rejected"``,
    an extra ``reason`` field is included (from ``event.reason``).
    """
    payload: dict[str, Any] = {
        "event": kind,
        "order_id": str(event.client_order_id),
        "instrument_id": str(event.instrument_id),
        "ts": str(event.ts_event),
    }
    if kind == "order_rejected":
        payload["reason"] = str(event.reason)
    return payload


# ---------------------------------------------------------------------------
# Bar serialization
# ---------------------------------------------------------------------------

def build_bar_event(bar: Any) -> dict[str, Any]:
    """Build a bar PubSub payload.

    Duck-typed contract — ``bar`` needs: ``bar_type`` (stringifiable, with a
    ``.instrument_id`` attribute), ``open``, ``high``, ``low``, ``close``,
    ``volume``, ``ts_event``.
    """
    return {
        "event": "bar",
        "bar_type": str(bar.bar_type),
        "instrument_id": str(bar.bar_type.instrument_id),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "ts": str(bar.ts_event),
    }


# ---------------------------------------------------------------------------
# Strategy snapshot / risk metrics serialization
# ---------------------------------------------------------------------------

def build_strategy_signal_snapshot(
    snapshot: Any, node_type: str, fields: dict[str, Any],
) -> dict[str, Any]:
    """Build a ``signal.snapshot`` payload. ``fields`` is the parsed JSON dict."""
    return {
        "type": "signal.snapshot",
        "node_type": node_type,
        "strategy_id": snapshot.strategy_id,
        "instrument_id": snapshot.instrument_id,
        "fields": fields,
        "ts": ts_ns_to_iso(snapshot.ts_event),
    }


def tag_risk_metrics(data: dict[str, Any], node_type: str) -> dict[str, Any]:
    """Mutate + return ``data`` with ``type`` and ``node_type`` set.

    Returns the same dict for chaining. Caller owns the dict (no defensive copy).
    """
    data["type"] = "risk.metrics"
    data["node_type"] = node_type
    return data


# ---------------------------------------------------------------------------
# Equity snapshot serialization
# ---------------------------------------------------------------------------

def build_equity_snapshot(
    node_type: str,
    equity: float,
    balance: float,
    unrealized: float,
    ts_iso: str,
) -> dict[str, Any]:
    """Build an ``equity.snapshot`` payload. All numeric values rounded to 2dp."""
    return {
        "type": "equity.snapshot",
        "node_type": node_type,
        "equity": round(equity, 2),
        "balance": round(balance, 2),
        "unrealized_pnl": round(unrealized, 2),
        "ts": ts_iso,
    }
