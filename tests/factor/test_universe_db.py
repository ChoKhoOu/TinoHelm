"""Tests for Universe DB integration — sync_from_csv, from_db_row, and API endpoints.

Uses the PostgreSQL URL from ``TINO_TEST_DATABASE_URL`` since JSONB column
semantics and FK constraints cannot be validated with SQLite.

Test cases
----------
- sync_from_csv: first sync inserts a row, returns correct id
- sync_from_csv: re-syncing the identical CSV returns the same id (idempotent)
- sync_from_csv: different CSV content creates a new distinct row
- from_db_row: reconstructed Universe PIT query matches original load_csv result
- GET /api/factor/universes/{id}: returns pit_rules_json correctly deserialized
- POST /api/factor/universes/sync: returns {id, name, created=True/False}
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import os
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module-level integration marker — sync_from_csv / from_db_row tests (1-4) need a
# real PostgreSQL instance with the migrated schema. The API mock tests (5-6) do not
# strictly need DB but are kept under the same marker so the whole module is gated
# uniformly behind `-m "not integration"` in environments without DB infrastructure.
pytestmark = pytest.mark.integration

DB_URL = os.environ.get("TINO_TEST_DATABASE_URL")
if not DB_URL:
    pytest.skip(
        "TINO_TEST_DATABASE_URL is required for Universe DB integration tests",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def engine():
    assert DB_URL is not None
    eng = create_async_engine(DB_URL, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="module")
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# CSV helper
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, rows: list[dict], filename: str = "test_uni.csv") -> Path:
    """Write a CSV from a list of dicts and return the path."""
    path = tmp_path / filename
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Cleanup helper — remove inserted rows by hash so tests are idempotent
# ---------------------------------------------------------------------------

async def _delete_by_hash(session: AsyncSession, csv_hash: str) -> None:
    from tinohelm.db.models import Universe as UniverseORM
    row = (await session.execute(
        select(UniverseORM).where(UniverseORM.source_csv_hash == csv_hash)
    )).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# 1. sync_from_csv: first sync inserts a row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_sync_from_csv_inserts_row(tmp_path, session_factory):
    """First sync of a CSV creates a new universes row with correct pit_rules_json."""
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    csv_path = _write_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        {"symbol": "ETHUSDT-PERP", "listing_date": "2020-03-01", "delisting_date": "2024-06-01"},
    ], filename="uni_insert_test.csv")
    csv_hash = _sha256_path(csv_path)

    async with session_factory() as session:
        # Ensure clean state
        await _delete_by_hash(session, csv_hash)

    async with session_factory() as session:
        uni, db_id = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    assert isinstance(db_id, int)
    assert db_id > 0
    assert uni.name == "uni_insert_test"

    # Verify pit_rules_json was stored correctly
    async with session_factory() as session:
        row = (await session.execute(
            select(UniverseORM).where(UniverseORM.id == db_id)
        )).scalar_one_or_none()

    assert row is not None
    pit = row.pit_rules_json
    assert "BTCUSDT-PERP" in pit
    assert "ETHUSDT-PERP" in pit
    assert pit["BTCUSDT-PERP"]["listing_date"] == "2020-01-01"
    assert pit["ETHUSDT-PERP"]["delisting_date"] == "2024-06-01"
    assert pit["BTCUSDT-PERP"]["delisting_date"] is None

    # Cleanup
    async with session_factory() as session:
        await _delete_by_hash(session, csv_hash)


# ---------------------------------------------------------------------------
# 2. sync_from_csv: same CSV → same row (idempotent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_sync_from_csv_idempotent(tmp_path, session_factory):
    """Syncing the exact same CSV twice returns the same id; no duplicate row."""
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    csv_path = _write_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
    ], filename="uni_idempotent_test.csv")
    csv_hash = _sha256_path(csv_path)

    async with session_factory() as session:
        await _delete_by_hash(session, csv_hash)

    # First sync
    async with session_factory() as session:
        _uni1, id1 = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    # Second sync — same CSV
    async with session_factory() as session:
        _uni2, id2 = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    assert id1 == id2, f"Expected same id on re-sync, got {id1} vs {id2}"

    # Verify only one row exists with this hash
    async with session_factory() as session:
        result = await session.execute(
            select(UniverseORM).where(UniverseORM.source_csv_hash == csv_hash)
        )
        rows = result.scalars().all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"

    async with session_factory() as session:
        await _delete_by_hash(session, csv_hash)


# ---------------------------------------------------------------------------
# 3. sync_from_csv: different CSV → new row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_sync_from_csv_different_content_creates_new_row(tmp_path, session_factory):
    """Two CSVs with different content create two separate universes rows."""
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    csv_a = _write_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
    ], filename="uni_diff_a.csv")
    # Different name so the unique `name` constraint is also satisfied
    csv_b = _write_csv(tmp_path, [
        {"symbol": "ETHUSDT-PERP", "listing_date": "2020-03-01", "delisting_date": ""},
    ], filename="uni_diff_b.csv")

    hash_a = _sha256_path(csv_a)
    hash_b = _sha256_path(csv_b)

    # Clean up from any prior run
    for h in (hash_a, hash_b):
        async with session_factory() as session:
            await _delete_by_hash(session, h)

    async with session_factory() as session:
        _uni_a, id_a = await Universe.sync_from_csv(csv_a, session)
        await session.commit()

    async with session_factory() as session:
        _uni_b, id_b = await Universe.sync_from_csv(csv_b, session)
        await session.commit()

    assert id_a != id_b, "Different CSVs must produce different DB rows"

    # Both rows exist in DB
    async with session_factory() as session:
        rows = (await session.execute(
            select(UniverseORM).where(
                UniverseORM.source_csv_hash.in_([hash_a, hash_b])
            )
        )).scalars().all()
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    # Cleanup
    for h in (hash_a, hash_b):
        async with session_factory() as session:
            await _delete_by_hash(session, h)


@pytest.mark.asyncio(loop_scope="module")
async def test_sync_from_csv_same_filename_new_content_gets_suffixed_name(
    tmp_path,
    session_factory,
):
    """Changing one CSV path creates a new immutable row with a unique name."""
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    csv_path = _write_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
    ], filename="uni_collision_test.csv")
    hash_a = _sha256_path(csv_path)

    async with session_factory() as session:
        await _delete_by_hash(session, hash_a)

    async with session_factory() as session:
        uni_a, id_a = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    csv_path = _write_csv(tmp_path, [
        {"symbol": "ETHUSDT-PERP", "listing_date": "2020-03-01", "delisting_date": ""},
    ], filename="uni_collision_test.csv")
    hash_b = _sha256_path(csv_path)

    async with session_factory() as session:
        await _delete_by_hash(session, hash_b)

    async with session_factory() as session:
        uni_b, id_b = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    assert id_a != id_b
    assert hash_a != hash_b
    assert uni_a.name == "uni_collision_test"
    assert uni_b.name == f"uni_collision_test-{hash_b[:12]}"
    assert uni_a.name != uni_b.name

    async with session_factory() as session:
        rows = (await session.execute(
            select(UniverseORM).where(UniverseORM.id.in_([id_a, id_b]))
        )).scalars().all()
    assert {row.name for row in rows} == {uni_a.name, uni_b.name}
    assert {row.source_csv_hash for row in rows} == {hash_a, hash_b}

    for csv_hash in (hash_a, hash_b):
        async with session_factory() as session:
            await _delete_by_hash(session, csv_hash)


# ---------------------------------------------------------------------------
# 4. from_db_row: reconstructed Universe PIT query is correct
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_from_db_row_pit_query(tmp_path, session_factory):
    """Universe.from_db_row returns a Universe with correct PIT query results."""
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe

    csv_path = _write_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        {"symbol": "DOTUSDT-PERP", "listing_date": "2020-09-01", "delisting_date": "2024-06-01"},
    ], filename="uni_from_db_row.csv")
    csv_hash = _sha256_path(csv_path)

    async with session_factory() as session:
        await _delete_by_hash(session, csv_hash)

    async with session_factory() as session:
        _uni, db_id = await Universe.sync_from_csv(csv_path, session)
        await session.commit()

    # Reload via from_db_row
    async with session_factory() as session:
        row = (await session.execute(
            select(UniverseORM).where(UniverseORM.id == db_id)
        )).scalar_one()

    reconstructed = Universe.from_db_row(row)

    # BTC active in 2023; DOT active in 2023 but delisted 2024-06-01
    symbols_2023 = reconstructed.get_symbols_at(datetime(2023, 1, 1))
    assert "BTCUSDT-PERP" in symbols_2023
    assert "DOTUSDT-PERP" in symbols_2023

    # DOT excluded after delisting
    symbols_2025 = reconstructed.get_symbols_at(datetime(2025, 1, 1))
    assert "BTCUSDT-PERP" in symbols_2025
    assert "DOTUSDT-PERP" not in symbols_2025

    async with session_factory() as session:
        await _delete_by_hash(session, csv_hash)


# ---------------------------------------------------------------------------
# 5 & 6. API endpoint tests via FastAPI TestClient (no real DB session needed)
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tinohelm.api.routes import universe as universe_module  # noqa: E402


def _make_universe_orm_row(
    db_id: int = 1,
    name: str = "test_uni",
    csv_path: str = "/tmp/test_uni.csv",
    csv_hash: str = "abc123",
    pit_rules: dict | None = None,
) -> MagicMock:
    """Return a MagicMock that looks like a Universe ORM row."""
    if pit_rules is None:
        pit_rules = {
            "BTCUSDT-PERP": {"listing_date": "2020-01-01", "delisting_date": None},
        }
    row = MagicMock()
    row.id = db_id
    row.name = name
    row.source_csv_path = csv_path
    row.source_csv_hash = csv_hash
    row.min_history_bars = 100
    row.new_coin_isolation_days = 7
    row.pit_rules_json = pit_rules
    row.created_at = datetime(2026, 4, 26, 0, 0, 0)
    row.updated_at = datetime(2026, 4, 26, 0, 0, 0)
    return row


_test_api_app = FastAPI()
_test_api_app.include_router(universe_module.router)


@pytest.fixture()
def api_client():
    """TestClient with get_db overridden to return a mock session."""
    from tinohelm.api.deps import get_db

    async def _mock_get_db():
        yield AsyncMock()

    _test_api_app.dependency_overrides[get_db] = _mock_get_db
    with TestClient(_test_api_app) as c:
        yield c
    _test_api_app.dependency_overrides.clear()


def test_get_universe_returns_pit_rules_json(api_client, tmp_path):
    """GET /api/factor/universes/{id} returns correctly deserialized pit_rules_json."""
    pit_rules = {
        "BTCUSDT-PERP": {"listing_date": "2020-01-01", "delisting_date": None},
        "ETHUSDT-PERP": {"listing_date": "2020-03-01", "delisting_date": "2024-06-01"},
    }
    mock_row = _make_universe_orm_row(db_id=42, pit_rules=pit_rules)

    with patch("tinohelm.api.routes.universe.select") as _mock_select:
        # Build a mock chain: select(ORM).where() → execute → scalar_one_or_none
        async def _mock_execute(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = mock_row
            return result

        from tinohelm.api.deps import get_db

        async def _db_with_row():
            session = AsyncMock()
            session.execute = _mock_execute
            yield session

        _test_api_app.dependency_overrides[get_db] = _db_with_row
        resp = api_client.get("/api/factor/universes/42")
        _test_api_app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == 42
    assert body["name"] == "test_uni"
    assert isinstance(body["pit_rules_json"], dict)
    assert "BTCUSDT-PERP" in body["pit_rules_json"]
    assert body["pit_rules_json"]["ETHUSDT-PERP"]["delisting_date"] == "2024-06-01"


def test_get_universe_404_when_not_found(api_client):
    """GET /api/factor/universes/{id} returns 404 when row does not exist."""
    async def _db_none():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        yield session

    from tinohelm.api.deps import get_db
    _test_api_app.dependency_overrides[get_db] = _db_none
    resp = api_client.get("/api/factor/universes/9999")
    _test_api_app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 404


def test_sync_endpoint_returns_created_true_for_new(tmp_path):
    """POST /api/factor/universes/sync returns created=true for a new CSV."""
    csv_path = tmp_path / "new_sync.csv"
    csv_path.write_text(
        "symbol,listing_date,delisting_date\n"
        "BTCUSDT-PERP,2020-01-01,\n"
    )

    import hashlib
    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    from tinohelm.api.deps import get_db
    from tinohelm.factor.universe import Universe

    # DB has no existing row for this hash
    async def _db_no_existing():
        session = AsyncMock()
        result_none = MagicMock()
        result_none.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_none)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        # After flush, the ORM row needs an id — simulate via side_effect
        async def _flush_side_effect():
            # sync_from_csv calls session.flush(); after that db_row.id must be set.
            # We do this by patching Universe.sync_from_csv instead (see below).
            pass
        session.flush.side_effect = _flush_side_effect
        yield session

    with patch.object(
        Universe,
        "sync_from_csv",
        new=AsyncMock(return_value=(Universe.from_symbols(["BTCUSDT-PERP"], name="new_sync"), 99)),
    ):
        _test_api_app.dependency_overrides[get_db] = _db_no_existing
        with TestClient(_test_api_app) as c:
            resp = c.post(
                "/api/factor/universes/sync",
                json={"csv_path": str(csv_path)},
            )
        _test_api_app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == 99
    assert body["name"] == "new_sync"
    assert body["created"] is True


def test_sync_endpoint_returns_created_false_for_existing(tmp_path):
    """POST /api/factor/universes/sync returns created=false when hash already in DB."""
    csv_path = tmp_path / "existing_sync.csv"
    csv_path.write_text(
        "symbol,listing_date,delisting_date\n"
        "BTCUSDT-PERP,2020-01-01,\n"
    )

    import hashlib
    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    mock_row = _make_universe_orm_row(db_id=7, name="existing_sync", csv_hash=csv_hash)

    from tinohelm.api.deps import get_db
    from tinohelm.factor.universe import Universe

    async def _db_with_existing():
        session = AsyncMock()
        result_existing = MagicMock()
        result_existing.scalar_one_or_none.return_value = mock_row
        session.execute = AsyncMock(return_value=result_existing)
        session.commit = AsyncMock()
        yield session

    with patch.object(
        Universe,
        "sync_from_csv",
        new=AsyncMock(return_value=(Universe.from_symbols(["BTCUSDT-PERP"], name="existing_sync"), 7)),
    ):
        _test_api_app.dependency_overrides[get_db] = _db_with_existing
        with TestClient(_test_api_app) as c:
            resp = c.post(
                "/api/factor/universes/sync",
                json={"csv_path": str(csv_path)},
            )
        _test_api_app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == 7
    assert body["created"] is False
