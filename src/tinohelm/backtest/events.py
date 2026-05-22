"""Backtest event publishing and DB status updates.

Shared by runner_cli.py (write events + DB) and consumer.py (fallback
DB write when subprocess dies without cleanup).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import redis
from sqlalchemy import update
from sqlalchemy.orm import Session

from tinohelm.core.utils import sanitize_for_json
from tinohelm.db.models import BacktestRun, RunStatus
from tinohelm.db.sync_engine import get_sync_engine

logger = logging.getLogger(__name__)


def publish_progress(
    r: redis.Redis,
    run_id: str,
    pct: int,
    elapsed_secs: float | None = None,
    eta_secs: float | None = None,
    total_bars: int | None = None,
    processed_bars: int | None = None,
    bars_per_sec: float | None = None,
    trades: int | None = None,
) -> None:
    """Publish a ``backtest.progress`` event to the EventBridge channel."""
    payload: dict = {
        "type": "backtest.progress",
        "run_id": run_id,
        "pct": pct,
        "elapsed_secs": elapsed_secs,
        "eta_secs": eta_secs,
        "total_bars": total_bars,
        "processed_bars": processed_bars,
        "bars_per_sec": bars_per_sec,
        "trades": trades,
    }
    r.publish(
        f"tino:backtest:progress:{run_id}",
        json.dumps(payload, default=str),
    )


def publish_stats(
    r: redis.Redis,
    run_id: str,
    trades: int,
    pnl: float,
    win_rate: float,
) -> None:
    """Publish a ``backtest.stats`` event to the EventBridge channel."""
    payload: dict = {
        "type": "backtest.stats",
        "run_id": run_id,
        "trades": trades,
        "pnl": round(pnl, 2),
        "win_rate": round(win_rate, 4),
    }
    r.publish(
        f"tino:backtest:progress:{run_id}",
        json.dumps(payload, default=str),
    )


def publish_completed(
    r: redis.Redis,
    run_id: str,
    status: str,
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    """Publish a terminal backtest event to the EventBridge channel."""
    _type_map = {"failed": "backtest.failed", "cancelled": "backtest.cancelled"}
    payload: dict = {
        "type": _type_map.get(status, "backtest.completed"),
        "run_id": run_id,
        "status": status,
        "summary": summary or {},
    }
    if error:
        payload["error"] = error
    r.publish(
        f"tino:backtest:progress:{run_id}",
        json.dumps(payload, default=str),
    )


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def update_db_status(
    db_url: str,
    run_id: str,
    status: str,
    result_summary: dict | None = None,
    error_msg: str | None = None,
    *,
    only_if_not_terminal: bool = False,
    strict: bool = False,
) -> bool:
    """Update the backtest_runs table using a synchronous DB session.

    The worker/runner_cli runs in a subprocess, so we use a cached sync engine
    to perform the update without depending on the async stack.

    Returns ``True`` when the UPDATE matched a row, ``False`` when it failed or
    when the compare-and-swap condition made it a no-op.  By default failures
    are logged and swallowed for best-effort parent safety-net paths; pass
    ``strict=True`` for durable terminal writes that must be observable by the
    caller.

    Args:
        only_if_not_terminal: When ``True``, the UPDATE is conditional —
            it only applies if the current DB status is NOT already a terminal
            value (``completed``, ``failed``, or ``cancelled``).  This lets
            parent-process safety-net writes act as atomic compare-and-swap
            without overwriting a terminal state already written by the
            subprocess.
        strict: Re-raise DB failures and row-miss/no-op outcomes instead of
            swallowing them.  Use this for subprocess-owned terminal success
            writes; a run must not return rc=0 if ``completed`` was not durable.
    """
    try:
        engine = get_sync_engine(db_url)
        with Session(engine) as session:
            values: dict[str, Any] = {"status": RunStatus(status)}
            if status in _TERMINAL_STATUSES:
                values["completed_at"] = datetime.utcnow()
            if result_summary is not None:
                values["result_summary_json"] = sanitize_for_json(result_summary)
            if error_msg is not None:
                values["error"] = error_msg

            stmt = update(BacktestRun).where(BacktestRun.run_id == run_id)
            if only_if_not_terminal:
                stmt = stmt.where(
                    BacktestRun.status.not_in(list(_TERMINAL_STATUSES))
                )
            result = session.execute(stmt.values(**values))
            session.commit()
            matched = result.rowcount is None or result.rowcount > 0
            if not matched:
                msg = (
                    f"DB status update for run {run_id} matched no rows "
                    f"(status={status!r}, only_if_not_terminal={only_if_not_terminal})"
                )
                if strict:
                    raise RuntimeError(msg)
                logger.warning(msg)
            return matched
    except Exception:
        logger.exception("Failed to update DB status for run %s", run_id)
        if strict:
            raise
        return False
