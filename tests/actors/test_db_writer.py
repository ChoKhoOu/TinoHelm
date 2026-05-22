"""Tests for DbWriterActor SQL bind-param wiring and batch/buffer semantics.

Since NT Actor is a Cython extension class, we cannot instantiate DbWriterActor
directly. Instead we lift the buffer / flush / persist methods off the class
and drive them with synthetic events, verifying:

1. Buffer only accepts the right NT event types (gating in ``_on_*_event``).
2. SQL bind params come from :mod:`tinohelm.node.actors.serialize` (so the
   serialization layer is the sole source of truth — a bind-param rename on
   the actor cannot happen without failing this test).
3. ``_write_batch`` dispatches fills and positions to the right SQL.
4. The SQL statements themselves reference all the expected columns
   (catches drift between the SQL string and the bind-param dict).
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tinohelm.node.actors import db_writer_actor as dwa
from tinohelm.node.actors.db_writer_actor import (
    _INSERT_FILL_SQL,
    _UPSERT_POSITION_SQL,
)
from tinohelm.node.actors.serialize import fill_db_fields, position_db_fields


def _make_enum(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _make_money(value: float, currency: str = "USDT") -> MagicMock:
    m = MagicMock()
    m.as_double.return_value = value
    m.currency = currency
    return m


@pytest.fixture
def fill_event() -> MagicMock:
    ev = MagicMock()
    ev.trade_id = "T-42"
    ev.position_id = "P-1"
    ev.client_order_id = "O-1"
    ev.venue_order_id = "VO-1"
    ev.strategy_id = "S-01"
    ev.instrument_id = "BTCUSDT-PERP.BINANCE"
    ev.order_side = _make_enum("BUY")
    ev.last_qty = "0.05"
    ev.last_px = "50000"
    ev.commission = _make_money(0.25)
    ev.liquidity_side = _make_enum("TAKER")
    ev.ts_event = 1_700_000_000_000_000_000
    return ev


@pytest.fixture
def position_event() -> MagicMock:
    """A PositionChanged-like event: wraps `.position` per NT contract."""
    pos = MagicMock()
    pos.id = "P-1"
    pos.strategy_id = "S-01"
    pos.instrument_id = "BTCUSDT-PERP.BINANCE"
    pos.side = _make_enum("LONG")
    pos.quantity = "0.100"
    pos.signed_qty = 0.100
    pos.avg_px_open = 50000.0
    pos.avg_px_close = None
    pos.realized_pnl = _make_money(50.0, "USDT")
    pos.entry = _make_enum("BUY")
    pos.peak_qty = "0.100"
    pos.ts_opened = 1_700_000_000_000_000_000
    pos.ts_closed = 0
    pos.duration_ns = 0
    pos.is_open = True
    pos.event_count = 2

    ev = MagicMock()
    ev.position = pos
    return ev


# ---------------------------------------------------------------------------
# SQL statement structural checks
# ---------------------------------------------------------------------------

class TestSqlStatements:
    """Pin the SQL literal contents so a column rename in migrations can't
    silently diverge from the bind-param dict produced by ``serialize.py``.
    """

    def test_insert_fill_uses_on_conflict_do_nothing(self):
        assert "ON CONFLICT (trade_id) DO NOTHING" in _INSERT_FILL_SQL

    def test_insert_fill_table_name(self):
        assert re.search(r"INSERT\s+INTO\s+fills\s*\(", _INSERT_FILL_SQL)

    def test_insert_fill_has_all_13_columns(self, fill_event):
        """Every key produced by fill_db_fields must appear as a named bind."""
        params = fill_db_fields(fill_event, "sandbox")
        for key in params:
            assert f":{key}" in _INSERT_FILL_SQL, f"missing bind :{key} in _INSERT_FILL_SQL"

    def test_upsert_position_uses_on_conflict_update(self):
        assert "ON CONFLICT (position_id) DO UPDATE SET" in _UPSERT_POSITION_SQL
        assert "updated_at = NOW()" in _UPSERT_POSITION_SQL

    def test_upsert_position_table_name(self):
        assert re.search(r"INSERT\s+INTO\s+positions\s*\(", _UPSERT_POSITION_SQL)

    def test_upsert_position_has_all_19_columns(self, position_event):
        params = position_db_fields(position_event.position, "sandbox")
        for key in params:
            assert f":{key}" in _UPSERT_POSITION_SQL, f"missing bind :{key} in _UPSERT_POSITION_SQL"

    def test_upsert_position_does_not_update_immutable_fields(self):
        """ts_opened, entry_side, avg_px_open, instrument_id, strategy_id_tag
        must NOT appear in the DO UPDATE SET clause (they never change after
        the position is opened).
        """
        _, update_clause = _UPSERT_POSITION_SQL.split("DO UPDATE SET", 1)
        for immutable in (
            "ts_opened", "entry_side", "avg_px_open",
            "instrument_id", "strategy_id_tag",
        ):
            assert immutable not in update_clause, (
                f"{immutable} should not appear in UPDATE SET (it is immutable)"
            )


# ---------------------------------------------------------------------------
# Buffer gating — emulate the Actor's _on_*_event slots
# ---------------------------------------------------------------------------

class _DbWriterStub:
    """Replicates DbWriterActor's pure buffer/flush/persist logic NT-free."""

    def __init__(self, node_type: str = "sandbox", db_engine: object | None = None):
        self._node_type = node_type
        self._db_engine = db_engine or MagicMock()
        self._buffer: list[dict] = []
        self._executor_calls: list = []
        self.log = MagicMock()

    def queue_for_executor(self, func, args):
        """Stand-in for NT's queue_for_executor — capture the call."""
        self._executor_calls.append((func, args))

    # The four methods below are copied verbatim from DbWriterActor
    # (they have no NT coupling) — importing them directly via getattr
    # preserves the exact production logic path.
    _on_order_event = dwa.DbWriterActor._on_order_event
    _on_position_event = dwa.DbWriterActor._on_position_event
    _flush = dwa.DbWriterActor._flush
    _write_batch = dwa.DbWriterActor._write_batch
    _persist_fill = dwa.DbWriterActor._persist_fill
    _persist_position = dwa.DbWriterActor._persist_position


class TestOrderEventGating:
    """_on_order_event only buffers OrderFilled; all others are dropped."""

    def test_order_filled_is_buffered(self, fill_event):
        from nautilus_trader.model.events import OrderFilled
        stub = _DbWriterStub()
        # Downcast the MagicMock to look like an OrderFilled
        fill_event.__class__ = OrderFilled
        stub._on_order_event(fill_event)
        assert len(stub._buffer) == 1
        assert stub._buffer[0]["type"] == "fill"
        assert stub._buffer[0]["event"] is fill_event

    def test_order_accepted_is_not_buffered(self):
        from nautilus_trader.model.events import OrderAccepted
        stub = _DbWriterStub()
        ev = MagicMock()
        ev.__class__ = OrderAccepted
        stub._on_order_event(ev)
        assert len(stub._buffer) == 0

    def test_order_rejected_is_not_buffered(self):
        from nautilus_trader.model.events import OrderRejected
        stub = _DbWriterStub()
        ev = MagicMock()
        ev.__class__ = OrderRejected
        stub._on_order_event(ev)
        assert len(stub._buffer) == 0

    def test_no_buffer_when_db_engine_missing(self, fill_event):
        """No DB engine → no buffering (avoids unbounded growth if DB fails)."""
        from nautilus_trader.model.events import OrderFilled
        stub = _DbWriterStub(db_engine=None)
        stub._db_engine = None
        fill_event.__class__ = OrderFilled
        stub._on_order_event(fill_event)
        assert len(stub._buffer) == 0


class TestPositionEventGating:
    """All three position lifecycle events (Opened/Changed/Closed) are buffered."""

    def test_position_opened_buffered(self, position_event):
        from nautilus_trader.model.events import PositionOpened
        stub = _DbWriterStub()
        position_event.__class__ = PositionOpened
        stub._on_position_event(position_event)
        assert len(stub._buffer) == 1
        assert stub._buffer[0]["type"] == "position"

    def test_position_changed_buffered(self, position_event):
        from nautilus_trader.model.events import PositionChanged
        stub = _DbWriterStub()
        position_event.__class__ = PositionChanged
        stub._on_position_event(position_event)
        assert len(stub._buffer) == 1

    def test_position_closed_buffered(self, position_event):
        from nautilus_trader.model.events import PositionClosed
        stub = _DbWriterStub()
        position_event.__class__ = PositionClosed
        stub._on_position_event(position_event)
        assert len(stub._buffer) == 1

    def test_unknown_event_not_buffered(self):
        stub = _DbWriterStub()
        stub._on_position_event(MagicMock())  # not a Position* subclass
        assert len(stub._buffer) == 0

    def test_no_buffer_when_db_engine_missing(self, position_event):
        from nautilus_trader.model.events import PositionOpened
        stub = _DbWriterStub()
        stub._db_engine = None
        position_event.__class__ = PositionOpened
        stub._on_position_event(position_event)
        assert len(stub._buffer) == 0


# ---------------------------------------------------------------------------
# Flush → queue_for_executor semantics
# ---------------------------------------------------------------------------

class TestFlushSemantics:
    def test_empty_buffer_no_executor_call(self):
        stub = _DbWriterStub()
        stub._flush()
        assert stub._executor_calls == []

    def test_no_db_engine_no_executor_call(self, fill_event):
        stub = _DbWriterStub(db_engine=None)
        stub._db_engine = None
        stub._buffer.append({"type": "fill", "event": fill_event})
        stub._flush()
        assert stub._executor_calls == []

    def test_flush_queues_batch_and_clears_buffer(self, fill_event):
        stub = _DbWriterStub()
        stub._buffer.append({"type": "fill", "event": fill_event})
        stub._buffer.append({"type": "fill", "event": fill_event})
        stub._flush()
        assert len(stub._executor_calls) == 1
        func, (batch,) = stub._executor_calls[0]
        assert func == stub._write_batch
        assert len(batch) == 2
        # Buffer drained — next flush must not re-send the same records
        assert stub._buffer == []

    def test_flush_snapshot_is_independent_of_future_buffer_mutations(self, fill_event):
        """Buffer is copied into the batch — later appends don't sneak into
        the in-flight executor task."""
        stub = _DbWriterStub()
        stub._buffer.append({"type": "fill", "event": fill_event})
        stub._flush()
        batch = stub._executor_calls[0][1][0]
        # Simulate another event arriving AFTER flush but before the executor runs
        stub._buffer.append({"type": "fill", "event": MagicMock()})
        assert len(batch) == 1  # still only the original one


# ---------------------------------------------------------------------------
# _write_batch → SQL dispatch
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal SQLAlchemy Session stand-in — records executed statements."""

    def __init__(self):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        # `text()` objects stringify to the original SQL
        self.executed.append((str(stmt), params or {}))

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestWriteBatchDispatch:
    def test_fill_item_routes_to_insert_fill_sql(self, fill_event, monkeypatch):
        stub = _DbWriterStub()
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)

        stub._write_batch([{"type": "fill", "event": fill_event}])
        assert len(session.executed) == 1
        sql, params = session.executed[0]
        assert "INSERT INTO fills" in sql
        assert params == fill_db_fields(fill_event, "sandbox")
        assert session.committed is True

    def test_position_item_routes_to_upsert_position_sql(self, position_event, monkeypatch):
        stub = _DbWriterStub()
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)

        stub._write_batch([{"type": "position", "event": position_event}])
        assert len(session.executed) == 1
        sql, params = session.executed[0]
        assert "INSERT INTO positions" in sql
        assert params == position_db_fields(position_event.position, "sandbox")
        assert session.committed is True

    def test_mixed_batch_preserves_order(
        self, fill_event, position_event, monkeypatch,
    ):
        stub = _DbWriterStub()
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)

        stub._write_batch([
            {"type": "fill", "event": fill_event},
            {"type": "position", "event": position_event},
            {"type": "fill", "event": fill_event},
        ])
        assert len(session.executed) == 3
        assert "INSERT INTO fills" in session.executed[0][0]
        assert "INSERT INTO positions" in session.executed[1][0]
        assert "INSERT INTO fills" in session.executed[2][0]

    def test_batch_exception_is_logged_not_raised(self, fill_event, monkeypatch):
        """An exception in the executor thread must not bubble up — the NT
        event loop thread must stay alive."""
        stub = _DbWriterStub()

        class _ExplodingSession:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, *a, **kw):
                raise RuntimeError("boom")

            def commit(self):
                pass

        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: _ExplodingSession())
        stub._write_batch([{"type": "fill", "event": fill_event}])
        assert stub.log.error.called
        assert "boom" in str(stub.log.error.call_args)

    def test_unknown_item_type_is_silently_skipped(self, monkeypatch):
        """Unknown type keys are silently dropped so a schema drift on the
        buffer-side doesn't crash the executor — the commit still happens."""
        stub = _DbWriterStub()
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)
        stub._write_batch([{"type": "mystery", "event": MagicMock()}])
        assert session.executed == []
        assert session.committed is True


# ---------------------------------------------------------------------------
# Node-type propagation
# ---------------------------------------------------------------------------

class TestNodeTypePropagation:
    """sandbox vs live must be distinct in the persisted row."""

    def test_fill_node_type_sandbox(self, fill_event, monkeypatch):
        stub = _DbWriterStub(node_type="sandbox")
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)
        stub._write_batch([{"type": "fill", "event": fill_event}])
        assert session.executed[0][1]["node_type"] == "sandbox"

    def test_fill_node_type_live(self, fill_event, monkeypatch):
        stub = _DbWriterStub(node_type="live")
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)
        stub._write_batch([{"type": "fill", "event": fill_event}])
        assert session.executed[0][1]["node_type"] == "live"

    def test_position_node_type(self, position_event, monkeypatch):
        stub = _DbWriterStub(node_type="live")
        session = _FakeSession()
        monkeypatch.setattr("sqlalchemy.orm.Session", lambda engine: session)
        stub._write_batch([{"type": "position", "event": position_event}])
        assert session.executed[0][1]["node_type"] == "live"
