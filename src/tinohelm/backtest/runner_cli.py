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

logger = logging.getLogger(__name__)


def get_settings():
    """Lazy wrapper kept patchable for tests and fast for early signal setup."""
    from tinohelm.core.config import get_settings as _get_settings
    return _get_settings()


def sanitize_for_json(value):
    from tinohelm.core.utils import sanitize_for_json as _sanitize_for_json
    return _sanitize_for_json(value)


def publish_completed(*args, **kwargs):
    from tinohelm.backtest.events import publish_completed as _publish_completed
    return _publish_completed(*args, **kwargs)


def publish_progress(*args, **kwargs):
    from tinohelm.backtest.events import publish_progress as _publish_progress
    return _publish_progress(*args, **kwargs)


def publish_stats(*args, **kwargs):
    from tinohelm.backtest.events import publish_stats as _publish_stats
    return _publish_stats(*args, **kwargs)


def update_db_status(*args, **kwargs):
    from tinohelm.backtest.events import update_db_status as _update_db_status
    return _update_db_status(*args, **kwargs)

# ---------------------------------------------------------------------------
# Global state for SIGTERM handler — set once in _run_queue_mode()
# ---------------------------------------------------------------------------

_current_run_id: str | None = None
_current_r: redis.Redis | None = None
_current_db_url: str | None = None
_terminalizing: bool = False
_TERMINALIZING_TTL_SECONDS = 60


class _BacktestCancelledAfterEngine(Exception):
    """Internal control-flow signal for post-engine, pre-terminal cancel."""


def _best_effort(label: str, func, *args, **kwargs) -> None:
    """Run a post-terminal side effect without changing the terminal outcome."""
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception("Best-effort %s failed", label)


def _terminalizing_key(run_id: str) -> str:
    """Redis marker consumed by the parent cancel watcher."""
    return f"tino:backtest:terminalizing:{run_id}"


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------

def _handle_sigterm(signum, frame):
    """Cancel current run if SIGTERM/SIGINT arrives mid-execution."""
    if _terminalizing:
        logger.info("Ignoring signal %s after run terminalization started", signum)
        return
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
    global _current_run_id, _current_r, _current_db_url, _terminalizing

    cfg = get_settings()
    r = redis.from_url(cfg.redis.url)
    _current_run_id = run_id
    _current_r = r
    _current_db_url = cfg.database.url
    _terminalizing = False

    # Read job payload: prefer stdin (from consumer), fall back to DB
    job = _load_job_payload(run_id, cfg.database.url)
    if job is None:
        logger.error("No job payload for run %s", run_id)
        _current_run_id = None
        _current_r = None
        _current_db_url = None
        return 1

    # Pre-cancel check
    cancel_key = f"tino:backtest:cancel:{run_id}"
    terminalizing_key = _terminalizing_key(run_id)
    if r.get(cancel_key):
        logger.info("Run %s was cancelled before execution", run_id)
        r.delete(cancel_key)
        update_db_status(cfg.database.url, run_id, "cancelled")
        _best_effort("publish pre-run cancellation", publish_completed, r, run_id, "cancelled")
        _current_run_id = None
        _current_r = None
        _current_db_url = None
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

        def _begin_terminalization() -> None:
            """Own terminal state before any post-engine artifact/DB/Redis writes."""
            global _terminalizing
            if _terminalizing:
                return
            if r.get(cancel_key):
                r.delete(cancel_key)
                update_db_status(cfg.database.url, run_id, "cancelled")
                _best_effort(
                    "publish post-engine cancellation",
                    publish_completed,
                    r,
                    run_id,
                    "cancelled",
                )
                logger.info("Backtest %s cancelled after engine execution", run_id)
                raise _BacktestCancelledAfterEngine()
            r.setex(terminalizing_key, _TERMINALIZING_TTL_SECONDS, "1")
            _terminalizing = True

        runner._before_artifact_export = _begin_terminalization

        # Publish progress: runner constructed, data loading next
        publish_progress(
            r, run_id, 2,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )
        r.setex(f"tino:backtest:progress:{run_id}", 86400, "2")

        # The one place in this codebase that creates a BacktestEngine
        results = asyncio.run(runner.run())

        # Progress: engine done, saving artifacts. Redis progress is advisory;
        # failures here must not turn a successful engine run into DB failed.
        _best_effort(
            "publish engine-complete progress",
            publish_progress,
            r,
            run_id,
            95,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )
        _best_effort(
            "store engine-complete progress",
            r.setex,
            f"tino:backtest:progress:{run_id}",
            86400,
            "95",
        )

        # Sanitize NaN/Infinity before any serialization
        results = sanitize_for_json(results)

        # BacktestRunner normally calls this before internal CSV/HTML export.
        # Unit tests or alternate runner implementations may not, so keep a
        # fallback before the queue-mode results.json / DB / Redis writes.
        if not _terminalizing:
            _begin_terminalization()

        # Save artifact JSON atomically.  Readers should never observe a
        # partially-written results.json while status polling races completion.
        artifact_path = artifact_dir / "results.json"
        tmp_artifact_path = artifact_dir / "results.json.tmp"
        with open(tmp_artifact_path, "w") as f:
            json.dump(results, f, separators=(",", ":"), allow_nan=False, default=str)
        tmp_artifact_path.replace(artifact_path)

        # Persist the terminal DB state immediately after durable artifact write.
        # From here on Redis events/cache pointers are advisory side effects and
        # must not overwrite a successful backtest as failed.
        stats = results.get("statistics", {})
        update_db_status(
            cfg.database.url,
            run_id,
            "completed",
            result_summary=stats,
            strict=True,
        )

        _best_effort(
            "publish stats",
            publish_stats,
            r,
            run_id,
            trades=int(stats.get("total_trades", 0)),
            pnl=float(stats.get("pnl_total", 0)),
            win_rate=float(stats.get("win_rate", 0)),
        )

        _best_effort(
            "publish completion progress",
            publish_progress,
            r,
            run_id,
            100,
            elapsed_secs=round(time.monotonic() - job_start_time, 1),
        )

        # Store only a small summary pointer in Redis.  The full payload lives
        # in the artifact file and can be multi-MB for trade/equity reports.
        pointer_json = json.dumps({
            "status": "completed",
            "summary": stats,
            "artifact_path": str(artifact_path),
        }, default=str)
        _best_effort(
            "store result pointer",
            r.setex,
            f"tino:backtest:result:{run_id}",
            86400,
            pointer_json,
        )
        _best_effort(
            "store completion progress",
            r.setex,
            f"tino:backtest:progress:{run_id}",
            86400,
            "100",
        )
        _best_effort("publish completion", publish_completed, r, run_id, "completed", summary=stats)
        _best_effort("delete cancel key", r.delete, cancel_key)

        logger.info("Backtest %s completed successfully", run_id)
        return 0

    except _BacktestCancelledAfterEngine:
        return 2
    except Exception as e:
        logger.exception("Backtest %s failed: %s", run_id, e)
        safe_error = "Internal backtest error. Check subprocess logs for details."
        _best_effort("publish failure", publish_completed, r, run_id, "failed", error=safe_error)
        try:
            update_db_status(
                cfg.database.url,
                run_id,
                "failed",
                error_msg=safe_error,
                only_if_not_terminal=True,
            )
        except Exception:
            logger.exception("Failed to write failed status for %s", run_id)
        return 1
    finally:
        if _terminalizing:
            try:
                r.delete(terminalizing_key)
            except Exception:
                logger.exception("Failed to delete terminalizing marker for %s", run_id)
        _current_run_id = None
        _current_r = None
        _current_db_url = None
        _terminalizing = False


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
    """Execute a single walk-forward fold, return result via stdout JSON.

    stdout protocol (single line, only output ever written to stdout):
      slim mode (default):
        success: {"status": "ok", "fitness": <float>, "metrics": {...}}
        failure: {"status": "fail", "error": "<message>"}
      full mode (result_mode="full" in fold config):
        success: {"status": "ok", "result": <sanitized full BacktestRunner result>}
        failure: {"status": "fail", "error": "<message>"}

    The result_mode field in the fold config selects the protocol variant.
    All other logging goes to stderr via the logger configured in main().
    Returns exit code: 0 = success, 1 = failure.
    """
    try:
        with open(fold_config_path) as f:
            cfg_dict = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "error": f"config read: {exc}"}), flush=True)
        return 1

    result_mode = cfg_dict.get("result_mode", "slim")

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

        if result_mode == "full":
            # Sanitize NaN/Infinity before serialization — PostgreSQL JSONB
            # rejects non-finite numbers (see CLAUDE.md Data & Serialization).
            sanitized = sanitize_for_json(result)
            print(
                json.dumps({"status": "ok", "result": sanitized}, default=str),
                flush=True,
            )
        else:
            # slim (default): only fitness + numeric metrics for trial loops
            fitness = extract_fitness(result, cfg_dict["fitness_objective"])
            metrics = result.get("statistics", {})
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
