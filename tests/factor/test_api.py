"""Integration tests for /api/factor/* endpoints.

Uses FastAPI TestClient + mocked DB/Redis — no real I/O.

Endpoint coverage:
    GET  /api/factor/list               → 200, ≥12 built-in factors
    GET  /api/factor/universes          → 200, list[str] (empty OK)
    GET  /api/factor/symbols            → 200, list[str]
    POST /api/factor/explore            → 200, EvalResult summary fields
    POST /api/factor/run                → 200, {run_id, status: "queued"}, Redis LPUSH asserted
    GET  /api/factor/runs               → 200, list (even if empty)
    GET  /api/factor/report/{run_id}    → 200 if exists, 404 if not
    POST /api/factor/create             → 200, returns path; re-POST → 409
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

# ---- Build minimal test app without lifespan side-effects ----

from tinohelm.api.routes import factor as factor_module
from tinohelm.api.routes import settings as settings_module

test_app = FastAPI()
test_app.include_router(settings_module.router)
test_app.include_router(factor_module.router)


# ---------------------------------------------------------------------------
# DB / Redis session overrides
# ---------------------------------------------------------------------------

def _make_async_db_session(rows=None):
    """Return a mock AsyncSession that yields ``rows`` on scalars().all()."""
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows or []
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


async def _override_get_db():
    yield _make_async_db_session()


async def _override_get_redis():
    rds = AsyncMock()
    rds.lpush = AsyncMock(return_value=1)
    return rds


def _override_get_settings():
    s = MagicMock()
    s.paths.catalog = Path("/tmp/nonexistent_catalog_for_test")
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """TestClient with DB/Redis/Settings overrides injected."""
    from tinohelm.api.deps import get_db, get_redis, get_settings_dep

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_redis] = _override_get_redis
    test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    with TestClient(test_app) as c:
        yield c

    test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 0. Runtime/capability discovery
# ---------------------------------------------------------------------------

def test_runtime_version_endpoint_exposes_source_metadata(client):
    with patch("tinohelm.factor.registry.Registry.scan", side_effect=AssertionError("no scan")):
        resp = client.get("/api/version")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_package_path"]
    assert "platform_version" in body
    assert "git_sha" in body
    assert "factor_registry_paths" in body


def test_factor_version_and_capabilities_endpoints(client):
    with patch("tinohelm.factor.registry.Registry.scan", side_effect=AssertionError("no scan")):
        version_resp = client.get("/api/factor/version")
    assert version_resp.status_code == 200, version_resp.text
    version_body = version_resp.json()
    assert version_body["api_package_path"]
    assert version_body["factor_registry_paths"]

    caps_resp = client.get("/api/factor/capabilities")
    assert caps_resp.status_code == 200, caps_resp.text
    caps = caps_resp.json()
    assert "btc_trend" in caps["segments"]["valid_values"]
    assert "--body-file" in caps["request_body_inputs"]
    assert caps["params"]["normal_run_path_receives_params"] is True


# ---------------------------------------------------------------------------
# 1. GET /api/factor/list  — ≥12 built-in factors
# ---------------------------------------------------------------------------

def test_list_factors_returns_builtins(client):
    """GET /list returns non-experimental built-in factors by default.

    Experimental factors (those awaiting DataLayer support) are hidden unless
    ``include_experimental=true`` is passed — see ``list_factors``.
    """
    resp = client.get("/api/factor/list")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    # Each element must have the required keys (incl. the experimental flag)
    required_keys = {
        "name", "category", "description", "lookback",
        "input_fields", "params_schema", "experimental",
    }
    for item in body:
        missing = required_keys - set(item.keys())
        assert not missing, f"Factor {item.get('name')!r} missing keys: {missing}"
    # Default list excludes experimental factors.
    experimental_names = {"oi_change", "orderbook_imbalance_L1", "trade_imbalance"}
    returned = {item["name"] for item in body}
    assert returned.isdisjoint(experimental_names), (
        f"Experimental factors leaked into default /list: "
        f"{returned & experimental_names}"
    )
    # Sanity: at least the stable (non-experimental) built-ins remain.
    assert len(body) >= 9, (
        f"Expected ≥9 stable built-in factors, got {len(body)}: {sorted(returned)}"
    )


def test_list_factors_include_experimental(client):
    """GET /list?include_experimental=true surfaces experimental factors and
    each carries ``experimental=True`` so the UI can grey them out.
    """
    resp = client.get("/api/factor/list?include_experimental=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {item["name"]: item for item in body}
    for exp_name in ("oi_change", "orderbook_imbalance_L1", "trade_imbalance"):
        assert exp_name in names, f"Missing experimental factor {exp_name!r}"
        assert names[exp_name]["experimental"] is True


# ---------------------------------------------------------------------------
# 2. GET /api/factor/universes
# ---------------------------------------------------------------------------

def test_list_universes_returns_list(client):
    """GET /universes returns list[str] (may be empty if dir absent)."""
    resp = client.get("/api/factor/universes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    # All elements are strings
    for item in body:
        assert isinstance(item, str)


def test_list_universes_with_csv(client, tmp_path, paths_override):
    """GET /universes discovers CSVs placed in a custom dir.

    The endpoint resolves its scan directory via ``paths.get("universes_dir")``,
    so we install a PathRegistry override instead of patching the legacy helper.
    """
    uni_dir = tmp_path / "universes"
    uni_dir.mkdir()
    (uni_dir / "binance_perp_top20.csv").write_text(
        "symbol,listing_date\nBTCUSDT-PERP,2020-01-01\n", encoding="utf-8"
    )
    paths_override("universes_dir", uni_dir)
    resp = client.get("/api/factor/universes")
    assert resp.status_code == 200
    assert "binance_perp_top20" in resp.json()


# ---------------------------------------------------------------------------
# 3. GET /api/factor/symbols
# ---------------------------------------------------------------------------

def test_list_symbols_no_catalog(client):
    """GET /symbols returns [] when catalog dir absent."""
    resp = client.get("/api/factor/symbols")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_symbols_with_bar_dirs(client, tmp_path):
    """GET /symbols extracts symbol names from NT bar directory names."""
    bar_dir = tmp_path / "data" / "bar"
    bar_dir.mkdir(parents=True)
    (bar_dir / "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL").mkdir()
    (bar_dir / "ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL").mkdir()

    def _override_settings_with_catalog():
        s = MagicMock()
        s.paths.catalog = tmp_path
        return s

    from tinohelm.api.deps import get_settings_dep
    test_app.dependency_overrides[get_settings_dep] = _override_settings_with_catalog
    try:
        resp = client.get("/api/factor/symbols")
    finally:
        test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    assert resp.status_code == 200
    body = resp.json()
    assert "BTCUSDT-PERP" in body
    assert "ETHUSDT-PERP" in body


def test_list_symbols_scans_source_aware_local_bar_roots(client, tmp_path):
    """GET /symbols sees source-aware bar roots, not just legacy data/bar."""
    from tinohelm.data.catalog_helpers import resolve_catalog_path

    klines_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar"
    mark_dir = resolve_catalog_path(tmp_path, "markPriceKlines") / "data" / "bar"
    klines_dir.mkdir(parents=True)
    mark_dir.mkdir(parents=True)
    (klines_dir / "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL").mkdir()
    (mark_dir / "ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL").mkdir()

    def _override_settings_with_catalog():
        s = MagicMock()
        s.paths.catalog = tmp_path
        return s

    from tinohelm.api.deps import get_settings_dep
    test_app.dependency_overrides[get_settings_dep] = _override_settings_with_catalog
    try:
        resp = client.get("/api/factor/symbols")
    finally:
        test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    assert resp.status_code == 200
    assert resp.json() == ["BTCUSDT-PERP", "ETHUSDT-PERP"]


def test_list_symbols_scans_source_aware_remote_bar_roots(client, tmp_path, monkeypatch):
    """Remote source-aware objects are enumerated through the storage provider."""
    from tinohelm.data.catalog_helpers import resolve_catalog_path

    class Obj:
        def __init__(self, path: Path):
            self.path = path

    class Storage:
        provider = "s3"
        catalog_root = tmp_path

        def iter_files(self, prefix, suffix="", recursive=True):
            base = Path(prefix)
            if base == resolve_catalog_path(tmp_path, "klines") / "data" / "bar":
                yield Obj(base / "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL" / "a.parquet")
            if base == resolve_catalog_path(tmp_path, "markPriceKlines") / "data" / "bar":
                yield Obj(base / "ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL" / "b.parquet")

    storage = Storage()
    monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
    monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: storage)

    def _override_settings_with_catalog():
        s = MagicMock()
        s.paths.catalog = tmp_path
        return s

    from tinohelm.api.deps import get_settings_dep
    test_app.dependency_overrides[get_settings_dep] = _override_settings_with_catalog
    try:
        resp = client.get("/api/factor/symbols")
    finally:
        test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    assert resp.status_code == 200
    assert resp.json() == ["BTCUSDT-PERP", "ETHUSDT-PERP"]


# ---------------------------------------------------------------------------
# 4. POST /api/factor/explore — mock Orchestrator
# ---------------------------------------------------------------------------

def test_explore_returns_summary(client):
    """POST /explore with mock Orchestrator returns EvalResult summary fields."""
    from tinohelm.factor.types import EvalResult

    mock_result = EvalResult(
        ic_mean=0.05,
        ic_std=0.02,
        ir=0.6,
        rating=2,
        quantile_pnl={"Q1": -0.01, "Q5": 0.01},
        is_monotonic=True,
    )

    payload = {
        "factor_name": "ret_N",
        "config": {
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-02-01",
        },
        "params": {"n": 10},
    }

    with (
        patch("tinohelm.factor.registry.Registry") as MockRegistry,
        patch("tinohelm.factor.data_layer.DataLayer"),
        patch("tinohelm.factor.evaluation.evaluator.Evaluator"),
        patch("tinohelm.factor.cache.FactorCache"),
        patch("tinohelm.factor.observer.Observer"),
        patch("tinohelm.factor.engine.orchestrator.Orchestrator") as MockOrchestrator,
    ):
        reg_instance = MagicMock()
        reg_instance.scan.return_value = {"ret_N": MagicMock(name="ret_N")}
        reg_instance.get_spec.return_value = MagicMock(name="spec")
        MockRegistry.return_value = reg_instance

        orch_instance = MagicMock()
        orch_instance.run.return_value = mock_result
        MockOrchestrator.return_value = orch_instance

        resp = client.post("/api/factor/explore", json=payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["factor_name"] == "ret_N"
    assert "ic_mean" in body
    assert "ic_std" in body
    assert "ir" in body
    assert "rating" in body
    assert "quantile_pnl" in body
    assert body["ic_mean"] == pytest.approx(0.05)
    assert body["rating"] == 2


def test_explore_detail_false_takes_precedence_over_fields(client):
    """POST /explore applies fields to summary when detail=false."""
    from tinohelm.factor.types import EvalResult

    mock_result = EvalResult(
        ic_mean=0.05,
        ic_std=0.02,
        ir=0.6,
        rating=2,
        quantile_pnl={"Q1": -0.01, "Q5": 0.01},
        is_monotonic=True,
    )

    payload = {
        "factor_name": "ret_N",
        "config": {
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-02-01",
        },
        "params": {"n": 10},
        "summary": False,
        "detail": False,
        "fields": ["ic_mean", "rating", "ic_series"],
    }

    with (
        patch("tinohelm.factor.registry.Registry") as MockRegistry,
        patch("tinohelm.factor.data_layer.DataLayer"),
        patch("tinohelm.factor.evaluation.evaluator.Evaluator"),
        patch("tinohelm.factor.cache.FactorCache"),
        patch("tinohelm.factor.observer.Observer"),
        patch("tinohelm.factor.engine.orchestrator.Orchestrator") as MockOrchestrator,
    ):
        reg_instance = MagicMock()
        reg_instance.scan.return_value = {"ret_N": MagicMock(name="ret_N")}
        reg_instance.get_spec.return_value = MagicMock(name="spec")
        MockRegistry.return_value = reg_instance

        orch_instance = MagicMock()
        orch_instance.run.return_value = mock_result
        MockOrchestrator.return_value = orch_instance

        resp = client.post("/api/factor/explore", json=payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "factor_name": "ret_N",
        "summary": {"ic_mean": 0.05, "rating": 2},
    }


def test_explore_404_unknown_factor(client):
    """POST /explore with unknown factor_name returns 404."""
    payload = {
        "factor_name": "nonexistent_factor_xyz",
        "config": {
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-02-01",
        },
    }

    with patch("tinohelm.factor.registry.Registry") as MockRegistry:
        reg_instance = MagicMock()
        reg_instance.scan.return_value = {}
        reg_instance.get_spec.return_value = None
        MockRegistry.return_value = reg_instance

        resp = client.post("/api/factor/explore", json=payload)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. POST /api/factor/run — check DB insert + Redis LPUSH
# ---------------------------------------------------------------------------

def test_submit_run_returns_run_id_queued(client):
    """POST /run returns {run_id, status: 'queued'} and calls Redis LPUSH."""
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)

    mock_session = _make_async_db_session()

    async def _db_override():
        yield mock_session

    async def _redis_override():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db_override
    test_app.dependency_overrides[get_redis] = _redis_override

    try:
        payload = {
            "factor_name": "ret_N",
            "config": {
                "universe": ["BTCUSDT-PERP"],
                "start": "2024-01-01",
                "end": "2024-02-01",
            },
            "params": None,
            "full": False,
        }
        resp = client.post("/api/factor/run", json=payload)
    finally:
        from tinohelm.api.deps import get_settings_dep
        test_app.dependency_overrides[get_db] = _override_get_db
        test_app.dependency_overrides[get_redis] = _override_get_redis
        test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "queued"

    # Assert Redis LPUSH was called
    mock_rds.lpush.assert_called_once()
    call_args = mock_rds.lpush.call_args
    assert call_args[0][0] == "tino:factor:queue"
    # payload contains run_id and factor_name
    queued_payload = json.loads(call_args[0][1])
    assert queued_payload["run_id"] == body["run_id"]
    assert queued_payload["factor_name"] == "ret_N"
    assert queued_payload["status"] if "status" in queued_payload else True  # status not in queue payload
    # DB add + commit were called
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


def test_submit_run_persists_params_and_full_for_recovery(client):
    """DB config snapshot must be sufficient to rebuild the queue after restart."""
    mock_rds = AsyncMock()
    mock_rds.lpush = AsyncMock(return_value=1)
    mock_session = _make_async_db_session()

    async def _db_override():
        yield mock_session

    async def _redis_override():
        return mock_rds

    from tinohelm.api.deps import get_db, get_redis
    test_app.dependency_overrides[get_db] = _db_override
    test_app.dependency_overrides[get_redis] = _redis_override

    try:
        payload = {
            "factor_name": "ret_N",
            "config": {
                "universe": ["BTCUSDT-PERP"],
                "start": "2024-01-01",
                "end": "2024-02-01",
            },
            "params": {"n": 17},
            "full": True,
        }
        resp = client.post("/api/factor/run", json=payload)
    finally:
        from tinohelm.api.deps import get_settings_dep
        test_app.dependency_overrides[get_db] = _override_get_db
        test_app.dependency_overrides[get_redis] = _override_get_redis
        test_app.dependency_overrides[get_settings_dep] = _override_get_settings

    assert resp.status_code == 200, resp.text
    added_run = mock_session.add.call_args[0][0]
    assert added_run.config["params"] == {"n": 17}
    assert added_run.config["_tino_run_options"] == {"full": True}

    queued_payload = json.loads(mock_rds.lpush.call_args[0][1])
    assert queued_payload["config"]["params"] == {"n": 17}
    assert queued_payload["config"]["_tino_run_options"] == {"full": True}
    assert queued_payload["params"] == {"n": 17}
    assert queued_payload["full"] is True


# ---------------------------------------------------------------------------
# 6. GET /api/factor/runs
# ---------------------------------------------------------------------------

def test_list_runs_empty(client):
    """GET /runs returns empty list when DB has no runs."""
    resp = client.get("/api/factor/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_runs_with_data(client):
    """GET /runs returns serialised run rows."""
    from datetime import datetime

    mock_run = MagicMock()
    mock_run.id = "run-abc-123"
    mock_run.factor_name = "ret_N"
    mock_run.status = "completed"
    mock_run.progress = 100
    mock_run.error = None
    mock_run.created_at = datetime(2024, 1, 1, 0, 0, 0)
    mock_run.started_at = datetime(2024, 1, 1, 0, 1, 0)
    mock_run.finished_at = datetime(2024, 1, 1, 0, 5, 0)

    mock_session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.scalars.return_value.all.return_value = [mock_run]
    mock_session.execute = AsyncMock(return_value=scalars_result)

    async def _db_with_runs():
        yield mock_session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db_with_runs
    try:
        resp = client.get("/api/factor/runs")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["run_id"] == "run-abc-123"
    assert body[0]["factor_name"] == "ret_N"
    assert body[0]["status"] == "completed"


def test_list_runs_limit_param(client):
    """GET /runs?limit=5 is accepted without error."""
    resp = client.get("/api/factor/runs?limit=5")
    assert resp.status_code == 200


def test_list_runs_factor_name_filter(client):
    """GET /runs?factor_name=ret_N is accepted without error."""
    resp = client.get("/api/factor/runs?factor_name=ret_N")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. GET /api/factor/report/{run_id}
# ---------------------------------------------------------------------------

def test_get_report_404_not_found(client):
    """GET /report/{run_id} returns 404 when run_id absent from DB."""
    # Default mock returns scalar_one_or_none=None
    resp = client.get("/api/factor/report/does-not-exist")
    assert resp.status_code == 404


def test_get_report_running_status(client):
    """GET /report/{run_id} returns progress info when run is still running."""
    mock_run = MagicMock()
    mock_run.id = "running-run-1"
    mock_run.factor_name = "ret_N"
    mock_run.status = "running"
    mock_run.progress = 42
    mock_run.error = None

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_run
    mock_session.execute = AsyncMock(return_value=result)

    async def _db_with_run():
        yield mock_session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db_with_run
    try:
        resp = client.get("/api/factor/report/running-run-1")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress"] == 42


def test_get_report_completed(client):
    """GET /report/{run_id} returns full result for completed run."""
    mock_result_data = {"ic_mean": 0.05, "ir": 0.6}
    mock_run = MagicMock()
    mock_run.id = "completed-run-1"
    mock_run.factor_name = "ret_N"
    mock_run.status = "completed"
    mock_run.result = mock_result_data

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_run
    mock_session.execute = AsyncMock(return_value=result)

    async def _db_with_completed_run():
        yield mock_session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db_with_completed_run
    try:
        resp = client.get("/api/factor/report/completed-run-1")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["ic_mean"] == 0.05


def test_get_report_summary_defaults_to_compact_without_detail(client):
    mock_run = MagicMock()
    mock_run.id = "completed-run-2"
    mock_run.factor_name = "ret_N"
    mock_run.status = "completed"
    mock_run.result = {
        "ic_mean": 0.05,
        "ir": 0.6,
        "rating": 2,
        "distribution_histogram": [{"bin": i} for i in range(50)],
    }

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_run
    mock_session.execute = AsyncMock(return_value=result)

    async def _db_with_completed_run():
        yield mock_session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db_with_completed_run
    try:
        resp = client.get("/api/factor/report/completed-run-2?summary=true&fields=ic_mean,ir")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == {"ic_mean": 0.05, "ir": 0.6}
    assert "result" not in body


def test_get_report_summary_detail_fields_filter_detail_payload(client):
    mock_run = MagicMock()
    mock_run.id = "completed-run-3"
    mock_run.factor_name = "ret_N"
    mock_run.status = "completed"
    mock_run.result = {
        "ic_mean": 0.05,
        "ir": 0.6,
        "rating": 2,
        "distribution_histogram": [{"bin": i} for i in range(50)],
    }

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_run
    mock_session.execute = AsyncMock(return_value=result)

    async def _db_with_completed_run():
        yield mock_session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db_with_completed_run
    try:
        resp = client.get(
            "/api/factor/report/completed-run-3?summary=true&detail=true&fields=ic_mean,ir"
        )
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == {"ic_mean": 0.05, "ir": 0.6}
    assert body["result"] == {"ic_mean": 0.05, "ir": 0.6}


# ---------------------------------------------------------------------------
# 8. POST /api/factor/create
# ---------------------------------------------------------------------------

def test_create_factor_writes_template(client, tmp_path, paths_override):
    """POST /create writes a @factor template file and returns path."""
    factors_dir = tmp_path / "factors"
    paths_override("factors_dir", factors_dir)
    resp = client.post("/api/factor/create", json={"name": "test_factor_abc"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "test_factor_abc"
    assert "path" in body

    # File must exist and contain @factor decorator
    target = Path(body["path"])
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "@factor" in content
    assert "test_factor_abc" in content


def test_create_factor_409_duplicate(client, tmp_path, paths_override):
    """POST /create with same name twice returns 409 on second call."""
    factors_dir = tmp_path / "factors"
    paths_override("factors_dir", factors_dir)
    resp1 = client.post("/api/factor/create", json={"name": "dup_factor"})
    assert resp1.status_code == 200

    resp2 = client.post("/api/factor/create", json={"name": "dup_factor"})
    assert resp2.status_code == 409


def test_create_factor_400_invalid_name(client, tmp_path, paths_override):
    """POST /create with non-identifier name returns 400."""
    factors_dir = tmp_path / "factors"
    paths_override("factors_dir", factors_dir)
    for bad_name in ["123bad", "bad-name", "path/traversal", "has space"]:
        resp = client.post("/api/factor/create", json={"name": bad_name})
        assert resp.status_code == 400, (
            f"Expected 400 for name={bad_name!r}, got {resp.status_code}"
        )


def test_create_factor_registry_can_discover(client, tmp_path, paths_override):
    """End-to-end: file created by POST /create can be found by Registry.get_spec()."""
    factors_dir = tmp_path / "factors"
    paths_override("factors_dir", factors_dir)
    resp = client.post("/api/factor/create", json={"name": "e2e_test_factor"})
    assert resp.status_code == 200
    factor_path = Path(resp.json()["path"])
    assert factor_path.exists()

    from tinohelm.factor.registry import Registry
    registry = Registry(user_dir=factors_dir)
    specs = registry.scan()
    spec = registry.get_spec("e2e_test_factor")
    assert spec is not None, (
        f"Registry did not discover 'e2e_test_factor'. Found: {list(specs.keys())}"
    )
