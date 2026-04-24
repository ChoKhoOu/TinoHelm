"""Single-backtest subprocess entry — fresh NT Rust runtime per process.

Two mutually exclusive modes:
  --run-id <uuid>           Queue mode: read job payload from stdin (or DB
                            fallback), run BacktestRunner, write results back
                            via events.py helpers.
  --fold-config <path>      Walk-forward fold mode: read JSON config from file,
                            run BacktestRunner, print single-line JSON result to
                            stdout for optimizer to parse.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import redis

from tinohelm.backtest.events import (
    publish_completed,
    publish_progress,
    publish_stats,
    update_db_status,
)
from tinohelm.core.config import get_settings
from tinohelm.core.utils import sanitize_for_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state for SIGTERM handler — set once in _run_queue_mode()
# ---------------------------------------------------------------------------

_current_run_id: str | None = None
_current_r: redis.Redis | None = None
_current_db_url: str | None = None


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------

def _handle_sigterm(signum, frame):
    """Cancel current run if SIGTERM/SIGINT arrives mid-execution."""
    if _current_run_id and _current_r and _current_db_url:
        try:
            update_db_status(_current_db_url, _current_run_id, "cancelled")
            publish_completed(_current_r, _current_run_id, "cancelled")
        except Exception:
            logger.exception("Failed to mark run cancelled on SIGTERM")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Queue mode
# ---------------------------------------------------------------------------

def _run_queue_mode(run_id: str) -> int:
    """Execute a backtest from queue payload (Redis-driven).

    Returns exit code: 0 = success, 1 = failure, 2 = cancelled.
    """
    global _current_run_id, _current_r, _current_db_url

    cfg = get_settings()
    r = redis.from_url(cfg.redis.url)
    _current_run_id = run_id
    _current_r = r
    _current_db_url = cfg.database.url

    # Read job payload: prefer stdin (from consumer), fall back to DB
    job = _load_job_payload(run_id, cfg.database.url)
    if job is None:
        logger.error("No job payload for run %s", run_id)
        return 1

    # Pre-cancel check
    cancel_key = f"tino:backtest:cancel:{run_id}"
    if r.get(cancel_key):
        logger.info("Run %s was cancelled before execution", run_id)
        r.delete(cancel_key)
        update_db_status(cfg.database.url, run_id, "cancelled")
        publish_completed(r, run_id, "cancelled")
        return 2

    update_db_status(cfg.database.url, run_id, "running")
    job_start_time = time.monotonic()
    publish_progress(r, run_id, 0)
    r.setex(f"tino:backtest:progress:{run_id}", 86400, "0")

    try:
        from tinohelm.backtest.runner import BacktestRunner

        # Backward compat: support both old single "symbol"/"interval" and
        # new "symbols"/"intervals" fields in the job payload.
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
            catalog_path=str(cfg.paths.catalog),
            symbols=symbols,
            intervals=intervals,
            start=datetime.fromisoformat(job["start"]),
            end=datetime.fromisoformat(job["end"]),
            fill_model=job.get("fill_model"),
            maker_fee=job.get("maker_fee"),
            taker_fee=job.get("taker_fee"),
            warmup_bars=job.get("warmup_bars"),
            tags=job.get("tags"),
            data_type=job.get("data_type", "klines"),
        )
        # Enable bar-level progress tracking via ProgressReporter actor
        runner._redis_client = r
        runner._run_id = run_id
        runner._job_start_time = job_start_time

        # Create artifact directory and set on runner for report export
        artifact_dir = Path(cfg.paths.artifacts) / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        runner.artifacts_dir = artifact_dir

        # Publish progress: runner constructed, data loading next
        publish_progress(
            r, run_id, 2,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )
        r.setex(f"tino:backtest:progress:{run_id}", 86400, "2")

        # The one place in this codebase that creates a BacktestEngine
        results = asyncio.run(runner.run())

        # Progress: engine done, saving artifacts
        publish_progress(
            r, run_id, 95,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )
        r.setex(f"tino:backtest:progress:{run_id}", 86400, "95")

        # Sanitize NaN/Infinity before any serialization
        results = sanitize_for_json(results)

        # Save artifact JSON
        artifact_path = artifact_dir / "results.json"
        with open(artifact_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Publish stats from results
        stats = results.get("statistics", {})
        publish_stats(
            r, run_id,
            trades=int(stats.get("total_trades", 0)),
            pnl=float(stats.get("pnl_total", 0)),
            win_rate=float(stats.get("win_rate", 0)),
        )

        # Publish completion
        publish_progress(
            r, run_id, 100,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )
        publish_completed(r, run_id, "completed", summary=stats)

        # Store result summary in Redis for quick access
        r.setex(
            f"tino:backtest:result:{run_id}",
            86400,
            json.dumps(results, default=str),
        )
        r.setex(f"tino:backtest:progress:{run_id}", 86400, "100")

        # Update database record
        update_db_status(
            cfg.database.url,
            run_id,
            "completed",
            result_summary=results.get("statistics", {}),
        )

        # Post-completion cancel check: cancel may have arrived mid-run
        if r.get(cancel_key):
            r.delete(cancel_key)
            update_db_status(cfg.database.url, run_id, "cancelled")
            publish_completed(r, run_id, "cancelled")
            logger.info("Backtest %s cancelled after execution", run_id)
            return 2

        logger.info("Backtest %s completed successfully", run_id)
        return 0

    except Exception as e:
        logger.exception("Backtest %s failed: %s", run_id, e)
        safe_error = "Internal backtest error. Check subprocess logs for details."
        publish_completed(r, run_id, "failed", error=safe_error)
        update_db_status(cfg.database.url, run_id, "failed", error_msg=safe_error)
        return 1


# ---------------------------------------------------------------------------
# Job payload loader
# ---------------------------------------------------------------------------

def _load_job_payload(run_id: str, db_url: str) -> dict | None:
    """Load job dict from stdin (consumer-supplied) or DB (recovery fallback).

    stdin path: consumer wrote JSON to the pipe before closing it.
    DB fallback: used during manual invocation or recovery scenarios.
    """
    # 1) stdin path — consumer wrote JSON to pipe
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                return json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("stdin read failed: %s (falling back to DB)", exc)

    # 2) DB fallback (recovery / manual invocation)
    try:
        from sqlalchemy.orm import Session

        from tinohelm.db.models import BacktestRun
        from tinohelm.db.sync_engine import get_sync_engine

        engine = get_sync_engine(db_url)
        with Session(engine) as session:
            row = (
                session.query(BacktestRun)
                .filter(BacktestRun.run_id == run_id)
                .one_or_none()
            )
            if row is None or not row.job_payload_json:
                return None
            return dict(row.job_payload_json)
    except Exception:
        logger.exception("DB fallback read failed for run %s", run_id)
        return None


# ---------------------------------------------------------------------------
# Fold mode
# ---------------------------------------------------------------------------

def _run_fold_mode(fold_config_path: str) -> int:
    """Execute a single walk-forward fold, return fitness via stdout JSON.

    stdout protocol (single line, only output ever written to stdout):
      success: {"status": "ok", "fitness": <float>, "metrics": {...}}
      failure: {"status": "fail", "error": "<message>"}

    All other logging goes to stderr via the logger configured in main().
    Returns exit code: 0 = success, 1 = failure.
    """
    try:
        with open(fold_config_path) as f:
            cfg_dict = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "error": f"config read: {exc}"}), flush=True)
        return 1

    try:
        from tinohelm.backtest.runner import BacktestRunner
        from tinohelm.backtest.optimizer_helpers import extract_fitness

        start_dt = datetime.fromisoformat(cfg_dict["start"])
        end_dt = datetime.fromisoformat(cfg_dict["end"])

        runner = BacktestRunner(
            strategy_path=cfg_dict["strategy_path"],
            config_path=cfg_dict["config_path"],
            strategy_params=cfg_dict.get("params", {}),
            catalog_path=cfg_dict["catalog_path"],
            symbol=cfg_dict.get("symbol", ""),
            interval=cfg_dict.get("interval", "1m"),
            start=start_dt,
            end=end_dt,
        )

        result = asyncio.run(runner.run())
        fitness = extract_fitness(result, cfg_dict["fitness_objective"])
        metrics = result.get("statistics", {})

        # Only numeric values for slim stdout payload
        print(
            json.dumps({
                "status": "ok",
                "fitness": float(fitness),
                "metrics": {
                    k: v for k, v in metrics.items()
                    if isinstance(v, (int, float))
                },
            }),
            flush=True,
        )
        return 0

    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), flush=True)
        logger.exception("Fold backtest failed")
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    # Configure logging to stderr — stdout is reserved for fold-mode JSON protocol
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    parser = argparse.ArgumentParser(
        description="TinoHelm backtest subprocess entry point.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", help="Backtest run_id (queue mode)")
    group.add_argument(
        "--fold-config",
        help="Path to JSON config file (walk-forward fold mode)",
    )
    args = parser.parse_args(argv)

    if args.run_id:
        return _run_queue_mode(args.run_id)
    return _run_fold_mode(args.fold_config)


if __name__ == "__main__":
    sys.exit(main())
