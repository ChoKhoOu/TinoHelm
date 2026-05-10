"""Database schema bootstrap for app startup."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from tinohelm.db.models import Base

logger = logging.getLogger(__name__)
_MIGRATIONS_DIR = Path(__file__).resolve().with_name("migrations")
_ALEMBIC_VERSION_TABLE = "alembic_version"
_ORM_TABLE_NAMES = frozenset(Base.metadata.tables)


def build_alembic_config(db_url: str) -> Config:
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    return alembic_cfg


async def bootstrap_database_schema(db_url: str) -> None:
    """Initialize the database schema before the API starts serving."""
    engine = create_async_engine(db_url, echo=False)
    alembic_cfg = build_alembic_config(db_url)

    has_tinohelm_tables = False
    has_alembic_version = False
    table_count = 0
    try:
        async with engine.begin() as connection:
            table_names = set(await connection.run_sync(lambda conn: inspect(conn).get_table_names()))
            existing_orm_tables = table_names & _ORM_TABLE_NAMES
            table_count = len(existing_orm_tables)
            has_alembic_version = _ALEMBIC_VERSION_TABLE in table_names
            if not existing_orm_tables:
                logger.info("Database has no TinoHelm ORM tables; creating ORM schema before stamping Alembic head")
                await connection.run_sync(Base.metadata.create_all)
            else:
                has_tinohelm_tables = True
    finally:
        await engine.dispose()

    if has_tinohelm_tables and has_alembic_version:
        logger.info("Database has %d TinoHelm ORM tables and Alembic state; running Alembic upgrade head", table_count)
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        return

    if has_tinohelm_tables:
        logger.info("Database has %d TinoHelm ORM tables but no Alembic state; stamping Alembic head", table_count)
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")
        return

    await asyncio.to_thread(command.stamp, alembic_cfg, "head")
