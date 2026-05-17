"""Tests for pure helpers in tinohelm.api.routes.data.

Covers interval ⇄ NT-suffix conversion, parquet size calculation, and the
storage-file deletion helper used by DELETE /api/data/catalog/{id}.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from fastapi import HTTPException

from tinohelm.api.routes.data import (
    CompactRequest,
    ConsolidateByPeriodRequest,
    DataFetchBatchRequest,
    DeleteRangeRequest,
    ResetFileNamesRequest,
    _fetch_batch_intervals,
    _split_fetch_date_ranges,
    _run_compact,
    cancel_data_fetch_job,
    consolidate_by_period,
    delete_catalog_entry,
    delete_range,
    list_data_catalog,
    list_data_types,
    reset_file_names,
    router,
    trigger_compact,
    trigger_data_fetch_batch,
    get_data_fetch_job,
    validate_data,
)
from tinohelm.data.catalog import CatalogSession, compact_bars


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



# Interval ⇄ NT-suffix tests live in tests/data/test_catalog_helpers.py
# (::TestIntervalToNTSuffix / ::TestNTSuffixToInterval) — the route-level
# aliases were removed in Issue #156 PR2.


class TestFetchBatchSplitting:
    def test_trades_multi_day_range_splits_by_configured_days(self):
        from datetime import date

        ranges = _split_fetch_date_ranges(
            data_type="trades",
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
        assert _fetch_batch_intervals("trades", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("bookTicker", ["1m", "5m"]) == []

    def test_fetch_batch_intervals_for_klines_fan_out(self):
        assert _fetch_batch_intervals("klines", ["1m", "5m"]) == ["1m", "5m"]
        assert _fetch_batch_intervals("markPriceKlines", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("indexPriceKlines", ["1m", "5m"]) == []

    def test_fetch_batch_trades_default_interval_enqueues_one_intervalless_job(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="trade_tick",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
        ))

        assert result["count"] == 1
        assert result["intervals"] == []
        assert result["jobs"] == [
            {
                "job_id": "job-1",
                "data_type": "trade_tick",
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
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
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
                SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
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
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
        ))

        assert result["intervals"] == []
        assert result["jobs"][0]["db_interval"] == "tick"
        assert [job.interval for job in db.jobs] == [None]
        enqueue.assert_awaited_once_with(rds, "job-1")

    def test_fetch_batch_accepts_canonical_quote_tick_type(self, monkeypatch):
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
            data_type="quote_tick",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
        ))

        assert result["data_type"] == "quote_tick"
        assert result["jobs"][0]["data_type"] == "quote_tick"
        assert db.jobs[0].data_type == "bookTicker"
        assert db.jobs[0].interval is None
        enqueue.assert_awaited_once_with(rds, "job-1")

    def test_fetch_batch_accepts_canonical_mark_price_type(self, monkeypatch):
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
            data_type="mark_price",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
        ))

        assert result["data_type"] == "mark_price"
        assert result["intervals"] == []
        assert result["jobs"][0]["data_type"] == "mark_price"
        assert result["jobs"][0]["db_interval"] == "tick"
        assert db.jobs[0].data_type == "markPriceKlines"
        assert db.jobs[0].interval is None
        enqueue.assert_awaited_once_with(rds, "job-1")

    def test_fetch_batch_accepts_canonical_index_price_type(self, monkeypatch):
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
            data_type="index_price",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=30)),
        ))

        assert result["data_type"] == "index_price"
        assert result["intervals"] == []
        assert result["jobs"][0]["data_type"] == "index_price"
        assert result["jobs"][0]["db_interval"] == "tick"
        assert db.jobs[0].data_type == "indexPriceKlines"
        assert db.jobs[0].interval is None
        enqueue.assert_awaited_once_with(rds, "job-1")


class TestFetchBatchIdentity:
    """Issue #163: one fetch-batch submission = one FetchBatch (shared batch_id)."""

    def _run_batch(self, body: DataFetchBatchRequest, monkeypatch) -> _FetchBatchDb:
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            AsyncMock(),
            SimpleNamespace(data=SimpleNamespace(tick_max_days_per_job=1)),
        ))
        return db

    def test_fetch_batch_assigns_one_shared_batch_id_across_fanout(self, monkeypatch):
        # 2 symbols x 1 interval x 3 days (trades split by 1 day) = 6 jobs,
        # but they all belong to the same FetchBatch, so share one batch_id.
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            intervals=["1m", "5m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            data_type="trade_tick",
        )

        db = self._run_batch(body, monkeypatch)

        batch_ids = {job.batch_id for job in db.jobs}
        assert len(db.jobs) >= 2, "fan-out should produce multiple jobs"
        assert len(batch_ids) == 1, (
            f"all jobs from one fetch-batch submission must share batch_id, got {batch_ids}"
        )
        only_batch_id = next(iter(batch_ids))
        assert only_batch_id, "batch_id must be non-empty"
        # Shape check: UUID-like string (36 chars with hyphens).
        assert isinstance(only_batch_id, str) and len(only_batch_id) == 36

    def test_fetch_batch_single_job_submission_still_has_batch_id(self, monkeypatch):
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        db = self._run_batch(body, monkeypatch)

        assert len(db.jobs) == 1
        assert db.jobs[0].batch_id
        assert isinstance(db.jobs[0].batch_id, str)

    def test_two_fetch_batch_submissions_get_distinct_batch_ids(self, monkeypatch):
        body1 = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )
        body2 = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        db1 = self._run_batch(body1, monkeypatch)
        db2 = self._run_batch(body2, monkeypatch)

        batch_id_1 = db1.jobs[0].batch_id
        batch_id_2 = db2.jobs[0].batch_id
        assert batch_id_1 and batch_id_2
        assert batch_id_1 != batch_id_2, (
            "distinct fetch-batch submissions must produce distinct batch_id values"
        )


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


class TestDataFetchJobJobApi:
    def test_job_payload_includes_started_at(self):
        import asyncio

        row = SimpleNamespace(
            job_id="job-1",
            symbol="BTCUSDT-PERP",
            data_type="klines",
            interval="1m",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            status="running",
            progress=42,
            message="Starting...",
            error=None,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            started_at=datetime(2026, 1, 1, 12, 3, 0),
            completed_at=None,
        )
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        db.execute = AsyncMock(return_value=result)

        payload = asyncio.run(get_data_fetch_job("job-1", db))

        assert payload["started_at"] == "2026-01-01T12:03:00Z"
        assert payload["completed_at"] is None


class TestSourceAwareBarMaintenance:
    def test_validate_data_rejects_out_of_scope_premium_index(self, tmp_path: Path, monkeypatch):
        import asyncio

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", lambda **kwargs: {"status": "ok"})
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(validate_data(
                "BTCUSDT-PERP",
                "1m",
                data_type="premiumIndexKlines",
                settings=settings,
            ))

        assert exc_info.value.status_code == 400
        assert "Unsupported bar data_type" in exc_info.value.detail

    def test_validate_data_uses_single_catalog_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "bars.parquet").write_bytes(b"data")

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
        storage = SimpleNamespace(
            provider="s3",
            catalog_root=tmp_path,
            iter_files=lambda *args, **kwargs: [],
        )

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
            data_type="klines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [(
            "BTCUSDT-PERP",
            "1m",
            str(resolve_catalog_path(tmp_path, "klines")),
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

    def test_run_compact_uses_single_catalog_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "bars.parquet").write_bytes(b"data")

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

        assert len(calls) == 1
        assert calls[0][0] == "BTCUSDT-PERP"
        assert calls[0][1] == "1m"
        assert Path(calls[0][2]) == tmp_path
        assert statements
        assert "data_catalog.source_type = :source_type_1" in str(statements[0])

    def test_run_compact_waits_on_shared_catalog_lock(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        calls = []

        def fake_compact(self, symbol, interval):
            calls.append((symbol, interval))
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

        monkeypatch.setattr("tinohelm.data.catalog.CatalogSession.compact_bars", fake_compact)
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

        assert calls == [("BTCUSDT-PERP", "1m")]

    def test_run_compact_updates_catalog_row_for_klines(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "bars.parquet").write_bytes(b"legacy-bytes")

        calls = []

        def fake_compact_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"bars_count": 7, "size_before": 100, "size_after": 12}

        class Entry:
            size_bytes = 1
            record_count = 2
            source_type = "klines"

        entry = Entry()

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
                return FakeResult(entry)

            async def commit(self):
                self.committed = True

        session = FakeSession()
        monkeypatch.setattr("tinohelm.data.catalog.compact_bars", fake_compact_bars)
        monkeypatch.setattr("tinohelm.db.session.get_session_factory", lambda: lambda: session)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        asyncio.run(_run_compact("BTCUSDT-PERP", "1m", settings, "klines", "klines"))

        assert len(calls) == 1
        assert calls[0][0] == "BTCUSDT-PERP"
        assert calls[0][1] == "1m"
        assert Path(calls[0][2]) == tmp_path
        assert entry.size_bytes == len(b"legacy-bytes")
        assert entry.record_count == 7
        assert entry.source_type == "klines"
        assert session.committed

    def test_trigger_compact_rejects_out_of_scope_premium_index(self, tmp_path: Path):
        import asyncio

        class FakeBackgroundTasks:
            def __init__(self):
                self.calls = []

            def add_task(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        background = FakeBackgroundTasks()
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        body = CompactRequest(symbol="BTCUSDT-PERP", interval="1m", data_type="premiumIndexKlines")

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(trigger_compact(body, background, settings))

        assert exc_info.value.status_code == 400
        assert "Unsupported bar data_type" in exc_info.value.detail
        assert not background.calls

    def test_trigger_compact_rejects_public_mark_price_contract(self, tmp_path: Path):
        import asyncio

        class FakeBackgroundTasks:
            def __init__(self):
                self.calls = []

            def add_task(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        background = FakeBackgroundTasks()
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        body = CompactRequest(symbol="BTCUSDT-PERP", interval="1m", data_type="mark_price")

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(trigger_compact(body, background, settings))

        assert exc_info.value.status_code == 400
        assert "Unsupported bar data_type" in exc_info.value.detail
        assert not background.calls

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
        monkeypatch.setattr("tinohelm.data.catalog.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = CatalogSession(tmp_path, storage=storage).compact_bars("BTCUSDT-PERP", "1m")

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
        monkeypatch.setattr("tinohelm.data.catalog.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        result = CatalogSession(tmp_path, storage=storage).compact_bars("BTCUSDT-PERP", "1m")

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
        monkeypatch.setattr("tinohelm.data.catalog.uuid4", lambda: SimpleNamespace(hex="abc123"))
        fs.rm = failing_rm
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        with pytest.raises(RuntimeError, match="delete failed"):
            CatalogSession(tmp_path, storage=storage).compact_bars("BTCUSDT-PERP", "1m")

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
            CatalogSession(tmp_path, storage=storage).compact_bars("BTCUSDT-PERP", "1m")

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
        monkeypatch.setattr("tinohelm.data.catalog.uuid4", lambda: SimpleNamespace(hex="abc123"))
        nt_mod = types.ModuleType("nautilus_trader")
        persistence_mod = types.ModuleType("nautilus_trader.persistence")
        catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
        catalog_mod.ParquetDataCatalog = FakeCatalog
        monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
        monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

        with pytest.raises(RuntimeError, match="compaction write failed after partial temp write"):
            CatalogSession(tmp_path, storage=storage).compact_bars("BTCUSDT-PERP", "1m")

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


# Parquet-size-for tests live in tests/data/test_catalog_session.py
# (::TestParquetSizeFor) — migrated to CatalogSession in Issue #156 PR2.
# Delete-storage-files branch tests live in tests/data/test_catalog_session.py
# (::TestDeleteStorage*) — route now delegates to CatalogSession.delete_storage.


class TestNtNativeMaintenanceRoutes:
    def test_reset_file_names_calls_catalog_reset(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeCatalog:
            def reset_all_file_names(self):
                calls.append(("reset_all_file_names",))

        monkeypatch.setattr("tinohelm.api.routes.data._catalog_for_root", lambda catalog_root, storage=None: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: SimpleNamespace(catalog_root=tmp_path))
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(reset_file_names(
            ResetFileNamesRequest(),
            settings,
        ))

        assert result == {
            "status": "ok",
            "verb": "reset-file-names",
            "scope": "catalog",
        }
        assert calls == [("reset_all_file_names",)]

    def test_consolidate_by_period_calls_catalog_verb_with_optional_bounds(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeCatalog:
            def consolidate_catalog_by_period(self, *, period, start=None, end=None):
                calls.append((period, start, end))

        monkeypatch.setattr("tinohelm.api.routes.data._catalog_for_root", lambda catalog_root, storage=None: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: SimpleNamespace(catalog_root=tmp_path))
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(consolidate_by_period(
            ConsolidateByPeriodRequest(period="1d"),
            settings,
        ))

        assert result == {
            "status": "ok",
            "verb": "consolidate-by-period",
            "scope": "catalog",
            "period": "1d",
        }
        assert calls == [(timedelta(days=1), None, None)]

    def test_delete_range_calls_catalog_verb_with_start_and_end(self, tmp_path: Path, monkeypatch):
        from datetime import datetime, timezone

        calls = []

        class FakeCatalog:
            def delete_catalog_range(self, *, start=None, end=None):
                calls.append((start, end))

        monkeypatch.setattr("tinohelm.api.routes.data._catalog_for_root", lambda catalog_root, storage=None: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: SimpleNamespace(catalog_root=tmp_path))
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(delete_range(
            DeleteRangeRequest(
                start="2024-01-01T00:00:00Z",
                end="2024-01-02T00:00:00Z",
            ),
            settings,
        ))

        assert result == {
            "status": "ok",
            "verb": "delete-range",
            "scope": "catalog",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-02T00:00:00Z",
        }
        assert calls == [
            (
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            )
        ]

    @pytest.mark.parametrize("period", ["", "xd", "1w", "0d", "-1h"])
    def test_parse_period_rejects_invalid_periods(self, period):
        from tinohelm.api.routes.data import _parse_period

        with pytest.raises(HTTPException):
            _parse_period(period)

    @pytest.mark.parametrize(
        ("start", "end", "detail", "error_type"),
        [
            (None, "2024-01-02T00:00:00Z", "Input should be a valid string", "validation"),
            ("2024-01-01T00:00:00Z", None, "Input should be a valid string", "validation"),
            ("2024-01-03T00:00:00Z", "2024-01-02T00:00:00Z", "start must be on or before end", "http"),
        ],
    )
    def test_delete_range_rejects_unsafe_or_invalid_bounds(self, start, end, detail, error_type, tmp_path: Path):
        from pydantic_core import ValidationError

        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        if error_type == "validation":
            with pytest.raises(ValidationError) as exc_info:
                DeleteRangeRequest(start=start, end=end)
            assert detail in str(exc_info.value)
            return

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(delete_range(DeleteRangeRequest(start=start, end=end), settings))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == detail

    async def test_catalog_maintenance_waits_on_global_lock(self, tmp_path: Path, monkeypatch):
        import threading

        from tinohelm.data.catalog_locks import get_catalog_lock

        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        class FakeCatalog:
            def reset_all_file_names(self):
                started.set()
                assert release.wait(timeout=1)
                calls.append("reset")

        monkeypatch.setattr("tinohelm.api.routes.data._catalog_for_root", lambda catalog_root, storage=None: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.storage.get_active_catalog_root", lambda settings=None: tmp_path)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **kwargs: SimpleNamespace(catalog_root=tmp_path))
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        task = asyncio.create_task(reset_file_names(ResetFileNamesRequest(), settings))
        while not started.is_set():
            await asyncio.sleep(0.001)

        lock = get_catalog_lock("data_catalog:__global__")
        assert lock.locked()
        release.set()
        result = await task

        assert result == {"status": "ok", "verb": "reset-file-names", "scope": "catalog"}
        assert calls == ["reset"]
        assert not lock.locked()


class TestDeleteCatalogEntry:
    async def test_delete_waits_on_catalog_lock(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        row = SimpleNamespace(
            id=123,
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="1m",
            source_type=None,
        )
        catalog_id = "BTCUSDT-PERP|bar|1m|"
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

        def fake_delete_storage(self, symbol, data_type, interval, *, source_type=None):
            calls.append((str(self.catalog_path), symbol, data_type, interval, source_type))
            return (2, 17)

        monkeypatch.setattr(CatalogSession, "delete_storage", fake_delete_storage)

        lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", "klines", "1m"))
        await lock.acquire()
        try:
            task = asyncio.create_task(delete_catalog_entry(catalog_id, db=db, settings=settings))
            await asyncio.sleep(0.02)
            assert calls == []
            db.delete.assert_not_awaited()
        finally:
            lock.release()

        result = await task

        assert result["status"] == "deleted"
        assert result["deleted_files"] == 2
        assert calls == [(str(tmp_path), "BTCUSDT-PERP", "bar", "1m", None)]
        db.delete.assert_awaited_once_with(row)
        db.commit.assert_awaited_once()

    async def test_delete_finishes_storage_and_db_after_request_cancellation(self, tmp_path: Path, monkeypatch):
        import threading

        from tinohelm.data.catalog import CatalogSession

        row = SimpleNamespace(
            id=123,
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="1m",
            source_type="klines",
        )
        catalog_id = "BTCUSDT-PERP|bar|1m|klines"
        started = threading.Event()
        release = threading.Event()
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

        def fake_delete_storage(self, symbol, data_type, interval, *, source_type=None):
            calls.append((str(self.catalog_path), symbol, data_type, interval, source_type))
            started.set()
            assert release.wait(timeout=1)
            return (2, 17)

        monkeypatch.setattr(CatalogSession, "delete_storage", fake_delete_storage)

        task = asyncio.create_task(delete_catalog_entry(catalog_id, db=db, settings=settings))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert calls == [(str(tmp_path), "BTCUSDT-PERP", "bar", "1m", "klines")]
        db.delete.assert_awaited_once_with(row)
        db.commit.assert_awaited_once()

    @pytest.mark.parametrize(
        ("row_data_type", "row_interval", "lock_data_type"),
        [
            ("funding_rate", "8h", "fundingRate"),
            ("order_book_delta", "tick", "order_book_delta"),
        ],
    )
    async def test_delete_waits_on_non_bar_catalog_lock(
        self,
        row_data_type: str,
        row_interval: str,
        lock_data_type: str,
        tmp_path: Path,
        monkeypatch,
    ):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock

        _catalog_locks.clear()
        row = SimpleNamespace(
            id=123,
            symbol="BTCUSDT-PERP",
            data_type=row_data_type,
            interval=row_interval,
            source_type=None,
        )
        catalog_id = f"BTCUSDT-PERP|{row_data_type}|{row_interval}|"
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

        def fake_delete_storage(self, symbol, data_type, interval, *, source_type=None):
            calls.append((str(self.catalog_path), symbol, data_type, interval, source_type))
            return (1, 3)

        monkeypatch.setattr(CatalogSession, "delete_storage", fake_delete_storage)

        lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", lock_data_type, row_interval))
        await lock.acquire()
        try:
            task = asyncio.create_task(delete_catalog_entry(catalog_id, db=db, settings=settings))
            await asyncio.sleep(0.02)
            assert calls == []
        finally:
            lock.release()

        await task

        assert calls == [(str(tmp_path), "BTCUSDT-PERP", row_data_type, row_interval, None)]

    async def test_delete_removes_filesystem_only_catalog_entry_without_db_row(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog import CatalogSession

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        db = SimpleNamespace(
            execute=AsyncMock(return_value=FakeResult()),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        calls = []

        def fake_delete_storage(self, symbol, data_type, interval, *, source_type=None):
            calls.append((str(self.catalog_path), symbol, data_type, interval, source_type))
            return (4, 64)

        monkeypatch.setattr(CatalogSession, "delete_storage", fake_delete_storage)

        result = await delete_catalog_entry(
            "BTCUSDT-PERP|quote_tick|tick|bookTicker",
            db=db,
            settings=settings,
        )

        assert result == {
            "status": "deleted",
            "symbol": "BTCUSDT-PERP",
            "data_type": "quote_tick",
            "deleted_files": 4,
            "freed_bytes": 64,
        }
        assert calls == [(str(tmp_path), "BTCUSDT-PERP", "quote_tick", "tick", "bookTicker")]
        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()


class TestMaintenanceApiSurface:
    def test_scan_route_removed_from_router(self):
        paths = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
        assert ("/api/data/scan", ("POST",)) not in paths

    def test_nt_native_maintenance_routes_exist(self):
        paths = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
        assert ("/api/data/reset-file-names", ("POST",)) in paths
        assert ("/api/data/consolidate", ("POST",)) in paths
        assert ("/api/data/consolidate-by-period", ("POST",)) in paths
        assert ("/api/data/delete-range", ("POST",)) in paths


class TestCatalogApiFacade:
    async def test_list_data_catalog_returns_live_summary_without_db_index(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        quote_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        quote_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(
            quote_dir / "quotes.parquet"
        )

        monkeypatch.setattr(
            "tinohelm.api.routes.data.select",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("live summary must not query data_catalog")
            ),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        rows = await list_data_catalog(settings=settings)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == "BTCUSDT-PERP|quote_tick|tick|bookTicker"
        assert row.symbol == symbol
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.record_count == 1
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 1)
        assert row.file_path == str(resolve_catalog_path(tmp_path, "bookTicker"))

    async def test_list_data_catalog_filters_out_non_phase1_types(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog import metrics_parquet_path
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)

        quote_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        quote_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(quote_dir / "quotes.parquet")

        mark_dir = (tmp_path / "data" / "mark_price_update" / nt_sym / "2025" / "01" / "01")
        mark_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [1.0]}).write_parquet(mark_dir / "mark.parquet")

        premium_dir = tmp_path / "out_of_scope" / "premiumIndexKlines"
        premium_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(premium_dir / "bars.parquet")

        metrics_path = metrics_parquet_path(symbol, tmp_path)
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(metrics_path)

        monkeypatch.setattr(
            "tinohelm.api.routes.data.select",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("live summary must not query data_catalog")
            ),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        rows = await list_data_catalog(settings=settings)

        assert [row.data_type for row in rows] == ["mark_price", "quote_tick"]

    async def test_list_data_catalog_shows_trades_as_trade_tick(self, tmp_path: Path, monkeypatch):
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        trade_dir = tmp_path / "data" / "trade_tick" / nt_sym
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [1.0], "size": [1.0]}).write_parquet(
            trade_dir / "trades.parquet"
        )

        monkeypatch.setattr(
            "tinohelm.api.routes.data.select",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("live summary must not query data_catalog")
            ),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        rows = await list_data_catalog(settings=settings)

        assert len(rows) == 1
        assert rows[0].id == "BTCUSDT-PERP|trade_tick|tick|trades"
        assert rows[0].data_type == "trade_tick"
        assert rows[0].source_type == "trades"

    @pytest.mark.parametrize(
        ("upstream_type", "public_type", "file_name"),
        [
            ("markPriceKlines", "mark_price", "mark.parquet"),
            ("indexPriceKlines", "index_price", "index.parquet"),
        ],
    )
    async def test_list_data_catalog_includes_direct_update_entries(
        self,
        upstream_type: str,
        public_type: str,
        file_name: str,
        tmp_path: Path,
        monkeypatch,
    ):
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        update_dir_name = "mark_price_update" if upstream_type == "markPriceKlines" else "index_price_update"
        update_dir = tmp_path / "data" / update_dir_name / nt_sym
        update_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(update_dir / file_name)

        monkeypatch.setattr(
            "tinohelm.api.routes.data.select",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("live summary must not query data_catalog")
            ),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        rows = await list_data_catalog(settings=settings)

        assert len(rows) == 1
        assert rows[0].symbol == symbol
        assert rows[0].data_type == public_type

    def test_list_data_types_returns_static_phase1_capability_list(self):
        import asyncio

        result = asyncio.run(list_data_types())
        by_type = {item["data_type"]: item for item in result}

        assert list(by_type) == [
            "bar",
            "trade_tick",
            "quote_tick",
            "mark_price",
            "index_price",
            "funding_rate",
        ]
        assert by_type["bar"]["upstream_data_type"] == "klines"
        assert by_type["trade_tick"]["upstream_data_type"] == "trades"
        assert by_type["quote_tick"]["upstream_data_type"] == "bookTicker"
        assert by_type["mark_price"]["upstream_data_type"] == "markPriceKlines"
        assert by_type["index_price"]["upstream_data_type"] == "indexPriceKlines"
        assert by_type["funding_rate"]["upstream_data_type"] == "fundingRate"
        assert by_type["bar"]["interval_required"] is True
        assert by_type["trade_tick"]["interval_required"] is False
        assert by_type["quote_tick"]["interval_required"] is False
        assert by_type["mark_price"]["interval_required"] is False
        assert by_type["index_price"]["interval_required"] is False
        assert by_type["funding_rate"]["interval_required"] is False
        assert all(item["implemented"] is True for item in result)
