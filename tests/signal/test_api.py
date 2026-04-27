"""Integration tests for ``/api/signal/*`` endpoints.

Uses FastAPI ``TestClient`` + mocked DB / Redis — no real I/O.

Endpoint coverage
-----------------
- ``GET  /api/signal/list``           — returns registry contents
- ``POST /api/signal/run``            — DB INSERT + Redis LPUSH
- ``GET  /api/signal/runs``           — pagination + status filter
- ``GET  /api/signal/report/{run_id}``— 200 if completed, 404 if absent
- ``POST /api/signal/cancel/{run_id}``— sets cancel flag
- ``POST /api/signal/compare``        — multi-run scorecard
- ``GET  /api/signal/export/{run_id}``— full snapshot for completed runs
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinohelm.api.routes import signal as signal_routes


# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

test_app = FastAPI()
test_app.include_router(signal_routes.router)


# ---------------------------------------------------------------------------
# Mock DB / Redis helpers
# ---------------------------------------------------------------------------

def _make_async_db_session(rows=None, scalar_one_or_none=None):
    """Build a mock AsyncSession.

    ``rows`` populates ``execute().scalars().all()``;
    ``scalar_one_or_none`` populates ``execute().scalar_one_or_none()``.
    """
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows or []
    result.scalar_one_or_none.return_value = scalar_one_or_none
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


async def _override_get_db_default():
    yield _make_async_db_session()


async def _override_get_redis_default():
    rds = AsyncMock()
    rds.lpush = AsyncMock(return_value=1)
    rds.set = AsyncMock(return_value=True)
    return rds


@pytest.fixture()
def client():
    from tinohelm.api.deps import get_db, get_redis

    test_app.dependency_overrides[get_db] = _override_get_db_default
    test_app.dependency_overrides[get_redis] = _override_get_redis_default
    with TestClient(test_app) as c:
        yield c
    test_app.dependency_overrides.clear()


@pytest.fixture()
def temp_signal_module(tmp_path, monkeypatch):
    """Install a temporary ``signals_dir`` containing one decorated kernel.

    The :class:`SignalRegistry` defaults its scan dir to ``paths.get("signals_dir")``,
    so we override the path registry for the duration of the test.
    """
    from tinohelm.core.paths import paths as _paths

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir()
    (signals_dir / "user_sig.py").write_text(
        '"""User signal fixture for /api/signal/list test."""\n'
        "from tinohelm.signal import signal\n"
        "@signal(\n"
        '    name="my_user_signal",\n'
        '    factor_ref="ret_N@1.0.0",\n'
        '    method="top_k_long_short",\n'
        '    rebalance_freq="1D",\n'
        '    universe_ref="top10_perp",\n'
        '    method_params={"k": 3},\n'
        ")\n"
        "def my_kernel(factor_panel):\n"
        "    return factor_panel\n"
    )
    _paths.override("signals_dir", signals_dir)
    yield signals_dir
    _paths.reset_overrides()


# ---------------------------------------------------------------------------
# 1. GET /api/signal/list
# ---------------------------------------------------------------------------

def test_list_signals_returns_registry_contents(client, temp_signal_module):
    """``/list`` discovers signals from the configured ``signals_dir``."""
    resp = client.get("/api/signal/list")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    names = [item["name"] for item in body]
    assert "my_user_signal" in names

    item = next(it for it in body if it["name"] == "my_user_signal")
    # Required summary fields are populated.
    for key in (
        "name", "version", "method", "weighting", "factor_ref",
        "universe_ref", "rebalance_freq", "gross_exposure",
        "net_exposure", "max_position", "extra_warmup_bars",
        "description", "deprecated",
    ):
        assert key in item, f"missing key {key!r} in /list response: {item}"


def test_list_signals_filters_deprecated_by_default(client, tmp_path):
    """Deprecated signals are hidden unless ``include_deprecated=true``."""
    from tinohelm.core.paths import paths as _paths

    signals_dir = tmp_path / "signals_dep"
    signals_dir.mkdir()
    (signals_dir / "dep_sig.py").write_text(
        "from tinohelm.signal import signal\n"
        "@signal(name='dep_sig', factor_ref='x@1', method='top_k_long_short',\n"
        "        rebalance_freq='1D', universe_ref='u', deprecated=True)\n"
        "def k(p): return p\n"
    )
    _paths.override("signals_dir", signals_dir)
    try:
        resp_default = client.get("/api/signal/list")
        resp_all = client.get("/api/signal/list?include_deprecated=true")
    finally:
        _paths.reset_overrides()

    names_default = {it["name"] for it in resp_default.json()}
    names_all = {it["name"] for it in resp_all.json()}
    assert "dep_sig" not in names_default
    assert "dep_sig" in names_all


# ---------------------------------------------------------------------------
# 2. POST /api/signal/run
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Universe resolution mock — shared by /run tests
# ---------------------------------------------------------------------------

def _make_universe_row(*, row_id: int = 42, name: str = "top10_perp"):
    """Build a stand-in ``UniverseORM`` row with two PIT-active symbols.

    Mirrors what ``Universe.sync_from_csv`` would have written: every
    symbol listed since 1970-01-01 (well outside the 7-day new-coin
    isolation window) so ``get_symbols_at`` always returns both.
    """
    row = MagicMock()
    row.id = row_id
    row.name = name
    row.pit_rules_json = {
        "BTCUSDT-PERP": {
            "listing_date": "2020-01-01",
            "delisting_date": None,
        },
        "ETHUSDT-PERP": {
            "listing_date": "2020-01-01",
            "delisting_date": None,
        },
    }
    return row


def _make_run_db_session(universe_row):
    """Build an AsyncSession whose ``.execute`` returns ``universe_row``."""
    session = AsyncMock()
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = universe_row
    session.execute = AsyncMock(return_value=select_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def test_run_creates_db_row_and_pushes_queue(client, temp_signal_module):
    """``/run`` inserts a SignalRun row + LPUSHes a payload to Redis."""
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)
    mock_session = _make_run_db_session(_make_universe_row())

    async def _db():
        yield mock_session

    async def _rds():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db
    test_app.dependency_overrides[get_redis] = _rds
    try:
        resp = client.post(
            "/api/signal/run",
            json={
                "signal_name": "my_user_signal",
                "universe_id": 42,
                "start": "2024-01-01",
                "end": "2024-04-01",
            },
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default
        test_app.dependency_overrides[get_redis] = _override_get_redis_default

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "queued"

    # DB add() called exactly once with a SignalRun instance carrying the
    # correct signal_name / status / config snapshot.
    mock_session.add.assert_called_once()
    inserted = mock_session.add.call_args[0][0]
    assert inserted.signal_name == "my_user_signal"
    assert inserted.status == "queued"
    assert inserted.config["start"] == "2024-01-01"
    assert inserted.config["method"] == "top_k_long_short"
    # Universe resolution populated instrument_ids + bar_type_template.
    assert inserted.config["instrument_ids"] == [
        "BTCUSDT-PERP.BINANCE",
        "ETHUSDT-PERP.BINANCE",
    ]
    assert (
        inserted.config["bar_type_template"]
        == "{instrument_id}-1-DAY-LAST-EXTERNAL"  # rebalance_freq="1D" → 1-DAY
    )
    assert inserted.universe_id == 42
    mock_session.commit.assert_called()

    # Redis LPUSH on tino:signal:queue with run_id + signal_name in payload.
    mock_rds.lpush.assert_called_once()
    args = mock_rds.lpush.call_args
    assert args[0][0] == "tino:signal:queue"
    payload = json.loads(args[0][1])
    assert payload["run_id"] == body["run_id"]
    assert payload["signal_name"] == "my_user_signal"
    assert "config" in payload


def test_run_resolves_universe_from_universe_id(client, temp_signal_module):
    """``/run`` looks up universe by id (primary path) and writes instrument_ids."""
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)
    mock_session = _make_run_db_session(_make_universe_row(row_id=7))

    async def _db():
        yield mock_session

    async def _rds():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db
    test_app.dependency_overrides[get_redis] = _rds
    try:
        resp = client.post(
            "/api/signal/run",
            json={"signal_name": "my_user_signal", "universe_id": 7},
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default
        test_app.dependency_overrides[get_redis] = _override_get_redis_default

    assert resp.status_code == 200, resp.text
    inserted = mock_session.add.call_args[0][0]
    assert inserted.universe_id == 7
    assert inserted.config["universe_id"] == 7
    # PIT symbols persisted alongside NT instrument_ids for the worker.
    assert inserted.config["universe_symbols"] == [
        "BTCUSDT-PERP",
        "ETHUSDT-PERP",
    ]
    assert len(inserted.config["instrument_ids"]) == 2


def test_run_rejects_unresolvable_universe(client, temp_signal_module):
    """``/run`` returns 422 when neither universe_id nor universe_ref resolves."""
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)
    # Session returns None for every .execute(SELECT ...) — no universe row.
    mock_session = _make_run_db_session(universe_row=None)

    async def _db():
        yield mock_session

    async def _rds():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db
    test_app.dependency_overrides[get_redis] = _rds
    try:
        resp = client.post(
            "/api/signal/run",
            json={"signal_name": "my_user_signal", "universe_id": 999},
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default
        test_app.dependency_overrides[get_redis] = _override_get_redis_default

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "universe" in detail.lower()


def test_run_rejects_empty_pit_universe(client, temp_signal_module):
    """``/run`` returns 422 when the universe row has zero active symbols.

    Simulates a universe whose members are all still inside the 7-day
    new-coin isolation window at the anchor time → :meth:`get_symbols_at`
    returns an empty list.  The helper raises ``ValueError`` which the
    route translates to HTTP 422 so callers see the root cause without
    paying for a worker round-trip.
    """
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)
    # Universe has one symbol but its listing_date is AFTER req.end —
    # i.e. not yet trading at the PIT anchor.
    future_row = MagicMock()
    future_row.id = 1
    future_row.name = "future_perp"
    future_row.pit_rules_json = {
        "NEWCOINUSDT-PERP": {
            "listing_date": "2099-01-01",
            "delisting_date": None,
        },
    }
    mock_session = _make_run_db_session(future_row)

    async def _db():
        yield mock_session

    async def _rds():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db
    test_app.dependency_overrides[get_redis] = _rds
    try:
        resp = client.post(
            "/api/signal/run",
            json={
                "signal_name": "my_user_signal",
                "universe_id": 1,
                "end": "2024-01-01",
            },
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default
        test_app.dependency_overrides[get_redis] = _override_get_redis_default

    assert resp.status_code == 422, resp.text
    assert "empty" in resp.json()["detail"].lower()


def test_run_404_unknown_signal(client, tmp_path):
    """``/run`` with an unknown signal_name returns 404."""
    from tinohelm.core.paths import paths as _paths

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    _paths.override("signals_dir", empty_dir)
    try:
        resp = client.post(
            "/api/signal/run",
            json={"signal_name": "nonexistent_signal_xyz"},
        )
    finally:
        _paths.reset_overrides()
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. GET /api/signal/runs
# ---------------------------------------------------------------------------

def test_list_runs_empty_returns_pagination_envelope(client):
    """``/runs`` returns an envelope ``{runs, page, page_size}`` even when empty."""
    resp = client.get("/api/signal/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs"] == []
    assert body["page"] == 1
    assert body["page_size"] == 20


def test_list_runs_with_data_includes_progress_stage(client):
    """``/runs`` surfaces ``progress_stage`` from the SignalRun row."""
    from datetime import datetime

    row = MagicMock()
    row.id = "sig-run-abc"
    row.signal_name = "my_sig"
    row.factor_ref = "ret_N@1.0.0"
    row.status = "running"
    row.progress = 70
    row.progress_stage = "evaluating"
    row.error = None
    row.created_at = datetime(2024, 1, 1, 0, 0, 0)
    row.started_at = datetime(2024, 1, 1, 0, 1, 0)
    row.finished_at = None

    session = _make_async_db_session(rows=[row])

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/runs")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    body = resp.json()
    assert resp.status_code == 200
    assert len(body["runs"]) == 1
    item = body["runs"][0]
    assert item["run_id"] == "sig-run-abc"
    assert item["progress_stage"] == "evaluating"
    assert item["status"] == "running"


def test_list_runs_with_status_filter(client):
    """``/runs?status=completed&page=2`` is accepted."""
    resp = client.get("/api/signal/runs?status=completed&page=2&page_size=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2
    assert body["page_size"] == 5


# ---------------------------------------------------------------------------
# 4. GET /api/signal/report/{run_id}
# ---------------------------------------------------------------------------

def test_get_report_404_when_absent(client):
    resp = client.get("/api/signal/report/does-not-exist")
    assert resp.status_code == 404


def test_get_report_running_returns_progress_stage(client):
    """Reports for in-flight runs include ``progress_stage`` but no ``result``."""
    row = MagicMock()
    row.id = "rid-running"
    row.signal_name = "sig"
    row.factor_ref = "ret_N@1.0.0"
    row.status = "running"
    row.progress = 40
    row.progress_stage = "computing"
    row.error = None

    session = _make_async_db_session(scalar_one_or_none=row)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/report/rid-running")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200
    body = resp.json()
    assert body["progress_stage"] == "computing"
    assert "result" not in body  # only completed runs surface ``result``


def test_get_report_completed_returns_full_result(client):
    """Completed report carries the full result_json blob."""
    row = MagicMock()
    row.id = "rid-done"
    row.signal_name = "sig"
    row.factor_ref = "ret_N@1.0.0"
    row.status = "completed"
    row.progress = 100
    row.progress_stage = "persisting"
    row.error = None
    row.result = {"sharpe": 1.5, "mdd": 0.07, "turnover_annualized": 12.0}

    session = _make_async_db_session(scalar_one_or_none=row)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/report/rid-done")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["sharpe"] == 1.5


# ---------------------------------------------------------------------------
# 5. POST /api/signal/cancel/{run_id}
# ---------------------------------------------------------------------------

def test_cancel_sets_redis_flag(client):
    """``/cancel/{run_id}`` does ``SET tino:signal:cancel:{run_id} 1 EX <ttl>``."""
    rds = AsyncMock()
    rds.set = AsyncMock(return_value=True)

    async def _rds():
        return rds

    from tinohelm.api.deps import get_redis
    test_app.dependency_overrides[get_redis] = _rds
    try:
        resp = client.post("/api/signal/cancel/some-run-id")
    finally:
        test_app.dependency_overrides[get_redis] = _override_get_redis_default

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "some-run-id"
    assert body["cancel_set"] is True

    rds.set.assert_called_once()
    # First positional arg is the cancel key.
    assert rds.set.call_args[0][0] == "tino:signal:cancel:some-run-id"
    # ``ex=`` kwarg holds the TTL.
    assert rds.set.call_args.kwargs.get("ex") is not None


# ---------------------------------------------------------------------------
# 6. POST /api/signal/compare
# ---------------------------------------------------------------------------

def _completed_run(run_id: str, name: str, sharpe: float, mdd: float, turnover: float):
    row = MagicMock()
    row.id = run_id
    row.signal_name = name
    row.factor_ref = "ret_N@1.0.0"
    row.status = "completed"
    row.result = {
        "sharpe": sharpe,
        "mdd": mdd,
        "turnover_annualized": turnover,
        "capacity_score": 0.5,
        "tail_loss_p99": -0.02,
        "total_return": sharpe * 0.1,
        "cost_drag": 0.01,
    }
    return row


def test_compare_returns_ranking_heatmap(client):
    """``/compare`` produces an F×M ranking heatmap with default 3 metrics."""
    runs = [
        _completed_run("r1", "sig_a", sharpe=1.5, mdd=0.10, turnover=12.0),
        _completed_run("r2", "sig_b", sharpe=2.0, mdd=0.05, turnover=20.0),
        _completed_run("r3", "sig_c", sharpe=0.5, mdd=0.20, turnover=8.0),
    ]
    session = _make_async_db_session(rows=runs)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.post(
            "/api/signal/compare",
            json={"run_ids": ["r1", "r2", "r3"]},
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metrics"] == ["sharpe", "mdd", "turnover_annualized"]
    table = body["ranking_heatmap"]
    assert table["factors"] == ["sig_a", "sig_b", "sig_c"]
    assert len(table["values"]) == 3
    # sig_b has highest Sharpe → rank 1; sig_c has lowest → rank 3.
    sharpe_col = [row[0] for row in table["rankings"]]
    assert sharpe_col == [2, 1, 3]
    # MDD is lower-is-better — sig_b has smallest mdd (0.05) → rank 1.
    mdd_col = [row[1] for row in table["rankings"]]
    assert mdd_col == [2, 1, 3]


def test_compare_400_when_runs_not_completed(client):
    """``/compare`` rejects requests where any run isn't completed."""
    runs = [_completed_run("r1", "sig_a", 1.5, 0.10, 12.0)]
    pending = MagicMock()
    pending.id = "r2"
    pending.signal_name = "sig_b"
    pending.status = "running"
    pending.result = None
    runs.append(pending)

    session = _make_async_db_session(rows=runs)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.post(
            "/api/signal/compare",
            json={"run_ids": ["r1", "r2"]},
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 400
    assert "not completed" in resp.json()["detail"]


def test_compare_404_when_runs_missing(client):
    """``/compare`` 404s when one or more run_ids don't exist in DB."""
    runs = [_completed_run("r1", "sig_a", 1.5, 0.10, 12.0)]
    session = _make_async_db_session(rows=runs)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.post(
            "/api/signal/compare",
            json={"run_ids": ["r1", "r-missing"]},
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 404
    assert "r-missing" in resp.json()["detail"]


def test_compare_invalid_metric_returns_422(client):
    """``/compare`` 422s when caller passes an unsupported metric."""
    resp = client.post(
        "/api/signal/compare",
        json={"run_ids": ["r1", "r2"], "metrics": ["sharpe", "not_a_metric"]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. GET /api/signal/export/{run_id}
# ---------------------------------------------------------------------------

def test_export_returns_portfolio_yaml_shape(client):
    """``/export`` returns portfolio.yaml-compatible JSON with strategy_class."""
    row = MagicMock()
    row.id = "rid-export"
    row.signal_name = "sig"
    row.factor_ref = "ret_N@1.0.0"
    row.status = "completed"
    row.code_hash = "abc123"
    row.config = {
        "method": "top_k_long_short",
        "method_params": {"k": 3},
        "rebalance_freq": "1h",
        "extra_warmup_bars": 5,
        "factor_ref": "ret_N@1.0.0",
        # PR #140 — universe resolution writes these into config.
        "instrument_ids": [
            "BTCUSDT-PERP.BINANCE",
            "ETHUSDT-PERP.BINANCE",
        ],
        "bar_type_template": "{instrument_id}-1-HOUR-LAST-EXTERNAL",
        "universe_symbols": ["BTCUSDT-PERP", "ETHUSDT-PERP"],
    }
    row.result = {"sharpe": 1.5, "mdd": 0.07}

    from datetime import datetime
    row.started_at = datetime(2024, 1, 1, 0, 0, 0)
    row.finished_at = datetime(2024, 1, 1, 0, 5, 0)

    session = _make_async_db_session(scalar_one_or_none=row)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db

    # Mock factor registry so test does not need real factor files.
    mock_factor_spec = MagicMock()
    mock_factor_spec.lookback = 20
    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = mock_factor_spec
    mock_registry_class = MagicMock(return_value=mock_registry)

    with patch(
        "tinohelm.factor.registry.Registry",
        mock_registry_class,
    ):
        try:
            resp = client.get("/api/signal/export/rid-export")
        finally:
            test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level portfolio.yaml-compatible shape.
    assert body["strategy_class"] == (
        "tinohelm.nt_adapter.signal_driven_strategy:SignalDrivenStrategy"
    )
    assert "config" in body
    assert "metadata" in body

    cfg = body["config"]
    assert cfg["signal_name"] == "sig"
    # warmup_bars = factor_lookback(20) + extra_warmup_bars(5) = 25
    assert cfg["warmup_bars"] == 25

    meta = body["metadata"]
    assert meta["exported_from_run_id"] == "rid-export"
    assert meta["factor_lookback"] == 20
    assert meta["extra_warmup_bars"] == 5
    assert meta["warmup_bars_derived"] == 25


def test_export_400_when_not_completed(client):
    """``/export`` 400s for runs that haven't completed yet."""
    row = MagicMock()
    row.id = "rid-running"
    row.status = "running"
    session = _make_async_db_session(scalar_one_or_none=row)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/export/rid-running")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 400


def test_export_404_when_absent(client):
    resp = client.get("/api/signal/export/missing-run")
    assert resp.status_code == 404
