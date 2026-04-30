"""Unit and integration tests for tinohelm.backtest.runner_cli."""
from __future__ import annotations

import asyncio as _asyncio
import io
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal job payload used across multiple tests
# ---------------------------------------------------------------------------

_MINIMAL_JOB = {
    "run_id": "r-1",
    "strategy_path": "fake/strat.py:FakeStrategy",
    "config_path": "fake/strat.py:FakeStrategyConfig",
    "symbol": "BTCUSDT-PERP",
    "interval": "1m",
    "start": "2025-01-01T00:00:00",
    "end": "2025-02-01T00:00:00",
}

_FAKE_STATS = {
    "total_trades": 10,
    "pnl_total": 500.0,
    "win_rate": 0.6,
}

_FAKE_RESULTS = {
    "statistics": _FAKE_STATS,
    "trade_log": [],
    "equity_curve": [],
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_settings(tmp_path: Path) -> MagicMock:
    """Return a mock settings object with temp paths and dummy URLs."""
    settings = MagicMock()
    settings.redis.url = "redis://localhost:6379"
    settings.database.url = "sqlite:///:memory:"
    settings.paths.catalog = tmp_path / "catalog"
    settings.paths.artifacts = tmp_path / "artifacts"
    return settings


def _mock_redis() -> MagicMock:
    """Return a mock Redis client; r.get returns None (no cancel flag)."""
    r = MagicMock()
    r.get.return_value = None
    return r


def _sync_run(coro):
    """Synchronously run a coroutine in a fresh event loop (test helper)."""
    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# T1: test_run_id_success
# ---------------------------------------------------------------------------

def test_run_id_success(tmp_path, monkeypatch):
    """_run_queue_mode returns 0 and calls update_db_status with status='completed'."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)

    # Inject job payload via stdin
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))

    # Patch events helpers
    mock_update_db = MagicMock()
    mock_publish_completed = MagicMock()
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", mock_update_db)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", mock_publish_completed)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)

    # Patch asyncio.run to execute coroutines synchronously
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    # BacktestRunner is imported lazily: from tinohelm.backtest.runner import BacktestRunner
    # Patch the class at its source module so the lazy import resolves to our mock.
    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0, f"Expected exit code 0, got {rc}"

    # update_db_status must be called with status="completed" at some point
    completed_calls = [
        c for c in mock_update_db.call_args_list
        if len(c.args) >= 3 and c.args[2] == "completed"
    ]
    assert completed_calls, (
        f"update_db_status never called with status='completed'. "
        f"All calls: {mock_update_db.call_args_list}"
    )


def test_run_id_success_stores_summary_pointer_not_full_result(tmp_path, monkeypatch):
    """Queue mode should not duplicate full result payloads into Redis."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    heavy_results = {
        "statistics": _FAKE_STATS,
        "trade_log": [{"trade_id": f"t-{i}", "pnl": i} for i in range(100)],
        "equity_curve": [{"ts": i, "equity": 10000 + i} for i in range(100)],
    }
    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=heavy_results)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    result_setex_calls = [
        c for c in r.setex.call_args_list
        if c.args and c.args[0] == "tino:backtest:result:r-1"
    ]
    assert len(result_setex_calls) == 1
    payload = json.loads(result_setex_calls[0].args[2])
    assert payload["status"] == "completed"
    assert payload["summary"] == _FAKE_STATS
    assert payload["artifact_path"].endswith("results.json")
    assert "trade_log" not in payload
    assert "equity_curve" not in payload


def test_run_id_cancel_after_engine_run_does_not_publish_completed(tmp_path, monkeypatch):
    """A cancel flag observed after engine completion wins before terminal writes."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    # First get() is pre-run cancel check; second is post-engine/pre-terminal.
    r.get.side_effect = [None, b"1"]

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    mock_update_db = MagicMock()
    mock_publish_completed = MagicMock()
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", mock_update_db)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", mock_publish_completed)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 2
    statuses = [c.args[2] for c in mock_update_db.call_args_list if len(c.args) >= 3]
    assert "cancelled" in statuses
    assert "completed" not in statuses
    completed_statuses = [
        c.args[2] for c in mock_publish_completed.call_args_list if len(c.args) >= 3
    ]
    assert completed_statuses == ["cancelled"]


def test_sigterm_during_terminalization_does_not_publish_cancel(monkeypatch):
    """Once finalization starts, SIGTERM must not create completed+cancelled."""
    from tinohelm.backtest import runner_cli

    mock_update_db = MagicMock()
    mock_publish_completed = MagicMock()
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", mock_update_db)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", mock_publish_completed)
    monkeypatch.setattr(runner_cli, "_current_run_id", "r-1")
    monkeypatch.setattr(runner_cli, "_current_r", MagicMock())
    monkeypatch.setattr(runner_cli, "_current_db_url", "sqlite:///:memory:")
    monkeypatch.setattr(runner_cli, "_terminalizing", True)

    runner_cli._handle_sigterm(signal.SIGTERM, None)

    mock_update_db.assert_not_called()
    mock_publish_completed.assert_not_called()


def test_run_id_success_deletes_late_cancel_key_after_terminalization(tmp_path, monkeypatch):
    """A cancel key set after the final pre-terminal check is cleaned on success."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    r.get.side_effect = [None, None]

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    r.delete.assert_any_call("tino:backtest:cancel:r-1")


def test_run_id_success_marks_terminalizing_for_parent_cancel_watcher(tmp_path, monkeypatch):
    """Child advertises finalization so the parent cancel watcher never escalates."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    r.get.side_effect = [None, None]

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    r.setex.assert_any_call("tino:backtest:terminalizing:r-1", 60, "1")
    r.delete.assert_any_call("tino:backtest:terminalizing:r-1")


def test_run_id_sets_terminalization_callback_before_runner_artifacts(tmp_path, monkeypatch):
    """runner_cli must let BacktestRunner mark terminalization before internal exports."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    r.get.side_effect = [None, None]
    callback_seen: list[bool] = []

    class FakeRunner:
        def __init__(self, **_kwargs):
            self.artifacts_dir = None
            self._redis_client = None
            self._run_id = ""
            self._job_start_time = 0.0

        async def run(self):
            callback = getattr(self, "_before_artifact_export", None)
            assert callable(callback), "runner_cli did not install _before_artifact_export"
            callback()
            callback_seen.append(runner_cli._terminalizing)
            return _FAKE_RESULTS

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    with patch("tinohelm.backtest.runner.BacktestRunner", FakeRunner):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    assert callback_seen == [True]
    r.setex.assert_any_call("tino:backtest:terminalizing:r-1", 60, "1")


def test_post_completion_publish_error_does_not_overwrite_completed(tmp_path, monkeypatch):
    """After DB status is completed, Redis event cleanup failures are best-effort."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    r.get.side_effect = [None, None]
    update_db_status = MagicMock()

    def _publish_completed(_r, _run_id, status, **_kwargs):
        if status == "completed":
            raise RuntimeError("redis publish down")

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", update_db_status)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", _publish_completed)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    statuses = [c.args[2] for c in update_db_status.call_args_list if len(c.args) >= 3]
    assert "completed" in statuses
    assert "failed" not in statuses


@pytest.mark.parametrize("failure_point", ["stats", "progress100", "result_pointer"])
def test_post_artifact_redis_side_effect_failure_still_completes(
    failure_point: str,
    tmp_path,
    monkeypatch,
):
    """After results.json is persisted, Redis cache/event failures must not fail the run."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()
    r.get.side_effect = [None, None]
    update_db_status = MagicMock()

    def _publish_stats(*_args, **_kwargs):
        if failure_point == "stats":
            raise RuntimeError("redis stats down")

    def _publish_progress(_r, _run_id, pct, **_kwargs):
        if failure_point == "progress100" and pct == 100:
            raise RuntimeError("redis progress down")

    def _setex(key, *_args):
        if failure_point == "result_pointer" and key == "tino:backtest:result:r-1":
            raise RuntimeError("redis result pointer down")

    r.setex.side_effect = _setex

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", update_db_status)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", _publish_progress)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", _publish_stats)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 0
    assert (settings.paths.artifacts / "r-1" / "results.json").exists()
    statuses = [c.args[2] for c in update_db_status.call_args_list if len(c.args) >= 3]
    assert "completed" in statuses
    assert "failed" not in statuses


# ---------------------------------------------------------------------------
# T2: test_run_id_failure
# ---------------------------------------------------------------------------

def test_run_id_failure(tmp_path, monkeypatch):
    """_run_queue_mode returns 1 and publish_completed is called with status='failed'."""
    from tinohelm.backtest import runner_cli

    settings = _mock_settings(tmp_path)
    r = _mock_redis()

    monkeypatch.setattr("tinohelm.backtest.runner_cli.get_settings", lambda: settings)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.redis.from_url", lambda url: r)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_MINIMAL_JOB)))

    mock_update_db = MagicMock()
    mock_publish_completed = MagicMock()
    monkeypatch.setattr("tinohelm.backtest.runner_cli.update_db_status", mock_update_db)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_completed", mock_publish_completed)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_progress", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.publish_stats", MagicMock())
    monkeypatch.setattr("tinohelm.backtest.runner_cli.sanitize_for_json", lambda x: x)
    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    # Make BacktestRunner.run raise
    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_queue_mode("r-1")

    assert rc == 1, f"Expected exit code 1, got {rc}"

    # publish_completed must be called with status="failed"
    # Signature: publish_completed(r, run_id, status, ...)
    failed_calls = [
        c for c in mock_publish_completed.call_args_list
        if len(c.args) >= 3 and c.args[2] == "failed"
    ]
    assert failed_calls, (
        f"publish_completed never called with status='failed'. "
        f"All calls: {mock_publish_completed.call_args_list}"
    )


# ---------------------------------------------------------------------------
# T3: test_run_id_cancel_sigterm (integration — real subprocess)
# ---------------------------------------------------------------------------

def _redis_available() -> bool:
    """Check if a local Redis instance is reachable for integration tests."""
    try:
        import redis as _redis
        r = _redis.from_url("redis://localhost:6379", socket_connect_timeout=1)
        r.ping()
        return True
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _redis_available(), reason="No local Redis available for integration test")
def test_run_id_cancel_sigterm(tmp_path):
    """Real subprocess: send SIGTERM mid-run and expect returncode=2.

    Requires a real Redis instance at localhost:6379 and a real PostgreSQL
    database reachable via the TINO_DATABASE__URL environment variable.
    Skipped automatically when no Redis is available.
    """
    job = {
        **_MINIMAL_JOB,
        "run_id": "r-sigterm-test",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "tinohelm.backtest.runner_cli", "--run-id", "r-sigterm-test"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Write job payload and close stdin
    proc.stdin.write(json.dumps(job).encode())
    proc.stdin.close()

    # Give subprocess a moment to start up (import + settings load) before SIGTERM
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("Subprocess did not exit within 10s after SIGTERM")

    assert proc.returncode == 2, (
        f"Expected returncode=2 (cancelled), got {proc.returncode}. "
        f"stderr: {proc.stderr.read().decode()[:500]}"
    )


# ---------------------------------------------------------------------------
# T4: test_fold_config_success
# ---------------------------------------------------------------------------

def test_fold_config_success(tmp_path, monkeypatch, capsys):
    """_run_fold_mode returns 0 and stdout last line is valid JSON with status=ok."""
    from tinohelm.backtest import runner_cli

    fold_cfg = {
        "strategy_path": "fake/strat.py:FakeStrategy",
        "config_path": "fake/strat.py:FakeStrategyConfig",
        "catalog_path": str(tmp_path / "catalog"),
        "symbol": "BTCUSDT-PERP",
        "interval": "1m",
        "start": "2025-01-01T00:00:00",
        "end": "2025-02-01T00:00:00",
        "fitness_objective": "sharpe_ratio",
    }
    cfg_path = tmp_path / "fold_config.json"
    cfg_path.write_text(json.dumps(fold_cfg))

    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FAKE_RESULTS)

    # BacktestRunner is lazily imported from tinohelm.backtest.runner
    # extract_fitness is lazily imported from tinohelm.backtest.optimizer_helpers
    with (
        patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance),
        patch("tinohelm.backtest.optimizer_helpers.extract_fitness", return_value=0.5),
    ):
        rc = runner_cli._run_fold_mode(str(cfg_path))

    assert rc == 0, f"Expected exit code 0, got {rc}"

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert lines, "No stdout output from _run_fold_mode"

    payload = json.loads(lines[-1])
    assert payload["status"] == "ok"
    assert isinstance(payload["fitness"], float)
    assert "metrics" in payload


# ---------------------------------------------------------------------------
# T5: test_fold_config_failure
# ---------------------------------------------------------------------------

def test_fold_config_failure(tmp_path, monkeypatch, capsys):
    """_run_fold_mode returns 1 and stdout last line is JSON with status=fail."""
    from tinohelm.backtest import runner_cli

    fold_cfg = {
        "strategy_path": "fake/strat.py:FakeStrategy",
        "config_path": "fake/strat.py:FakeStrategyConfig",
        "catalog_path": str(tmp_path / "catalog"),
        "symbol": "BTCUSDT-PERP",
        "interval": "1m",
        "start": "2025-01-01T00:00:00",
        "end": "2025-02-01T00:00:00",
        "fitness_objective": "sharpe_ratio",
    }
    cfg_path = tmp_path / "fold_config.json"
    cfg_path.write_text(json.dumps(fold_cfg))

    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(side_effect=RuntimeError("fold exploded"))

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_fold_mode(str(cfg_path))

    assert rc == 1, f"Expected exit code 1, got {rc}"

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert lines, "No stdout output from _run_fold_mode on failure"

    payload = json.loads(lines[-1])
    assert payload["status"] == "fail"
    assert "error" in payload
    assert "fold exploded" in payload["error"]


# ---------------------------------------------------------------------------
# T6: test_fold_config_full_mode_success
# ---------------------------------------------------------------------------

_FULL_FAKE_RESULTS = {
    "statistics": {
        "total_trades": 42,
        "sharpe_ratio": 1.5,
        "pnl_total": 1234.0,
        "win_rate": 0.65,
    },
    "equity_curve": [
        {"timestamp": "2025-01-01T00:00:00", "equity": 10000.0},
        {"timestamp": "2025-01-02T00:00:00", "equity": 10200.0},
    ],
    "daily_returns": [0.0, 0.02, -0.005, 0.01],
    "monthly_returns": [{"month": "2025-01", "return": 0.015}],
    "trade_log": [{"trade_id": "t1", "pnl": 100.0}],
}


def test_fold_config_full_mode_success(tmp_path, monkeypatch, capsys):
    """_run_fold_mode with result_mode='full' returns sanitized full result dict.

    Assertions:
      - exit code 0
      - stdout is single-line JSON with status=ok
      - payload["result"]["equity_curve"] is a non-empty list
      - payload["result"]["daily_returns"] is a non-empty list
      - payload["result"]["statistics"] is a dict
    """
    from tinohelm.backtest import runner_cli

    fold_cfg = {
        "strategy_path": "fake/strat.py:FakeStrategy",
        "config_path": "fake/strat.py:FakeStrategyConfig",
        "catalog_path": str(tmp_path / "catalog"),
        "symbol": "BTCUSDT-PERP",
        "interval": "1m",
        "start": "2025-01-01T00:00:00",
        "end": "2025-02-01T00:00:00",
        "fitness_objective": "sharpe_ratio",
        "result_mode": "full",
    }
    cfg_path = tmp_path / "fold_config_full.json"
    cfg_path.write_text(json.dumps(fold_cfg))

    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=_FULL_FAKE_RESULTS)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_fold_mode(str(cfg_path))

    assert rc == 0, f"Expected exit code 0, got {rc}"

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"Full mode must emit exactly 1 stdout line, got {len(lines)}: {lines}"
    )

    payload = json.loads(lines[0])
    assert payload["status"] == "ok", f"Expected status=ok, got {payload}"
    assert "result" in payload, f"Full mode payload must have 'result' key, got {list(payload)}"

    result = payload["result"]
    assert isinstance(result.get("equity_curve"), list) and len(result["equity_curve"]) > 0, (
        f"result['equity_curve'] must be a non-empty list, got: {result.get('equity_curve')}"
    )
    assert isinstance(result.get("daily_returns"), list) and len(result["daily_returns"]) > 0, (
        f"result['daily_returns'] must be a non-empty list, got: {result.get('daily_returns')}"
    )
    assert isinstance(result.get("statistics"), dict), (
        f"result['statistics'] must be a dict, got: {type(result.get('statistics'))}"
    )
    # Full mode must NOT have fitness/metrics keys (those are slim-mode only)
    assert "fitness" not in payload, "Full mode payload must not contain 'fitness'"
    assert "metrics" not in payload, "Full mode payload must not contain 'metrics'"


# ---------------------------------------------------------------------------
# T7: test_fold_config_full_mode_nan_sanitized
# ---------------------------------------------------------------------------

def test_fold_config_full_mode_nan_sanitized(tmp_path, monkeypatch, capsys):
    """Full mode: NaN/Infinity values in BacktestRunner result are sanitized
    to null in the stdout JSON (PostgreSQL JSONB rejects non-finite numbers).

    Mock BacktestRunner.run returns a result with NaN in statistics and
    Infinity in equity_curve — asserts the final JSON string contains neither
    the literal 'NaN' nor 'Infinity'.
    """
    from tinohelm.backtest import runner_cli

    nan_results = {
        "statistics": {
            "sharpe_ratio": float("nan"),
            "max_drawdown": float("inf"),
            "total_trades": 5,
        },
        "equity_curve": [
            {"timestamp": "2025-01-01T00:00:00", "equity": float("nan")},
            {"timestamp": "2025-01-02T00:00:00", "equity": 10200.0},
        ],
        "daily_returns": [float("inf"), 0.01, float("-inf")],
        "monthly_returns": [],
    }

    fold_cfg = {
        "strategy_path": "fake/strat.py:FakeStrategy",
        "config_path": None,
        "catalog_path": str(tmp_path / "catalog"),
        "symbol": "BTCUSDT-PERP",
        "interval": "1m",
        "start": "2025-01-01T00:00:00",
        "end": "2025-02-01T00:00:00",
        "fitness_objective": "sharpe_ratio",
        "result_mode": "full",
    }
    cfg_path = tmp_path / "fold_config_nan.json"
    cfg_path.write_text(json.dumps(fold_cfg))

    monkeypatch.setattr("tinohelm.backtest.runner_cli.asyncio.run", _sync_run)

    mock_runner_instance = MagicMock()
    mock_runner_instance.run = AsyncMock(return_value=nan_results)

    with patch("tinohelm.backtest.runner.BacktestRunner", return_value=mock_runner_instance):
        rc = runner_cli._run_fold_mode(str(cfg_path))

    assert rc == 0, f"Expected exit code 0, got {rc}"

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert lines, "No stdout output"

    raw_json = lines[-1]
    # PostgreSQL JSONB compatibility: no literal NaN or Infinity in the JSON string
    assert "NaN" not in raw_json, f"stdout JSON must not contain literal 'NaN': {raw_json[:200]}"
    assert "Infinity" not in raw_json, (
        f"stdout JSON must not contain literal 'Infinity': {raw_json[:200]}"
    )

    # Verify the sanitized values are null (not some other replacement)
    payload = json.loads(raw_json)
    result = payload["result"]
    assert result["statistics"]["sharpe_ratio"] is None, (
        "NaN sharpe_ratio must be sanitized to null"
    )
    assert result["statistics"]["max_drawdown"] is None, (
        "Infinity max_drawdown must be sanitized to null"
    )
