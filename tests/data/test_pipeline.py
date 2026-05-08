"""Tests for tinohelm.data.pipeline.BinanceVisionPipeline.

All network, DB, and filesystem side-effects are mocked.
"""
from __future__ import annotations

import asyncio
import json
from io import BytesIO
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from tinohelm.data.downloader import VisionCsvPayload
from tinohelm.data.pipeline import (
    BinanceVisionPipeline,
    IngestResult,
    _ns_to_utc_date,
    recover_pending_ingest_rollbacks,
)


def _csv_payload(name: str, content: bytes) -> VisionCsvPayload:
    file_obj = BytesIO()
    file_obj.write(content)
    file_obj.seek(0)
    return VisionCsvPayload(name=name, file=file_obj)


def test_ns_to_utc_date_uses_integer_seconds_at_day_boundary() -> None:
    ns = 1_737_417_599_999_999_999

    assert _ns_to_utc_date(ns) == date(2025, 1, 20)


# ---------------------------------------------------------------------------
# 1. BinanceVisionPipeline init
# ---------------------------------------------------------------------------

class TestPipelineInit:
    def test_catalog_path_stored_as_str(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert p.catalog_path == "/tmp/test_catalog"

    def test_catalog_path_from_pathlib(self, tmp_path):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        assert p.catalog_path == str(tmp_path)

    def test_downloader_created(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert p.downloader is not None

    def test_custom_raw_dir(self, tmp_path):
        raw = tmp_path / "raw"
        p = BinanceVisionPipeline(catalog_path="/tmp/x", raw_dir=str(raw))
        assert p.downloader.raw_dir == raw


# ---------------------------------------------------------------------------
# 2. IngestResult dataclass
# ---------------------------------------------------------------------------

class TestIngestResult:
    def test_defaults(self):
        r = IngestResult(symbol="X", data_type="klines", objects_count=0, files_written=0)
        assert r.skipped is False
        assert r.rest_fallback_used is False
        assert r.rest_fallback_range is None
        assert r.file_paths == []
        assert r.start is None
        assert r.end is None

    def test_explicit_fields(self):
        r = IngestResult(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            objects_count=1234,
            files_written=3,
            file_paths=["/a", "/b", "/c"],
            start=date(2025, 1, 1),
            end=date(2025, 1, 31),
            skipped=True,
            rest_fallback_used=True,
            rest_fallback_range=(date(2025, 1, 28), date(2025, 1, 31)),
        )
        assert r.objects_count == 1234
        assert r.files_written == 3
        assert r.skipped is True
        assert r.rest_fallback_used is True
        assert r.rest_fallback_range == (date(2025, 1, 28), date(2025, 1, 31))


class TestFundingRateWrites:
    def test_legacy_json_cache_merges_incremental_records(self, tmp_path: Path, paths_override, monkeypatch):
        from tinohelm.data.funding_cache import _load_cache, _save_cache

        paths_override("funding_rates", tmp_path / "funding_rates")
        _save_cache("BTCUSDT-PERP", [
            {"funding_time_ms": 1_000, "funding_rate": 0.01, "mark_price": 100.0},
        ])

        def _write_parquet(**_kwargs):
            return tmp_path / "catalog" / "funding_rates" / "BTCUSDT-PERP.parquet"

        monkeypatch.setattr("tinohelm.data.catalog.write_funding_rate_parquet", _write_parquet)
        pipeline = BinanceVisionPipeline(catalog_path=tmp_path / "catalog")
        records = [
            SimpleNamespace(funding_time_ms=2_000, funding_rate=0.02),
        ]

        pipeline._write_funding_rates(records, "BTCUSDT-PERP")
        assert _load_cache("BTCUSDT-PERP") == [
            {"funding_time_ms": 1_000, "funding_rate": 0.01, "mark_price": 100.0},
        ]

        pipeline._flush_pending_funding_cache("BTCUSDT-PERP")

        cached = _load_cache("BTCUSDT-PERP")
        assert [row["funding_time_ms"] for row in cached] == [1_000, 2_000]
        assert cached[0]["mark_price"] == 100.0
        assert cached[1]["funding_rate"] == 0.02


class TestBoundedCsvConversion:
    def test_full_file_conversion_reads_csv_payload_without_filesystem_staging(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"a,b\n1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._write_objects = MagicMock(return_value=["memory://catalog/file.parquet"])

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                assert df["a"].tolist() == [1]
                assert kwargs == {"symbol": "BTCUSDT-PERP"}
                return ["object-1"]

        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "metrics",
            None,
            False,
        )

        assert count == 1
        assert paths == ["memory://catalog/file.parquet"]
        p._write_objects.assert_called_once_with(
            ["object-1"], "BTCUSDT-PERP", "metrics", None, merge=False,
        )

    def test_chunked_conversion_reads_csv_payload_in_chunks(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._chunk_rows = 1
        p._agg_trades_chunk_rows = 1
        p._write_objects = MagicMock(side_effect=[
            ["memory://catalog/a.parquet"],
            ["memory://catalog/b.parquet"],
        ])

        class Converter:
            supports_chunked = True

            def validate_schema(self, df):
                assert list(df.columns) == [0, 1]

            def convert_chunk(self, chunk, instrument, **kwargs):
                return [tuple(chunk.iloc[0].tolist())]

        seen_progress: list[int] = []
        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "aggTrades",
            None,
            False,
            chunk_cb=seen_progress.append,
        )

        assert count == 2
        assert paths == ["memory://catalog/a.parquet", "memory://catalog/b.parquet"]
        assert seen_progress == [1, 2]

    def test_cleanup_closes_in_memory_csv_payload(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        p._cleanup_raw_file(payload)

        assert payload.closed


class TestIngestEarlyFailClosed:
    def test_empty_csv_payload_fails_before_catalog_db_update(self, tmp_path: Path, monkeypatch):
        task = SimpleNamespace(url="memory://empty.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-empty.csv", b""))

        class Converter:
            supports_chunked = True

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._clean_overlapping_parquet = MagicMock(return_value=None)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
        update_catalog = AsyncMock()
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())
        monkeypatch.setattr(p, "_update_db_catalog", update_catalog)

        with pytest.raises(RuntimeError, match="conversion failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        update_catalog.assert_not_awaited()

    def test_overlapping_cleanup_restores_prior_files_when_later_conversion_fails(
        self, tmp_path: Path, monkeypatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()

        task = SimpleNamespace(url="memory://empty.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-empty.csv", b""))

        class Converter:
            supports_chunked = True

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        update_catalog = AsyncMock()
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())
        monkeypatch.setattr(p, "_update_db_catalog", update_catalog)

        with pytest.raises(RuntimeError, match="conversion failed"):
            asyncio.run(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not (tmp_path / ".ingest-rollback").exists()
        update_catalog.assert_not_awaited()

    def test_failed_ingest_deletes_current_outputs_before_restoring_old_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()
        partial_path = trade_dir / "partial-current-run.parquet"

        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        def write_partial(*_args, **_kwargs):
            partial_path.write_bytes(b"partial")
            return [str(partial_path)]

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        p._write_objects = MagicMock(side_effect=write_partial)
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())
        monkeypatch.setattr(p, "_update_db_catalog", AsyncMock(side_effect=RuntimeError("db down")))

        with pytest.raises(RuntimeError, match="db down"):
            asyncio.run(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_failed_ingest_restores_old_files_even_when_current_output_delete_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()
        partial_path = trade_dir / "partial-current-run.parquet"

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="aggTrades",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )
        assert guard is not None
        assert not old_path.exists()
        partial_path.write_bytes(b"partial")

        def delete_raises(_file_paths):
            raise RuntimeError("delete failed")

        monkeypatch.setattr(guard, "delete_current_outputs", delete_raises)

        with pytest.raises(RuntimeError, match="rollback restored old parquet"):
            p._rollback_failed_ingest(guard, [str(partial_path)], set())

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert partial_path.exists()
        assert list((tmp_path / ".ingest-rollback").rglob("manifest.json"))

    def test_recover_skips_unresolved_manifest_after_verified_catalog_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="aggTrades",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )
        assert guard is not None
        committed_path = trade_dir / "committed-current-run.parquet"
        committed_path.write_bytes(b"committed")
        guard.record_catalog_commit(
            symbol=symbol,
            data_type="trade_tick",
            interval="tick",
            source_type="aggTrades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
            file_path=str(resolve_catalog_path(tmp_path, "aggTrades")),
            record_count=1,
            size_bytes=9,
        )
        manifest = next((tmp_path / ".ingest-rollback").rglob("manifest.json"))
        assert json.loads(manifest.read_text())["resolved"] is False
        checked: list[dict] = []

        def persisted(payload):
            checked.append(payload)
            return True

        monkeypatch.setattr("tinohelm.data.pipeline._catalog_commit_is_persisted", persisted)

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 0
        assert checked
        assert not old_path.exists()
        assert committed_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_recover_pending_ingest_rollbacks_restores_crash_stranded_backup(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="aggTrades",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )

        assert guard is not None
        assert not old_path.exists()
        partial_path = trade_dir / "partial-current-run.parquet"
        partial_path.write_bytes(b"partial")
        assert list((tmp_path / ".ingest-rollback").rglob("manifest.json"))

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 1
        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_recover_skips_resolved_manifest_left_after_success_cleanup_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="aggTrades",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )
        committed_path = trade_dir / "committed-current-run.parquet"
        committed_path.write_bytes(b"committed")

        assert guard is not None
        assert not old_path.exists()
        monkeypatch.setattr("tinohelm.data.pipeline.shutil.rmtree", lambda *_args, **_kwargs: None)
        guard.discard(best_effort=True)
        manifest = next((tmp_path / ".ingest-rollback").rglob("manifest.json"))
        assert json.loads(manifest.read_text())["resolved"] is True

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 0
        assert not old_path.exists()
        assert committed_path.exists()
        assert committed_path.read_bytes() == b"committed"

    def test_rollback_manifest_write_failure_preserves_previous_valid_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.pipeline import _ParquetCleanupGuard

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        rollback_prefix = tmp_path / ".ingest-rollback" / "job"
        guard = _ParquetCleanupGuard(p._storage, rollback_prefix)
        old_a = tmp_path / "data" / "trade_tick" / "BTC" / "old-a.parquet"
        old_b = old_a.with_name("old-b.parquet")
        backup_a = rollback_prefix / "old-a.parquet"
        backup_b = rollback_prefix / "old-b.parquet"
        backup_a.parent.mkdir(parents=True)
        backup_a.write_bytes(b"old-a")
        backup_b.write_bytes(b"old-b")

        guard.add_backup(old_a, backup_a)
        guard.persist_manifest()
        manifest_path = rollback_prefix / "manifest.json"
        first_manifest = json.loads(manifest_path.read_text())
        assert len(first_manifest["backups"]) == 1

        original_replace = Path.replace

        def fail_manifest_replace(path: Path, target: Path | str):
            if path.name.startswith(".manifest.json.") and Path(target).name == "manifest.json":
                raise RuntimeError("crash during manifest replace")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_manifest_replace)
        guard.add_backup(old_b, backup_b)
        with pytest.raises(RuntimeError, match="crash during manifest replace"):
            guard.persist_manifest()

        assert json.loads(manifest_path.read_text()) == first_manifest

    def test_recover_no_overlap_manifest_removes_partial_current_run_file(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="aggTrades",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )
        partial_path = trade_dir / "partial-current-run.parquet"
        partial_path.write_bytes(b"partial")

        assert guard is not None
        assert list((tmp_path / ".ingest-rollback").rglob("manifest.json"))

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 0
        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_recover_first_time_single_file_manifest_removes_partial_current_run_file(self, tmp_path: Path):
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbol = "BTCUSDT-PERP"
        partial_path = funding_rate_parquet_path(symbol, tmp_path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="fundingRate",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )
        partial_path.parent.mkdir(parents=True)
        partial_path.write_bytes(b"partial")

        assert guard is not None
        manifest = next((tmp_path / ".ingest-rollback").rglob("manifest.json"))
        payload = json.loads(manifest.read_text())
        assert payload["backups"] == []
        assert payload["original_paths"] == []

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 0
        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_recover_partial_manifest_preserves_unlisted_original_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path

        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_a = trade_dir / "old-a.parquet"
        old_b = trade_dir / "old-b.parquet"
        old_b.write_bytes(b"old-b")
        rollback_prefix = tmp_path / ".ingest-rollback" / "job"
        backup_a = rollback_prefix / "old-a.parquet"
        backup_a.parent.mkdir(parents=True)
        backup_a.write_bytes(b"old-a")
        manifest = {
            "version": 1,
            "complete": True,
            "resolved": False,
            "rollback_prefix": str(rollback_prefix),
            "target_dir": str(trade_dir),
            "preserved_paths": [],
            "original_paths": [str(old_a), str(old_b)],
            "backups": [{"original_path": str(old_a), "backup_path": str(backup_a)}],
        }
        (rollback_prefix / "manifest.json").write_text(json.dumps(manifest))
        partial_path = trade_dir / "partial-current-run.parquet"
        partial_path.write_bytes(b"partial")

        restored = recover_pending_ingest_rollbacks(tmp_path)

        assert restored == 1
        assert old_a.read_bytes() == b"old-a"
        assert old_b.read_bytes() == b"old-b"
        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_cleanup_guard_best_effort_discard_does_not_fail_success_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.pipeline import _ParquetCleanupGuard

        class RemoteStorage:
            provider = "s3"

        guard = _ParquetCleanupGuard(RemoteStorage(), tmp_path / ".ingest-rollback" / "job")
        guard.add_backup(tmp_path / "old.parquet", tmp_path / ".ingest-rollback" / "job" / "old.parquet")

        def delete_prefix_raises(_storage, _prefix):
            raise RuntimeError("tos delete failed")

        monkeypatch.setattr("tinohelm.data.storage.delete_prefix", delete_prefix_raises)

        guard.discard(best_effort=True)
        assert guard._active is True

    def test_db_update_cancellation_after_commit_recancels_after_successful_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))
        written_path = tmp_path / "data" / "trade_tick" / "BTCUSDT-PERP.BINANCE" / "current.parquet"
        update_completed = []

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        def write_objects(*_args, **_kwargs):
            written_path.parent.mkdir(parents=True, exist_ok=True)
            written_path.write_bytes(b"parquet-placeholder")
            return [str(written_path)]

        async def run_ingest_with_cancel():
            update_started = asyncio.Event()

            async def update_catalog(*_args, **_kwargs):
                update_started.set()
                await asyncio.sleep(0.01)
                update_completed.append(True)

            p = BinanceVisionPipeline(catalog_path=tmp_path)
            p.downloader = mock_dl
            p._convert_workers = 1
            p._clean_overlapping_parquet = MagicMock(return_value=None)
            p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
            p._write_objects = MagicMock(side_effect=write_objects)
            p._written_file_size = MagicMock(return_value=123)
            p._update_db_catalog = AsyncMock(side_effect=update_catalog)
            monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())

            ingest_task = asyncio.create_task(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))
            await update_started.wait()
            ingest_task.cancel()
            await ingest_task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_ingest_with_cancel())

        assert update_completed == [True]
        assert written_path.exists()

    def test_rollback_manifest_records_commit_intent_before_db_catalog_update(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))
        written_path = tmp_path / "data" / "trade_tick" / "BTCUSDT-PERP.BINANCE" / "current.parquet"
        events: list[str] = []

        class Guard:
            def record_catalog_commit(self, **kwargs):
                events.append("record_catalog_commit")
                assert kwargs["symbol"] == "BTCUSDT-PERP"
                assert kwargs["data_type"] == "trade_tick"
                assert kwargs["source_type"] == "aggTrades"
                assert kwargs["record_count"] == 1
                assert kwargs["size_bytes"] == 123

            def mark_resolved(self):
                events.append("mark_resolved")

            def discard(self, *, best_effort: bool = False):
                events.append(f"discard:{best_effort}")

            def delete_current_outputs(self, *_args, **_kwargs):
                events.append("delete_current_outputs")

            def restore(self, *, discard: bool = True):
                events.append(f"restore:{discard}")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        def write_objects(*_args, **_kwargs):
            written_path.parent.mkdir(parents=True, exist_ok=True)
            written_path.write_bytes(b"parquet-placeholder")
            return [str(written_path)]

        async def update_catalog(*_args, **_kwargs):
            events.append("db_update")
            assert events[:1] == ["record_catalog_commit"]

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._clean_overlapping_parquet = MagicMock(return_value=Guard())
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
        p._write_objects = MagicMock(side_effect=write_objects)
        p._written_file_size = MagicMock(return_value=123)
        p._update_db_catalog = AsyncMock(side_effect=update_catalog)
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="aggTrades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))

        assert result.objects_count == 1
        assert events == ["record_catalog_commit", "db_update", "discard:True"]
        assert written_path.exists()

    def test_final_progress_cancellation_after_db_commit_recancels(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))
        written_path = tmp_path / "data" / "trade_tick" / "BTCUSDT-PERP.BINANCE" / "current.parquet"

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        def write_objects(*_args, **_kwargs):
            written_path.parent.mkdir(parents=True, exist_ok=True)
            written_path.write_bytes(b"parquet-placeholder")
            return [str(written_path)]

        async def progress(pct: int, _msg: str):
            if pct == 100:
                current = asyncio.current_task()
                assert current is not None
                current.cancel()
                await asyncio.sleep(0)

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._clean_overlapping_parquet = MagicMock(return_value=None)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
        p._write_objects = MagicMock(side_effect=write_objects)
        p._written_file_size = MagicMock(return_value=123)
        p._update_db_catalog = AsyncMock()
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
                progress_cb=progress,
            ))

        p._update_db_catalog.assert_awaited_once()
        assert written_path.exists()

    def test_no_guard_rollback_preserves_preexisting_single_file_parquet(self, tmp_path: Path):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        path = tmp_path / "data" / "funding_rate" / "btcusdt-perp.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old-valid-parquet-placeholder")

        p._rollback_failed_ingest(None, [str(path)], {path})

        assert path.read_bytes() == b"old-valid-parquet-placeholder"

    def test_in_place_guard_restores_preexisting_single_file_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbol = "BTCUSDT-PERP"
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        path = funding_rate_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old-valid-parquet-placeholder")

        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="fundingRate",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 2),
        )
        assert guard is not None
        path.write_bytes(b"mutated-current-run")

        p._rollback_failed_ingest(guard, [str(path)], set())

        assert path.read_bytes() == b"old-valid-parquet-placeholder"
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_in_place_guard_deletes_untracked_new_single_file_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbol = "BTCUSDT-PERP"
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        path = funding_rate_parquet_path(symbol, tmp_path)

        guard = p._clean_overlapping_parquet(
            symbol=symbol,
            data_type="fundingRate",
            interval=None,
            start=date(2025, 1, 1),
            end=date(2025, 1, 2),
        )
        assert guard is not None
        path.parent.mkdir(parents=True)
        path.write_bytes(b"partial-current-run")

        p._rollback_failed_ingest(guard, [], set())

        assert not path.exists()

    def test_cleanup_guard_preserves_explicit_preserved_paths(self, tmp_path: Path):
        from tinohelm.data.pipeline import _ParquetCleanupGuard

        target_dir = tmp_path / "catalog" / "data" / "bar" / "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
        target_dir.mkdir(parents=True)
        preserved = target_dir / "old-non-overlap.parquet"
        current = target_dir / "current-run.parquet"
        preserved.write_bytes(b"preserved")
        current.write_bytes(b"current")
        guard = _ParquetCleanupGuard(
            BinanceVisionPipeline(catalog_path=tmp_path / "catalog")._storage,
            tmp_path / "catalog" / ".ingest-rollback" / "job",
            target_dir=target_dir,
            preserved_paths={preserved},
        )

        guard.delete_current_outputs([str(preserved), str(current)])

        assert preserved.read_bytes() == b"preserved"
        assert not current.exists()

    def test_fresh_guard_deletes_unreturned_partial_stream_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        partial_path = trade_dir / "partial-before-error.parquet"
        task = SimpleNamespace(url="memory://partial.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))

        def write_then_raise(*_args, **_kwargs):
            trade_dir.mkdir(parents=True, exist_ok=True)
            partial_path.write_bytes(b"partial")
            raise RuntimeError("chunk write failed")

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
        p._convert_one_file = MagicMock(side_effect=write_then_raise)
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: SimpleNamespace(supports_chunked=True))
        monkeypatch.setattr(p, "_update_db_catalog", AsyncMock())

        with pytest.raises(RuntimeError, match="conversion failed"):
            asyncio.run(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        assert not partial_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_cancelled_rest_fallback_progress_rolls_back_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        current_path = trade_dir / "current-before-rest.parquet"
        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        def convert_one(*_args, **_kwargs):
            trade_dir.mkdir(parents=True, exist_ok=True)
            current_path.write_bytes(b"current")
            return 1, [str(current_path)]

        async def run_and_cancel():
            p = BinanceVisionPipeline(catalog_path=tmp_path)
            p.downloader = Downloader()
            p._convert_workers = 1
            p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
            p._convert_one_file = MagicMock(side_effect=convert_one)
            p._detect_vision_coverage_end = MagicMock(return_value=date(2025, 1, 1))
            p._rest_fallback = AsyncMock(side_effect=AssertionError("fallback should not start"))
            monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: SimpleNamespace(supports_chunked=False))
            progress_entered = asyncio.Event()

            async def progress_cb(_pct: int, msg: str):
                if msg.startswith("REST fallback"):
                    progress_entered.set()
                    await asyncio.sleep(10)

            ingest_task = asyncio.create_task(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 2),
                progress_cb=progress_cb,
            ))
            await asyncio.wait_for(progress_entered.wait(), timeout=1)
            assert current_path.exists()
            ingest_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await ingest_task

        asyncio.run(run_and_cancel())

        assert not current_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_funding_ingest_failure_restores_parquet_and_json_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbol = "BTCUSDT-PERP"
        old_parquet = b"old-valid-parquet-placeholder"
        old_cache = b'[{"funding_time_ms": 1704067200000, "funding_rate": "0.01"}]'
        parquet_path = funding_rate_parquet_path(symbol, tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(old_parquet)
        cache_path = tmp_path / "funding-cache" / "btcusdt-perp.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(old_cache)

        task = SimpleNamespace(url="memory://funding.csv", granularity="daily", dest_path=Path("BTCUSDT-fundingRate-2025-01-02.csv"))
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-fundingRate-2025-01-02.csv", b"a,b\n1,2\n"))

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(funding_time_ms=1_704_153_600_000, funding_rate=0.02)]

        def write_parquet(**_kwargs):
            parquet_path.write_bytes(b"mutated-current-run")
            return parquet_path

        def save_cache(_symbol, _records):
            cache_path.write_bytes(b"mutated-cache")

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._funding_cache_covers = MagicMock(return_value=False)
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())
        monkeypatch.setattr("tinohelm.data.catalog.write_funding_rate_parquet", write_parquet)
        monkeypatch.setattr("tinohelm.data.funding_cache._cache_path", lambda _symbol: cache_path)
        monkeypatch.setattr("tinohelm.data.funding_cache._save_cache", save_cache)
        monkeypatch.setattr(p, "_update_db_catalog", AsyncMock(side_effect=RuntimeError("db down")))

        with pytest.raises(RuntimeError, match="db down"):
            asyncio.run(p.ingest(
                symbol=symbol,
                data_type="fundingRate",
                start=date(2025, 1, 2),
                end=date(2025, 1, 2),
            ))

        assert parquet_path.read_bytes() == old_parquet
        assert cache_path.read_bytes() == old_cache
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_funding_cache_restore_removes_new_cache_when_none_existed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        symbol = "BTCUSDT-PERP"
        cache_path = tmp_path / "funding-cache" / "btcusdt-perp.json"
        monkeypatch.setattr("tinohelm.data.funding_cache._cache_path", lambda _symbol: cache_path)

        snapshot = BinanceVisionPipeline._snapshot_funding_cache(symbol)
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(b"mutated-cache")

        BinanceVisionPipeline._restore_funding_cache(snapshot)

        assert not cache_path.exists()

    def test_cancelled_ingest_restores_overlap_cleanup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()

        task = SimpleNamespace(url="memory://slow.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                raise AssertionError("download should not start while progress callback is blocked")

        async def run_and_cancel():
            p = BinanceVisionPipeline(catalog_path=tmp_path)
            p.downloader = Downloader()
            p._convert_workers = 1
            p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
            monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: SimpleNamespace(supports_chunked=False))
            progress_entered = asyncio.Event()

            async def progress_cb(_pct: int, msg: str):
                if msg.startswith("Downloading"):
                    progress_entered.set()
                    await asyncio.sleep(10)

            ingest_task = asyncio.create_task(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
                progress_cb=progress_cb,
            ))
            await asyncio.wait_for(progress_entered.wait(), timeout=1)
            assert not old_path.exists()
            ingest_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await ingest_task

        asyncio.run(run_and_cancel())

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_cancelled_ingest_waits_for_executor_outputs_before_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import threading

        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()
        late_path = trade_dir / "late-current.parquet"
        started = threading.Event()
        release = threading.Event()

        task = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        def slow_convert(*_args, **_kwargs):
            started.set()
            release.wait(timeout=2)
            late_path.write_bytes(b"late")
            return 1, [str(late_path)]

        async def run_and_cancel():
            p = BinanceVisionPipeline(catalog_path=tmp_path)
            p.downloader = Downloader()
            p._convert_workers = 1
            p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
            p._convert_one_file = MagicMock(side_effect=slow_convert)
            monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: SimpleNamespace(supports_chunked=False))
            ingest_task = asyncio.create_task(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))
            assert await asyncio.to_thread(started.wait, 1)
            assert not old_path.exists()
            ingest_task.cancel()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(ingest_task), timeout=0.2)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await ingest_task

        asyncio.run(run_and_cancel())

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not late_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_cancelled_ingest_waits_for_running_converter_when_sibling_task_cancels_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import threading

        from tinohelm.data.catalog import resolve_catalog_path

        symbol = "BTCUSDT-PERP"
        nt_symbol = "BTCUSDT-PERP.BINANCE"
        trade_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_symbol
        trade_dir.mkdir(parents=True)
        old_path = trade_dir / "old-overlap.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(old_path)
        old_payload = old_path.read_bytes()
        late_path = trade_dir / "late-current-after-sibling-cancel.parquet"
        started = threading.Event()
        release = threading.Event()

        first = SimpleNamespace(url="memory://one.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"))
        second = SimpleNamespace(url="memory://two.csv", granularity="daily", dest_path=Path("BTCUSDT-aggTrades-2025-01-02.csv"))

        class Downloader:
            concurrency = 1

            def __init__(self):
                self.second_entered = asyncio.Event()

            def plan_downloads(self, **_kwargs):
                return [first, second]

            async def execute_task(self, task):
                if task is first:
                    return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")
                self.second_entered.set()
                await asyncio.sleep(10)
                raise AssertionError("second download should be cancelled")

        def slow_convert(*_args, **_kwargs):
            started.set()
            release.wait(timeout=2)
            late_path.write_bytes(b"late")
            return 1, [str(late_path)]

        async def run_and_cancel():
            downloader = Downloader()
            p = BinanceVisionPipeline(catalog_path=tmp_path)
            p.downloader = downloader
            p._convert_workers = 1
            p._get_instrument = MagicMock(return_value=SimpleNamespace(id=nt_symbol))
            p._convert_one_file = MagicMock(side_effect=slow_convert)
            monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: SimpleNamespace(supports_chunked=False))
            ingest_task = asyncio.create_task(p.ingest(
                symbol=symbol,
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 2),
            ))
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.wait_for(downloader.second_entered.wait(), timeout=1)
            ingest_task.cancel()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(ingest_task), timeout=0.2)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await ingest_task

        asyncio.run(run_and_cancel())

        assert old_path.exists()
        assert old_path.read_bytes() == old_payload
        assert not late_path.exists()
        assert not (tmp_path / ".ingest-rollback").exists()

    def test_restore_preserves_rollback_backup_when_restore_copy_fails(self, tmp_path: Path):
        from tinohelm.data.pipeline import _ParquetCleanupGuard

        class Storage:
            provider = "s3"

            def __init__(self):
                self.deleted: list[Path] = []

            def copy_path(self, source, dest):
                raise OSError(f"restore failed: {source} -> {dest}")

            def iter_files(self, prefix, *, suffix="", recursive=True):
                self.deleted.append(Path(prefix))
                return []

        storage = Storage()
        guard = _ParquetCleanupGuard(storage, tmp_path / ".ingest-rollback" / "job")
        guard.add_backup(tmp_path / "active.parquet", tmp_path / ".ingest-rollback" / "job" / "active.parquet")

        with pytest.raises(RuntimeError, match="restore failed"):
            guard.restore()

        assert storage.deleted == []

    def test_remote_parquet_metadata_read_error_is_not_replaceable_unknown_range(self, tmp_path: Path):
        class Storage:
            provider = "s3"

            def open_input_file(self, path_or_object):
                raise OSError("remote auth failed")

        with pytest.raises(OSError, match="remote auth failed"):
            BinanceVisionPipeline._parquet_time_range(tmp_path / "broken.parquet", storage=Storage())

    def test_funding_rate_parquet_failure_does_not_commit_json_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        saved: list[list[dict]] = []

        def boom(**_kwargs):
            raise RuntimeError("parquet write failed")

        monkeypatch.setattr("tinohelm.data.catalog.write_funding_rate_parquet", boom)
        monkeypatch.setattr(
            "tinohelm.data.funding_cache._save_cache",
            lambda _symbol, records: saved.append(records),
        )
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        records = [SimpleNamespace(funding_time_ms=1_704_067_200_000, funding_rate=0.01)]

        with pytest.raises(RuntimeError, match="parquet write failed"):
            p._write_funding_rates(records, "BTCUSDT-PERP")

        assert saved == []


class TestCatalogStorageStats:
    def test_remote_single_file_metrics_write_passes_storage_for_in_memory_upload(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        uploaded: list[tuple[Path, bytes]] = []

        class RemoteStorage:
            provider = "s3"
            catalog_root = tmp_path

            def upload_bytes(self, logical_path, payload):
                uploaded.append((Path(logical_path), payload))
                return "s3://bucket/catalog/metrics.parquet"

            def upload_path(self, local_path, *, logical_path=None):  # pragma: no cover - must not be called
                raise AssertionError("remote metrics writes must not upload a local staged file")

        def fake_write_metrics(records, write_symbol, catalog_root, storage=None):
            assert records == [object_marker]
            assert write_symbol == symbol
            assert Path(catalog_root) == tmp_path
            assert storage is p._storage
            assert not path.exists()
            storage.upload_bytes(path, b"merged-in-memory")
            return path

        object_marker = object()
        monkeypatch.setattr("tinohelm.data.catalog.write_metrics_parquet", fake_write_metrics)
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._storage = RemoteStorage()
        p.catalog_path = str(tmp_path)

        result = p._write_objects([object_marker], symbol, "metrics", None)

        assert result == [str(path)]
        assert uploaded == [(path, b"merged-in-memory")]
        assert not path.exists()

    def test_written_file_size_uses_remote_storage_iter_files(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)

        class RemoteStorage:
            provider = "s3"

            def iter_files(self, prefix, *, suffix="", recursive=True):
                assert Path(prefix) == path
                assert suffix == ".parquet"
                assert recursive is False
                return iter([SimpleNamespace(path=path, size=123)])

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._storage = RemoteStorage()

        assert p._written_file_size({str(path)}) == 123

    def test_trade_tick_stats_sum_all_parquet_files_in_source_path(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "a.parquet")
        pl.DataFrame({"ts_event": [3, 4, 5], "price": [102.0, 103.0, 104.0], "size": [3.0, 4.0, 5.0]}).write_parquet(trade_dir / "b.parquet")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "aggTrades", None, "aggTrades") == (5, expected_size)

    def test_trade_tick_stats_unknown_when_any_parquet_count_unreadable(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "ok.parquet")
        (trade_dir / "bad.parquet").write_text("not parquet", encoding="utf-8")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "aggTrades", None, "aggTrades") == (None, expected_size)

    def test_metrics_stats_count_rows_from_single_file_path(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "metrics", None, "metrics") == (3, path.stat().st_size)

    def test_book_depth_stats_count_rows_from_single_file_path(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path

        symbol = "BTCUSDT-PERP"
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "bookDepth", None, "bookDepth") == (2, path.stat().st_size)
        assert p._catalog_storage_stats(symbol, "order_book_delta", None, "order_book_delta") == (2, path.stat().st_size)


class TestUpdateDbCatalog:
    def test_updates_incremental_stats_without_full_storage_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(side_effect=AssertionError("full scan called"))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=15, size_bytes=150, source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 3)
        assert fake_session.row.record_count == 25
        assert fake_session.row.size_bytes == 250
        assert fake_session.commits == 2
        p._catalog_storage_stats.assert_not_called()

    def test_disjoint_update_with_unknown_new_record_count_clears_count_without_full_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(side_effect=AssertionError("full scan called"))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=None, size_bytes=150, source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 3)
        assert fake_session.row.record_count is None
        assert fake_session.row.size_bytes == 250
        assert fake_session.commits == 2
        p._catalog_storage_stats.assert_not_called()

    def test_overlapping_updates_replace_stats_from_storage_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(12, 120))
        p._catalog_storage_coverage = MagicMock(return_value=(date(2025, 1, 3), date(2025, 1, 4)))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 3)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 12
        assert fake_session.row.size_bytes == 120
        assert fake_session.commits == 3
        assert p._catalog_storage_stats.call_count == 2

    def test_overlapping_update_preserves_dates_when_storage_coverage_unknown(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(2, 200))
        p._catalog_storage_coverage = MagicMock(return_value=None)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 15), date(2025, 1, 15),
                record_count=2, size_bytes=200, source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 1)
        assert fake_session.row.end_date == date(2025, 1, 31)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == 200

    def test_overlapping_update_recomputes_dates_from_storage_coverage(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader_helpers import normalize_symbol

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        replacement_day = date(2025, 1, 15)
        day_start_ns = 1_736_899_200_000_000_000
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({
            "ts_event": [day_start_ns, day_start_ns + 60_000_000_000],
            "price": [100.0, 101.0],
            "size": [1.0, 2.0],
        }).write_parquet(trade_dir / "replacement-day.parquet")

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "aggTrades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "aggTrades", None,
                replacement_day, replacement_day,
                record_count=2, size_bytes=trade_dir.joinpath("replacement-day.parquet").stat().st_size,
                source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == replacement_day
        assert fake_session.row.end_date == replacement_day
        assert fake_session.row.record_count == 2

    def test_metrics_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="metrics",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="metrics",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 3
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_book_depth_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="bookDepth",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="bookDepth",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_order_book_delta_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "order_book_delta", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="order_book_delta",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "order_book_delta", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="order_book_delta",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.data_type == "order_book_delta"
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_metrics_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="metrics",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="metrics",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 3
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_book_depth_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="bookDepth",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="bookDepth",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_funding_rate_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import funding_rate_parquet_path
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        path = funding_rate_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3, 4], "funding_rate": [0.1, 0.2, 0.3, 0.4]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="fundingRate",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="fundingRate",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 4
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_bar_disjoint_update_refreshes_size_from_storage_without_recounting(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(5, 250))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "klines", "1m",
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="klines",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "klines", "1m",
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=2, size_bytes=999, source_type="klines",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 12
        assert fake_session.row.size_bytes == 250
        p._catalog_storage_stats.assert_called_once_with("BTCUSDT-PERP", "klines", "1m", "klines")

    def test_overlapping_update_keeps_unknown_storage_record_count(self, tmp_path: Path):
        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(None, 220))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))

        assert fake_session.row.record_count is None
        assert fake_session.row.size_bytes == 220


# ---------------------------------------------------------------------------
# 3. ingest() — klines requires interval
# ---------------------------------------------------------------------------

class TestIngestValidation:
    def test_ingest_requires_interval_for_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_requires_interval_for_mark_price_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="markPriceKlines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_requires_interval_for_index_price_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="indexPriceKlines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_no_interval_required_for_agg_trades(self):
        """aggTrades does not need interval — should not raise ValueError on that check."""
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        # We patch everything deep so we just test the validation path exits cleanly
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []  # No tasks → returns early
        p.downloader = mock_dl

        # Patch _update_db_catalog to avoid DB access
        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 5),
            ))

        assert result.skipped is True


# ---------------------------------------------------------------------------
# 4. ingest() — early return when no tasks
# ---------------------------------------------------------------------------

class TestIngestNoTasks:
    def test_returns_skipped_result_when_no_download_tasks(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        assert result.files_written == 0
        assert result.symbol == "BTCUSDT-PERP"
        assert result.data_type == "fundingRate"


class TestIngestFailClosed:
    def test_conversion_write_failure_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-aggTrades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_clean_overlapping_parquet", lambda *_args: None)
        monkeypatch.setattr(p, "_detect_vision_coverage_end", lambda _tasks: None)
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(side_effect=RuntimeError("remote parquet write failed"))
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="remote parquet write failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_csv_read_failure_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-aggTrades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_clean_overlapping_parquet", lambda *_args: None)
        monkeypatch.setattr(p, "_detect_vision_coverage_end", lambda _tasks: None)
        monkeypatch.setattr(p, "_detect_header", MagicMock(side_effect=OSError("csv header unreadable")))
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="csv header unreadable"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_nonempty_conversion_with_no_written_files_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-aggTrades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                pass

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_clean_overlapping_parquet", lambda *_args: None)
        monkeypatch.setattr(p, "_detect_vision_coverage_end", lambda _tasks: None)
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(return_value=[])
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="wrote no parquet files"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_rest_fallback_failure_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-aggTrades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                pass

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_clean_overlapping_parquet", lambda *_args: None)
        monkeypatch.setattr(p, "_detect_vision_coverage_end", lambda _tasks: date(2025, 1, 1))
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(return_value=["s3://bucket/catalog/ticks/aggTrades/day.parquet"])
        p._rest_fallback = AsyncMock(side_effect=RuntimeError("rest fallback write failed"))
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="rest fallback write failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 2),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_rest_fallback_empty_result_fails_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-aggTrades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                pass

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_clean_overlapping_parquet", lambda *_args: None)
        monkeypatch.setattr(p, "_detect_vision_coverage_end", lambda _tasks: date(2025, 1, 1))
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(return_value=["/catalog/ticks/aggTrades/day.parquet"])
        p._rest_fallback = AsyncMock(return_value=(0, []))
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="REST fallback produced no objects/files"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 2),
            ))

        p._update_db_catalog.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4b. ingest() — funding-rate cache short-circuit
# ---------------------------------------------------------------------------

class TestFundingRateCacheShortCircuit:
    """The pipeline must skip plan_downloads when the JSON cache already
    covers [start, end] — defense-in-depth against repeated Binance hits.
    """

    def test_ingest_skips_when_funding_cache_covers_range(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        # Seed a cache spanning 2024-01-01 → 2024-02-01 (UTC ms).
        # 1704067200000 = 2024-01-01 00:00:00 UTC
        # 1706745600000 = 2024-02-01 00:00:00 UTC
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        p._funding_parquet_covers = MagicMock(return_value=True)

        # plan_downloads must NOT be called because JSON + primary Parquet cover the range.
        mock_dl = MagicMock()
        mock_dl.plan_downloads.side_effect = AssertionError(
            "plan_downloads must not run when cache covers range"
        )
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        mock_dl.plan_downloads.assert_not_called()

    def test_ingest_does_not_skip_when_json_cache_covers_but_primary_parquet_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc

        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        p._funding_parquet_covers = MagicMock(return_value=False)
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="fundingRate",
            start=date(2024, 1, 5),
            end=date(2024, 1, 20),
        ))

        assert result.skipped is True
        mock_dl.plan_downloads.assert_called_once()

    def test_ingest_runs_when_funding_cache_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []  # also no tasks → skipped
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 1),
                end=date(2024, 1, 5),
            ))

        # Empty cache means we fall through to plan_downloads as before.
        mock_dl.plan_downloads.assert_called_once()

    def test_ingest_runs_when_funding_cache_has_gap(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        # Cache covers up to 2024-01-10; requested end is 2024-01-20 → gap.
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_844_800_000, "funding_rate": 0.02},  # 2024-01-10
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 1),
                end=date(2024, 1, 20),
            ))

        # Gap detected → plan_downloads must run.
        mock_dl.plan_downloads.assert_called_once()

    def test_short_circuit_only_applies_to_funding_rate(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """A full funding cache must not short-circuit a klines ingest."""
        import tinohelm.data.funding_cache as fc
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                interval="1m",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        mock_dl.plan_downloads.assert_called_once()


# ---------------------------------------------------------------------------
# 5. ingest_sync — sync wrapper
# ---------------------------------------------------------------------------

class TestIngestSync:
    def test_sync_wrapper_exists(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert hasattr(p, "ingest_sync")
        assert callable(p.ingest_sync)

    def test_sync_wrapper_calls_ingest(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        expected = IngestResult(
            symbol="BTCUSDT-PERP", data_type="fundingRate",
            objects_count=0, files_written=0, skipped=True,
        )

        async def _fake_ingest(**kwargs):
            return expected

        with patch.object(p, "ingest", side_effect=_fake_ingest):
            result = p.ingest_sync(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            )

        assert result is expected

    def test_sync_wrapper_propagates_value_error(self):
        """interval validation raises before any async work — sync wrapper propagates it."""
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            p.ingest_sync(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            )


# ---------------------------------------------------------------------------
# 6. progress_cb — callback is invoked
# ---------------------------------------------------------------------------

class TestProgressCallback:
    def test_progress_cb_called(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        calls: list[tuple[int, str]] = []

        def _cb(pct: int, msg: str):
            calls.append((pct, msg))

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                progress_cb=_cb,
            ))

        # At minimum the 0% and 100% progress calls must have been made
        pcts = [c[0] for c in calls]
        assert 0 in pcts
        assert 100 in pcts

    def test_async_progress_cb_accepted(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        calls: list[tuple[int, str]] = []

        async def _async_cb(pct: int, msg: str):
            calls.append((pct, msg))

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                progress_cb=_async_cb,
            ))

        # Async callback must also be awaited properly
        assert len(calls) > 0


# ---------------------------------------------------------------------------
# 7. _detect_vision_coverage_end
# ---------------------------------------------------------------------------

class TestDetectVisionCoverageEnd:
    def _make_task(self, granularity: str, stem: str) -> MagicMock:
        task = MagicMock()
        task.granularity = granularity
        task.dest_path = MagicMock()
        task.dest_path.stem = stem
        return task

    def test_daily_task_parses_date(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("daily", "BTCUSDT-aggTrades-2025-03-15")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2025, 3, 15)

    def test_monthly_task_returns_last_day_of_month(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("monthly", "BTCUSDT-klines-1m-2025-03")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2025, 3, 31)

    def test_monthly_december_returns_dec_31(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("monthly", "BTCUSDT-klines-1m-2024-12")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2024, 12, 31)

    def test_empty_tasks_returns_none(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        result = p._detect_vision_coverage_end(tasks=[])
        assert result is None
