"""Tests for standalone data worker entry point."""
from pathlib import Path


def test_data_worker_main_uses_asyncio_run():
    source = Path("src/tinohelm/data/worker_main.py").read_text()

    assert "asyncio.run(run_worker())" in source


def test_data_worker_main_recovers_before_starting_consumers():
    source = Path("src/tinohelm/data/worker_main.py").read_text()

    recover_call = source.index("await recover_interrupted_jobs(redis_client)")
    start_call = source.index("worker_task = start_data_worker(")

    assert recover_call < start_call
    assert "catalog_root = get_active_catalog_root(settings)" in source
    assert "await stop_data_worker_and_wait(timeout=30.0)" in source


def test_compose_has_standalone_data_worker_service():
    source = Path("docker-compose.yml").read_text()

    assert "  data-worker:\n" in source
    assert 'command: ["python", "-m", "tinohelm.data.worker_main"]' in source
