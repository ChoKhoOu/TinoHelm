"""Asyncio consumer pool — one subprocess per backtest, fresh NT Rust state."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime

import redis.asyncio as aioredis

from tinohelm.backtest.events import update_db_status

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


async def start_consumers(
    n: int,
    redis_url: str,
    catalog_path: str,
    artifacts_path: str,
    db_url: str,
) -> tuple[list[asyncio.Task], aioredis.Redis]:
    """Spawn *n* long-running consumer tasks.

    Each task blocks on BRPOP and, upon receiving a job, launches a fresh
    ``runner_cli`` subprocess with a cancel watcher co-task.

    Returns (consumer_tasks, redis_connection). Caller must pass rds to
    stop_consumers for cleanup.
    """
    _shutdown_event.clear()
    rds = aioredis.from_url(redis_url, decode_responses=True)

    tasks: list[asyncio.Task] = []
    for i in range(n):
        t = asyncio.create_task(
            _consumer_loop(i, rds, catalog_path, artifacts_path, db_url),
            name=f"bt-consumer-{i}",
        )
        tasks.append(t)
    logger.info("Started %d backtest consumer(s)", n)
    return tasks, rds


async def stop_consumers(
    tasks: list[asyncio.Task],
    rds: aioredis.Redis,
    *,
    timeout: float = 30.0,
) -> None:
    """Signal shutdown, wait for all consumers to finish, then close the Redis connection."""
    _shutdown_event.set()
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("Backtest consumer shutdown timed out after %.1fs", timeout)
    finally:
        await rds.aclose()
    logger.info("All backtest consumers stopped")


async def _consumer_loop(
    slot_idx: int,
    rds: aioredis.Redis,
    catalog_path: str,
    artifacts_path: str,
    db_url: str,
) -> None:
    logger.info("bt-consumer-%d started", slot_idx)
    proc: asyncio.subprocess.Process | None = None

    while not _shutdown_event.is_set():
        try:
            result = await rds.brpop("tino:backtest:queue", timeout=5)
            if result is None:
                continue
            _, job_json = result
            try:
                job = json.loads(job_json)
            except json.JSONDecodeError:
                logger.exception("Malformed job on queue (dropped): %s", job_json[:200])
                continue
            run_id = job["run_id"]
            logger.info("bt-consumer-%d picked up run %s", slot_idx, run_id)

            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "tinohelm.backtest.runner_cli",
                "--run-id", run_id,
                stdin=asyncio.subprocess.PIPE,
                stdout=None,   # inherit — logs go to parent stdout
                stderr=None,
            )
            # Write job payload to subprocess stdin with explicit drain so
            # large payloads do not stall at the pipe transport layer; then
            # close stdin to signal EOF for json.load in subprocess.
            payload_bytes = json.dumps(job).encode()
            proc.stdin.write(payload_bytes)
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()

            watcher = asyncio.create_task(
                _cancel_watcher(run_id, proc, rds),
                name=f"cancel-watcher-{run_id}",
            )
            try:
                returncode = await proc.wait()
                logger.info("run %s subprocess exited with code %d", run_id, returncode)
            finally:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

            # Parent-process safety net: converge DB status for any non-normal exit.
            # Uses only_if_not_terminal=True so we never overwrite a terminal state
            # the subprocess already wrote (atomic compare-and-swap at DB level).
            await _fallback_db_status(run_id, returncode, db_url, rds)

            proc = None

        except asyncio.CancelledError:
            # Shutdown path — give inflight subprocess a grace period
            if proc is not None and proc.returncode is None:
                logger.info("Sending SIGTERM to inflight subprocess on shutdown")
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Inflight subprocess did not exit in 10s — SIGKILL")
                    proc.kill()
                    await proc.wait()
            raise

        except Exception:
            logger.exception("bt-consumer-%d error — continuing", slot_idx)

    logger.info("bt-consumer-%d stopped", slot_idx)


async def _fallback_db_status(
    run_id: str,
    returncode: int,
    db_url: str,
    rds: aioredis.Redis,
) -> None:
    """Parent-process safety net: converge DB status for abnormal subprocess exits.

    Branches:
    - returncode == 0  : subprocess completed normally; no action (it wrote
                         its own terminal status).
    - returncode == 2  : SIGTERM handler path (cancelled); converge to
                         ``cancelled`` only if not already terminal.
    - other non-zero   : SIGKILL, OOM, native panic, etc.; converge to
                         ``failed`` with returncode in error_msg.
    """
    if returncode == 0:
        # Normal exit — subprocess is responsible for its own terminal write.
        return

    if returncode == 2:
        fallback_status = "cancelled"
        fallback_error = None
        # Idempotent cleanup: cancel key may already be deleted by _cancel_watcher
        cancel_key = f"tino:backtest:cancel:{run_id}"
        try:
            await rds.delete(cancel_key)
        except Exception:
            logger.exception("Failed to delete cancel key %s in fallback", cancel_key)
    else:
        fallback_status = "failed"
        fallback_error = f"Subprocess exited with code {returncode}"

    logger.warning(
        "run %s subprocess exited with code %d — fallback DB write: %s",
        run_id,
        returncode,
        fallback_status,
    )
    await asyncio.to_thread(
        update_db_status,
        db_url,
        run_id,
        fallback_status,
        None,
        fallback_error,
        only_if_not_terminal=True,
    )


async def _cancel_watcher(
    run_id: str,
    proc: asyncio.subprocess.Process,
    rds: aioredis.Redis,
    *,
    poll_interval: float = 2.0,
    sigterm_grace: float = 5.0,
) -> None:
    cancel_key = f"tino:backtest:cancel:{run_id}"
    while proc.returncode is None:
        try:
            flag = await rds.get(cancel_key)
            if flag:
                logger.info("Cancel flag set for %s — SIGTERM", run_id)
                await rds.delete(cancel_key)
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=sigterm_grace)
                except asyncio.TimeoutError:
                    logger.warning("Subprocess %s did not exit in %.1fs — SIGKILL",
                                   run_id, sigterm_grace)
                    proc.kill()
                    await proc.wait()
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cancel_watcher error for %s — retry in %.1fs",
                             run_id, poll_interval)
        await asyncio.sleep(poll_interval)


async def recover_interrupted_runs(rds: aioredis.Redis) -> int:
    """Re-queue backtest_runs rows stuck in 'running' state.

    Called once during lifespan startup.  Legacy rows without
    ``job_payload_json`` are marked 'failed' (cannot re-enqueue without payload).
    """
    from sqlalchemy import select

    from tinohelm.db.models import BacktestRun, RunStatus
    from tinohelm.db.session import get_session_factory

    factory = get_session_factory()
    recovered = 0
    async with factory() as db:
        rows = (await db.execute(
            select(BacktestRun).where(BacktestRun.status == RunStatus.running)
        )).scalars().all()
        for run in rows:
            if run.job_payload_json:
                run.status = RunStatus.queued
                run.error = None
                run.completed_at = None
                await rds.lpush(
                    "tino:backtest:queue",
                    json.dumps(run.job_payload_json, default=str),
                )
                recovered += 1
            else:
                run.status = RunStatus.failed
                run.error = "Interrupted by server restart (no stored payload)"
                run.completed_at = datetime.utcnow()
        await db.commit()
    if recovered:
        logger.info("Re-queued %d interrupted backtest run(s)", recovered)
    return recovered
