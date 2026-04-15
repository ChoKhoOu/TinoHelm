"""DbWriterActor — persists terminal trading events to PostgreSQL.

Subscribes to OrderFilled and PositionClosed events via msgbus,
buffers them, and flushes to DB via queue_for_executor (non-blocking).
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.events import (
    OrderFilled,
    PositionClosed,
)

from tinohelm.node.actors._utils import ts_ns_to_iso


class DbWriterActorConfig(ActorConfig):
    db_url: str = ""
    node_type: str = "sandbox"
    flush_interval_secs: int = 1  # Trade-off: non-blocking vs up to 1s durability window on crash


class DbWriterActor(Actor):
    """Persists OrderFilled and PositionClosed events to PostgreSQL.

    Uses queue_for_executor for non-blocking DB writes on the NT event loop.
    """

    def __init__(self, config: DbWriterActorConfig) -> None:
        super().__init__(config)
        self._db_url = config.db_url or os.environ.get("TINO_DATABASE__URL", "")
        self._node_type = config.node_type
        self._flush_interval = config.flush_interval_secs
        self._db_engine: Any = None
        self._buffer: list[dict] = []

    def on_start(self) -> None:
        if self._db_url:
            try:
                from tinohelm.db.sync_engine import get_sync_engine
                self._db_engine = get_sync_engine(self._db_url)
                self.log.info("DbWriterActor: DB persistence enabled")
            except Exception as e:
                self.log.error(f"DbWriterActor: Failed to init DB engine: {e}")
                self._db_engine = None

        # Subscribe to terminal events only
        self.msgbus.subscribe("events.order.*", self._on_order_event)
        self.msgbus.subscribe("events.position.*", self._on_position_event)

        # Flush timer
        self.clock.set_timer(
            name="db_flush",
            interval=timedelta(seconds=self._flush_interval),
        )

        self.log.info("DbWriterActor started")

    def on_event(self, event: Event) -> None:
        if isinstance(event, TimeEvent) and event.name == "db_flush":
            self._flush()

    def on_stop(self) -> None:
        # Flush remaining buffer synchronously on stop.
        # This blocks the event loop briefly, but is acceptable during shutdown
        # to avoid losing buffered events.
        if self._buffer and self._db_engine:
            self._write_batch(list(self._buffer))
            self._buffer.clear()

    def _on_order_event(self, event: Event) -> None:
        if not isinstance(event, OrderFilled):
            return
        if not self._db_engine:
            return
        self._buffer.append({"type": "fill", "event": event})

    def _on_position_event(self, event: Event) -> None:
        # Persist ALL position events (opened/changed/closed) using UPSERT,
        # maintaining a live snapshot of positions in the DB.
        from nautilus_trader.model.events import PositionOpened, PositionChanged
        if not isinstance(event, (PositionOpened, PositionChanged, PositionClosed)):
            return
        if not self._db_engine:
            return
        self._buffer.append({"type": "position", "event": event})

    def _flush(self) -> None:
        if not self._buffer or not self._db_engine:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        self.queue_for_executor(self._write_batch, (batch,))

    def _write_batch(self, batch: list[dict]) -> None:
        """Execute in executor thread — never blocks the NT event loop."""
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        try:
            with Session(self._db_engine) as session:
                for item in batch:
                    if item["type"] == "fill":
                        self._persist_fill(session, item["event"])
                    elif item["type"] == "position":
                        self._persist_position(session, item["event"])
                session.commit()
        except Exception as e:
            self.log.error(f"DbWriterActor batch write error: {e}")

    def _persist_fill(self, session: Any, event: OrderFilled) -> None:
        from sqlalchemy import text
        stmt = text("""
            INSERT INTO fills (
                node_type, trade_id, position_id, client_order_id,
                venue_order_id, strategy_id_tag, instrument_id, order_side,
                last_qty, last_px, commission, liquidity_side, ts_event
            ) VALUES (
                :node_type, :trade_id, :position_id, :client_order_id,
                :venue_order_id, :strategy_id_tag, :instrument_id, :order_side,
                :last_qty, :last_px, :commission, :liquidity_side, :ts_event
            )
            ON CONFLICT (trade_id) DO NOTHING
        """)
        session.execute(stmt, {
            "node_type": self._node_type,  # will be set properly via config
            "trade_id": str(event.trade_id),
            "position_id": str(event.position_id) if event.position_id else None,
            "client_order_id": str(event.client_order_id),
            "venue_order_id": str(event.venue_order_id) if event.venue_order_id else None,
            "strategy_id_tag": str(event.strategy_id) if event.strategy_id else None,
            "instrument_id": str(event.instrument_id),
            "order_side": event.order_side.name,
            "last_qty": str(event.last_qty),
            "last_px": str(event.last_px),
            "commission": str(event.commission.as_double()) if event.commission else None,
            "liquidity_side": str(event.liquidity_side.name) if event.liquidity_side else None,
            "ts_event": ts_ns_to_iso(event.ts_event),
        })

    def _persist_position(self, session: Any, event: PositionClosed) -> None:
        from sqlalchemy import text
        pos = event.position
        stmt = text("""
            INSERT INTO positions (
                node_type, position_id, strategy_id_tag, instrument_id,
                side, quantity, signed_qty, avg_px_open, avg_px_close,
                realized_pnl, unrealized_pnl, currency, entry_side,
                peak_qty, ts_opened, ts_closed, duration,
                is_open, event_count
            ) VALUES (
                :node_type, :position_id, :strategy_id_tag, :instrument_id,
                :side, :quantity, :signed_qty, :avg_px_open, :avg_px_close,
                :realized_pnl, :unrealized_pnl, :currency, :entry_side,
                :peak_qty, :ts_opened, :ts_closed, :duration,
                :is_open, :event_count
            )
            ON CONFLICT (position_id) DO UPDATE SET
                side = EXCLUDED.side,
                quantity = EXCLUDED.quantity,
                signed_qty = EXCLUDED.signed_qty,
                avg_px_close = EXCLUDED.avg_px_close,
                realized_pnl = EXCLUDED.realized_pnl,
                currency = EXCLUDED.currency,
                peak_qty = EXCLUDED.peak_qty,
                ts_closed = EXCLUDED.ts_closed,
                duration = EXCLUDED.duration,
                is_open = EXCLUDED.is_open,
                event_count = EXCLUDED.event_count,
                updated_at = NOW()
        """)
        session.execute(stmt, {
            "node_type": self._node_type,
            "position_id": str(pos.id),
            "strategy_id_tag": str(pos.strategy_id) if pos.strategy_id else "",
            "instrument_id": str(pos.instrument_id),
            "side": pos.side.name,
            "quantity": str(pos.quantity),
            "signed_qty": float(pos.signed_qty),
            "avg_px_open": float(pos.avg_px_open),
            "avg_px_close": float(pos.avg_px_close) if pos.avg_px_close else None,
            "realized_pnl": pos.realized_pnl.as_double() if pos.realized_pnl else None,
            "unrealized_pnl": None,
            "currency": str(pos.realized_pnl.currency) if pos.realized_pnl else None,
            "entry_side": pos.entry.name,
            "peak_qty": str(pos.peak_qty),
            "ts_opened": ts_ns_to_iso(pos.ts_opened),
            "ts_closed": ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None,
            "duration": str(pos.duration_ns) if pos.duration_ns is not None and pos.duration_ns > 0 else None,
            "is_open": pos.is_open,
            "event_count": pos.event_count,
        })
