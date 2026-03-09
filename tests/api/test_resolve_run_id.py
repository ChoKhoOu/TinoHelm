"""Tests for resolve_run_id prefix matching."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from tinohelm.api.routes.backtest import resolve_run_id


def _mock_db(run_ids: list[str]) -> AsyncMock:
    """Create a mock AsyncSession that returns given run_ids for LIKE queries."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = run_ids
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
async def test_full_uuid_passthrough():
    """Full UUID is returned as-is without DB query."""
    db = AsyncMock()
    full = "ac5ef009-1234-5678-abcd-ef0123456789"
    result = await resolve_run_id(full, db)
    assert result == full
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_prefix_unique_match():
    """Short prefix matching exactly one run returns the full UUID."""
    full_id = "ac5ef009-1234-5678-abcd-ef0123456789"
    db = _mock_db([full_id])
    result = await resolve_run_id("ac5ef009", db)
    assert result == full_id


@pytest.mark.asyncio
async def test_prefix_no_match():
    """No matching run raises 404."""
    db = _mock_db([])
    with pytest.raises(HTTPException) as exc_info:
        await resolve_run_id("deadbeef", db)
    assert exc_info.value.status_code == 404
    assert "deadbeef" in exc_info.value.detail


@pytest.mark.asyncio
async def test_prefix_ambiguous():
    """Multiple matches raise 409."""
    db = _mock_db([
        "ac5ef009-1111-2222-3333-444444444444",
        "ac5ef009-aaaa-bbbb-cccc-dddddddddddd",
    ])
    with pytest.raises(HTTPException) as exc_info:
        await resolve_run_id("ac5ef", db)
    assert exc_info.value.status_code == 409
    assert "Ambiguous" in exc_info.value.detail


@pytest.mark.asyncio
async def test_prefix_too_short():
    """Prefix shorter than 4 chars raises 400."""
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await resolve_run_id("abc", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_prefix_non_hex():
    """Non-hex characters raise 400."""
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await resolve_run_id("xyz12345", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_prefix_case_insensitive():
    """Uppercase input is lowered before matching."""
    full_id = "ac5ef009-1234-5678-abcd-ef0123456789"
    db = _mock_db([full_id])
    result = await resolve_run_id("AC5EF009", db)
    assert result == full_id


@pytest.mark.asyncio
async def test_prefix_with_whitespace():
    """Leading/trailing whitespace is stripped."""
    full_id = "ac5ef009-1234-5678-abcd-ef0123456789"
    db = _mock_db([full_id])
    result = await resolve_run_id("  ac5ef009  ", db)
    assert result == full_id
