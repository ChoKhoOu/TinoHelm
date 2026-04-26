"""Tests for alembic migration 012.

Verifies upgrade/downgrade roundtrip, index existence, nullable new columns,
and that pre-existing factor_runs rows are unaffected.

Uses the real PostgreSQL instance (same as alembic.ini config) since JSONB/FK
semantics cannot be validated with SQLite.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

# Module-level integration marker — these tests require a real PostgreSQL instance
# and a working alembic CLI (subprocess). CI without DB service must filter them out
# via `-m "not integration"`.
pytestmark = pytest.mark.integration

DB_URL = "postgresql+asyncpg://tinohelm:tinohelm_secret@localhost:5432/tinohelm"

# Repo root resolved from this file's location: <repo>/tests/db/test_migration_012.py
# parents[0]=<repo>/tests/db, parents[1]=<repo>/tests, parents[2]=<repo>
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ── 7 required indexes per AC-3 ──
EXPECTED_INDEXES = {
    "ix_universes_name",
    "ix_factor_runs_baseline",
    "ix_factor_runs_universe",
    "ix_signal_runs_signal_name",
    "ix_signal_runs_factor_ref",
    "ix_signal_runs_status",
    "ix_exposures_cache_lookup",
}

TABLES_012 = {"universes", "signal_runs", "exposures_cache"}

FACTOR_RUNS_NEW_COLS = {
    "baseline_id",
    "oos_ic_series",
    "neutralization_config",
    "universe_id",
    "signal_spec_id",
    "segment_results",
    "progress_stage",
}


# ── helpers ──

def _run_alembic(*args: str) -> None:
    """Run alembic CLI via subprocess (isolates from the test process state)."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + list(args),
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


async def _get_version(conn: AsyncConnection) -> str:
    row = await conn.execute(text("SELECT version_num FROM alembic_version"))
    return row.scalar_one()


async def _get_tables(conn: AsyncConnection) -> set[str]:
    row = await conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ))
    return {r[0] for r in row}


async def _get_indexes(conn: AsyncConnection) -> set[str]:
    names_sql = ", ".join(f"'{n}'" for n in sorted(EXPECTED_INDEXES))
    row = await conn.execute(text(
        f"SELECT indexname FROM pg_indexes "
        f"WHERE tablename IN ('universes', 'factor_runs', 'signal_runs', 'exposures_cache') "
        f"  AND indexname IN ({names_sql})"
    ))
    return {r[0] for r in row}


async def _get_factor_runs_columns(conn: AsyncConnection) -> set[str]:
    row = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'factor_runs'"
    ))
    return {r[0] for r in row}


async def _get_nullable_cols(conn: AsyncConnection, table: str) -> dict[str, bool]:
    """Returns {column_name: is_nullable} for a table."""
    row = await conn.execute(text(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = :tbl",
    ), {"tbl": table})
    return {r[0]: r[1] == "YES" for r in row}


# ── fixtures ──

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def engine():
    eng = create_async_engine(DB_URL, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def ensure_at_012():
    """Guarantee DB is at 012 before any test in this module runs."""
    _run_alembic("upgrade", "head")


# ── tests ──

@pytest.mark.asyncio(loop_scope="module")
async def test_upgrade_version_is_012(engine, ensure_at_012):
    """After upgrade head, alembic_version must be '012'."""
    async with engine.connect() as conn:
        ver = await _get_version(conn)
    assert ver == "012", f"Expected version 012, got {ver}"


@pytest.mark.asyncio(loop_scope="module")
async def test_new_tables_exist(engine, ensure_at_012):
    """universes, signal_runs, exposures_cache must exist after upgrade."""
    async with engine.connect() as conn:
        tables = await _get_tables(conn)
    missing = TABLES_012 - tables
    assert not missing, f"Missing tables after upgrade: {missing}"


@pytest.mark.asyncio(loop_scope="module")
async def test_seven_indexes_present(engine, ensure_at_012):
    """AC-3: exactly 7 required indexes must exist after upgrade."""
    async with engine.connect() as conn:
        found = await _get_indexes(conn)
    missing = EXPECTED_INDEXES - found
    assert not missing, f"Missing indexes after upgrade: {missing}"
    assert len(found) == 7, f"Expected 7 indexes, got {len(found)}: {found}"


@pytest.mark.asyncio(loop_scope="module")
async def test_factor_runs_seven_new_columns(engine, ensure_at_012):
    """factor_runs must have all 7 new columns after upgrade."""
    async with engine.connect() as conn:
        cols = await _get_factor_runs_columns(conn)
    missing = FACTOR_RUNS_NEW_COLS - cols
    assert not missing, f"Missing factor_runs columns: {missing}"


@pytest.mark.asyncio(loop_scope="module")
async def test_new_columns_nullable(engine, ensure_at_012):
    """All 7 new factor_runs columns must be nullable (AC-2)."""
    async with engine.connect() as conn:
        nullable_map = await _get_nullable_cols(conn, "factor_runs")
    not_nullable = {
        col for col in FACTOR_RUNS_NEW_COLS
        if not nullable_map.get(col, True)
    }
    assert not not_nullable, f"New columns are NOT nullable: {not_nullable}"


@pytest.mark.asyncio(loop_scope="module")
async def test_progress_stage_nullable_signal_runs(engine, ensure_at_012):
    """signal_runs.progress_stage must be nullable."""
    async with engine.connect() as conn:
        nullable_map = await _get_nullable_cols(conn, "signal_runs")
    assert nullable_map.get("progress_stage"), "signal_runs.progress_stage must be nullable"


@pytest.mark.asyncio(loop_scope="module")
async def test_existing_factor_run_new_cols_null(engine, ensure_at_012):
    """Pre-existing factor_runs rows have NULL in all 7 new columns (AC-2)."""
    run_id = str(uuid.uuid4())
    async with engine.begin() as conn:
        # Insert a minimal factor_run row (only pre-012 columns)
        await conn.execute(text(
            "INSERT INTO factor_runs (id, factor_name, status, config) "
            "VALUES (:id, :fn, 'queued', :cfg)"
        ), {"id": run_id, "fn": "test_factor", "cfg": '{"test": true}'})

    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT baseline_id, oos_ic_series, neutralization_config, "
            "       universe_id, signal_spec_id, segment_results, progress_stage "
            "FROM factor_runs WHERE id = :id"
        ), {"id": run_id})
        values = row.fetchone()

    assert values is not None
    assert all(v is None for v in values), (
        f"Expected all NULL for new columns, got: {dict(zip(FACTOR_RUNS_NEW_COLS, values))}"
    )

    # cleanup
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM factor_runs WHERE id = :id"), {"id": run_id})


@pytest.mark.asyncio(loop_scope="module")
async def test_downgrade_removes_tables_and_columns(engine, ensure_at_012):
    """Downgrade -1: new tables dropped, factor_runs new columns removed, version = 011."""
    _run_alembic("downgrade", "-1")

    async with engine.connect() as conn:
        ver = await _get_version(conn)
        tables = await _get_tables(conn)
        cols = await _get_factor_runs_columns(conn)

    assert ver == "011", f"After downgrade expected 011, got {ver}"
    remaining_new_tables = TABLES_012 & tables
    assert not remaining_new_tables, f"Tables not dropped on downgrade: {remaining_new_tables}"
    remaining_new_cols = FACTOR_RUNS_NEW_COLS & cols
    assert not remaining_new_cols, f"Columns not dropped on downgrade: {remaining_new_cols}"

    # Restore to 012 so other tests / subsequent runs are clean
    _run_alembic("upgrade", "head")


@pytest.mark.asyncio(loop_scope="module")
async def test_upgrade_after_downgrade_idempotent(engine):
    """After downgrade+upgrade roundtrip DB is back at 012 with all indexes."""
    async with engine.connect() as conn:
        ver = await _get_version(conn)
        found = await _get_indexes(conn)

    assert ver == "012"
    assert found == EXPECTED_INDEXES
