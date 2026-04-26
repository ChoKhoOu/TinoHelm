"""Tests for the expanded GET /api/signal/export/{run_id} endpoint.

Verifies:
- strategy_class field present with correct value
- server-side warmup_bars derivation from factor registry
- 404 for unknown run_id
- 400 for non-completed run
- _parse_rebalance_to_ns helper converts common frequencies correctly
- graceful degradation when factor registry has no entry for factor_ref
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinohelm.api.routes import signal as signal_routes
from tinohelm.api.routes.signal import _parse_rebalance_to_ns

# ---------------------------------------------------------------------------
# Test application wiring
# ---------------------------------------------------------------------------

test_app = FastAPI()
test_app.include_router(signal_routes.router)


def _make_db_session(scalar_one_or_none=None):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one_or_none
    session.execute = AsyncMock(return_value=result)
    return session


async def _override_get_db_default():
    yield _make_db_session()


async def _override_get_redis_default():
    rds = AsyncMock()
    return rds


@pytest.fixture()
def client():
    from tinohelm.api.deps import get_db, get_redis

    test_app.dependency_overrides[get_db] = _override_get_db_default
    test_app.dependency_overrides[get_redis] = _override_get_redis_default
    with TestClient(test_app) as c:
        yield c
    test_app.dependency_overrides.clear()


def _mock_run(
    run_id: str = "test-run-id",
    signal_name: str = "test_signal",
    factor_ref: str = "ret_5@1.0.0",
    status: str = "completed",
    extra_warmup_bars: int = 10,
    rebalance_freq: str = "1h",
    code_hash: str = "abc",
):
    """Build a MagicMock SignalRun with the given attributes."""
    from datetime import datetime

    row = MagicMock()
    row.id = run_id
    row.signal_name = signal_name
    row.factor_ref = factor_ref
    row.status = status
    row.code_hash = code_hash
    row.config = {
        "factor_ref": factor_ref,
        "extra_warmup_bars": extra_warmup_bars,
        "rebalance_freq": rebalance_freq,
        "method": "top_k_long_short",
        "weighting": "equal",
        "universe_ref": "top10_perp",
        "method_params": {"k": 5},
        "cost_model": {"name": "taker_8bps", "fee_bps_per_side": 4.0},
        "gross_exposure": 1.0,
        "net_exposure": 0.0,
        "max_position": 0.10,
        "version": "1.0.0",
        "code_hash": code_hash,
    }
    row.result = {"sharpe": 1.2}
    row.started_at = datetime(2024, 2, 1)
    row.finished_at = datetime(2024, 2, 2)
    return row


def _mock_factor_registry(lookback: int):
    """Return a (registry_class_mock, registry_instance_mock) pair."""
    factor_spec = MagicMock()
    factor_spec.lookback = lookback

    registry_instance = MagicMock()
    registry_instance.get_spec.return_value = factor_spec

    registry_class = MagicMock(return_value=registry_instance)
    return registry_class, registry_instance


# ---------------------------------------------------------------------------
# 1. strategy_class field present
# ---------------------------------------------------------------------------

def test_export_returns_strategy_class(client):
    """export JSON must contain strategy_class exactly."""
    run = _mock_run(factor_ref="ret_5@1.0.0", extra_warmup_bars=0)
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    registry_class, _ = _mock_factor_registry(lookback=5)

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    with patch("tinohelm.factor.registry.Registry", registry_class):
        try:
            resp = client.get("/api/signal/export/test-run-id")
        finally:
            test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategy_class"] == (
        "tinohelm.nt_adapter.signal_driven_strategy:SignalDrivenStrategy"
    )


# ---------------------------------------------------------------------------
# 2. Server-side warmup_bars derivation
# ---------------------------------------------------------------------------

def test_export_derives_warmup_bars_from_factor_spec(client):
    """warmup_bars = factor_spec.lookback + signal_spec.extra_warmup_bars."""
    # factor_ref "ret_5" → lookback=5; extra_warmup_bars=10 → warmup=15
    run = _mock_run(factor_ref="ret_5@1.0.0", extra_warmup_bars=10)
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    registry_class, _ = _mock_factor_registry(lookback=5)

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    with patch("tinohelm.factor.registry.Registry", registry_class):
        try:
            resp = client.get("/api/signal/export/test-run-id")
        finally:
            test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["config"]["warmup_bars"] == 15          # 5 + 10
    assert body["metadata"]["factor_lookback"] == 5
    assert body["metadata"]["extra_warmup_bars"] == 10
    assert body["metadata"]["warmup_bars_derived"] == 15


def test_export_warmup_zero_extra(client):
    """With extra_warmup_bars=0, warmup_bars equals factor lookback alone."""
    run = _mock_run(factor_ref="ret_20@1.0.0", extra_warmup_bars=0)
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    registry_class, _ = _mock_factor_registry(lookback=20)

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    with patch("tinohelm.factor.registry.Registry", registry_class):
        try:
            resp = client.get("/api/signal/export/test-run-id")
        finally:
            test_app.dependency_overrides[get_db] = _override_get_db_default

    body = resp.json()
    assert body["config"]["warmup_bars"] == 20
    assert body["metadata"]["warmup_bars_derived"] == 20


# ---------------------------------------------------------------------------
# 3. 404 for unknown run_id
# ---------------------------------------------------------------------------

def test_export_404_for_unknown_run(client):
    """Missing run_id returns HTTP 404."""
    session = _make_db_session(scalar_one_or_none=None)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/export/00000000-0000-0000-0000-000000000000")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. 400 for non-completed run
# ---------------------------------------------------------------------------

def test_export_400_for_running_status(client):
    """status='running' → HTTP 400."""
    run = _mock_run(status="running")
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/export/test-run-id")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 400


def test_export_400_for_queued_status(client):
    """status='queued' → HTTP 400."""
    run = _mock_run(status="queued")
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    try:
        resp = client.get("/api/signal/export/test-run-id")
    finally:
        test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. Graceful degradation when factor not in registry
# ---------------------------------------------------------------------------

def test_export_warmup_falls_back_to_extra_when_factor_missing(client):
    """If factor_ref not in registry, warmup_bars = extra_warmup_bars only."""
    run = _mock_run(factor_ref="unknown_factor@1.0.0", extra_warmup_bars=7)
    session = _make_db_session(scalar_one_or_none=run)

    async def _db():
        yield session

    # Registry returns None for the factor → graceful degradation
    registry_instance = MagicMock()
    registry_instance.get_spec.return_value = None
    registry_class = MagicMock(return_value=registry_instance)

    from tinohelm.api.deps import get_db
    test_app.dependency_overrides[get_db] = _db
    with patch("tinohelm.factor.registry.Registry", registry_class):
        try:
            resp = client.get("/api/signal/export/test-run-id")
        finally:
            test_app.dependency_overrides[get_db] = _override_get_db_default

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # factor_lookback = 0 (not found), warmup_bars = 0 + 7 = 7
    assert body["config"]["warmup_bars"] == 7
    assert body["metadata"]["factor_lookback"] == 0
    assert body["metadata"]["warmup_bars_derived"] == 7


# ---------------------------------------------------------------------------
# 6. _parse_rebalance_to_ns unit tests (pure helper)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("freq, expected_ns", [
    ("1h",  3_600_000_000_000),
    ("4h", 14_400_000_000_000),
    ("1d", 86_400_000_000_000),
    ("30m",  1_800_000_000_000),
    ("5m",    300_000_000_000),
    ("60s",    60_000_000_000),
    ("1H",  3_600_000_000_000),   # case-insensitive
])
def test_parse_rebalance_to_ns(freq, expected_ns):
    assert _parse_rebalance_to_ns(freq) == expected_ns


def test_parse_rebalance_to_ns_empty_falls_back_to_1h():
    assert _parse_rebalance_to_ns("") == 3_600_000_000_000


def test_parse_rebalance_to_ns_invalid_falls_back_to_1h():
    assert _parse_rebalance_to_ns("invalid") == 3_600_000_000_000
