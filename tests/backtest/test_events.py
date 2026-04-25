"""Unit tests for tinohelm.backtest.events — event publishing and DB write."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tinohelm.backtest.events import (
    publish_completed,
    publish_progress,
    publish_stats,
    update_db_status,
)
from tinohelm.db.models import BacktestRun, Base, RunStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis() -> MagicMock:
    return MagicMock()


def _make_sqlite_db() -> tuple[str, "Engine"]:
    """Create an in-memory SQLite engine with BacktestRun table."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return "sqlite:///:memory:", engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_publish_progress_builds_correct_payload():
    """publish_progress calls redis.publish with a payload containing all expected keys."""
    r = _make_redis()
    run_id = "run-abc"

    publish_progress(
        r,
        run_id,
        pct=42,
        elapsed_secs=10.5,
        eta_secs=20.0,
        total_bars=1000,
        processed_bars=420,
        bars_per_sec=42.0,
        trades=5,
    )

    assert r.publish.call_count == 1
    channel, raw = r.publish.call_args[0]
    assert channel == f"tino:backtest:progress:{run_id}"

    payload = json.loads(raw)
    assert payload["type"] == "backtest.progress"
    assert payload["run_id"] == run_id
    assert payload["pct"] == 42

    # All documented keys must be present
    expected_keys = {
        "type", "run_id", "pct",
        "elapsed_secs", "eta_secs",
        "total_bars", "processed_bars",
        "bars_per_sec", "trades",
    }
    assert expected_keys == set(payload.keys())


def test_publish_stats_rounds_numbers():
    """publish_stats rounds pnl to 2 decimal places and win_rate to 4."""
    r = _make_redis()
    run_id = "run-xyz"

    publish_stats(r, run_id, trades=10, pnl=123.456789, win_rate=0.666666)

    _, raw = r.publish.call_args[0]
    payload = json.loads(raw)

    assert payload["pnl"] == round(123.456789, 2)
    assert payload["win_rate"] == round(0.666666, 4)
    assert payload["type"] == "backtest.stats"
    assert payload["trades"] == 10


def test_publish_completed_maps_status_to_event_type():
    """publish_completed maps status values to the correct event type strings."""
    cases = [
        ("failed", "backtest.failed"),
        ("cancelled", "backtest.cancelled"),
        ("completed", "backtest.completed"),
        ("any_other", "backtest.completed"),
    ]

    for status, expected_type in cases:
        r = _make_redis()
        publish_completed(r, "run-1", status=status)

        _, raw = r.publish.call_args[0]
        payload = json.loads(raw)
        assert payload["type"] == expected_type, (
            f"status={status!r} should map to {expected_type!r}, got {payload['type']!r}"
        )


def test_update_db_status_completed_with_summary():
    """update_db_status sets status=completed and persists result_summary_json."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    run_id = "run-test-001"
    with Session(engine) as session:
        session.add(
            BacktestRun(
                run_id=run_id,
                strategy_name="test_strategy",
                symbol="BTCUSDT-PERP",
                interval="5m",
                start_date=__import__("datetime").date(2025, 1, 1),
                end_date=__import__("datetime").date(2025, 2, 1),
                status=RunStatus.running,
            )
        )
        session.commit()

    # Patch get_sync_engine to return our test engine
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "tinohelm.backtest.events.get_sync_engine", return_value=engine
    ):
        update_db_status(
            "sqlite:///:memory:",
            run_id,
            "completed",
            result_summary={"total_trades": 5},
        )

    with Session(engine) as session:
        row = session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        ).scalar_one()
        assert row.status == RunStatus.completed
        assert row.result_summary_json["total_trades"] == 5


def test_update_db_status_failed_with_error():
    """update_db_status sets status=failed and persists the error message."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    run_id = "run-test-002"
    with Session(engine) as session:
        session.add(
            BacktestRun(
                run_id=run_id,
                strategy_name="test_strategy",
                symbol="BTCUSDT-PERP",
                interval="5m",
                start_date=__import__("datetime").date(2025, 1, 1),
                end_date=__import__("datetime").date(2025, 2, 1),
                status=RunStatus.running,
            )
        )
        session.commit()

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "tinohelm.backtest.events.get_sync_engine", return_value=engine
    ):
        update_db_status(
            "sqlite:///:memory:",
            run_id,
            "failed",
            error_msg="msg",
        )

    with Session(engine) as session:
        row = session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        ).scalar_one()
        assert row.status == RunStatus.failed
        assert row.error == "msg"


def test_update_db_status_running_does_not_set_completed_at():
    """update_db_status must NOT write completed_at when transitioning to running."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    run_id = "run-test-003"
    with Session(engine) as session:
        session.add(
            BacktestRun(
                run_id=run_id,
                strategy_name="test_strategy",
                symbol="BTCUSDT-PERP",
                interval="5m",
                start_date=__import__("datetime").date(2025, 1, 1),
                end_date=__import__("datetime").date(2025, 2, 1),
                status=RunStatus.queued,
                completed_at=None,
            )
        )
        session.commit()

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "tinohelm.backtest.events.get_sync_engine", return_value=engine
    ):
        update_db_status("sqlite:///:memory:", run_id, "running")

    with Session(engine) as session:
        row = session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        ).scalar_one()
        assert row.status == RunStatus.running
        assert row.completed_at is None


def test_update_db_status_only_if_not_terminal_does_not_overwrite_completed():
    """only_if_not_terminal=True must not overwrite an already-terminal status.

    Simulates the parent-process safety-net writing 'failed' after a subprocess
    already wrote 'completed'.  The row must remain 'completed'.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    run_id = "run-test-004"
    with Session(engine) as session:
        session.add(
            BacktestRun(
                run_id=run_id,
                strategy_name="test_strategy",
                symbol="BTCUSDT-PERP",
                interval="5m",
                start_date=__import__("datetime").date(2025, 1, 1),
                end_date=__import__("datetime").date(2025, 2, 1),
                status=RunStatus.completed,
            )
        )
        session.commit()

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "tinohelm.backtest.events.get_sync_engine", return_value=engine
    ):
        update_db_status(
            "sqlite:///:memory:",
            run_id,
            "failed",
            error_msg="Subprocess exited with code -9",
            only_if_not_terminal=True,
        )

    # The row must still be 'completed' — the fallback write must be a no-op.
    with Session(engine) as session:
        row = session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        ).scalar_one()
        assert row.status == RunStatus.completed, (
            f"Expected status=completed after no-op CAS, got {row.status!r}"
        )
        assert row.error is None, (
            "error field must not be written when the CAS condition fails"
        )


def test_update_db_status_only_if_not_terminal_updates_running():
    """only_if_not_terminal=True must still update when status is non-terminal.

    Simulates the parent safety-net writing 'failed' for a run stuck in 'running'.
    The WHERE clause must allow the update to go through.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    run_id = "run-test-005"
    with Session(engine) as session:
        session.add(
            BacktestRun(
                run_id=run_id,
                strategy_name="test_strategy",
                symbol="BTCUSDT-PERP",
                interval="5m",
                start_date=__import__("datetime").date(2025, 1, 1),
                end_date=__import__("datetime").date(2025, 2, 1),
                status=RunStatus.running,
            )
        )
        session.commit()

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "tinohelm.backtest.events.get_sync_engine", return_value=engine
    ):
        update_db_status(
            "sqlite:///:memory:",
            run_id,
            "failed",
            error_msg="Subprocess exited with code -9",
            only_if_not_terminal=True,
        )

    with Session(engine) as session:
        row = session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        ).scalar_one()
        assert row.status == RunStatus.failed, (
            f"Expected status=failed after CAS on running row, got {row.status!r}"
        )
        assert row.error == "Subprocess exited with code -9"
