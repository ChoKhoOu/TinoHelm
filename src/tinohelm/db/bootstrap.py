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
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ORM_TABLE_NAMES = frozenset(Base.metadata.tables)


async def bootstrap_database_schema(db_url: str) -> None:
    """Initialize the database schema before the API starts serving."""
    engine = create_async_engine(db_url, echo=False)
    alembic_cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    has_tinohelm_tables = False
    table_count = 0
    try:
        async with engine.begin() as connection:
            table_names = set(await connection.run_sync(lambda conn: inspect(conn).get_table_names()))
            existing_orm_tables = table_names & _ORM_TABLE_NAMES
            table_count = len(existing_orm_tables)
            if not existing_orm_tables:
                logger.info("Database has no TinoHelm ORM tables; creating ORM schema before stamping Alembic head")
                await connection.run_sync(Base.metadata.create_all)
            else:
                has_tinohelm_tables = True
    finally:
        await engine.dispose()

    if has_tinohelm_tables:
        logger.info("Database has %d TinoHelm ORM tables; running Alembic upgrade head", table_count)
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        return

    await asyncio.to_thread(command.stamp, alembic_cfg, "head")
