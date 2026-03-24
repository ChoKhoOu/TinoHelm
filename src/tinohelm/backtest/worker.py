"""Backtest worker — runs in subprocess, dequeues jobs from Redis."""
from __future__ import annotations

import json
import logging
import math
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
import redis

logger = logging.getLogger(__name__)

from tinohelm.db.sync_engine import get_sync_engine


def _deep_sanitize(obj):
    """Recursively replace NaN/Infinity with None in nested structures."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _deep_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_sanitize(v) for v in obj]
    return obj


def _publish_progress(
    r: redis.Redis,
    run_id: str,
    pct: int,
    elapsed_secs: float | None = None,
) -> None:
    """Publish a ``backtest.progress`` event to the EventBridge channel."""
    payload: dict = {
        "type": "backtest.progress",
        "run_id": run_id,
        "pct": pct,
        "elapsed_secs": elapsed_secs,
    }
    r.publish(
        f"tino:backtest:progress:{run_id}",
        json.dumps(payload, default=str),
    )


def _publish_stats(
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


def _publish_completed(
    r: redis.Redis,
    run_id: str,
    status: str,
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    """Publish a ``backtest.completed`` event to the EventBridge channel."""
    payload: dict = {
        "type": "backtest.completed",
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


def backtest_worker(redis_url: str, catalog_path: str, artifacts_path: str, db_url: str, idle_timeout: int = 0) -> None:
    """Worker process: dequeue backtest jobs from Redis and execute them.

    Runs in an infinite loop until terminated.

    Args:
        idle_timeout: Seconds of idle time before self-terminating.
            0 = keep-alive (never auto-exit). Used for the minimum
            resident worker.  Ephemeral workers use e.g. 60.
    """
    running = True
    current_run_id: str | None = None

    def _signal_handler(sig, frame):
        nonlocal running
        running = False
        # If we are processing a run when SIGTERM arrives, mark it cancelled
        if current_run_id is not None:
            try:
                _update_db_status(db_url, current_run_id, "cancelled")
                _publish_completed(r, current_run_id, "cancelled")
            except Exception:
                logger.exception("Failed to mark run %s as cancelled on SIGTERM", current_run_id)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    r = redis.from_url(redis_url)
    kind = "keep-alive" if idle_timeout == 0 else f"ephemeral({idle_timeout}s)"
    logger.info("Backtest worker started (%s), waiting for jobs...", kind)

    idle_since = time.monotonic()

    while running:
        try:
            # Block-wait for a job (timeout 5s to check running flag)
            result = r.brpop("tino:backtest:queue", timeout=5)
            if result is None:
                # No job — check idle timeout for ephemeral workers
                if idle_timeout > 0 and (time.monotonic() - idle_since) > idle_timeout:
                    logger.info("Ephemeral worker idle for %ds, shutting down (pid=%s)", idle_timeout, sys.argv[0] if sys.argv else "?")
                    break
                continue

            # Got a job — reset idle timer
            idle_since = time.monotonic()

            _, job_data = result
            job = json.loads(job_data)
            run_id = job["run_id"]
            current_run_id = run_id

            logger.info(f"Processing backtest job: {run_id}")

            # Check if this run has been cancelled before we start
            cancel_key = f"tino:backtest:cancel:{run_id}"
            if r.get(cancel_key):
                logger.info(f"Backtest {run_id} was cancelled before execution")
                r.delete(cancel_key)
                _update_db_status(db_url, run_id, "cancelled")
                _publish_completed(r, run_id, "cancelled")
                current_run_id = None
                continue

            # Mark as running in DB + Redis
            _update_db_status(db_url, run_id, "running")
            job_start_time = time.monotonic()
            _publish_progress(r, run_id, 0)
            r.setex(f"tino:backtest:progress:{run_id}", 86400, "0")

            try:
                from tinohelm.backtest.runner import BacktestRunner

                # Backward compat: support both old single "symbol"/"interval" and new "symbols"/"intervals"
                # Empty list is valid for portfolio strategies (symbols/interval come from portfolio.yaml)
                symbols = job.get("symbols")
                if symbols is None:
                    sym = job.get("symbol")
                    symbols = [sym] if sym else []
                intervals = job.get("intervals")
                if intervals is None:
                    ivl = job.get("interval")
                    intervals = [ivl] if ivl else ["1m"]

                runner = BacktestRunner(
                    strategy_path=job["strategy_path"],
                    config_path=job["config_path"],
                    strategy_params=job.get("params", {}),
                    catalog_path=catalog_path,
                    symbols=symbols,
                    intervals=intervals,
                    start=datetime.fromisoformat(job["start"]),
                    end=datetime.fromisoformat(job["end"]),
                    fill_model=job.get("fill_model"),
                )
                # Enable bar-level progress tracking via ProgressReporter actor
                runner._redis_client = r
                runner._run_id = run_id

                # Publish progress: engine setup done
                elapsed = round(time.monotonic() - job_start_time, 1)
                _publish_progress(r, run_id, 10, elapsed_secs=elapsed)
                r.setex(f"tino:backtest:progress:{run_id}", 86400, "10")

                # Create artifact directory and set on runner for report export
                artifact_dir = Path(artifacts_path) / run_id
                artifact_dir.mkdir(parents=True, exist_ok=True)
                runner.artifacts_dir = artifact_dir

                results = runner.run()

                # Progress: engine done, saving artifacts
                elapsed = round(time.monotonic() - job_start_time, 1)
                _publish_progress(r, run_id, 95, elapsed_secs=elapsed)
                r.setex(f"tino:backtest:progress:{run_id}", 86400, "95")

                # Sanitize NaN/Infinity before any serialization
                results = _deep_sanitize(results)

                # Save artifact JSON
                artifact_path = artifact_dir / "results.json"
                with open(artifact_path, "w") as f:
                    json.dump(results, f, indent=2, default=str)

                # Publish stats from results
                stats = results.get("statistics", {})
                _publish_stats(
                    r, run_id,
                    trades=int(stats.get("total_trades", 0)),
                    pnl=float(stats.get("pnl_total", 0)),
                    win_rate=float(stats.get("win_rate", 0)),
                )

                # Publish completion
                elapsed = round(time.monotonic() - job_start_time, 1)
                _publish_progress(r, run_id, 100, elapsed_secs=elapsed)
                _publish_completed(r, run_id, "completed", summary=stats)

                # Store result summary in a Redis key for quick access
                r.setex(
                    f"tino:backtest:result:{run_id}",
                    86400,  # 24h TTL
                    json.dumps(results, default=str),
                )

                # Store progress for status polling
                r.setex(f"tino:backtest:progress:{run_id}", 86400, "100")

                # Update database record
                _update_db_status(
                    db_url,
                    run_id,
                    "completed",
                    result_summary=results.get("statistics", {}),
                )

                # Check cancel after full completion — if cancel arrived during
                # execution, override the completed status to cancelled
                if r.get(cancel_key):
                    logger.info("Backtest %s cancelled after execution", run_id)
                    r.delete(cancel_key)
                    _update_db_status(db_url, run_id, "cancelled")
                    _publish_completed(r, run_id, "cancelled")
                else:
                    logger.info("Backtest %s completed successfully", run_id)

            except Exception as e:
                logger.exception("Backtest %s failed: %s", run_id, e)
                safe_error = "Internal backtest error. Check server logs for details."
                _publish_completed(r, run_id, "failed", error=safe_error)

                # Update database record on failure
                _update_db_status(db_url, run_id, "failed", error_msg=safe_error)

            finally:
                current_run_id = None

        except Exception as e:
            if running:
                logger.error(f"Worker error: {e}")
            current_run_id = None

    r.close()
    logger.info("Backtest worker stopped")


# Alias used by process_manager and watchdog
def run_worker(redis_url: str, catalog_path: str, artifacts_path: str, db_url: str, idle_timeout: int = 0) -> None:
    """Entry-point alias for backtest_worker (used by ProcessManager)."""
    backtest_worker(redis_url, catalog_path, artifacts_path, db_url, idle_timeout)


def _update_db_status(
    db_url: str,
    run_id: str,
    status: str,
    result_summary: dict | None = None,
    error_msg: str | None = None,
) -> None:
    """Update the backtest_runs table using a synchronous DB session.

    The worker runs in a subprocess, so we use a cached sync engine
    to perform the update without depending on the async stack.
    """
    try:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from tinohelm.db.models import BacktestRun, RunStatus

        engine = get_sync_engine(db_url)
        with Session(engine) as session:
            values: dict = {
                "status": RunStatus(status),
                "completed_at": datetime.utcnow(),
            }
            if result_summary is not None:
                # Sanitize for JSON: replace NaN/Infinity with None
                import math
                def _sanitize(obj):
                    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                        return None
                    if isinstance(obj, dict):
                        return {k: _sanitize(v) for k, v in obj.items()}
                    if isinstance(obj, list):
                        return [_sanitize(v) for v in obj]
                    return obj
                values["result_summary_json"] = _sanitize(result_summary)
            if error_msg is not None:
                values["error"] = error_msg
            session.execute(
                update(BacktestRun).where(BacktestRun.run_id == run_id).values(**values)
            )
            session.commit()
    except Exception:
        logger.exception("Failed to update DB status for run %s", run_id)
