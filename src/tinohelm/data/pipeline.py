"""BinanceVisionPipeline — unified data ingestion orchestrator.

Three-layer architecture:
  Download (downloader.py) → Convert (converters/) → Store (catalog.py)

Provides both async and sync entry points for use in FastAPI routes
and BacktestRunner subprocesses respectively.

All NT-/asyncio-/pandas-free pure logic (category resolution, progress
math, date-boundary conversions, Vision-stem parsing, CSV header sniffing)
lives in :mod:`tinohelm.data.pipeline_helpers` so it can be unit tested
without the heavy framework dependencies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from tinohelm.data.converters import get_converter
from tinohelm.data.downloader import VisionCsvPayload, VisionDownloader, _KLINES_TYPES
from tinohelm.data.pipeline_helpers import (
    DOWNLOAD_PROGRESS_BASE,
    INTERVAL_CONVENTION,
    KLINES_REST_FETCH_FN,
    REST_FALLBACK_TYPES,
    WRITE_CATEGORY,
    compute_chunk_subprogress,
    compute_stage_pct,
    csv_has_header,
    date_end_dt,
    date_end_ns,
    date_start_dt,
    date_start_ns,
    is_rest_fallback_supported,
    parse_vision_coverage_end,
    resolve_db_category,
    resolve_db_interval,
    resolve_write_category,
)

logger = logging.getLogger(__name__)


def _ns_to_utc_date(ns: int) -> date:
    """Convert Unix epoch nanoseconds to a UTC calendar date."""
    return datetime.fromtimestamp(ns // 1_000_000_000, UTC).date()


def _timestamp_stat_to_ns(value: Any) -> int:
    """Normalize Parquet timestamp statistic values to epoch nanoseconds."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1_000_000_000)
    return int(value)

# Backwards-compat aliases (kept so external imports keep working):
# ``api/routes/data.py`` historically reaches into ``_WRITE_CATEGORY``;
# the canonical name is now :data:`WRITE_CATEGORY` exported from
# ``pipeline_helpers``.
_REST_FALLBACK_TYPES = REST_FALLBACK_TYPES
_WRITE_CATEGORY = WRITE_CATEGORY
_INTERVAL_CONVENTION = INTERVAL_CONVENTION

# Backwards-compatible fallback; runtime chunking is settings-driven.
_CHUNK_SIZE = 1_000_000
_ROLLBACK_MANIFEST = "manifest.json"

CsvSource = Path | VisionCsvPayload


@dataclass
class IngestResult:
    """Result of a Pipeline.ingest() call."""
    symbol: str
    data_type: str
    objects_count: int
    files_written: int
    file_paths: list[str] = field(default_factory=list)
    start: date | None = None
    end: date | None = None
    skipped: bool = False
    rest_fallback_used: bool = False
    rest_fallback_range: tuple[date, date] | None = None


@dataclass
class _CleanupBackup:
    original_path: Path
    backup_path: Path


@dataclass
class _FundingCacheSnapshot:
    path: Path
    existed: bool
    payload: bytes | None


class _ParquetCleanupGuard:
    """Rollback handle for overlapping parquet files removed before ingest writes."""

    def __init__(
        self,
        storage: Any,
        rollback_prefix: Path,
        *,
        target_dir: Path | None = None,
        preserved_paths: set[Path] | None = None,
        original_paths: set[Path] | None = None,
    ) -> None:
        self._storage = storage
        self._rollback_prefix = rollback_prefix
        self._target_dir = Path(target_dir) if target_dir is not None else None
        self._preserved_paths = {Path(p) for p in preserved_paths or set()}
        self._original_paths = {Path(p) for p in original_paths or set()}
        self._backups: list[_CleanupBackup] = []
        self._catalog_commit: dict[str, Any] | None = None
        self._active = True

    def add_backup(self, original_path: Path, backup_path: Path) -> None:
        original = Path(original_path)
        self._original_paths.add(original)
        self._backups.append(
            _CleanupBackup(original_path=original, backup_path=Path(backup_path))
        )

    def _manifest_payload(self, *, resolved: bool = False) -> dict[str, Any]:
        return {
            "version": 1,
            "complete": True,
            "resolved": resolved,
            "rollback_prefix": str(self._rollback_prefix),
            "target_dir": str(self._target_dir) if self._target_dir is not None else None,
            "preserved_paths": sorted(str(path) for path in self._preserved_paths),
            "original_paths": sorted(str(path) for path in self._original_paths),
            "catalog_commit": self._catalog_commit,
            "backups": [
                {
                    "original_path": str(backup.original_path),
                    "backup_path": str(backup.backup_path),
                }
                for backup in self._backups
            ],
        }

    def _write_manifest(self, payload: dict[str, Any]) -> None:
        manifest_path = self._rollback_prefix / _ROLLBACK_MANIFEST
        temp_path = self._rollback_prefix / f".{_ROLLBACK_MANIFEST}.{uuid4().hex}.tmp"
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        if getattr(self._storage, "provider", "local") == "local":
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(encoded)
            temp_path.replace(manifest_path)
            return

        self._storage.upload_bytes(temp_path, encoded)
        try:
            self._storage.copy_path(temp_path, manifest_path)
        finally:
            try:
                self._storage.delete_path(temp_path)
            except Exception:
                logger.warning("Failed to delete temporary rollback manifest %s", temp_path, exc_info=True)

    def persist_manifest(self) -> None:
        """Persist enough rollback metadata for crash recovery before delete."""
        self._write_manifest(self._manifest_payload(resolved=False))

    def mark_resolved(self) -> None:
        """Mark rollback metadata as already handled before best-effort cleanup."""
        self._write_manifest(self._manifest_payload(resolved=True))

    def record_catalog_commit(
        self,
        *,
        symbol: str,
        data_type: str,
        interval: str,
        source_type: str | None,
        start: date,
        end: date,
        file_path: str,
        record_count: int | None,
        size_bytes: int | None,
        pre_update_row: dict[str, Any] | None,
        ingest_run_id: str,
    ) -> None:
        """Persist the intended DB catalog commit without resolving rollback.

        Startup recovery uses this as proof material: if the process crashes
        after the DB commit but before ``discard()``, recovery can verify the
        catalog row and avoid restoring old parquet over a committed ingest. If
        the DB row is absent or stale, the same manifest remains unresolved and
        recovery restores the backups.
        """
        self._catalog_commit = {
            "symbol": symbol,
            "data_type": data_type,
            "interval": interval,
            "source_type": source_type,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "file_path": file_path,
            "record_count": record_count,
            "size_bytes": size_bytes,
            "pre_update_row": pre_update_row,
            "ingest_run_id": ingest_run_id,
        }
        self._write_manifest(self._manifest_payload(resolved=False))

    @property
    def backups(self) -> list[_CleanupBackup]:
        return list(self._backups)

    def delete_current_outputs(self, file_paths: list[str] | set[str]) -> None:
        """Delete parquet files produced by the current failed ingest attempt."""
        from tinohelm.data.storage import delete_prefix

        seen: set[Path] = set()
        delete_failures: list[Path] = []
        for raw_path in file_paths:
            path = Path(raw_path)
            if path in seen or path in self._preserved_paths:
                continue
            seen.add(path)
            try:
                delete_prefix(self._storage, path)
            except Exception:
                delete_failures.append(path)
                logger.warning("Failed to delete current-run parquet %s", path, exc_info=True)

        if self._target_dir is None:
            if delete_failures:
                raise RuntimeError(f"failed to delete current-run parquet: {delete_failures}")
            return
        try:
            active_objects = list(
                self._storage.iter_files(self._target_dir, suffix=".parquet", recursive=False)
            )
        except Exception as exc:
            raise RuntimeError(
                f"failed to list current-run parquet under {self._target_dir}"
            ) from exc

        for obj in active_objects:
            path = Path(obj.path)
            if path in self._preserved_paths or path in seen:
                continue
            try:
                delete_prefix(self._storage, path)
            except Exception:
                delete_failures.append(path)
                logger.warning("Failed to delete current-run parquet %s", path, exc_info=True)
        if delete_failures:
            raise RuntimeError(f"failed to delete current-run parquet: {delete_failures}")

    def restore(self, *, discard: bool = True) -> None:
        """Restore backed-up objects to their original logical paths."""
        if not self._active:
            return
        restore_failures: list[tuple[Path, Path, BaseException]] = []
        for backup in reversed(self._backups):
            try:
                self._storage.copy_path(backup.backup_path, backup.original_path)
            except BaseException as exc:
                restore_failures.append((backup.backup_path, backup.original_path, exc))
        if restore_failures:
            detail = ", ".join(
                f"{src} -> {dest}: {exc}" for src, dest, exc in restore_failures
            )
            raise RuntimeError(f"failed to restore rollback parquet backups: {detail}") from restore_failures[0][2]
        if discard:
            self.discard(best_effort=True)

    def discard(self, *, best_effort: bool = False) -> None:
        """Drop rollback copies after the replacement is known-good."""
        if not self._active:
            return
        if not self._backups and self._target_dir is None:
            self._active = False
            return
        try:
            try:
                self.mark_resolved()
            except Exception:
                if best_effort:
                    logger.warning(
                        "Failed to mark rollback parquet backups resolved under %s",
                        self._rollback_prefix,
                        exc_info=True,
                    )
                else:
                    raise
            if getattr(self._storage, "provider", "local") != "local":
                from tinohelm.data.storage import delete_prefix

                delete_prefix(self._storage, self._rollback_prefix)
            else:
                shutil.rmtree(self._rollback_prefix, ignore_errors=True)
                try:
                    self._rollback_prefix.parent.rmdir()
                except OSError:
                    pass
        except Exception:
            if best_effort:
                logger.warning(
                    "Failed to discard rollback parquet backups under %s",
                    self._rollback_prefix,
                    exc_info=True,
                )
                return
            raise
        self._active = False


def _catalog_row_to_snapshot(row: Any | None) -> dict[str, Any] | None:
    """Normalize a DataCatalog row into JSON-stable fields for recovery checks."""
    if row is None:
        return None
    return {
        "symbol": row.symbol,
        "data_type": row.data_type,
        "interval": row.interval,
        "source_type": row.source_type,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "file_path": row.file_path,
        "record_count": row.record_count,
        "size_bytes": row.size_bytes,
        "last_ingest_id": getattr(row, "last_ingest_id", None),
    }


def _catalog_snapshot_matches(current: dict[str, Any] | None, expected: Any) -> bool:
    if current is None:
        return expected is None
    if expected is None or not isinstance(expected, dict):
        return False
    expected_snapshot = {
        "symbol": expected.get("symbol"),
        "data_type": expected.get("data_type"),
        "interval": expected.get("interval"),
        "source_type": expected.get("source_type"),
        "start_date": expected.get("start_date"),
        "end_date": expected.get("end_date"),
        "file_path": expected.get("file_path"),
        "record_count": expected.get("record_count"),
        "size_bytes": expected.get("size_bytes"),
    }
    if "last_ingest_id" in expected:
        expected_snapshot["last_ingest_id"] = expected.get("last_ingest_id")
    else:
        current = {key: value for key, value in current.items() if key != "last_ingest_id"}
    return current == expected_snapshot


def _parse_catalog_commit_payload(commit_payload: Any) -> dict[str, Any] | None:
    """Validate rollback manifest DB commit witness payload."""
    if not isinstance(commit_payload, dict):
        return None
    if "pre_update_row" not in commit_payload:
        return None
    try:
        ingest_run_id = commit_payload.get("ingest_run_id")
        if not ingest_run_id:
            return None
        source_type = commit_payload.get("source_type")
        return {
            "symbol": str(commit_payload["symbol"]),
            "data_type": str(commit_payload["data_type"]),
            "interval": str(commit_payload["interval"]),
            "source_type": str(source_type) if source_type is not None else None,
            "start": date.fromisoformat(str(commit_payload["start_date"])),
            "end": date.fromisoformat(str(commit_payload["end_date"])),
            "file_path": str(commit_payload["file_path"]),
            "ingest_run_id": str(ingest_run_id),
            "pre_update_row": commit_payload.get("pre_update_row"),
        }
    except Exception:
        return None


async def _catalog_commit_current_snapshot(parsed: dict[str, Any]) -> dict[str, Any] | None:
    from sqlalchemy import select
    from tinohelm.db.models import DataCatalog
    from tinohelm.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(DataCatalog).where(
            DataCatalog.symbol == parsed["symbol"],
            DataCatalog.data_type == parsed["data_type"],
            DataCatalog.interval == parsed["interval"],
        )
        if parsed["source_type"] is None:
            stmt = stmt.where(DataCatalog.source_type.is_(None))
        else:
            stmt = stmt.where(DataCatalog.source_type == parsed["source_type"])
        row = (await session.execute(stmt)).scalar_one_or_none()
    return _catalog_row_to_snapshot(row)


def _catalog_commit_matches_current_snapshot(
    current: dict[str, Any] | None,
    parsed: dict[str, Any],
) -> bool:
    if current is None:
        return False
    if current.get("last_ingest_id") != parsed["ingest_run_id"]:
        return False
    if current["file_path"] != parsed["file_path"]:
        return False
    if date.fromisoformat(current["start_date"]) > parsed["start"]:
        return False
    if date.fromisoformat(current["end_date"]) < parsed["end"]:
        return False
    return True


def _catalog_commit_supersedes_manifest(
    current: dict[str, Any] | None,
    parsed: dict[str, Any],
) -> bool:
    if current is None:
        return False
    current_token = current.get("last_ingest_id")
    if not current_token or current_token == parsed["ingest_run_id"]:
        return False
    return not _catalog_snapshot_matches(current, parsed.get("pre_update_row"))


async def _catalog_commit_is_persisted_async(commit_payload: Any) -> bool:
    """Return True only when the DB row carries this ingest's exact commit token."""
    parsed = _parse_catalog_commit_payload(commit_payload)
    if parsed is None:
        return False
    current = await _catalog_commit_current_snapshot(parsed)
    return _catalog_commit_matches_current_snapshot(current, parsed)


def _catalog_commit_is_persisted(commit_payload: Any) -> bool:
    """Synchronous wrapper for non-startup recovery callers and tests."""
    return asyncio.run(_catalog_commit_is_persisted_async(commit_payload))


async def _catalog_commit_is_superseded_async(commit_payload: Any) -> bool:
    parsed = _parse_catalog_commit_payload(commit_payload)
    if parsed is None:
        return False
    current = await _catalog_commit_current_snapshot(parsed)
    return _catalog_commit_supersedes_manifest(current, parsed)


async def _catalog_commit_should_discard_rollback_async(commit_payload: Any) -> bool:
    if await _catalog_commit_is_persisted_async(commit_payload):
        return True
    return await _catalog_commit_is_superseded_async(commit_payload)


def _catalog_commit_should_discard_rollback(commit_payload: Any) -> bool:
    if _catalog_commit_is_persisted(commit_payload):
        return True
    return asyncio.run(_catalog_commit_is_superseded_async(commit_payload))


def _rollback_manifest_objects(active_storage: Any, rollback_root: Path) -> list[Any]:
    return [
        obj for obj in active_storage.iter_files(rollback_root, suffix=".json", recursive=True)
        if Path(obj.path).name == _ROLLBACK_MANIFEST
    ]


def _read_rollback_manifest(active_storage: Any, manifest_obj: Any) -> dict[str, Any]:
    try:
        payload = json.loads(active_storage.read_bytes(manifest_obj).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to read ingest rollback manifest {manifest_obj.path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"failed to read ingest rollback manifest {manifest_obj.path}")
    return payload


def _discard_rollback_prefix(active_storage: Any, rollback_prefix: Path) -> None:
    from tinohelm.data.storage import delete_prefix

    if getattr(active_storage, "provider", "local") != "local":
        delete_prefix(active_storage, rollback_prefix)
        return
    shutil.rmtree(rollback_prefix, ignore_errors=True)
    try:
        rollback_prefix.parent.rmdir()
    except OSError:
        pass


def _restore_rollback_payload(active_storage: Any, manifest_obj: Any, payload: dict[str, Any]) -> int:
    from tinohelm.data.storage import delete_prefix

    try:
        backups = payload.get("backups") or []
        rollback_prefix = Path(payload.get("rollback_prefix") or Path(manifest_obj.path).parent)
        target_dir_raw = payload.get("target_dir")
        target_dir = Path(target_dir_raw) if target_dir_raw else None
        preserved_paths = {Path(path) for path in payload.get("preserved_paths") or []}
        original_paths = {Path(path) for path in payload.get("original_paths") or []}
    except Exception as exc:
        raise RuntimeError(f"failed to read ingest rollback manifest {manifest_obj.path}") from exc

    restored_paths = {Path(entry["original_path"]) for entry in backups}
    restored = 0
    try:
        for entry in reversed(backups):
            active_storage.copy_path(Path(entry["backup_path"]), Path(entry["original_path"]))
            restored += 1
        if target_dir is not None and payload.get("complete") is True:
            keep_paths = restored_paths | preserved_paths | original_paths
            for obj in list(active_storage.iter_files(target_dir, suffix=".parquet", recursive=False)):
                active_path = Path(obj.path)
                if active_path in keep_paths:
                    continue
                delete_prefix(active_storage, active_path)
    except Exception as exc:
        raise RuntimeError(
            f"failed to restore ingest rollback backups from {manifest_obj.path}"
        ) from exc

    _discard_rollback_prefix(active_storage, rollback_prefix)
    return restored


def recover_pending_ingest_rollbacks(
    catalog_path: str | Path,
    *,
    storage: Any | None = None,
) -> int:
    """Restore crash-stranded ingest rollback backups before new mutations."""
    from tinohelm.data.storage import get_catalog_storage

    catalog_root = Path(catalog_path)
    active_storage = storage or get_catalog_storage(catalog_root=catalog_root)
    rollback_root = catalog_root / ".ingest-rollback"
    manifests = _rollback_manifest_objects(active_storage, rollback_root)
    restored = 0
    for manifest_obj in manifests:
        payload = _read_rollback_manifest(active_storage, manifest_obj)
        rollback_prefix = Path(payload.get("rollback_prefix") or Path(manifest_obj.path).parent)

        if payload.get("resolved") is True:
            try:
                _discard_rollback_prefix(active_storage, rollback_prefix)
            except Exception:
                logger.warning(
                    "Failed to discard resolved ingest rollback prefix %s",
                    rollback_prefix,
                    exc_info=True,
                )
            continue

        catalog_commit = payload.get("catalog_commit")
        if catalog_commit and _catalog_commit_should_discard_rollback(catalog_commit):
            try:
                _discard_rollback_prefix(active_storage, rollback_prefix)
            except Exception:
                logger.warning(
                    "Failed to discard committed ingest rollback prefix %s",
                    rollback_prefix,
                    exc_info=True,
                )
            continue

        restored += _restore_rollback_payload(active_storage, manifest_obj, payload)

    if restored:
        logger.warning("Restored %d parquet object(s) from pending ingest rollback", restored)
    return restored


async def recover_pending_ingest_rollbacks_async(
    catalog_path: str | Path,
    *,
    storage: Any | None = None,
) -> int:
    """Async startup recovery; DB witness checks stay on the current event loop."""
    from tinohelm.data.storage import get_catalog_storage

    catalog_root = Path(catalog_path)
    active_storage = storage or await asyncio.to_thread(get_catalog_storage, catalog_root=catalog_root)
    rollback_root = catalog_root / ".ingest-rollback"
    manifests = await asyncio.to_thread(_rollback_manifest_objects, active_storage, rollback_root)
    restored = 0
    for manifest_obj in manifests:
        payload = await asyncio.to_thread(_read_rollback_manifest, active_storage, manifest_obj)
        rollback_prefix = Path(payload.get("rollback_prefix") or Path(manifest_obj.path).parent)

        if payload.get("resolved") is True:
            try:
                await asyncio.to_thread(_discard_rollback_prefix, active_storage, rollback_prefix)
            except Exception:
                logger.warning(
                    "Failed to discard resolved ingest rollback prefix %s",
                    rollback_prefix,
                    exc_info=True,
                )
            continue

        catalog_commit = payload.get("catalog_commit")
        if catalog_commit and await _catalog_commit_should_discard_rollback_async(catalog_commit):
            try:
                await asyncio.to_thread(_discard_rollback_prefix, active_storage, rollback_prefix)
            except Exception:
                logger.warning(
                    "Failed to discard committed ingest rollback prefix %s",
                    rollback_prefix,
                    exc_info=True,
                )
            continue

        restored += await asyncio.to_thread(_restore_rollback_payload, active_storage, manifest_obj, payload)

    if restored:
        logger.warning("Restored %d parquet object(s) from pending ingest rollback", restored)
    return restored


class BinanceVisionPipeline:
    """Orchestrate download → convert → store for Binance Vision data.

    Parameters
    ----------
    catalog_path:
        Path to the NautilusTrader ParquetDataCatalog root.
    raw_dir:
        Path for raw downloaded CSV/ZIP files.
    """

    def __init__(
        self,
        catalog_path: str | Path,
        raw_dir: str | Path = "~/.tino/data/raw",
    ) -> None:
        from tinohelm.core.config import get_settings
        from tinohelm.data.storage import get_catalog_storage

        cfg = get_settings()
        self._storage = get_catalog_storage(settings=cfg, catalog_root=catalog_path)
        self.catalog_path = str(self._storage.catalog_root)
        self.downloader = VisionDownloader(
            raw_dir=raw_dir, concurrency=cfg.data.download_concurrency,
        )
        self._convert_workers = max(1, cfg.data.convert_workers)
        self._chunk_rows = max(1, cfg.data.chunk_rows)
        self._agg_trades_chunk_rows = max(1, cfg.data.agg_trades_chunk_rows)
        self._csv_queue_maxsize = max(1, cfg.data.csv_queue_maxsize)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(
        self,
        symbol: str,
        data_type: str,
        start: date,
        end: date,
        asset_class: str = "um",
        interval: str | None = None,
        progress_cb: Callable[[int, str], Any] | None = None,
    ) -> IngestResult:
        """Ingest data from Binance Vision into the Parquet catalog.

        Async entry point — use from FastAPI routes or other async contexts.

        Parameters
        ----------
        symbol:
            TinoHelm-style symbol (e.g. ``"BTCUSDT-PERP"``).
        data_type:
            Vision data type key (e.g. ``"klines"``, ``"aggTrades"``).
        start / end:
            Inclusive date range.
        asset_class:
            ``"um"`` or ``"cm"``.
        interval:
            Required for klines-family types (e.g. ``"1m"``, ``"5m"``).
        progress_cb:
            Optional ``(percent: int, message: str)`` callback.
        """
        if data_type in _KLINES_TYPES and not interval:
            raise ValueError(f"interval is required for data_type={data_type!r}")
        if asset_class not in ("um", "cm"):
            raise ValueError(f"asset_class must be 'um' or 'cm', got {asset_class!r}")

        async def _progress(pct: int, msg: str):
            if progress_cb:
                try:
                    result = progress_cb(pct, msg)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

        await _progress(0, f"Planning downloads for {symbol} {data_type}...")

        # Early exit: funding-rate cache already covers [start, end].
        if data_type == "fundingRate":
            from tinohelm.data.catalog import CatalogSession

            _funding_session = CatalogSession(self.catalog_path, storage=self._storage)
            if (
                _funding_session.funding_cache_covers(symbol, start, end)
                and _funding_session.funding_parquet_covers(symbol, start, end)
            ):
                await _progress(100, "Funding rate cache already covers range")
                return IngestResult(
                    symbol=symbol, data_type=data_type,
                    objects_count=0, files_written=0,
                    start=start, end=end, skipped=True,
                )

        # 1. Plan downloads (monthly-first + daily-tail)
        tasks = self.downloader.plan_downloads(
            data_type=data_type,
            symbol=symbol,
            asset_class=asset_class,
            start=start,
            end=end,
            interval=interval,
        )

        if not tasks:
            await _progress(100, "No downloads needed")
            return IngestResult(
                symbol=symbol, data_type=data_type,
                objects_count=0, files_written=0,
                start=start, end=end, skipped=True,
            )

        # 2. Prepare converter (before downloads so conversion can start immediately)
        instrument = self._get_instrument(symbol)
        converter = get_converter(data_type)
        kwargs = self._build_converter_kwargs(
            data_type, symbol, interval, instrument,
        )
        cleanup_guard = self._clean_overlapping_parquet(symbol, data_type, interval, start, end)

        # 3. Pipelined download → convert (overlap via asyncio.Queue)
        dl_concurrency = self.downloader.concurrency
        n_converters = self._convert_workers
        sem = asyncio.Semaphore(dl_concurrency)
        total_tasks = len(tasks)

        csv_queue: asyncio.Queue[CsvSource | None] = asyncio.Queue(maxsize=self._csv_queue_maxsize)
        download_done = 0
        convert_done = 0
        total_objects = 0
        all_file_paths: list[str] = []
        preexisting_output_paths = self._catalog_current_paths(symbol, data_type, interval, data_type)
        rollback_done = False
        download_errors: list[tuple[str, Exception]] = []
        download_success_indices: set[int] = set()
        download_failed_indices: dict[int, Exception] = {}
        convert_errors: list[tuple[str, Exception]] = []
        import threading

        pending_convert_futures: set[asyncio.Future] = set()
        pending_convert_names: dict[asyncio.Future, str] = {}
        pending_convert_events: dict[asyncio.Future, threading.Event] = {}
        pending_convert_results: dict[asyncio.Future, dict[str, Any]] = {}
        recorded_convert_futures: set[asyncio.Future] = set()

        def _clear_current_task_cancellation() -> None:
            current_task = asyncio.current_task()
            while current_task is not None and current_task.cancelling():
                current_task.uncancel()

        def _recancel_current_task() -> None:
            current_task = asyncio.current_task()
            if current_task is not None:
                current_task.cancel()

        def _discard_convert_future(convert_future: asyncio.Future) -> None:
            pending_convert_futures.discard(convert_future)
            pending_convert_names.pop(convert_future, None)
            pending_convert_events.pop(convert_future, None)
            pending_convert_results.pop(convert_future, None)

        def _record_convert_result(
            convert_future: asyncio.Future,
            objects_count: int,
            file_paths: list[str],
        ) -> None:
            nonlocal total_objects
            if convert_future in recorded_convert_futures:
                return
            recorded_convert_futures.add(convert_future)
            _discard_convert_future(convert_future)
            if objects_count > 0:
                total_objects += objects_count
            if file_paths:
                all_file_paths.extend(file_paths)

        async def _await_convert_future_after_cancel(convert_future: asyncio.Future) -> None:
            _clear_current_task_cancellation()
            csv_name = pending_convert_names.get(convert_future, "<unknown>")
            try:
                n, fps = await asyncio.shield(convert_future)
                _record_convert_result(convert_future, n, fps)
            except asyncio.CancelledError:
                done_event = pending_convert_events.get(convert_future)
                result_box = pending_convert_results.get(convert_future)
                if done_event is None or result_box is None:
                    _discard_convert_future(convert_future)
                    logger.warning(
                        "Convert executor was cancelled before rollback collection for %s",
                        csv_name,
                    )
                    return
                await asyncio.to_thread(done_event.wait)
                if "exception" in result_box:
                    _discard_convert_future(convert_future)
                    logger.warning(
                        "Convert executor failed after cancellation for %s",
                        csv_name,
                        exc_info=(type(result_box["exception"]), result_box["exception"], None),
                    )
                    return
                n, fps = result_box.get("result", (0, []))
                _record_convert_result(convert_future, n, fps)
            except Exception:
                _discard_convert_future(convert_future)
                logger.warning(
                    "Convert executor failed after cancellation for %s",
                    csv_name,
                    exc_info=True,
                )

        async def _drain_pending_convert_futures() -> None:
            while pending_convert_futures:
                futures = list(pending_convert_futures)
                await asyncio.gather(
                    *(_await_convert_future_after_cancel(future) for future in futures),
                    return_exceptions=True,
                )

        async def _await_ignoring_cancellation(awaitable) -> Any:
            task = asyncio.ensure_future(awaitable)
            while True:
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    _clear_current_task_cancellation()
                    if task.done():
                        return task.result()

        def _rollback_once() -> None:
            nonlocal rollback_done
            if rollback_done:
                return
            rollback_done = True
            self._rollback_failed_ingest(
                cleanup_guard,
                all_file_paths,
                preexisting_output_paths,
            )

        try:
            await _progress(
                DOWNLOAD_PROGRESS_BASE,
                f"Downloading {len(tasks)} file(s) (×{dl_concurrency}, convert ×{n_converters})...",
            )
        except asyncio.CancelledError:
            _rollback_once()
            raise

        async def _download_one(index: int, task):
            nonlocal download_done
            async with sem:
                try:
                    csv_path = await self.downloader.execute_task(task)
                    if csv_path:
                        download_success_indices.add(index)
                        await csv_queue.put(csv_path)
                    else:
                        exc = RuntimeError("downloader returned no CSV payload")
                        download_failed_indices[index] = exc
                        download_errors.append((task.url, exc))
                except Exception as exc:
                    logger.warning(
                        "Download failed for %s: %s", task.url, exc,
                    )
                    download_failed_indices[index] = exc
                    download_errors.append((task.url, exc))
                download_done += 1
                await _progress(
                    compute_stage_pct(convert_done, total_tasks),
                    f"下载 {download_done}/{total_tasks}",
                )

        async def _download_all():
            await asyncio.gather(*[_download_one(idx, t) for idx, t in enumerate(tasks)])
            # Send one sentinel per converter worker
            for _ in range(n_converters):
                await csv_queue.put(None)

        async def _convert_consumer():
            nonlocal convert_done, total_objects
            schema_validated = False
            loop = asyncio.get_running_loop()
            while True:
                csv_path = await csv_queue.get()
                if csv_path is None:
                    break

                csv_name = self._csv_display_name(csv_path)
                # Thread-safe chunk callback: interpolates within current file's range
                _cd = convert_done  # snapshot before executor starts
                def _chunk_cb(objects_so_far):
                    chunk_rows = self._chunk_rows_for(data_type)
                    chunks = max(1, objects_so_far // chunk_rows)
                    sub = compute_chunk_subprogress(_cd, total_tasks, chunks)
                    asyncio.run_coroutine_threadsafe(
                        _progress(sub, f"转换中 {objects_so_far:,} objects..."),
                        loop,
                    )

                convert_future: asyncio.Future | None = None
                try:
                    done_event = threading.Event()
                    result_box: dict[str, Any] = {}

                    def _run_convert_one_file():
                        try:
                            result = self._convert_one_file(
                                csv_path, converter, instrument, kwargs,
                                symbol, data_type, interval, schema_validated,
                                _chunk_cb,
                            )
                            result_box["result"] = result
                            return result
                        except BaseException as exc:
                            result_box["exception"] = exc
                            raise
                        finally:
                            done_event.set()

                    convert_future = loop.run_in_executor(None, _run_convert_one_file)
                    pending_convert_futures.add(convert_future)
                    pending_convert_names[convert_future] = csv_name
                    pending_convert_events[convert_future] = done_event
                    pending_convert_results[convert_future] = result_box
                    try:
                        n, fps = await asyncio.shield(convert_future)
                    except asyncio.CancelledError:
                        await _await_convert_future_after_cancel(convert_future)
                        raise
                    if n <= 0 or not fps:
                        _discard_convert_future(convert_future)
                        raise RuntimeError(
                            f"conversion produced no objects/files for {csv_name}"
                        )
                    schema_validated = True
                    _record_convert_result(convert_future, n, fps)
                except Exception as exc:
                    if convert_future is not None:
                        _discard_convert_future(convert_future)
                    logger.warning("Convert failed for %s", csv_name, exc_info=True)
                    convert_errors.append((csv_name, exc))
                self._cleanup_raw_file(csv_path)
                convert_done += 1
                pct = compute_stage_pct(convert_done, total_tasks)
                await _progress(
                    pct,
                    f"已完成 {convert_done}/{total_tasks} ({total_objects:,} objects)",
                )

        consumer_tasks = [asyncio.create_task(_convert_consumer()) for _ in range(n_converters)]
        download_task = asyncio.create_task(_download_all())
        pipeline_tasks = [download_task, *consumer_tasks]
        try:
            await asyncio.gather(*pipeline_tasks)
        except asyncio.CancelledError:
            for task in pipeline_tasks:
                task.cancel()
            _clear_current_task_cancellation()
            await _await_ignoring_cancellation(
                asyncio.gather(*pipeline_tasks, return_exceptions=True)
            )
            await _await_ignoring_cancellation(_drain_pending_convert_futures())
            _rollback_once()
            raise

        def _rest_fallback_start_for_tail_download_errors() -> date | None:
            if not download_failed_indices or not is_rest_fallback_supported(data_type):
                return None
            if not download_success_indices:
                return None
            first_failed = min(download_failed_indices)
            expected_tail = set(range(first_failed, len(tasks)))
            if set(download_failed_indices) != expected_tail:
                return None
            last_success = first_failed - 1
            if last_success not in download_success_indices:
                return None
            last_success_task = tasks[last_success]
            vision_end = parse_vision_coverage_end(
                last_success_task.granularity,
                last_success_task.dest_path.stem,
            )
            if not vision_end or vision_end >= end:
                return None
            return vision_end + timedelta(days=1)

        tail_rest_start = _rest_fallback_start_for_tail_download_errors()
        blocking_download_errors = bool(download_errors) and tail_rest_start is None
        if blocking_download_errors or convert_errors:
            _rollback_once()
            parts: list[str] = []
            first_exc: Exception | None = None
            if download_errors:
                first_url, first_exc = download_errors[0]
                parts.append(f"download failed for {first_url}: {first_exc}")
            if convert_errors:
                first_name, exc = convert_errors[0]
                first_exc = first_exc or exc
                parts.append(f"conversion failed for {first_name}: {exc}")
            await _progress(100, "Failed")
            raise RuntimeError("; ".join(parts)) from first_exc

        if total_objects <= 0:
            _rollback_once()
            await _progress(100, "Failed")
            raise RuntimeError(
                f"{symbol} {data_type} converted 0 objects from {len(tasks)} downloaded file(s)"
            )

        # 4. REST API fallback for recent data gap
        rest_fallback_used = False
        rest_fallback_range = None

        if is_rest_fallback_supported(data_type):
            rest_start = tail_rest_start
            if rest_start is None:
                vision_end = self._detect_vision_coverage_end(tasks)
                if vision_end and vision_end < end:
                    rest_start = vision_end + timedelta(days=1)
            if rest_start is not None:
                try:
                    await _progress(92, f"REST fallback: {rest_start} → {end}")
                except asyncio.CancelledError:
                    _rollback_once()
                    raise
                try:
                    fb_count, fb_paths = await self._rest_fallback(
                        symbol, data_type, interval, rest_start, end, instrument,
                    )
                    if fb_count <= 0 or not fb_paths:
                        raise RuntimeError(
                            f"REST fallback produced no objects/files for {symbol} {data_type} "
                            f"[{rest_start}..{end}]"
                        )
                    total_objects += fb_count
                    all_file_paths.extend(fb_paths)
                    rest_fallback_used = True
                    rest_fallback_range = (rest_start, end)
                except asyncio.CancelledError:
                    _rollback_once()
                    raise
                except Exception as exc:
                    logger.warning(
                        "REST fallback failed for %s %s [%s..%s]",
                        symbol, data_type, rest_start, end, exc_info=True,
                    )
                    _rollback_once()
                    await _progress(100, "Failed")
                    raise RuntimeError(
                        f"REST fallback failed for {symbol} {data_type} [{rest_start}..{end}]: {exc}"
                    ) from exc

        # 5. Update DB catalog
        try:
            await _progress(96, "Updating catalog database...")
        except asyncio.CancelledError:
            _rollback_once()
            raise
        # Compute size_bytes from written files
        unique_paths = set(all_file_paths)
        try:
            written_size = self._written_file_size(unique_paths) if unique_paths else None
        except Exception:
            _rollback_once()
            await _progress(100, "Failed")
            logger.exception("Failed to stat written catalog files")
            raise
        if task_cancelled := asyncio.current_task():
            if task_cancelled.cancelling():
                _rollback_once()
                raise asyncio.CancelledError
        record_count = total_objects if total_objects > 0 else None
        ingest_run_id = uuid4().hex
        if cleanup_guard is not None:
            try:
                from tinohelm.data.catalog import resolve_catalog_path

                pre_update_row = await self._read_db_catalog_snapshot(
                    symbol,
                    data_type,
                    interval,
                    data_type,
                )
                cleanup_guard.record_catalog_commit(
                    symbol=symbol,
                    data_type=resolve_db_category(data_type),
                    interval=resolve_db_interval(data_type, interval),
                    source_type=data_type,
                    start=start,
                    end=end,
                    file_path=str(resolve_catalog_path(self.catalog_path, data_type)),
                    record_count=record_count,
                    size_bytes=written_size,
                    pre_update_row=pre_update_row,
                    ingest_run_id=ingest_run_id,
                )
            except asyncio.CancelledError:
                _rollback_once()
                raise
            except Exception:
                _rollback_once()
                await _progress(100, "Failed")
                logger.exception("Failed to persist ingest rollback DB-commit intent")
                raise
        post_commit_was_cancelled = False
        update_task = asyncio.create_task(self._update_db_catalog(
            symbol, data_type, interval, start, end,
            record_count=record_count,
            size_bytes=written_size,
            source_type=data_type,
            ingest_run_id=ingest_run_id,
        ))
        try:
            await asyncio.shield(update_task)
        except asyncio.CancelledError:
            post_commit_was_cancelled = True
            _clear_current_task_cancellation()
            try:
                await _await_ignoring_cancellation(update_task)
            except asyncio.CancelledError:
                _rollback_once()
                await _progress(100, "Failed")
                logger.exception("DB catalog update was cancelled before commit")
                raise
            except Exception:
                _rollback_once()
                await _progress(100, "Failed")
                logger.exception("Failed to update DB catalog")
                raise
        except Exception:
            _rollback_once()
            await _progress(100, "Failed")
            logger.exception("Failed to update DB catalog")
            raise

        if cleanup_guard is not None:
            cleanup_guard.discard(best_effort=True)

        if post_commit_was_cancelled:
            logger.info(
                "Cancellation arrived after DB catalog commit for %s %s; treating committed ingest as success",
                symbol,
                data_type,
            )

        try:
            await _progress(100, f"Done: {total_objects} objects")
        except asyncio.CancelledError:
            _clear_current_task_cancellation()
            post_commit_was_cancelled = True
            logger.info(
                "Cancellation arrived after DB catalog commit while publishing final progress for %s %s; "
                "treating committed ingest as success",
                symbol,
                data_type,
            )

        result = IngestResult(
            symbol=symbol,
            data_type=data_type,
            objects_count=total_objects,
            files_written=len(all_file_paths),
            file_paths=all_file_paths,
            start=start,
            end=end,
            rest_fallback_used=rest_fallback_used,
            rest_fallback_range=rest_fallback_range,
        )
        if post_commit_was_cancelled:
            _recancel_current_task()
        return result

    def _rollback_failed_ingest(
        self,
        cleanup_guard: _ParquetCleanupGuard | None,
        file_paths: list[str],
        preexisting_paths: set[Path] | None = None,
    ) -> None:
        """Remove current-run outputs, then restore old overlapping parquet."""
        if cleanup_guard is not None:
            delete_exc: BaseException | None = None
            try:
                cleanup_guard.delete_current_outputs(file_paths)
            except BaseException as exc:
                delete_exc = exc
                logger.warning(
                    "Failed to delete current-run outputs during ingest rollback",
                    exc_info=True,
                )

            try:
                cleanup_guard.restore(discard=delete_exc is None)
            except BaseException as restore_exc:
                if delete_exc is not None:
                    raise RuntimeError(
                        "rollback failed to restore old parquet after current-output deletion also failed"
                    ) from restore_exc
                raise

            if delete_exc is not None:
                raise RuntimeError(
                    "rollback restored old parquet but failed to delete some current-run outputs"
                ) from delete_exc
            return
        self._delete_written_paths(file_paths, preserve=preexisting_paths)

    def _delete_written_paths(
        self,
        file_paths: list[str] | set[str],
        *,
        preserve: set[Path] | None = None,
    ) -> None:
        """Delete current-run outputs when no cleanup guard exists."""
        from tinohelm.data.storage import delete_prefix

        preserved = {Path(p) for p in preserve or set()}
        seen: set[Path] = set()
        delete_failures: list[Path] = []
        for raw_path in file_paths:
            path = Path(raw_path)
            if path in seen or path in preserved:
                continue
            seen.add(path)
            try:
                delete_prefix(self._storage, path)
            except Exception:
                delete_failures.append(path)
                logger.warning("Failed to delete current-run parquet %s", path, exc_info=True)
        if delete_failures:
            raise RuntimeError(f"failed to delete current-run parquet: {delete_failures}")

    def _catalog_current_paths(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        source_type: str | None,
    ) -> set[Path]:
        """Snapshot existing parquet paths for rollback-safe no-guard writers."""
        target_dir = self._catalog_item_dir(symbol, data_type, interval, source_type)
        if target_dir is None:
            return set()
        try:
            return {
                Path(obj.path)
                for obj in self._storage.iter_files(target_dir, suffix=".parquet", recursive=True)
            }
        except Exception:
            logger.warning("Failed to snapshot catalog paths under %s", target_dir, exc_info=True)
            return set()


    def ingest_sync(self, **kwargs) -> IngestResult:
        """Synchronous wrapper for :meth:`ingest`.

        Use from BacktestRunner subprocesses and other sync contexts.
        Creates a new event loop internally.
        """
        return asyncio.run(self.ingest(**kwargs))

    # ------------------------------------------------------------------
    # Streaming conversion (bounded memory)
    # ------------------------------------------------------------------

    @staticmethod
    def _csv_display_name(csv_source: CsvSource) -> str:
        if isinstance(csv_source, VisionCsvPayload):
            return csv_source.name
        return str(csv_source)

    @staticmethod
    def _open_csv_binary(csv_source: CsvSource):
        if isinstance(csv_source, VisionCsvPayload):
            return csv_source.open()
        return open(csv_source, "rb")

    @staticmethod
    def _detect_header(path: CsvSource) -> int | None:
        """Return 0 if the CSV has a text header row, else None (no header).

        Thin I/O wrapper around :func:`pipeline_helpers.csv_has_header`.
        """
        with BinanceVisionPipeline._open_csv_binary(path) as fh:
            first_raw = fh.readline()
        if isinstance(first_raw, bytes):
            first = first_raw.decode("utf-8-sig", errors="replace")
        else:
            first = str(first_raw)
        return 0 if csv_has_header(first) else None

    def _convert_one_file(
        self, csv_path: CsvSource, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
        chunk_cb=None,
    ) -> tuple[int, list[str]]:
        """Convert a single CSV file/payload (sync, runs in executor thread)."""
        hdr = self._detect_header(csv_path)

        if converter.supports_chunked:
            return self._stream_chunked_file(
                csv_path, hdr, converter, instrument, kwargs,
                symbol, data_type, interval, schema_validated,
                chunk_cb=chunk_cb,
            )
        else:
            return self._stream_full_file(
                csv_path, hdr, converter, instrument, kwargs,
                symbol, data_type, interval, schema_validated,
            )

    async def _ingest_streaming(
        self,
        csv_paths: list[CsvSource],
        converter,
        instrument,
        kwargs: dict,
        symbol: str,
        data_type: str,
        interval: str | None,
        _progress,
    ) -> tuple[int, list[str]]:
        """Process CSVs one at a time to bound memory usage.

        For chunked converters: reads in chunks, converts, flushes periodically.
        For non-chunked converters: reads one full CSV, converts, writes, moves on.
        Deletes each CSV (and its ZIP) after processing to free disk space.
        """
        total_objects = 0
        all_file_paths: list[str] = []
        schema_validated = False

        for csv_idx, csv_path in enumerate(csv_paths):
            csv_name = self._csv_display_name(csv_path)
            try:
                hdr = self._detect_header(csv_path)
            except Exception:
                logger.warning("Failed to read CSV header %s", csv_name, exc_info=True)
                raise

            if converter.supports_chunked:
                n, fps = self._stream_chunked_file(
                    csv_path, hdr, converter, instrument, kwargs,
                    symbol, data_type, interval, schema_validated,
                )
            else:
                n, fps = self._stream_full_file(
                    csv_path, hdr, converter, instrument, kwargs,
                    symbol, data_type, interval, schema_validated,
                )

            schema_validated = True
            if n <= 0 or not fps:
                raise RuntimeError(
                    f"conversion produced no objects/files for {csv_name}"
                )
            total_objects += n
            all_file_paths.extend(fps)

            # Free disk: remove processed CSV and its ZIP
            self._cleanup_raw_file(csv_path)

            pct = 78 + int(12 * (csv_idx + 1) / len(csv_paths))
            await _progress(
                pct,
                f"Converted {csv_idx + 1}/{len(csv_paths)} files ({total_objects} objects)",
            )

        return total_objects, all_file_paths

    def _stream_chunked_file(
        self, csv_path: CsvSource, hdr, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
        chunk_cb=None,
    ) -> tuple[int, list[str]]:
        """Read one CSV source in chunks, convert, flush periodically."""
        source = None
        csv_name = self._csv_display_name(csv_path)
        try:
            chunk_rows = self._chunk_rows_for(data_type)
            source = self._open_csv_binary(csv_path)
            reader = pd.read_csv(source, header=hdr, chunksize=chunk_rows)
        except Exception:
            if source is not None:
                source.close()
            logger.warning("Failed to open CSV %s", csv_name, exc_info=True)
            raise

        count = 0
        fps: list[str] = []
        chunk_objects: list = []

        try:
            for chunk in reader:
                if hdr is None:
                    chunk.columns = range(len(chunk.columns))
                if not schema_validated:
                    converter.validate_schema(chunk)
                    schema_validated = True

                objs = converter.convert_chunk(chunk, instrument, **kwargs)
                chunk_objects.extend(objs)

                if len(chunk_objects) >= chunk_rows:
                    fp = self._write_objects(
                        chunk_objects, symbol, data_type, interval, merge=False,
                    )
                    if not fp:
                        raise RuntimeError(
                            f"{data_type} conversion produced {len(chunk_objects)} objects but wrote no parquet files"
                        )
                    count += len(chunk_objects)
                    fps.extend(fp)
                    chunk_objects = []
                    if chunk_cb:
                        chunk_cb(count)
        finally:
            source.close()

        if chunk_objects:
            fp = self._write_objects(
                chunk_objects, symbol, data_type, interval, merge=False,
            )
            if not fp:
                raise RuntimeError(
                    f"{data_type} conversion produced {len(chunk_objects)} objects but wrote no parquet files"
                )
            count += len(chunk_objects)
            fps.extend(fp)
            if chunk_cb:
                chunk_cb(count)

        return count, fps

    def _chunk_rows_for(self, data_type: str) -> int:
        if data_type == "aggTrades":
            return self._agg_trades_chunk_rows
        return self._chunk_rows

    def _stream_full_file(
        self, csv_path: CsvSource, hdr, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
    ) -> tuple[int, list[str]]:
        """Read one full CSV source, convert, write."""
        csv_name = self._csv_display_name(csv_path)
        try:
            with self._open_csv_binary(csv_path) as source:
                df = pd.read_csv(source, header=hdr)
            if hdr is None:
                df.columns = range(len(df.columns))
        except Exception:
            logger.warning("Failed to read CSV %s", csv_name, exc_info=True)
            raise

        if not schema_validated:
            converter.validate_schema(df)

        objects = converter.convert(df, instrument, **kwargs)
        del df

        if not objects:
            return 0, []

        fps = self._write_objects(
            objects, symbol, data_type, interval, merge=False,
        )
        if not fps:
            raise RuntimeError(
                f"{data_type} conversion produced {len(objects)} objects but wrote no parquet files"
            )
        count = len(objects)
        del objects
        return count, fps

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _write_objects(
        self,
        objects: list,
        symbol: str,
        data_type: str,
        interval: str | None,
        merge: bool = True,
    ) -> list[str]:
        """Write converted NT objects to the Parquet catalog."""
        if not objects:
            return []

        if data_type in {"markPriceKlines", "indexPriceKlines", "fundingRate"}:
            from tinohelm.data.catalog import _catalog_for_root, _iter_catalog_files, ensure_catalog_dirs

            catalog_path = Path(self.catalog_path)
            if getattr(self._storage, "provider", "local") == "local":
                ensure_catalog_dirs(catalog_path)
            catalog = _catalog_for_root(catalog_path, self._storage)
            update_dir = self._catalog_item_dir(symbol, data_type, interval, data_type)
            if update_dir is None:
                raise RuntimeError(f"No direct-update catalog path for data_type={data_type!r}")
            existing = {
                str(p)
                for p in _iter_catalog_files(self._storage, update_dir, recursive=True)
            }
            catalog.write_data(objects, skip_disjoint_check=True)
            current = sorted({str(p) for p in _iter_catalog_files(self._storage, update_dir, recursive=True)})
            written = sorted(set(current) - existing)
            if objects and not current:
                raise RuntimeError(
                    f"{objects[0].__class__.__name__} write for {symbol} produced no parquet files under {update_dir}"
                )
            return written or current

        category = resolve_write_category(data_type)

        if category == "bar":
            from tinohelm.data.catalog import write_bars
            if not interval:
                raise ValueError(f"Cannot write bars without interval for data_type={data_type!r}")
            paths = write_bars(
                bars=objects, symbol=symbol,
                interval=interval, catalog_path=self.catalog_path,
                merge=merge, source_type=data_type,
                storage=self._storage,
            )
            return [str(p) for p in paths]

        elif category == "trade_tick":
            from tinohelm.data.catalog import write_trade_ticks
            paths = write_trade_ticks(
                ticks=objects, symbol=symbol,
                catalog_path=self.catalog_path,
                source_type=data_type,
                storage=self._storage,
            )
            return paths

        elif category == "quote_tick":
            from tinohelm.data.catalog import write_quote_ticks
            paths = write_quote_ticks(
                ticks=objects, symbol=symbol,
                catalog_path=self.catalog_path,
                source_type=data_type,
                storage=self._storage,
            )
            return paths

        elif category == "metrics":
            from tinohelm.data.catalog import write_metrics_parquet
            path = write_metrics_parquet(objects, symbol, self.catalog_path, storage=self._storage)
            return [str(path)]

        elif category == "order_book_delta":
            from tinohelm.data.catalog import write_book_depth_parquet
            path = write_book_depth_parquet(objects, symbol, self.catalog_path, storage=self._storage)
            return [str(path)]

        else:
            raise RuntimeError(
                f"No catalog writer for data_type={data_type!r} (category={category!r})"
            )


    # ------------------------------------------------------------------
    # REST API fallback
    # ------------------------------------------------------------------

    def _detect_vision_coverage_end(
        self,
        tasks,
    ) -> date | None:
        """Detect the last date covered by Vision downloads.

        Thin wrapper around :func:`pipeline_helpers.parse_vision_coverage_end`
        that pulls ``granularity`` and ``stem`` off the last task. Stems look
        like ``BTCUSDT-klines-1m-2025-03-15`` (daily) or
        ``BTCUSDT-aggTrades-2025-03`` (monthly).
        """
        if not tasks:
            return None
        last_task = tasks[-1]
        return parse_vision_coverage_end(last_task.granularity, last_task.dest_path.stem)

    async def _rest_fallback(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        start: date,
        end: date,
        instrument,
    ) -> tuple[int, list[str]]:
        """Use REST API to fill the gap between Vision coverage and requested end."""
        start_dt = date_start_dt(start)
        end_dt = date_end_dt(end)

        # Klines-family: unified handler with per-type fetch function
        if data_type in KLINES_REST_FETCH_FN:
            return await self._rest_fallback_klines(
                fetch_fn_name=KLINES_REST_FETCH_FN[data_type],
                symbol=symbol, data_type=data_type,
                interval=interval, start_dt=start_dt,
                end_dt=end_dt, instrument=instrument,
            )

        if data_type == "aggTrades":
            return await self._rest_fallback_trades(
                symbol, start_dt, end_dt,
            )

        # trades: no REST fallback (Binance /fapi/v1/trades has
        # different schema from aggTrades; skip to avoid mismatch)
        logger.info(
            "No REST fallback for data_type=%r, skipping", data_type,
        )
        return 0, []

    async def _rest_fallback_klines(
        self, fetch_fn_name: str, symbol: str, data_type: str,
        interval: str | None, start_dt, end_dt, instrument,
    ) -> tuple[int, list[str]]:
        """Shared REST fallback for all klines-family types."""
        from tinohelm.data.catalog import write_bars

        if not interval:
            return 0, []

        import importlib
        mod = importlib.import_module("tinohelm.data.providers.binance")
        fetch_fn = getattr(mod, fetch_fn_name)

        klines = await fetch_fn(
            symbol=symbol, interval=interval,
            start=start_dt, end=end_dt,
        )
        if not klines:
            return 0, []

        converter = get_converter(data_type)
        kwargs = self._build_converter_kwargs(
            data_type, symbol, interval, instrument,
        )
        df = pd.DataFrame(klines)
        bars = converter.convert(df, instrument, **kwargs)
        if bars:
            paths = write_bars(
                bars,
                symbol,
                interval,
                self.catalog_path,
                merge=False,
                source_type=data_type,
                storage=self._storage,
            )
            return len(bars), [str(p) for p in paths]
        return 0, []

    async def _rest_fallback_trades(
        self, symbol: str, start_dt, end_dt,
    ) -> tuple[int, list[str]]:
        """REST fallback for aggTrades only."""
        from tinohelm.data.providers.binance import fetch_agg_trades
        from tinohelm.data.catalog import (
            agg_trades_to_trade_ticks,
            write_trade_ticks,
        )

        agg_trades = await fetch_agg_trades(
            symbol=symbol, start=start_dt, end=end_dt,
        )
        if not agg_trades:
            return 0, []

        ticks = agg_trades_to_trade_ticks(agg_trades, symbol)
        if ticks:
            paths = write_trade_ticks(
                ticks,
                symbol,
                self.catalog_path,
                source_type="aggTrades",
                storage=self._storage,
            )
            return len(ticks), paths
        return 0, []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clean_overlapping_parquet(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        start: date,
        end: date,
    ) -> _ParquetCleanupGuard | None:
        """Delete overlapping parquet only after creating rollback copies.

        Streaming writes need old overlapping files out of the target directory so
        NT can return newly written files deterministically.  The destructive
        delete is guarded by catalog-local rollback copies; if any later ingest
        phase fails, the caller restores this guard before surfacing failure.
        """
        category = resolve_write_category(data_type)
        if data_type in {"markPriceKlines", "indexPriceKlines"}:
            target_dir = self._catalog_item_dir(symbol, data_type, interval, data_type)
            if target_dir is None:
                return None
        elif category == "bar" and interval:
            from tinohelm.data.catalog import _make_bar_type, _make_instrument, resolve_catalog_path
            inst = _make_instrument(symbol)
            bar_type = _make_bar_type(inst.id, interval)
            resolved = resolve_catalog_path(self.catalog_path, data_type)
            target_dir = Path(resolved) / "data" / "bar" / str(bar_type)
        elif category in {"trade_tick", "quote_tick"}:
            from tinohelm.data.catalog import resolve_catalog_path
            inst = self._get_instrument(symbol)
            resolved = resolve_catalog_path(self.catalog_path, data_type)
            nt_category = "quote_tick" if category == "quote_tick" else "trade_tick"
            target_dir = Path(resolved) / "data" / nt_category / str(inst.id)
        elif category in {"metrics", "order_book_delta", "funding_rate"}:
            target_path = self._catalog_item_dir(symbol, data_type, interval, data_type)
            if target_path is None:
                return None
            rollback_prefix = Path(self.catalog_path) / ".ingest-rollback" / uuid4().hex
            guard = _ParquetCleanupGuard(
                self._storage,
                rollback_prefix,
                target_dir=target_path,
            )
            try:
                if self._storage.exists(target_path):
                    if target_path.suffix == ".parquet":
                        backup_path = rollback_prefix / target_path.name
                        self._storage.copy_path(target_path, backup_path)
                        guard.add_backup(target_path, backup_path)
                    else:
                        backup_dir = rollback_prefix / target_path.name
                        for original_path in self._storage.iter_files(target_path, suffix=".parquet", recursive=False):
                            backup_path = backup_dir / original_path.path.name
                            self._storage.copy_path(original_path.path, backup_path)
                            guard.add_backup(original_path.path, backup_path)
            except FileNotFoundError:
                pass
            except Exception:
                guard.restore()
                raise
            guard.persist_manifest()
            return guard
        else:
            return None  # unsupported data types have no catalog cleanup target

        from tinohelm.data.storage import delete_prefix

        parquet_objects = list(self._storage.iter_files(target_dir, suffix=".parquet", recursive=False))

        start_ns = date_start_ns(start)
        end_ns = date_end_ns(end)
        overlapping: list[Any] = []

        for obj in parquet_objects:
            try:
                time_range = self._parquet_time_range(obj, storage=self._storage)
            except TypeError:
                # Backward-compat for tests/callers monkeypatching the old
                # one-argument helper signature.
                time_range = self._parquet_time_range(obj.path)
            if time_range is None:
                # Cannot determine range — replace conservatively, but keep rollback.
                overlapping.append(obj)
                continue
            file_min, file_max = time_range
            if file_max >= start_ns and file_min < end_ns:
                overlapping.append(obj)

        rollback_prefix = Path(self.catalog_path) / ".ingest-rollback" / uuid4().hex
        preserved_paths = {Path(obj.path) for obj in parquet_objects if obj not in overlapping}
        original_paths = {Path(obj.path) for obj in parquet_objects}
        guard = _ParquetCleanupGuard(
            self._storage,
            rollback_prefix,
            target_dir=target_dir,
            preserved_paths=preserved_paths,
            original_paths=original_paths,
        )
        guard.persist_manifest()
        if not overlapping:
            return guard

        try:
            for obj in overlapping:
                original_path = Path(obj.path)
                backup_path = rollback_prefix / original_path.name
                self._storage.copy_path(original_path, backup_path)
                guard.add_backup(original_path, backup_path)
            guard.persist_manifest()
            for obj in overlapping:
                delete_prefix(self._storage, Path(obj.path))
        except Exception:
            guard.restore()
            raise

        logger.info(
            "Cleaned %d overlapping parquet file(s) in %s for [%s, %s] with rollback",
            len(overlapping), target_dir, start, end,
        )
        return guard

    @staticmethod
    def _parquet_time_range(path_or_object, storage=None) -> tuple[int, int] | None:
        """Read min/max timestamp from parquet row-group statistics.

        Returns ``(min_ts_ns, max_ts_ns)`` or ``None`` if timestamp statistics are
        genuinely absent. I/O, auth, and parse failures propagate so callers do
        not treat unreadable remote objects as safely replaceable unknown ranges.
        """
        import pyarrow.parquet as pq

        if storage is not None and getattr(storage, "provider", "local") != "local":
            with storage.open_input_file(path_or_object) as fh:
                pf = pq.ParquetFile(fh)
                return BinanceVisionPipeline._parquet_file_time_range(pf)

        path = path_or_object.path if hasattr(path_or_object, "path") else path_or_object
        pf = pq.ParquetFile(str(Path(path)))
        return BinanceVisionPipeline._parquet_file_time_range(pf)

    @staticmethod
    def _parquet_row_count(path_or_object, storage=None) -> int | None:
        """Read parquet row count from metadata without materializing rows."""
        try:
            import pyarrow.parquet as pq

            if storage is not None and getattr(storage, "provider", "local") != "local":
                with storage.open_input_file(path_or_object) as fh:
                    return int(pq.ParquetFile(fh).metadata.num_rows)
            path = path_or_object.path if hasattr(path_or_object, "path") else path_or_object
            return int(pq.ParquetFile(str(Path(path))).metadata.num_rows)
        except Exception:
            return None

    def _written_file_size(self, paths: set[str | Path] | list[str | Path]) -> int | None:
        """Return total size for freshly written catalog files.

        Remote writers return logical paths that do not materialize on the local
        filesystem, so remote size accounting must query the active storage
        provider instead of relying on ``Path.exists()``.
        """
        unique_paths = {Path(p) for p in paths}
        if not unique_paths:
            return None

        if getattr(self._storage, "provider", "local") == "local":
            missing = [path for path in unique_paths if not path.exists()]
            if missing:
                raise FileNotFoundError(f"written parquet path(s) missing: {missing}")
            return sum(path.stat().st_size for path in unique_paths)

        total = 0
        missing: list[Path] = []
        for path in unique_paths:
            objects = list(self._storage.iter_files(path, suffix=".parquet", recursive=False))
            if not objects:
                missing.append(path)
                continue
            for obj in objects:
                total += int(obj.size or 0)
        if missing:
            raise FileNotFoundError(f"written parquet path(s) missing: {missing}")
        return total

    @staticmethod
    def _parquet_file_time_range(pf) -> tuple[int, int] | None:
        schema = pf.schema_arrow
        col_idx = None
        for name in ("ts_event", "ts_init"):
            idx = schema.get_field_index(name)
            if idx >= 0:
                col_idx = idx
                break
        if col_idx is None:
            return None

        min_ts: int | None = None
        max_ts: int | None = None
        for i in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(i).column(col_idx).statistics
            if stats is None or not stats.has_min_max:
                return None  # incomplete statistics
            stat_min = _timestamp_stat_to_ns(stats.min)
            stat_max = _timestamp_stat_to_ns(stats.max)
            if min_ts is None or stat_min < min_ts:
                min_ts = stat_min
            if max_ts is None or stat_max > max_ts:
                max_ts = stat_max

        return (int(min_ts), int(max_ts)) if min_ts is not None else None

    @staticmethod
    def _cleanup_raw_file(csv_path: CsvSource) -> None:
        """Remove processed legacy raw CSV/ZIP files; close bounded CSV payloads."""
        if isinstance(csv_path, VisionCsvPayload):
            csv_path.close()
            return
        try:
            csv_path.unlink(missing_ok=True)
            csv_path.with_suffix(".zip").unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _get_instrument(symbol: str):
        """Get NT CryptoPerpetual instrument from instruments.py."""
        from tinohelm.data.catalog import _make_instrument
        return _make_instrument(symbol)

    @staticmethod
    def _build_converter_kwargs(
        data_type: str,
        symbol: str,
        interval: str | None,
        instrument,
    ) -> dict:
        """Build extra kwargs for converter.convert()."""
        kwargs: dict[str, Any] = {"symbol": symbol}

        if data_type in _KLINES_TYPES and interval:
            from tinohelm.data.catalog import _make_bar_type
            bar_type = _make_bar_type(instrument.id, interval)
            kwargs["bar_type"] = bar_type

        return kwargs

    def _catalog_storage_stats(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        source_type: str | None,
    ) -> tuple[int | None, int]:
        """Return full-scan row and byte counts for a catalog storage path."""
        target_dir = self._catalog_item_dir(symbol, data_type, interval, source_type)
        if target_dir is None:
            return None, 0
        parquet_objects = list(self._storage.iter_files(target_dir, suffix=".parquet", recursive=True))
        if not parquet_objects:
            return None, 0

        total_rows = 0
        total_size = 0
        saw_unknown_rows = False
        for obj in parquet_objects:
            if obj.size is not None:
                total_size += int(obj.size)
            else:
                try:
                    total_size += obj.path.stat().st_size
                except OSError:
                    pass
            row_count = self._parquet_row_count(obj, storage=self._storage)
            if row_count is None:
                saw_unknown_rows = True
            elif not saw_unknown_rows:
                total_rows += row_count

        if saw_unknown_rows:
            return None, total_size
        return total_rows, total_size

    def _catalog_storage_coverage(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        source_type: str | None,
    ) -> tuple[date, date] | None:
        """Return stored min/max event dates for a catalog storage path."""
        target_dir = self._catalog_item_dir(symbol, data_type, interval, source_type)
        if target_dir is None:
            return None
        paths = list(self._storage.iter_files(target_dir, suffix=".parquet", recursive=True))
        if not paths:
            return None

        min_ts: int | None = None
        max_ts: int | None = None
        saw_unknown_range = False
        for obj in paths:
            time_range = self._parquet_time_range(obj, storage=self._storage)
            if time_range is None:
                saw_unknown_range = True
                continue
            file_min, file_max = time_range
            min_ts = file_min if min_ts is None else min(min_ts, file_min)
            max_ts = file_max if max_ts is None else max(max_ts, file_max)

        if saw_unknown_range or min_ts is None or max_ts is None:
            return None
        return (_ns_to_utc_date(min_ts), _ns_to_utc_date(max_ts))

    def _catalog_item_dir(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        source_type: str | None,
    ) -> Path | None:
        """Resolve the concrete on-disk directory represented by one DB row."""
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import make_bar_type_str, normalize_symbol

        category = resolve_write_category(data_type)
        if category == "metrics":
            from tinohelm.data.catalog import metrics_parquet_path

            return metrics_parquet_path(symbol, self.catalog_path)
        if category == "order_book_delta":
            from tinohelm.data.catalog import book_depth_parquet_path

            return book_depth_parquet_path(symbol, self.catalog_path)
        if category == "funding_rate":
            from tinohelm.data.catalog import funding_rate_update_dir

            return funding_rate_update_dir(symbol, self.catalog_path)

        if data_type == "markPriceKlines":
            from tinohelm.data.catalog import mark_price_update_dir

            return mark_price_update_dir(symbol, self.catalog_path)
        if data_type == "indexPriceKlines":
            from tinohelm.data.catalog import index_price_update_dir

            return index_price_update_dir(symbol, self.catalog_path)

        effective_source = source_type or data_type
        resolved = resolve_catalog_path(self.catalog_path, effective_source)
        nt_symbol = normalize_symbol(symbol)
        if category == "bar":
            if not interval:
                return None
            return Path(resolved) / "data" / "bar" / make_bar_type_str(symbol, interval)
        if category == "trade_tick":
            return Path(resolved) / "data" / "trade_tick" / nt_symbol
        if category == "quote_tick":
            return Path(resolved) / "data" / "quote_tick" / nt_symbol
        return None

    async def _read_db_catalog_snapshot(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        source_type: str | None,
    ) -> dict[str, Any] | None:
        """Read the pre-update DataCatalog row for crash-recovery proof."""
        from sqlalchemy import select
        from tinohelm.db.models import DataCatalog
        from tinohelm.db.session import get_session_factory

        category = resolve_db_category(data_type)
        db_interval = resolve_db_interval(data_type, interval)
        factory = get_session_factory()
        async with factory() as session:
            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == category,
                DataCatalog.interval == db_interval,
            )
            if source_type is None:
                stmt = stmt.where(DataCatalog.source_type.is_(None))
            else:
                stmt = stmt.where(DataCatalog.source_type == source_type)
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _catalog_row_to_snapshot(row)

    async def _update_db_catalog(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
        start: date,
        end: date,
        record_count: int | None = None,
        size_bytes: int | None = None,
        source_type: str | None = None,
        ingest_run_id: str | None = None,
    ) -> None:
        """Upsert a DataCatalog row for the ingested data."""
        from sqlalchemy import select, text
        from tinohelm.db.models import DataCatalog
        from tinohelm.db.session import get_session_factory

        from tinohelm.data.catalog import resolve_catalog_path

        category = resolve_db_category(data_type)
        db_interval = resolve_db_interval(data_type, interval)
        effective_path = str(resolve_catalog_path(self.catalog_path, source_type))
        lock_key = f"data_catalog:{symbol}:{category}:{db_interval}:{source_type or ''}"

        factory = get_session_factory()
        async with factory() as session:
            try:
                bind = session.get_bind()
                dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
            except Exception:
                dialect_name = ""
            if dialect_name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
                    {"key": lock_key},
                )

            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == category,
                DataCatalog.interval == db_interval,
                DataCatalog.source_type == source_type,
            ).with_for_update()
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                overlaps_existing = start <= existing.end_date and end >= existing.start_date
                existing.file_path = effective_path
                write_category = resolve_write_category(data_type)
                if overlaps_existing:
                    storage_record_count, storage_size_bytes = self._catalog_storage_stats(
                        symbol,
                        data_type,
                        interval,
                        source_type,
                    )
                    existing.record_count = storage_record_count
                    existing.size_bytes = storage_size_bytes
                    if write_category in {"bar", "trade_tick", "quote_tick"}:
                        storage_coverage = self._catalog_storage_coverage(
                            symbol,
                            data_type,
                            interval,
                            source_type,
                        )
                        if storage_coverage is not None:
                            existing.start_date, existing.end_date = storage_coverage
                    else:
                        existing.start_date = min(existing.start_date, start)
                        existing.end_date = max(existing.end_date, end)
                else:
                    existing.start_date = min(existing.start_date, start)
                    existing.end_date = max(existing.end_date, end)
                    if (
                        write_category in {"metrics", "order_book_delta", "funding_rate"}
                        or data_type in {"markPriceKlines", "indexPriceKlines"}
                    ):
                        storage_record_count, storage_size_bytes = self._catalog_storage_stats(
                            symbol,
                            data_type,
                            interval,
                            source_type,
                        )
                        existing.record_count = storage_record_count
                        existing.size_bytes = storage_size_bytes
                    else:
                        if record_count is None:
                            existing.record_count = None
                        elif existing.record_count is not None:
                            existing.record_count += record_count
                        if size_bytes is not None:
                            if write_category == "bar":
                                _, storage_size_bytes = self._catalog_storage_stats(
                                    symbol,
                                    data_type,
                                    interval,
                                    source_type,
                                )
                                existing.size_bytes = storage_size_bytes
                            else:
                                existing.size_bytes = (existing.size_bytes or 0) + size_bytes
                if source_type is not None:
                    existing.source_type = source_type
                existing.last_ingest_id = ingest_run_id
            else:
                session.add(DataCatalog(
                    symbol=symbol,
                    data_type=category,
                    interval=db_interval,
                    start_date=start,
                    end_date=end,
                    file_path=effective_path,
                    size_bytes=size_bytes or 0,
                    record_count=record_count,
                    source_type=source_type,
                    last_ingest_id=ingest_run_id,
                ))

            await session.commit()
