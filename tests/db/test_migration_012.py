"""Tests for alembic migrations 012 and 013.

Verifies upgrade/downgrade roundtrip, index existence, nullable new columns,
and that pre-existing factor_runs rows are unaffected.

Uses the PostgreSQL URL from ``TINO_TEST_DATABASE_URL`` since JSONB/FK semantics
cannot be validated with SQLite.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

# Module-level integration marker — these tests require a real PostgreSQL instance
# and a working alembic CLI (subprocess). CI without DB service must filter them out
# via `-m "not integration"`.
pytestmark = pytest.mark.integration

DB_URL = os.environ.get("TINO_TEST_DATABASE_URL")
if not DB_URL:
    pytest.skip(
        "TINO_TEST_DATABASE_URL is required for migration integration tests",
        allow_module_level=True,
    )

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
    import subprocess
    import sys

    assert DB_URL is not None
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + list(args),
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "TINO_DATABASE__URL": DB_URL},
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def _reset_database_to_pre_012() -> None:
    """Build an isolated synthetic 011 schema in the test database.

    The project started Alembic after the original base tables already existed
    (see migration ``add_watchlist``).  A clean PostgreSQL database therefore
    cannot run the full Alembic chain from scratch: migration 002 expects
    ``strategies`` and ``backtest_runs`` to be present already.  For these
    migration tests we create the current ORM schema, remove exactly the objects
    introduced by 012/013, and stamp the DB at revision 011.  The tests then run
    the real upgrades/downgrades against the same database URL used by the
    assertion engine.
    """
    assert DB_URL is not None

    async def _reset() -> None:
        from tinohelm.db.models import Base

        engine = create_async_engine(DB_URL, pool_pre_ping=True)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
                await conn.run_sync(Base.metadata.create_all)

                # Remove 012/013/014-created tables / constraints / columns from the
                # current ORM schema so Alembic can add them for real.
                await conn.execute(text("DROP TABLE IF EXISTS signal_runs CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS exposures_cache CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS universes CASCADE"))
                await conn.execute(text("DROP INDEX IF EXISTS ix_factor_runs_baseline"))
                await conn.execute(text("DROP INDEX IF EXISTS ix_factor_runs_universe"))
                for col in FACTOR_RUNS_NEW_COLS:
                    await conn.execute(text(f"ALTER TABLE factor_runs DROP COLUMN IF EXISTS {col}"))
                await conn.execute(text("ALTER TABLE data_catalog DROP COLUMN IF EXISTS last_ingest_id"))
                await conn.execute(text("DROP INDEX IF EXISTS ix_data_fetch_jobs_batch_id"))
                await conn.execute(text("ALTER TABLE data_fetch_jobs DROP COLUMN IF EXISTS batch_id"))
                await conn.execute(text("ALTER TABLE data_fetch_jobs DROP COLUMN IF EXISTS started_at"))
                await conn.execute(text("ALTER TABLE data_fetch_jobs DROP COLUMN IF EXISTS batch_finalize_started_at"))
                await conn.execute(text("ALTER TABLE data_fetch_jobs DROP COLUMN IF EXISTS batch_finalized_at"))
                await conn.execute(text("ALTER TABLE data_fetch_jobs DROP COLUMN IF EXISTS batch_finalize_error"))

                await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
                await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
                await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('011')"))
        finally:
            await engine.dispose()

    asyncio.run(_reset())


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
    return await _get_table_columns(conn, "factor_runs")


async def _get_table_columns(conn: AsyncConnection, table: str) -> set[str]:
    row = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :tbl"
    ), {"tbl": table})
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
    assert DB_URL is not None
    eng = create_async_engine(DB_URL, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def ensure_at_head():
    """Guarantee DB is at Alembic head before any test in this module runs."""
    _reset_database_to_pre_012()
    _run_alembic("upgrade", "head")


# ── tests ──

@pytest.mark.asyncio(loop_scope="module")
async def test_upgrade_version_is_head(engine, ensure_at_head):
    """After upgrade head, alembic_version must be '015'."""
    async with engine.connect() as conn:
        ver = await _get_version(conn)
    assert ver == "015", f"Expected version 015, got {ver}"


@pytest.mark.asyncio(loop_scope="module")
async def test_new_tables_exist(engine, ensure_at_head):
    """universes, signal_runs, exposures_cache must exist after upgrade."""
    async with engine.connect() as conn:
        tables = await _get_tables(conn)
    missing = TABLES_012 - tables
    assert not missing, f"Missing tables after upgrade: {missing}"


@pytest.mark.asyncio(loop_scope="module")
async def test_seven_indexes_present(engine, ensure_at_head):
    """AC-3: exactly 7 required indexes must exist after upgrade."""
    async with engine.connect() as conn:
        found = await _get_indexes(conn)
    missing = EXPECTED_INDEXES - found
    assert not missing, f"Missing indexes after upgrade: {missing}"
    assert len(found) == 7, f"Expected 7 indexes, got {len(found)}: {found}"


@pytest.mark.asyncio(loop_scope="module")
async def test_factor_runs_seven_new_columns(engine, ensure_at_head):
    """factor_runs must have all 7 new columns after upgrade."""
    async with engine.connect() as conn:
        cols = await _get_factor_runs_columns(conn)
    missing = FACTOR_RUNS_NEW_COLS - cols
    assert not missing, f"Missing factor_runs columns: {missing}"


@pytest.mark.asyncio(loop_scope="module")
async def test_new_columns_nullable(engine, ensure_at_head):
    """All 7 new factor_runs columns must be nullable (AC-2)."""
    async with engine.connect() as conn:
        nullable_map = await _get_nullable_cols(conn, "factor_runs")
    not_nullable = {
        col for col in FACTOR_RUNS_NEW_COLS
        if not nullable_map.get(col, True)
    }
    assert not not_nullable, f"New columns are NOT nullable: {not_nullable}"


@pytest.mark.asyncio(loop_scope="module")
async def test_data_catalog_commit_witness_column_nullable(engine, ensure_at_head):
    """013 adds nullable last_ingest_id as durable catalog commit witness."""
    async with engine.connect() as conn:
        cols = await _get_table_columns(conn, "data_catalog")
        nullable_map = await _get_nullable_cols(conn, "data_catalog")

    assert "last_ingest_id" in cols
    assert nullable_map.get("last_ingest_id") is True


@pytest.mark.asyncio(loop_scope="module")
async def test_data_fetch_jobs_batch_id_column_and_index(engine, ensure_at_head):
    """014 adds nullable ``batch_id`` to data_fetch_jobs plus a supporting
    btree index. This pins those guarantees so the FetchBatch scheduler
    (#164/#165/#166) can rely on both the column and an index-backed lookup
    by batch_id after upgrade to head.

    Legacy pre-#163 rows arrive with ``batch_id IS NULL``; the application
    layer always populates it for new jobs.
    """
    async with engine.connect() as conn:
        cols = await _get_table_columns(conn, "data_fetch_jobs")
        nullable_map = await _get_nullable_cols(conn, "data_fetch_jobs")
        indexes = await conn.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'data_fetch_jobs' "
            "  AND indexname = 'ix_data_fetch_jobs_batch_id'"
        ))
        found_indexes = {r[0] for r in indexes}

    assert "batch_id" in cols, "014 must add batch_id column to data_fetch_jobs"
    assert nullable_map.get("batch_id") is True, (
        "batch_id must be nullable so legacy pre-#163 rows stay valid"
    )
    assert "ix_data_fetch_jobs_batch_id" in found_indexes, (
        "014 must create ix_data_fetch_jobs_batch_id for FetchBatch lookups"
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_data_fetch_jobs_started_at_column_nullable(engine, ensure_at_head):
    """015 adds nullable ``started_at`` to data_fetch_jobs for running-age tracking."""
    async with engine.connect() as conn:
        cols = await _get_table_columns(conn, "data_fetch_jobs")
        nullable_map = await _get_nullable_cols(conn, "data_fetch_jobs")

    assert "started_at" in cols, "015 must add started_at column to data_fetch_jobs"
    assert nullable_map.get("started_at") is True, (
        "started_at must be nullable so queued/terminal jobs can clear it"
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_data_fetch_jobs_batch_finalize_columns_nullable(engine, ensure_at_head):
    """016 adds nullable batch finalize witness columns to data_fetch_jobs."""
    async with engine.connect() as conn:
        cols = await _get_table_columns(conn, "data_fetch_jobs")
        nullable_map = await _get_nullable_cols(conn, "data_fetch_jobs")

    for col in ("batch_finalize_started_at", "batch_finalized_at", "batch_finalize_error"):
        assert col in cols, f"016 must add {col} to data_fetch_jobs"
        assert nullable_map.get(col) is True, (
            f"{col} must be nullable so legacy / in-flight rows remain valid"
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_progress_stage_nullable_signal_runs(engine, ensure_at_head):
    """signal_runs.progress_stage must be nullable."""
    async with engine.connect() as conn:
        nullable_map = await _get_nullable_cols(conn, "signal_runs")
    assert nullable_map.get("progress_stage"), "signal_runs.progress_stage must be nullable"


@pytest.mark.asyncio(loop_scope="module")
async def test_existing_factor_run_new_cols_null(engine, ensure_at_head):
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
async def test_downgrade_removes_tables_and_columns(engine, ensure_at_head):
    """Downgrade 013 then 012 removes commit witness, then 012 objects."""
    _run_alembic("downgrade", "012")

    async with engine.connect() as conn:
        ver = await _get_version(conn)
        tables = await _get_tables(conn)
        factor_cols = await _get_factor_runs_columns(conn)
        catalog_cols = await _get_table_columns(conn, "data_catalog")

    assert ver == "012", f"After downgrade to 012 expected 012, got {ver}"
    assert "last_ingest_id" not in catalog_cols
    assert TABLES_012 <= tables
    assert FACTOR_RUNS_NEW_COLS <= factor_cols

    _run_alembic("downgrade", "011")

    async with engine.connect() as conn:
        ver = await _get_version(conn)
        tables = await _get_tables(conn)
        factor_cols = await _get_factor_runs_columns(conn)

    assert ver == "011", f"After downgrade to 011 expected 011, got {ver}"
    remaining_new_tables = TABLES_012 & tables
    assert not remaining_new_tables, f"Tables not dropped on downgrade: {remaining_new_tables}"
    remaining_new_cols = FACTOR_RUNS_NEW_COLS & factor_cols
    assert not remaining_new_cols, f"Columns not dropped on downgrade: {remaining_new_cols}"

    # Restore to Alembic head so other tests / subsequent runs are clean.
    _run_alembic("upgrade", "head")


@pytest.mark.asyncio(loop_scope="module")
async def test_upgrade_after_downgrade_idempotent(engine, ensure_at_head):
    """After downgrade+upgrade roundtrip DB is back at head with all indexes."""
    async with engine.connect() as conn:
        ver = await _get_version(conn)
        found = await _get_indexes(conn)
        catalog_cols = await _get_table_columns(conn, "data_catalog")
        data_fetch_cols = await _get_table_columns(conn, "data_fetch_jobs")

    assert ver == "015"
    assert found == EXPECTED_INDEXES
    assert "last_ingest_id" in catalog_cols
    assert "started_at" in data_fetch_cols
