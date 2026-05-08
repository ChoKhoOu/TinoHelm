"""Tests for pure helpers in tinohelm.api.routes.data.

Covers interval ⇄ NT-suffix conversion, parquet size calculation, and the
storage-file deletion helper used by DELETE /api/data/catalog/{id}.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from fastapi import HTTPException

from tinohelm.api.routes.data import (
    CompactRequest,
    DataFetchBatchRequest,
    _UNIT_MAP,
    _UNIT_REVERSE,
    _delete_storage_files,
    _fetch_batch_intervals,
    _split_fetch_date_ranges,
    _interval_to_nt,
    _nt_to_interval,
    _parquet_size_for,
    _run_compact,
    _compact_bars_with_storage,
    cancel_data_fetch_job,
    delete_catalog_entry,
    scan_data_catalog,
    trigger_compact,
    trigger_data_fetch_batch,
    validate_data,
)
from tinohelm.data.catalog import compact_bars


class _FetchBatchDb:
    def __init__(self):
        self.jobs = []
        self._next_id = 0

    def add(self, job):
        self.jobs.append(job)

    async def flush(self):
        self._next_id += 1
        self.jobs[-1].job_id = f"job-{self._next_id}"

    async def commit(self):
        pass


class _FakeS3File(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.closed_flag = False

    def close(self) -> None:
        self.closed_flag = True
        super().close()


class _FakeS3FileSystem:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.open_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str]] = []
        self.rm_calls: list[str] = []

    def info(self, path: str) -> dict:
        if path not in self.objects:
            raise FileNotFoundError(path)
        return {"name": path, "size": len(self.objects[path]), "type": "file"}

    def find(self, prefix: str, withdirs: bool = False, detail: bool = True):
        matches = {
            path: self.info(path)
            for path in sorted(self.objects)
            if path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/")
        }
        return matches if detail else list(matches)

    def ls(self, prefix: str, detail: bool = True):
        prefix = prefix.rstrip("/")
        out = []
        for path in sorted(self.objects):
            if not path.startswith(prefix + "/"):
                continue
            rel = path[len(prefix) + 1 :]
            if "/" in rel:
                continue
            out.append(self.info(path) if detail else path)
        return out

    def open(self, path: str, mode: str = "rb"):
        assert mode == "rb"
        self.open_calls.append((path, mode))
        return _FakeS3File(self.objects[path])

    def put_file(self, local_path: str, remote_path: str) -> None:
        self.put_calls.append((local_path, remote_path))
        self.objects[remote_path] = Path(local_path).read_bytes()

    def rm(self, path: str) -> None:
        self.rm_calls.append(path)
        if path not in self.objects:
            raise FileNotFoundError(path)
        del self.objects[path]


class _CompactStorage:
    provider = "s3"

    def __init__(self, root: Path, fs: _FakeS3FileSystem) -> None:
        self.catalog_root = root
        self._fs = fs
        self.fs_storage_options = {"endpoint_url": "https://example.com"}
        self.fs_rust_storage_options = {"endpoint_url": "https://example.com"}

    def uri_for_catalog_root(self, catalog_root: Path | str | None = None) -> str:
        if catalog_root is None:
            return "s3://bucket/catalog"
        rel = Path(catalog_root).relative_to(self.catalog_root).as_posix() if Path(catalog_root) != self.catalog_root else ""
        return "s3://bucket/catalog" if not rel else f"s3://bucket/catalog/{rel}"

    def iter_files(self, prefix: Path | str, suffix: str = "", recursive: bool = True):
        prefix = Path(prefix)
        prefix_rel = prefix.relative_to(self.catalog_root).as_posix().rstrip("/")
        for key, payload in self._fs.objects.items():
            if not key.startswith("bucket/catalog/"):
                continue
            rel = key.removeprefix("bucket/catalog/")
            if not rel.startswith(prefix_rel + "/"):
                continue
            remainder = rel[len(prefix_rel) + 1 :]
            if not recursive and "/" in remainder:
                continue
            if suffix and not rel.endswith(suffix):
                continue
            obj = type("Obj", (), {})()
            obj.key = key
            obj.path = self.catalog_root / rel
            obj.size = len(payload)
            obj.last_modified = None
            yield obj

    def exists(self, path: Path | str) -> bool:
        return True

    def open_input_file(self, path_or_object):
        key = getattr(path_or_object, "key", None)
        if key is None:
            key = f"bucket/catalog/{Path(path_or_object).name}"
        return self._fs.open(key)

    def read_bytes(self, path_or_object):
        with self.open_input_file(path_or_object) as fh:
            return fh.read()

    def delete_path(self, local_path: Path | str) -> None:
        key = getattr(local_path, "key", None)
        if key is None:
            rel = Path(local_path).relative_to(self.catalog_root).as_posix()
            key = f"bucket/catalog/{rel}"
        self._fs.rm(key)

    def upload_path(self, local_path: Path | str, *, logical_path: Path | str | None = None) -> str:
        path = Path(local_path)
        rel_path = Path(logical_path) if logical_path is not None else path
        try:
            rel = rel_path.relative_to(self.catalog_root).as_posix()
        except ValueError:
            rel = rel_path.name
        remote_path = f"bucket/catalog/{rel}"
        self._fs.put_file(str(path), remote_path)
        return f"s3://{remote_path}"

    def copy_path(self, source_path: Path | str, dest_path: Path | str) -> str:
        source = getattr(source_path, "key", None)
        if source is None:
            source = f"bucket/catalog/{Path(source_path).relative_to(self.catalog_root).as_posix()}"
        dest = getattr(dest_path, "key", None)
        if dest is None:
            dest = f"bucket/catalog/{Path(dest_path).relative_to(self.catalog_root).as_posix()}"
        self._fs.objects[dest] = self._fs.objects[source]
        return f"s3://{dest}"



# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestUnitMaps:
    def test_unit_map_values(self):
        assert _UNIT_MAP == {"m": "MINUTE", "h": "HOUR", "d": "DAY"}

    def test_unit_reverse_is_inverse(self):
        assert _UNIT_REVERSE == {v: k for k, v in _UNIT_MAP.items()}

    def test_unit_reverse_roundtrip(self):
        for short, long in _UNIT_MAP.items():
            assert _UNIT_REVERSE[long] == short


# ---------------------------------------------------------------------------
# _interval_to_nt
# ---------------------------------------------------------------------------


class TestIntervalToNt:
    @pytest.mark.parametrize(
        "interval, expected",
        [
            ("1m", "1-MINUTE"),
            ("5m", "5-MINUTE"),
            ("15m", "15-MINUTE"),
            ("1h", "1-HOUR"),
            ("4h", "4-HOUR"),
            ("1d", "1-DAY"),
            ("99m", "99-MINUTE"),
        ],
    )
    def test_converts(self, interval: str, expected: str):
        assert _interval_to_nt(interval) == expected

    @pytest.mark.parametrize("bad", ["", "5", "5x", "abc", "m5", "5M", "1ms"])
    def test_invalid_raises(self, bad: str):
        with pytest.raises(ValueError, match="Invalid interval"):
            _interval_to_nt(bad)

    def test_error_message_includes_input(self):
        with pytest.raises(ValueError, match="abc"):
            _interval_to_nt("abc")


# ---------------------------------------------------------------------------
# _nt_to_interval
# ---------------------------------------------------------------------------


class TestNtToInterval:
    @pytest.mark.parametrize(
        "suffix, expected",
        [
            ("1-MINUTE", "1m"),
            ("5-MINUTE", "5m"),
            ("15-MINUTE", "15m"),
            ("1-HOUR", "1h"),
            ("4-HOUR", "4h"),
            ("1-DAY", "1d"),
        ],
    )
    def test_converts(self, suffix: str, expected: str):
        assert _nt_to_interval(suffix) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "MINUTE",
            "5",
            "5-minute",     # regex is digit-WORD but _UNIT_REVERSE is case-sensitive
            "5-FOO",
            "abc-MINUTE",
        ],
    )
    def test_unknown_returns_none(self, bad: str):
        assert _nt_to_interval(bad) is None

    def test_round_trip_from_interval_to_nt_and_back(self):
        for interval in ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d"]:
            suffix = _interval_to_nt(interval)
            assert _nt_to_interval(suffix) == interval


class TestFetchBatchSplitting:
    def test_agg_trades_multi_day_range_splits_by_configured_days(self):
        from datetime import date

        ranges = _split_fetch_date_ranges(
            data_type="aggTrades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            max_days_per_job=1,
        )

        assert ranges == [
            (date(2024, 1, 1), date(2024, 1, 1)),
            (date(2024, 1, 2), date(2024, 1, 2)),
            (date(2024, 1, 3), date(2024, 1, 3)),
        ]

    def test_non_agg_trades_range_is_not_split(self):
        from datetime import date

        assert _split_fetch_date_ranges(
            data_type="klines",
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            max_days_per_job=1,
        ) == [(date(2024, 1, 1), date(2024, 1, 3))]

    def test_fetch_batch_rejects_empty_intervals_for_bar_data(self):
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=[],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        with pytest.raises(HTTPException) as exc:
            import asyncio

            asyncio.run(trigger_data_fetch_batch(body, AsyncMock(), AsyncMock(), SimpleNamespace()))

        assert exc.value.status_code == 400
        assert exc.value.detail == "intervals must not be empty"

    def test_fetch_batch_rejects_start_after_end(self):
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 2),
            end=date(2024, 1, 1),
        )

        with pytest.raises(HTTPException) as exc:
            import asyncio

            asyncio.run(trigger_data_fetch_batch(body, AsyncMock(), AsyncMock(), SimpleNamespace()))

        assert exc.value.status_code == 400
        assert exc.value.detail == "start must be on or before end"

    def test_fetch_batch_response_intervals_for_raw_tick_type_are_string_only(self):
        assert _fetch_batch_intervals("aggTrades", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("trades", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("bookTicker", ["1m", "5m"]) == []

    def test_fetch_batch_intervals_for_kline_types_fan_out(self):
        assert _fetch_batch_intervals("klines", ["1m", "5m"]) == ["1m", "5m"]
        assert _fetch_batch_intervals("markPriceKlines", ["1m", "5m"]) == ["1m", "5m"]

    def test_fetch_batch_agg_trades_default_interval_enqueues_one_intervalless_job(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="aggTrades",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["count"] == 1
        assert result["intervals"] == []
        assert result["jobs"] == [
            {
                "job_id": "job-1",
                "data_type": "aggTrades",
                "db_interval": "tick",
                "interval": None,
                "start": "2024-01-01",
                "end": "2024-01-01",
            }
        ]
        assert [job.interval for job in db.jobs] == [None]
        enqueue.assert_awaited_once_with(rds, "job-1")

    def test_fetch_batch_klines_fans_out_body_intervals(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m", "5m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["count"] == 2
        assert result["intervals"] == ["1m", "5m"]
        assert [job.interval for job in db.jobs] == ["1m", "5m"]
        assert enqueue.await_count == 2

    def test_fetch_batch_rejects_unknown_data_type_before_jobs(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="mystery",
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(trigger_data_fetch_batch(
                body,
                db,
                AsyncMock(),
                SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
            ))

        assert exc.value.status_code == 400
        assert "Unsupported data_type" in exc.value.detail
        assert db.jobs == []
        enqueue.assert_not_awaited()
    def test_fetch_batch_raw_tick_allows_empty_intervals(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=[],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="bookTicker",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["intervals"] == []
        assert result["jobs"][0]["db_interval"] == "tick"
        assert [job.interval for job in db.jobs] == [None]
        enqueue.assert_awaited_once_with(rds, "job-1")


class TestCancelDataFetchJob:
    def test_cancel_queued_job_updates_to_cancelled(self):
        import asyncio

        db = AsyncMock()
        update_result = MagicMock()
        update_result.rowcount = 1
        db.execute = AsyncMock(return_value=update_result)
        db.commit = AsyncMock()

        result = asyncio.run(cancel_data_fetch_job("job-queued", db))

        assert result == {"status": "cancelled", "job_id": "job-queued"}
        db.commit.assert_awaited_once()
        assert db.execute.await_count == 1
        assert "data_fetch_jobs.status =" in str(db.execute.await_args.args[0])

    def test_cancel_running_job_returns_conflict_without_status_update(self):
        import asyncio

        db = AsyncMock()
        update_result = MagicMock()
        update_result.rowcount = 0
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = SimpleNamespace(status="running")
        db.execute = AsyncMock(side_effect=[update_result, select_result])
        db.commit = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            asyncio.run(cancel_data_fetch_job("job-running", db))

        assert exc.value.status_code == 409
        assert "cannot be cancelled safely" in exc.value.detail
        db.commit.assert_awaited_once()


class TestSourceAwareBarMaintenance:
    def test_validate_data_resolves_source_aware_bar_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path

        calls = []

        def fake_validate_bars(*, symbol, interval, catalog_path, storage=None):
            calls.append((symbol, interval, catalog_path))
            return {"status": "ok"}

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", fake_validate_bars)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(validate_data(
            "BTCUSDT-PERP",
            "1m",
            data_type="markPriceKlines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [("BTCUSDT-PERP", "1m", str(resolve_catalog_path(tmp_path, "markPriceKlines")))]

    def test_validate_data_falls_back_to_legacy_flat_default_klines_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        legacy_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "bars.parquet").write_bytes(b"legacy")
        resolved_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        resolved_dir.mkdir(parents=True)

        calls = []

        def fake_validate_bars(*, symbol, interval, catalog_path, storage=None):
            calls.append((symbol, interval, catalog_path))
            return {"status": "ok"}

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", fake_validate_bars)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(validate_data(
            "BTCUSDT-PERP",
            "1m",
            data_type="klines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path))]

    def test_validate_data_passes_remote_storage_to_validate_bars(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path

        calls = []
        storage = SimpleNamespace(provider="s3", catalog_root=tmp_path)

        def fake_validate_bars(*, symbol, interval, catalog_path, storage=None):
            calls.append((symbol, interval, catalog_path, storage))
            return {"status": "ok"}

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", fake_validate_bars)
        monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: storage)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(validate_data(
            "BTCUSDT-PERP",
            "1m",
            data_type="markPriceKlines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [(
            "BTCUSDT-PERP",
            "1m",
            str(resolve_catalog_path(tmp_path, "markPriceKlines")),
            storage,
        )]

    def test_validate_bars_uses_remote_catalog_uri_and_storage_file_stats(self, tmp_path: Path, monkeypatch):
        from datetime import timedelta
        import sys
        import types

        from tinohelm.data.catalog import validate_bars

        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        key = f"bucket/catalog/data/bar/{bar_type_str}/bars.parquet"
        fs = _FakeS3FileSystem({key: b"payload"})
        storage = _CompactStorage(tmp_path, fs)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def _ts_ns(dt: datetime) -> int:
            return int(dt.timestamp() * 1_000_000_000)

        bars = [
            SimpleNamespace(ts_event=_ts_ns(t0), open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0),
            SimpleNamespace(ts_event=_ts_ns(t0 + timedelta(minutes=1)), open=100.5, high=102.0, low=100.0, close=101.0, volume=2.0),
        ]
        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        class FakeCatalog:
            init_calls = []
            from_uri_calls = []

            def __init__(self, catalog_path, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                if str(catalog_path).startswith("s3://"):
                    raise AssertionError("remote validator must pass bucket/key path, not an s3 URI")
                self.init_calls.append((catalog_path, fs_protocol, fs_storage_options, fs_rust_storage_options))

            @classmethod
            def from_uri(cls, *args, **kwargs):
                cls.from_uri_calls.append((args, kwargs))
                raise AssertionError("from_uri would merge fsspec's host into s3fs options")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: FakeBarType())
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = validate_bars("BTCUSDT-PERP", "1m", tmp_path, storage=storage)

        assert FakeCatalog.from_uri_calls == []
        assert FakeCatalog.init_calls == [
            ("bucket/catalog", "s3", storage.fs_storage_options, storage.fs_rust_storage_options)
        ]
        assert result["total_bars"] == 2
        assert result["file_count"] == 1
        assert result["size_bytes"] == len(b"payload")
        assert result["status"] == "ok"

    def test_run_compact_falls_back_to_legacy_flat_default_klines_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        legacy_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "bars.parquet").write_bytes(b"legacy")
        resolved_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        resolved_dir.mkdir(parents=True)

        calls = []
        statements = []

        def fake_compact_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"bars_count": 1, "size_before": 6, "size_after": 6}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                statements.append(stmt)
                return FakeResult()

        monkeypatch.setattr("tinohelm.data.catalog.compact_bars", fake_compact_bars)
        monkeypatch.setattr("tinohelm.db.session.get_session_factory", lambda: FakeSession)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        asyncio.run(_run_compact("BTCUSDT-PERP", "1m", settings, "klines", "klines"))

        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path))]
        assert statements
        assert "data_catalog.source_type = :source_type_1" in str(statements[0])

    def test_run_compact_waits_on_shared_catalog_lock(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        calls = []

        def fake_compact(storage, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"bars_count": 1, "size_before": 6, "size_after": 6}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return FakeResult()

        monkeypatch.setattr("tinohelm.api.routes.data._compact_bars_with_storage", fake_compact)
        monkeypatch.setattr("tinohelm.db.session.get_session_factory", lambda: FakeSession)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        async def scenario():
            lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", "klines", "1m"))
            await lock.acquire()
            try:
                task = asyncio.create_task(_run_compact("BTCUSDT-PERP", "1m", settings, "klines", "klines"))
                await asyncio.sleep(0.02)
                assert calls == []
            finally:
                lock.release()
            await task

        asyncio.run(scenario())

        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path / "bar" / "klines"))]

    def test_run_compact_updates_legacy_null_source_row_for_default_klines(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        legacy_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "bars.parquet").write_bytes(b"legacy-bytes")
        resolved_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        resolved_dir.mkdir(parents=True)

        calls = []

        def fake_compact_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"bars_count": 7, "size_before": 100, "size_after": 12}

        class Entry:
            size_bytes = 1
            record_count = 2
            source_type = None

        legacy_entry = Entry()
        execute_results = [None, legacy_entry]

        class FakeResult:
            def __init__(self, item):
                self.item = item

            def scalar_one_or_none(self):
                return self.item

        class FakeSession:
            committed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return FakeResult(execute_results.pop(0))

            async def commit(self):
                self.committed = True

        session = FakeSession()
        monkeypatch.setattr("tinohelm.data.catalog.compact_bars", fake_compact_bars)
        monkeypatch.setattr("tinohelm.db.session.get_session_factory", lambda: lambda: session)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        asyncio.run(_run_compact("BTCUSDT-PERP", "1m", settings, "klines", "klines"))

        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path))]
        assert legacy_entry.size_bytes == len(b"legacy-bytes")
        assert legacy_entry.record_count == 7
        assert legacy_entry.source_type is None
        assert session.committed
        assert execute_results == []

    def test_trigger_compact_passes_source_aware_bar_contract(self, tmp_path: Path):
        import asyncio

        class FakeBackgroundTasks:
            def __init__(self):
                self.calls = []

            def add_task(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        background = FakeBackgroundTasks()
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        body = CompactRequest(symbol="BTCUSDT-PERP", interval="1m", data_type="markPriceKlines")

        result = asyncio.run(trigger_compact(body, background, settings))

        assert result["status"] == "accepted"
        assert len(background.calls) == 1
        fn, args, kwargs = background.calls[0]
        assert fn.__name__ == "_run_compact"
        assert args == ("BTCUSDT-PERP", "1m", settings, "markPriceKlines", "markPriceKlines")
        assert kwargs == {}

    def test_remote_compact_writes_remote_nt_catalog_without_local_upload(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        bar_dir = tmp_path / "data" / "bar" / bar_type_str
        old_keys = [
            f"bucket/catalog/data/bar/{bar_type_str}/old-a.parquet",
            f"bucket/catalog/data/bar/{bar_type_str}/old-b.parquet",
        ]
        fs = _FakeS3FileSystem({old_keys[0]: b"old-a", old_keys[1]: b"old-b"})
        storage = _CompactStorage(tmp_path, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]
        compacted_name = "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"

        class FakeCatalog:
            init_calls = []
            from_uri_calls = []

            def __init__(self, catalog_path=None, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                if str(catalog_path).startswith("s3://"):
                    raise AssertionError("remote compaction must pass bucket/key path, not an s3 URI")
                self.catalog_path = catalog_path
                self.init_calls.append((catalog_path, fs_protocol, fs_storage_options, fs_rust_storage_options))

            @classmethod
            def from_uri(cls, *args, **kwargs):
                cls.from_uri_calls.append((args, kwargs))
                raise AssertionError("from_uri would merge fsspec's host into s3fs options")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    base = str(self.catalog_path)
                    fs.objects[f"{base}/data/bar/{bar_type_str}/{compacted_name}"] = b"new"

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        monkeypatch.setattr("tinohelm.api.routes.data.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = _compact_bars_with_storage(storage, "BTCUSDT-PERP", "1m", tmp_path)

        temp_key = f"bucket/catalog/.compaction/{bar_type_str}-abc123/data/bar/{bar_type_str}/{compacted_name}"
        final_key = f"bucket/catalog/data/bar/{bar_type_str}/{compacted_name}"
        backup_a_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/old-a.parquet"
        backup_b_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/old-b.parquet"
        assert result == {
            "files_before": 2,
            "files_after": 1,
            "bars_count": 2,
            "size_before": len(b"old-a") + len(b"old-b"),
            "size_after": len(b"new"),
        }
        assert FakeCatalog.from_uri_calls == []
        assert FakeCatalog.init_calls == [
            ("bucket/catalog", "s3", storage.fs_storage_options, storage.fs_rust_storage_options),
            (f"bucket/catalog/.compaction/{bar_type_str}-abc123", "s3", storage.fs_storage_options, storage.fs_rust_storage_options),
        ]
        assert fs.rm_calls == old_keys + [backup_a_key, backup_b_key, temp_key]
        assert fs.put_calls == []
        assert fs.objects == {final_key: b"new"}
        assert not bar_dir.exists()

    def test_remote_compact_keeps_same_named_final_file_when_overwriting_existing_object(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        compacted_name = "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"
        bar_dir = tmp_path / "data" / "bar" / bar_type_str
        final_key = f"bucket/catalog/data/bar/{bar_type_str}/{compacted_name}"
        other_old_key = f"bucket/catalog/data/bar/{bar_type_str}/old-b.parquet"
        fs = _FakeS3FileSystem({final_key: b"old-final", other_old_key: b"old-b"})
        storage = _CompactStorage(tmp_path, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]

        class FakeCatalog:
            def __init__(self, catalog_path=None, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                self.catalog_path = catalog_path

            @classmethod
            def from_uri(cls, *args, **kwargs):
                raise AssertionError("remote compaction must not use from_uri")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    base = str(self.catalog_path).removeprefix("s3://")
                    fs.objects[f"{base}/data/bar/{bar_type_str}/{compacted_name}"] = b"new"

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        monkeypatch.setattr("tinohelm.api.routes.data.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = _compact_bars_with_storage(storage, "BTCUSDT-PERP", "1m", tmp_path)

        temp_key = f"bucket/catalog/.compaction/{bar_type_str}-abc123/data/bar/{bar_type_str}/{compacted_name}"
        rollback_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/{compacted_name}"
        rollback_other_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/old-b.parquet"
        assert result == {
            "files_before": 2,
            "files_after": 1,
            "bars_count": 2,
            "size_before": len(b"old-final") + len(b"old-b"),
            "size_after": len(b"new"),
        }
        assert fs.rm_calls == [other_old_key, rollback_key, rollback_other_key, temp_key]
        assert fs.put_calls == []
        assert fs.objects == {final_key: b"new"}
        assert not bar_dir.exists()

    def test_remote_compact_restores_same_named_file_if_old_delete_fails(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        compacted_name = "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"
        bar_dir = tmp_path / "data" / "bar" / bar_type_str
        final_key = f"bucket/catalog/data/bar/{bar_type_str}/{compacted_name}"
        other_old_key = f"bucket/catalog/data/bar/{bar_type_str}/old-b.parquet"
        fs = _FakeS3FileSystem({final_key: b"old-final", other_old_key: b"old-b"})
        storage = _CompactStorage(tmp_path, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]

        class FakeCatalog:
            def __init__(self, catalog_path=None, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                self.catalog_path = catalog_path

            @classmethod
            def from_uri(cls, *args, **kwargs):
                raise AssertionError("remote compaction must not use from_uri")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    base = str(self.catalog_path).removeprefix("s3://")
                    fs.objects[f"{base}/data/bar/{bar_type_str}/{compacted_name}"] = b"new"

        def failing_rm(path: str) -> None:
            fs.rm_calls.append(path)
            if path == other_old_key:
                raise RuntimeError("delete failed")
            if path not in fs.objects:
                raise FileNotFoundError(path)
            del fs.objects[path]

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        monkeypatch.setattr("tinohelm.api.routes.data.uuid4", lambda: SimpleNamespace(hex="abc123"))
        fs.rm = failing_rm
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        with pytest.raises(RuntimeError, match="delete failed"):
            _compact_bars_with_storage(storage, "BTCUSDT-PERP", "1m", tmp_path)

        temp_key = f"bucket/catalog/.compaction/{bar_type_str}-abc123/data/bar/{bar_type_str}/{compacted_name}"
        rollback_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/{compacted_name}"
        rollback_other_key = f"bucket/catalog/.compaction-rollback/{bar_type_str}-abc123/old-b.parquet"
        assert fs.rm_calls == [other_old_key, final_key, rollback_key, rollback_other_key, temp_key]
        assert fs.objects == {final_key: b"old-final", other_old_key: b"old-b"}
        assert not bar_dir.exists()

    def test_remote_compact_leaves_old_objects_intact_if_write_fails(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        old_keys = [
            f"bucket/catalog/data/bar/{bar_type_str}/old-a.parquet",
            f"bucket/catalog/data/bar/{bar_type_str}/old-b.parquet",
        ]
        fs = _FakeS3FileSystem({old_keys[0]: b"old-a", old_keys[1]: b"old-b"})
        storage = _CompactStorage(tmp_path, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]

        class FakeCatalog:
            def __init__(self, catalog_path=None, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                self.catalog_path = catalog_path

            @classmethod
            def from_uri(cls, *args, **kwargs):
                raise AssertionError("remote compaction must not use from_uri")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    raise RuntimeError("compaction write failed")

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        with pytest.raises(RuntimeError, match="compaction write failed"):
            _compact_bars_with_storage(storage, "BTCUSDT-PERP", "1m", tmp_path)

        assert fs.rm_calls == []
        assert fs.objects == {old_keys[0]: b"old-a", old_keys[1]: b"old-b"}

    def test_remote_compact_cleans_temp_prefix_if_write_partially_fails(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        compacted_name = "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"
        old_keys = [
            f"bucket/catalog/data/bar/{bar_type_str}/old-a.parquet",
            f"bucket/catalog/data/bar/{bar_type_str}/old-b.parquet",
        ]
        fs = _FakeS3FileSystem({old_keys[0]: b"old-a", old_keys[1]: b"old-b"})
        storage = _CompactStorage(tmp_path, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]

        class FakeCatalog:
            def __init__(self, catalog_path=None, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
                self.catalog_path = catalog_path

            @classmethod
            def from_uri(cls, *args, **kwargs):
                raise AssertionError("remote compaction must not use from_uri")

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    base = str(self.catalog_path).removeprefix("s3://")
                    fs.objects[f"{base}/data/bar/{bar_type_str}/{compacted_name}"] = b"partial-new"
                    raise RuntimeError("compaction write failed after partial temp write")

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        monkeypatch.setattr("tinohelm.api.routes.data.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        with pytest.raises(RuntimeError, match="compaction write failed after partial temp write"):
            _compact_bars_with_storage(storage, "BTCUSDT-PERP", "1m", tmp_path)

        temp_key = f"bucket/catalog/.compaction/{bar_type_str}-abc123/data/bar/{bar_type_str}/{compacted_name}"
        assert fs.rm_calls == [temp_key]
        assert fs.objects == {old_keys[0]: b"old-a", old_keys[1]: b"old-b"}


    def test_local_compact_preserves_nt_parquet_basename_and_skips_same_name_delete(self, tmp_path: Path, monkeypatch):
        bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        compacted_name = "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"
        bar_dir = tmp_path / "data" / "bar" / bar_type_str
        bar_dir.mkdir(parents=True)
        same_name_file = bar_dir / compacted_name
        same_name_file.write_bytes(b"old-final")
        other_old = bar_dir / "old-b.parquet"
        other_old.write_bytes(b"old-b")

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=2), SimpleNamespace(ts_event=1)]

        class FakeCatalog:
            def __init__(self, catalog_path=None):
                self.catalog_path = Path(catalog_path)

            def bars(self, bar_types):
                assert bar_types == [bar_type_str]
                return bars

            def write_data(self, data, skip_disjoint_check=False):
                if data and hasattr(data[0], "ts_event"):
                    temp_bar_dir = self.catalog_path / "data" / "bar" / bar_type_str
                    temp_bar_dir.mkdir(parents=True, exist_ok=True)
                    (temp_bar_dir / compacted_name).write_bytes(b"new")

        import sys
        import types

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda instrument_id, interval: bar_type)
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = compact_bars("BTCUSDT-PERP", "1m", tmp_path)

        assert result == {
            "files_before": 2,
            "files_after": 1,
            "bars_count": 2,
            "size_before": len(b"old-final") + len(b"old-b"),
            "size_after": len(b"new"),
        }
        assert same_name_file.read_bytes() == b"new"
        assert not other_old.exists()


# ---------------------------------------------------------------------------
# _parquet_size_for
# ---------------------------------------------------------------------------


class TestParquetSizeFor:
    def test_returns_zero_when_dir_missing(self, tmp_path: Path):
        # No bar directory at all
        assert _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5m") == 0

    def test_sums_parquet_files(self, tmp_path: Path):
        # Build the expected directory for normalized symbol
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "a.parquet").write_bytes(b"x" * 100)
        (bar_dir / "b.parquet").write_bytes(b"y" * 200)
        # Non-parquet files must be ignored
        (bar_dir / "ignore.txt").write_bytes(b"z" * 1000)

        assert _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5m") == 300

    def test_invalid_interval_propagates_value_error(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid interval"):
            _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5x")


# ---------------------------------------------------------------------------
# _delete_storage_files
# ---------------------------------------------------------------------------


class TestDeleteStorageFiles:
    def test_bar_deletes_parquet_and_dir(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "a.parquet").write_bytes(b"x" * 50)
        (bar_dir / "b.parquet").write_bytes(b"y" * 75)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        )
        assert deleted == 2
        assert freed == 125
        assert not bar_dir.exists()

    def test_bar_noop_when_dir_missing(self, tmp_path: Path):
        assert _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        ) == (0, 0)

    def test_bar_source_aware_non_default_does_not_delete_flat_legacy(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        dir_name = f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        mark_dir = resolve_catalog_path(tmp_path, "markPriceKlines") / "data" / "bar" / dir_name
        mark_dir.mkdir(parents=True)
        mark_path = mark_dir / "mark.parquet"
        mark_path.write_bytes(b"x" * 30)
        legacy_dir = tmp_path / "data" / "bar" / dir_name
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path), "markPriceKlines"
        )

        assert deleted == 1
        assert freed == 30
        assert not mark_path.exists()
        assert legacy_path.exists()

    def test_unknown_non_default_source_type_does_not_delete_flat_legacy(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        dir_name = f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        legacy_dir = tmp_path / "data" / "bar" / dir_name
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path), "unknownSource"
        )

        assert deleted == 0
        assert freed == 0
        assert legacy_path.exists()

    def test_trade_tick_deletes_parquet(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        (tick_dir / "a.parquet").write_bytes(b"x" * 30)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path)
        )
        assert deleted == 1
        assert freed == 30

    def test_trade_tick_deletes_source_aware_agg_trades(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        parquet_path = tick_dir / "a.parquet"
        parquet_path.write_bytes(b"x" * 30)
        legacy_tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_parquet_path = legacy_tick_dir / "legacy.parquet"
        legacy_parquet_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path), "aggTrades"
        )
        assert deleted == 2
        assert freed == 50
        assert not parquet_path.exists()
        assert not legacy_parquet_path.exists()

    def test_trade_tick_deletes_source_aware_trades_without_flat_legacy_fallback(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        trades_dir = resolve_catalog_path(tmp_path, "trades") / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        trades_path = trades_dir / "trades.parquet"
        trades_path.write_bytes(b"x" * 30)
        legacy_tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_path = legacy_tick_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path), "trades"
        )

        assert deleted == 1
        assert freed == 30
        assert not trades_path.exists()
        assert legacy_path.exists()

    def test_quote_tick_handled(self, tmp_path: Path):
        # No real data: exercises the branch and verifies (0, 0) default
        assert _delete_storage_files(
            "BTCUSDT-PERP", "quote_tick", "tick", str(tmp_path)
        ) == (0, 0)

    def test_quote_tick_deletes_source_aware_book_ticker(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        parquet_path = tick_dir / "a.parquet"
        parquet_path.write_bytes(b"x" * 40)
        legacy_tick_dir = tmp_path / "data" / "quote_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_parquet_path = legacy_tick_dir / "legacy.parquet"
        legacy_parquet_path.write_bytes(b"y" * 10)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "quote_tick", "tick", str(tmp_path), "bookTicker"
        )
        assert deleted == 2
        assert freed == 50
        assert not parquet_path.exists()
        assert not legacy_parquet_path.exists()

    def test_metrics_deletes_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        parquet_path = metrics_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 25)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "metrics", "5m", str(tmp_path)
        )

        assert deleted == 1
        assert freed == 25
        assert not parquet_path.exists()

    def test_order_book_delta_deletes_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path

        parquet_path = book_depth_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 35)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "order_book_delta", "tick", str(tmp_path)
        )

        assert deleted == 1
        assert freed == 35
        assert not parquet_path.exists()

    def test_funding_rate_deletes_parquet(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths
        from tinohelm.data.catalog import funding_rate_parquet_path

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        _paths.override("funding_rates", cache_dir)
        parquet_path = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 45)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()

        assert deleted == 1
        assert freed == 45
        assert not parquet_path.exists()

    def test_funding_rate_deletes_parquet_and_legacy_json(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths
        from tinohelm.data.catalog import funding_rate_parquet_path

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        target = cache_dir / "btcusdt-perp.json"
        target.write_bytes(b'[{"funding": 0.0001}]')
        _paths.override("funding_rates", cache_dir)
        parquet_path = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 45)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()

        assert deleted == 2
        assert freed == 45 + len(b'[{"funding": 0.0001}]')
        assert not parquet_path.exists()
        assert not target.exists()

    def test_funding_rate_deletes_json(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        target = cache_dir / "btcusdt-perp.json"
        target.write_bytes(b'[{"funding": 0.0001}]')
        _paths.override("funding_rates", cache_dir)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()
        assert deleted == 1
        assert freed == len(b'[{"funding": 0.0001}]')
        assert not target.exists()

    def test_funding_rate_missing_is_noop(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        _paths.override("funding_rates", cache_dir)
        try:
            result = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()
        assert result == (0, 0)

    def test_unknown_data_type_warns_and_returns_zero(self, tmp_path: Path, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="tinohelm.api.routes.data")
        result = _delete_storage_files(
            "BTCUSDT-PERP", "mystery", "irrelevant", str(tmp_path)
        )
        assert result == (0, 0)
        assert any("No storage handler" in r.getMessage() for r in caplog.records)

    def test_bar_removes_empty_dir(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "only.parquet").write_bytes(b"a" * 10)

        _delete_storage_files("BTCUSDT-PERP", "bar", "5m", str(tmp_path))
        # Directory was pruned because it was empty post-delete
        assert not bar_dir.exists()

    def test_bar_keeps_dir_if_non_parquet_remains(self, tmp_path: Path):
        """If there are other files in the dir (e.g. metadata), only parquet are removed and dir stays."""
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "one.parquet").write_bytes(b"x" * 5)
        (bar_dir / "meta.txt").write_bytes(b"keep")

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        )
        assert deleted == 1
        assert freed == 5
        assert bar_dir.exists()
        assert (bar_dir / "meta.txt").exists()


class TestDeleteCatalogEntry:
    async def test_delete_waits_on_legacy_default_catalog_lock(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        row = SimpleNamespace(
            id=123,
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="1m",
            source_type=None,
        )
        calls = []

        class FakeResult:
            def scalar_one_or_none(self):
                return row

        db = SimpleNamespace(
            execute=AsyncMock(return_value=FakeResult()),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        def fake_delete_storage_files(*args):
            calls.append(args)
            return (2, 17)

        monkeypatch.setattr(
            "tinohelm.api.routes.data._delete_storage_files",
            fake_delete_storage_files,
        )

        lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", "klines", "1m"))
        await lock.acquire()
        try:
            task = asyncio.create_task(delete_catalog_entry(123, db=db, settings=settings))
            await asyncio.sleep(0.02)
            assert calls == []
            db.delete.assert_not_awaited()
        finally:
            lock.release()

        result = await task

        assert result["status"] == "deleted"
        assert result["deleted_files"] == 2
        assert calls == [("BTCUSDT-PERP", "bar", "1m", str(tmp_path), None)]
        db.delete.assert_awaited_once_with(row)
        db.commit.assert_awaited_once()

    @pytest.mark.parametrize(
        ("row_data_type", "row_interval", "worker_data_type"),
        [
            ("funding_rate", "8h", "fundingRate"),
            ("order_book_delta", "tick", "bookDepth"),
        ],
    )
    async def test_delete_waits_on_non_bar_legacy_default_source_lock(
        self,
        row_data_type: str,
        row_interval: str,
        worker_data_type: str,
        tmp_path: Path,
        monkeypatch,
    ):
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        row = SimpleNamespace(
            id=123,
            symbol="BTCUSDT-PERP",
            data_type=row_data_type,
            interval=row_interval,
            source_type=None,
        )
        calls = []

        class FakeResult:
            def scalar_one_or_none(self):
                return row

        db = SimpleNamespace(
            execute=AsyncMock(return_value=FakeResult()),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        monkeypatch.setattr(
            "tinohelm.api.routes.data._delete_storage_files",
            lambda *args: calls.append(args) or (1, 3),
        )

        lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", worker_data_type, None))
        await lock.acquire()
        try:
            task = asyncio.create_task(delete_catalog_entry(123, db=db, settings=settings))
            await asyncio.sleep(0.02)
            assert calls == []
        finally:
            lock.release()

        await task

        assert calls == [("BTCUSDT-PERP", row_data_type, row_interval, str(tmp_path), None)]


class TestScanDataCatalog:
    @staticmethod
    def _empty_db(added_rows: list):
        execute_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(
            execute=AsyncMock(return_value=execute_result),
            add=MagicMock(side_effect=added_rows.append),
            commit=AsyncMock(),
        )

    async def test_scan_creates_source_aware_book_ticker_quote_tick(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        quote_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        quote_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(
            quote_dir / "quotes.parquet"
        )

        added_rows = []
        execute_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=execute_result),
            add=MagicMock(side_effect=added_rows.append),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert isinstance(row, DataCatalog)
        assert row.symbol == symbol
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.source_type == "bookTicker"
        assert row.record_count == 1
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 1)

    async def test_scan_reads_remote_source_aware_quote_tick_without_local_dirs(self, tmp_path: Path, monkeypatch):
        from tinohelm.db.models import DataCatalog

        class _RemoteScanStorage:
            provider = "s3"

            def __init__(self, catalog_root: Path, objects: dict[str, bytes]) -> None:
                self.catalog_root = catalog_root
                self.objects = objects

            def iter_files(self, prefix: Path | str, suffix: str = "", recursive: bool = True):
                root = Path(prefix)
                prefix_rel = root.relative_to(self.catalog_root).as_posix().rstrip("/")
                for key, payload in sorted(self.objects.items()):
                    if not key.startswith(prefix_rel + "/"):
                        continue
                    rel = key[len(prefix_rel) + 1 :]
                    if suffix and not rel.endswith(suffix):
                        continue
                    obj = SimpleNamespace(
                        key=key,
                        path=self.catalog_root / key,
                        size=len(payload),
                        last_modified=None,
                    )
                    yield obj

            def open_input_file(self, path_or_object):
                key = getattr(path_or_object, "key", None)
                if key is None:
                    key = str(Path(path_or_object).relative_to(self.catalog_root))
                return BytesIO(self.objects[key])

        source_root = tmp_path / "quotes" / "bookTicker"
        rel_dir = "quotes/bookTicker/data/quote_tick/BTCUSDT-PERP.BINANCE"
        tmp_file = tmp_path / "quotes.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(tmp_file)
        objects = {f"{rel_dir}/quotes.parquet": tmp_file.read_bytes()}
        storage = _RemoteScanStorage(tmp_path, objects)

        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: storage)
        added_rows = []
        db = self._empty_db(added_rows)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert isinstance(row, DataCatalog)
        assert row.symbol == "BTCUSDT-PERP"
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.source_type == "bookTicker"
        assert row.record_count == 1
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 1)
        assert row.file_path == str(source_root)
        assert not (tmp_path / "quotes" / "bookTicker" / "data").exists()

    async def test_scan_combines_source_aware_and_legacy_book_ticker_stats(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "bookTicker")
        source_dir = source_root / "data" / "quote_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "quote_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_dir / "source.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        expected_size = (source_dir / "source.parquet").stat().st_size + (legacy_dir / "legacy.parquet").stat().st_size
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert row.symbol == symbol
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.source_type == "bookTicker"
        assert row.record_count == 5
        assert row.size_bytes == expected_size
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 5)
        assert row.file_path == str(source_root)

    async def test_scan_book_ticker_unknown_legacy_count_clears_existing_record_count(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "bookTicker")
        source_dir = source_root / "data" / "quote_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "quote_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        source_path = source_dir / "source.parquet"
        legacy_path = legacy_dir / "legacy.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_path
        )
        legacy_path.write_bytes(b"not parquet")
        legacy_mtime = datetime(2025, 1, 5, tzinfo=timezone.utc).timestamp()
        legacy_path.touch()
        import os

        os.utime(legacy_path, (legacy_mtime, legacy_mtime))
        expected_size = source_path.stat().st_size + legacy_path.stat().st_size
        existing_row = DataCatalog(
            symbol=symbol,
            data_type="quote_tick",
            interval="tick",
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 2),
            file_path=str(tmp_path),
            size_bytes=1,
            record_count=99,
            source_type="bookTicker",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: existing_row)),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["updated"] == 1
        db.add.assert_not_called()
        assert existing_row.record_count is None
        assert existing_row.size_bytes == expected_size
        assert existing_row.start_date == date(2025, 1, 1)
        assert existing_row.end_date == date(2025, 1, 5)
        assert existing_row.file_path == str(source_root)
        assert existing_row.source_type == "bookTicker"

    async def test_scan_combines_source_aware_and_legacy_agg_trades_stats(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "aggTrades")
        source_dir = source_root / "data" / "trade_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "trade_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_dir / "source.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        expected_size = (source_dir / "source.parquet").stat().st_size + (legacy_dir / "legacy.parquet").stat().st_size
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert row.symbol == symbol
        assert row.data_type == "trade_tick"
        assert row.interval == "tick"
        assert row.source_type == "aggTrades"
        assert row.record_count == 5
        assert row.size_bytes == expected_size
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 5)
        assert row.file_path == str(source_root)

    async def test_scan_keeps_source_aware_trades_separate_from_legacy_agg_trades(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        trades_root = resolve_catalog_path(tmp_path, "trades")
        trades_dir = trades_root / "data" / "trade_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            trades_dir / "trades.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 2
        rows = {(row.source_type, row.file_path): row for row in added_rows}
        trades_row = rows[("trades", str(trades_root))]
        agg_row = rows[("aggTrades", str(tmp_path))]
        assert trades_row.record_count == 2
        assert agg_row.record_count == 3

    async def test_scan_non_default_tick_does_not_adopt_null_default_row(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        trades_root = resolve_catalog_path(tmp_path, "trades")
        trades_dir = trades_root / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(trades_dir / "trades.parquet")
        legacy_row = DataCatalog(
            symbol=symbol,
            data_type="trade_tick",
            interval="tick",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 1),
            file_path=str(tmp_path),
            size_bytes=1,
            record_count=1,
            source_type=None,
        )
        added_rows = []

        async def execute(stmt):
            text = str(stmt)
            if "source_type IS NULL" in text:
                return SimpleNamespace(scalar_one_or_none=lambda: legacy_row)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute), add=MagicMock(side_effect=added_rows.append), commit=AsyncMock())
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["created"] == 1
        assert result["updated"] == 0
        assert legacy_row.source_type is None
        assert added_rows[0].source_type == "trades"
