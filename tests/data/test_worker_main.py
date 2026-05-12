"""Tests for standalone data worker entry point."""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import yaml


def test_run_worker_recovers_before_starting_consumers(monkeypatch, tmp_path: Path) -> None:
    from tinohelm.data.worker_main import run_worker

    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://unit-test"))
    redis_client = AsyncMock()
    events: list[tuple] = []

    async def recover(client) -> None:
        events.append(("recover", client))

    loop = asyncio.new_event_loop()
    worker_task: asyncio.Future[None] = loop.create_future()
    worker_task.set_result(None)
    loop.close()

    def start_worker(*, redis_url: str, catalog_path: str):
        events.append(("start", redis_url, catalog_path))
        return worker_task

    async def stop_worker(*, timeout: float = 30.0) -> None:
        events.append(("stop", timeout))

    monkeypatch.setattr("tinohelm.data.worker_main.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.data.worker_main.get_active_catalog_root", lambda _settings: tmp_path)
    monkeypatch.setattr("tinohelm.data.worker_main.aioredis.from_url", lambda _url: redis_client)
    monkeypatch.setattr("tinohelm.data.worker_main.recover_interrupted_jobs", recover)
    monkeypatch.setattr("tinohelm.data.worker_main.start_data_worker", start_worker)
    monkeypatch.setattr("tinohelm.data.worker_main.stop_data_worker_and_wait", stop_worker)

    asyncio.run(run_worker())

    assert events == [
        ("recover", redis_client),
        ("start", "redis://unit-test", str(tmp_path)),
        ("stop", 30.0),
    ]
    redis_client.close.assert_awaited_once()


def test_main_installs_sigterm_handler_and_cancels_loop_tasks(monkeypatch) -> None:
    import tinohelm.data.worker_main as worker_main

    fake_loop = MagicMock()
    registered: dict[str, object] = {}
    cancelled_tasks = [MagicMock(), MagicMock()]

    def add_signal_handler(sig, handler) -> None:
        registered["signal"] = sig
        registered["handler"] = handler

    async def fake_run_worker() -> None:
        return None

    def run_until_complete(coro) -> None:
        registered["coroutine"] = coro
        coro.close()

    set_event_loop = MagicMock()
    fake_loop.add_signal_handler.side_effect = add_signal_handler
    fake_loop.run_until_complete.side_effect = run_until_complete

    monkeypatch.setattr(worker_main.logging, "basicConfig", MagicMock())
    monkeypatch.setattr(worker_main.asyncio, "new_event_loop", lambda: fake_loop)
    monkeypatch.setattr(worker_main.asyncio, "set_event_loop", set_event_loop)
    monkeypatch.setattr(worker_main.asyncio, "all_tasks", lambda loop: set(cancelled_tasks))
    monkeypatch.setattr(worker_main, "run_worker", fake_run_worker)

    worker_main.main()

    assert registered["signal"] == signal.SIGTERM
    assert registered["coroutine"] is not None
    set_event_loop.assert_called_once_with(fake_loop)
    registered["handler"]()
    for task in cancelled_tasks:
        task.cancel.assert_called_once()
    fake_loop.close.assert_called_once()


def test_compose_has_standalone_data_worker_service() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text())

    data_worker = compose["services"]["data-worker"]
    assert data_worker["command"] == ["python", "-m", "tinohelm.data.worker_main"]


def test_compose_api_and_data_worker_share_storage_roots() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text())

    api = compose["services"]["api"]
    data_worker = compose["services"]["data-worker"]

    assert api["volumes"] == data_worker["volumes"]
    for key in (
        "TINO_DATABASE__URL",
        "TINO_REDIS__URL",
        "TINO_PATHS__STRATEGIES",
        "TINO_PATHS__ACTORS",
        "TINO_PATHS__CATALOG",
        "TINO_PATHS__ARTIFACTS",
        "TINO_PATHS__FUNDING_RATES",
        "TINO_PATHS__DATA_CACHE",
        "TINO_STORAGE__PROVIDER",
        "TINO_STORAGE__TOS__REGION",
        "TINO_STORAGE__TOS__BUCKET",
        "TINO_STORAGE__TOS__PREFIX",
        "TINO_STORAGE__TOS__ENDPOINT",
        "TINO_STORAGE__TOS__ACCESS_KEY",
        "TINO_STORAGE__TOS__SECRET_KEY",
        "TINO_STORAGE__TOS__SECURITY_TOKEN",
        "TINO_STORAGE__TOS__STAGING_DIR",
        "TINO_PATHS__FACTOR_CACHE",
        "TINO_PATHS__LOGS",
        "TINO_PATHS__RESEARCH",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "HOST_HOME",
    ):
        assert api["environment"][key] == data_worker["environment"][key]
