"""Integration tests for ``POST /api/factor/cancel/{run_id}``.

Uses FastAPI ``TestClient`` + mocked Redis — no real I/O.

Coverage
--------
- Cancel endpoint sets the Redis flag with the correct key and TTL
- Response body shape: ``{run_id, status: "cancellation_requested"}``
- Endpoint is reachable (404 on unknown run_id would still set the flag)
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinohelm.api.routes import factor as factor_routes


# ---------------------------------------------------------------------------
# Test app — include only the factor router
# ---------------------------------------------------------------------------

test_app = FastAPI()
test_app.include_router(factor_routes.router)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_mock_redis() -> AsyncMock:
    rds = AsyncMock()
    rds.set = AsyncMock(return_value=True)
    return rds


async def _override_get_db_default():
    from unittest.mock import MagicMock, AsyncMock as _AM
    session = _AM()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    session.execute = _AM(return_value=result)
    session.add = MagicMock()
    session.commit = _AM()
    yield session


@pytest.fixture()
def client():
    from tinohelm.api.deps import get_db, get_redis

    mock_rds = _make_mock_redis()

    async def _override_get_redis():
        return mock_rds

    test_app.dependency_overrides[get_db] = _override_get_db_default
    test_app.dependency_overrides[get_redis] = _override_get_redis
    with TestClient(test_app) as c:
        yield c, mock_rds
    test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Cancel endpoint sets the Redis flag
# ---------------------------------------------------------------------------

def test_cancel_sets_redis_flag(client):
    """``POST /cancel/{run_id}`` sets ``tino:factor:cancel:{run_id}`` with EX TTL."""
    c, mock_rds = client
    resp = c.post("/api/factor/cancel/test-run-abc")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == "test-run-abc"
    assert body["status"] == "cancellation_requested"

    # Verify Redis SET was called with the correct key.
    mock_rds.set.assert_called_once()
    call_args = mock_rds.set.call_args
    # First positional argument is the cancel key.
    assert call_args[0][0] == "tino:factor:cancel:test-run-abc"
    # Value must be "1".
    assert call_args[0][1] == "1"
    # TTL (ex=) must be set and positive.
    ex_value = call_args.kwargs.get("ex")
    assert ex_value is not None
    assert ex_value > 0


def test_cancel_different_run_ids_use_distinct_keys(client):
    """Each run_id generates a unique cancel key."""
    c, mock_rds = client

    resp_a = c.post("/api/factor/cancel/run-aaa")
    resp_b = c.post("/api/factor/cancel/run-bbb")

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    assert resp_a.json()["run_id"] == "run-aaa"
    assert resp_b.json()["run_id"] == "run-bbb"

    # Two separate SET calls with distinct keys.
    assert mock_rds.set.call_count == 2
    keys_used = {call[0][0] for call in mock_rds.set.call_args_list}
    assert "tino:factor:cancel:run-aaa" in keys_used
    assert "tino:factor:cancel:run-bbb" in keys_used


def test_cancel_response_shape(client):
    """Response body contains exactly ``run_id`` and ``status``."""
    c, _ = client
    resp = c.post("/api/factor/cancel/shape-test-run")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"run_id", "status"}
    assert body["status"] == "cancellation_requested"
