"""Standalone data-fetch worker entry point."""
from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as aioredis

from tinohelm.core.config import get_settings
from tinohelm.data.storage import get_active_catalog_root
from tinohelm.data.worker import recover_interrupted_jobs, start_data_worker, stop_data_worker_and_wait


async def run_worker() -> None:
    settings = get_settings()
    catalog_root = get_active_catalog_root(settings)
    redis_client = aioredis.from_url(settings.redis.url)

    try:
        await recover_interrupted_jobs(redis_client)
        worker_task = start_data_worker(
            redis_url=settings.redis.url,
            catalog_path=str(catalog_root),
        )
        await worker_task
    finally:
        await stop_data_worker_and_wait(timeout=30.0)
        await redis_client.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_sigterm() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
    try:
        loop.run_until_complete(run_worker())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
