from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from tinohelm.db import bootstrap as bootstrap_module


class FakeAsyncConnection:
    def __init__(self, table_names: set[str]) -> None:
        self.table_names = table_names
        self.create_all_calls = 0

    async def run_sync(self, fn):
        name = getattr(fn, "__qualname__", "")
        if name.endswith("MetaData.create_all"):
            self.create_all_calls += 1
            self.table_names.update(bootstrap_module._ORM_TABLE_NAMES)
            return None
        return fn(FakeSyncConnection(self.table_names))


class FakeBeginContext:
    def __init__(self, connection: FakeAsyncConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeAsyncConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeAsyncEngine:
    def __init__(self, table_names: set[str]) -> None:
        self.connection = FakeAsyncConnection(table_names)
        self.disposed = False

    def begin(self) -> FakeBeginContext:
        return FakeBeginContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


class FakeInspector:
    def __init__(self, table_names: set[str]) -> None:
        self._table_names = table_names

    def get_table_names(self) -> list[str]:
        return sorted(self._table_names)


class FakeSyncConnection:
    def __init__(self, table_names: set[str]) -> None:
        self.table_names = table_names


def test_build_alembic_config_uses_package_migrations_dir():
    db_url = "postgresql+asyncpg://user:pass@localhost/db"

    cfg = bootstrap_module.build_alembic_config(db_url)

    assert cfg.get_main_option("sqlalchemy.url") == db_url
    assert Path(cfg.get_main_option("script_location")) == bootstrap_module._MIGRATIONS_DIR


def test_bootstrap_creates_all_and_stamps_head_when_no_orm_tables(monkeypatch):
    stamp = MagicMock()
    upgrade = MagicMock()
    db_url = "postgresql+asyncpg://user:pass@localhost/db"
    engine = FakeAsyncEngine(set())

    monkeypatch.setattr(bootstrap_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(bootstrap_module, "inspect", lambda conn: FakeInspector(conn.table_names))
    monkeypatch.setattr(bootstrap_module.command, "stamp", stamp)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", upgrade)

    asyncio.run(bootstrap_module.bootstrap_database_schema(db_url))

    assert engine.connection.create_all_calls == 1
    assert bootstrap_module._ORM_TABLE_NAMES <= engine.connection.table_names
    assert "research_jobs" in engine.connection.table_names
    assert engine.disposed is True
    upgrade.assert_not_called()
    stamp.assert_called_once()
    assert stamp.call_args.args[1] == "head"
    assert stamp.call_args.args[0].get_main_option("sqlalchemy.url") == db_url
    assert Path(stamp.call_args.args[0].get_main_option("script_location")) == bootstrap_module._MIGRATIONS_DIR


def test_bootstrap_creates_all_when_only_unrelated_tables_exist(monkeypatch):
    stamp = MagicMock()
    upgrade = MagicMock()
    db_url = "postgresql+asyncpg://user:pass@localhost/db"
    engine = FakeAsyncEngine({"external_table"})

    monkeypatch.setattr(bootstrap_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(bootstrap_module, "inspect", lambda conn: FakeInspector(conn.table_names))
    monkeypatch.setattr(bootstrap_module.command, "stamp", stamp)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", upgrade)

    asyncio.run(bootstrap_module.bootstrap_database_schema(db_url))

    assert engine.connection.create_all_calls == 1
    assert "external_table" in engine.connection.table_names
    upgrade.assert_not_called()
    stamp.assert_called_once()


def test_bootstrap_stamps_head_when_orm_tables_exist_without_alembic_version(monkeypatch):
    stamp = MagicMock()
    upgrade = MagicMock()
    db_url = "postgresql+asyncpg://user:pass@localhost/db"
    engine = FakeAsyncEngine({"strategies", "research_jobs"})

    monkeypatch.setattr(bootstrap_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(bootstrap_module, "inspect", lambda conn: FakeInspector(conn.table_names))
    monkeypatch.setattr(bootstrap_module.command, "stamp", stamp)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", upgrade)

    asyncio.run(bootstrap_module.bootstrap_database_schema(db_url))

    assert engine.connection.create_all_calls == 0
    upgrade.assert_not_called()
    stamp.assert_called_once()
    assert stamp.call_args.args[1] == "head"
    assert stamp.call_args.args[0].get_main_option("sqlalchemy.url") == db_url
    assert Path(stamp.call_args.args[0].get_main_option("script_location")) == bootstrap_module._MIGRATIONS_DIR



def test_bootstrap_upgrades_head_when_alembic_version_exists(monkeypatch):
    stamp = MagicMock()
    upgrade = MagicMock()
    db_url = "postgresql+asyncpg://user:pass@localhost/db"
    engine = FakeAsyncEngine({"strategies", "research_jobs", "alembic_version"})

    monkeypatch.setattr(bootstrap_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(bootstrap_module, "inspect", lambda conn: FakeInspector(conn.table_names))
    monkeypatch.setattr(bootstrap_module.command, "stamp", stamp)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", upgrade)

    asyncio.run(bootstrap_module.bootstrap_database_schema(db_url))

    assert engine.connection.create_all_calls == 0
    stamp.assert_not_called()
    upgrade.assert_called_once()
    assert upgrade.call_args.args[1] == "head"
    assert upgrade.call_args.args[0].get_main_option("sqlalchemy.url") == db_url
    assert Path(upgrade.call_args.args[0].get_main_option("script_location")) == bootstrap_module._MIGRATIONS_DIR
