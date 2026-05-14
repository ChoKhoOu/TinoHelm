"""ParquetDataCatalog manager for TinoHelm — writes NautilusTrader native format."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator
from urllib.parse import urlparse
from uuid import uuid4

from tinohelm.data.catalog_helpers import (
    CATEGORY_DIR,
    INTERVAL_MAP,
    WRITABLE_CATEGORIES,
    build_validation_issues,
    classify_status,
    count_duplicates,
    dedupe_by_ts,
    detect_price_jumps,
    find_gaps,
    interval_to_nanoseconds,
    interval_to_nt_suffix,
    interval_to_step_unit,
    is_ohlc_valid,
    merge_bars,
    ns_to_iso,
    nt_suffix_to_interval,
    resolve_catalog_path,
)
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY as _PIPELINE_WRITE_CATEGORY

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl


# ---------------------------------------------------------------------------
# Backward-compatibility aliases — pre-extraction code paths and tests
# imported these private names directly. They now re-export the helpers so
# existing callers (and any future ones that landed on historical names)
# keep working unchanged.
# ---------------------------------------------------------------------------
_INTERVAL_MAP = INTERVAL_MAP
_CATEGORY_DIR = CATEGORY_DIR
# ``_SOURCE_TO_CATEGORY`` used to be a catalog-local subset of
# ``pipeline_helpers.WRITE_CATEGORY``. The subset is now derived on the fly
# in ``resolve_catalog_path`` — the alias below is kept for any external
# caller that grep-imported the old constant directly.
_SOURCE_TO_CATEGORY: dict[str, str] = {
    src: cat
    for src, cat in _PIPELINE_WRITE_CATEGORY.items()
    if cat in WRITABLE_CATEGORIES
}

# Default source-types carried by legacy (pre-source-aware) catalog rows.
# Used by ``CatalogSession`` to replicate the double-delete / fallback semantics
# that ``api/routes/data.py`` implements via its own ``_LEGACY_DEFAULT_SOURCE``
# table. Keep the two in sync — candidate 2 (``DataTypeRegistry``) will
# eventually collapse them.
_LEGACY_DEFAULT_SOURCE: dict[str, str] = {
    "bar": "klines",
    "trade_tick": "aggTrades",
    "quote_tick": "bookTicker",
    "funding_rate": "fundingRate",
    "order_book_delta": "bookDepth",
    "liquidation": "liquidationSnapshot",
    "metrics": "metrics",
}
_LEGACY_DEFAULT_SOURCE_FOR_BAR = _LEGACY_DEFAULT_SOURCE["bar"]


def _interval_to_nanoseconds(interval: str) -> int:
    """Backward-compatible wrapper around :func:`interval_to_nanoseconds`."""
    return interval_to_nanoseconds(interval)


def _make_instrument(symbol: str):
    """Create a CryptoPerpetual instrument for the given Binance futures symbol.

    Delegates to instruments.make_instrument() which uses real exchange
    parameters from Binance exchangeInfo API (cached locally for 24h).
    """
    from tinohelm.data.instruments import make_instrument
    return make_instrument(symbol)


def _make_bar_type(instrument_id, interval: str):
    """Create a BarType from instrument_id and interval string like '5m', '1h'."""
    from nautilus_trader.model.data import BarType, BarSpecification
    from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType

    step, agg_name = interval_to_step_unit(interval)
    aggregation = getattr(BarAggregation, agg_name)

    return BarType(
        instrument_id=instrument_id,
        bar_spec=BarSpecification(step, aggregation, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )


def _parquet_stat_value_to_ns(value: Any) -> int:
    """Normalise a Parquet ``min``/``max`` statistic to epoch nanoseconds."""
    from datetime import datetime, timezone

    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    return int(value)


def _parquet_object_stats(path_or_object: Any, storage: Any | None = None) -> dict[str, Any] | None:
    """Return row count / timestamp range / size stats for one parquet object."""
    try:
        import pyarrow.parquet as pq

        if storage is not None and getattr(storage, "provider", "local") != "local":
            with storage.open_input_file(path_or_object) as fh:
                metadata = pq.ParquetFile(fh).metadata
        else:
            path = path_or_object.path if hasattr(path_or_object, "path") else path_or_object
            metadata = pq.read_metadata(Path(path))
    except Exception:
        return None

    total_rows = int(metadata.num_rows)
    ts_min: int | None = None
    ts_max: int | None = None
    for rg_idx in range(metadata.num_row_groups):
        rg = metadata.row_group(rg_idx)
        for col_idx in range(rg.num_columns):
            col = rg.column(col_idx)
            if col.path_in_schema not in {"ts_event", "ts_init"}:
                continue
            if col.statistics and col.statistics.has_min_max:
                stat_min = _parquet_stat_value_to_ns(col.statistics.min)
                stat_max = _parquet_stat_value_to_ns(col.statistics.max)
                ts_min = stat_min if ts_min is None else min(ts_min, stat_min)
                ts_max = stat_max if ts_max is None else max(ts_max, stat_max)
    size = getattr(path_or_object, "size", None)
    if size is None:
        path = getattr(path_or_object, "path", path_or_object)
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
    return {"rows": total_rows, "ts_min": ts_min, "ts_max": ts_max, "size": int(size or 0)}


def _last_modified_date(obj: Any) -> Any:
    """Extract a last-modified calendar date from a storage object or path."""
    from datetime import date, datetime, timezone

    value = getattr(obj, "last_modified", None)
    if value is None:
        path = getattr(obj, "path", None)
        if path is not None:
            try:
                return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc).date()
            except OSError:
                return None
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.date()
    if isinstance(value, (int, float)):
        if value > 10_000_000_000_000_000:  # nanoseconds
            scale = 1_000_000_000
        elif value > 10_000_000_000_000:  # microseconds
            scale = 1_000_000
        elif value > 10_000_000_000:  # milliseconds
            scale = 1_000
        else:
            scale = 1
        return datetime.fromtimestamp(value / scale, tz=timezone.utc).date()
    return None


def _aggregate_parquet_object_stats(objects: list, storage: Any) -> dict[str, Any] | None:
    """Aggregate size, row count, and date coverage over a list of parquet objects.

    Returns ``None`` when no object yields either timestamp statistics or a
    last-modified fallback. ``record_count`` is ``None`` when any file's row
    count was unavailable (the caller must not treat the partial sum as exact).
    """
    from datetime import datetime, timezone

    total_size = 0
    total_rows = 0
    all_rows_known = True
    min_ts: int | None = None
    max_ts: int | None = None
    fallback_dates: list = []

    for obj in objects:
        stats = _parquet_object_stats(obj, storage=storage)
        if stats is None:
            all_rows_known = False
            if getattr(obj, "size", None) is not None:
                total_size += int(obj.size)
            fallback = _last_modified_date(obj)
            if fallback is not None:
                fallback_dates.append(fallback)
            continue

        total_size += int(stats["size"])
        rows = stats.get("rows")
        if rows is None:
            all_rows_known = False
        elif all_rows_known:
            total_rows += int(rows)
        obj_min = stats.get("ts_min")
        obj_max = stats.get("ts_max")
        if obj_min is None or obj_max is None:
            fallback = _last_modified_date(obj)
            if fallback is not None:
                fallback_dates.append(fallback)
        else:
            min_ts = int(obj_min) if min_ts is None else min(min_ts, int(obj_min))
            max_ts = int(obj_max) if max_ts is None else max(max_ts, int(obj_max))

    if min_ts is not None and max_ts is not None:
        start_dt = datetime.fromtimestamp(min_ts // 1_000_000_000, tz=timezone.utc).date()
        end_dt = datetime.fromtimestamp(max_ts // 1_000_000_000, tz=timezone.utc).date()
    elif fallback_dates:
        start_dt = min(fallback_dates)
        end_dt = max(fallback_dates)
    else:
        return None

    return {
        "start_date": start_dt,
        "end_date": end_dt,
        "size_bytes": total_size,
        "record_count": total_rows if all_rows_known else None,
        "all_record_counts_known": all_rows_known,
    }


def _funding_parquet_time_range(path: Path, storage: Any) -> tuple[int, int] | None:
    """Read min/max ``ts_event`` from a funding-rate parquet.

    Funding-rate parquets carry ``ts_event`` as the row-level funding time
    in nanoseconds (see :func:`write_funding_rate_parquet`). This helper
    returns ``None`` when statistics are missing; I/O errors propagate.
    """
    import pyarrow.parquet as pq

    if getattr(storage, "provider", "local") != "local":
        with storage.open_input_file(path) as fh:
            pf = pq.ParquetFile(fh)
            return _parquet_file_ts_event_range(pf)
    pf = pq.ParquetFile(str(Path(path)))
    return _parquet_file_ts_event_range(pf)


def _parquet_file_ts_event_range(pf) -> tuple[int, int] | None:
    schema = pf.schema_arrow
    idx = schema.get_field_index("ts_event")
    if idx < 0:
        return None
    min_ts: int | None = None
    max_ts: int | None = None
    for i in range(pf.metadata.num_row_groups):
        stats = pf.metadata.row_group(i).column(idx).statistics
        if stats is None or not stats.has_min_max:
            return None
        stat_min = int(stats.min)
        stat_max = int(stats.max)
        if min_ts is None or stat_min < min_ts:
            min_ts = stat_min
        if max_ts is None or stat_max > max_ts:
            max_ts = stat_max
    if min_ts is None:
        return None
    return (min_ts, max_ts)


def ensure_catalog_dirs(catalog_path: str | Path) -> Path:
    """Ensure catalog directory exists."""
    path = Path(catalog_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class _FundingCacheSnapshot:
    path: Path
    existed: bool
    payload: bytes | None


class FundingRateTxn:
    """Context manager owning the funding-rate dual-store transaction.

    Responsibilities
    ----------------
    * ``write_parquet(records)`` writes the Parquet primary and stages records
      to be merged into the JSON cache after the DB commit succeeds.
    * ``flush_json()`` merges the staged records into the legacy JSON cache.
      Callers must invoke it explicitly after a successful DB commit — the
      session will not auto-flush so that cancelled/half-committed ingests
      leave the JSON read-side stale rather than lying ahead of the DB.
    * Exit with exception rolls the JSON read-side back to the pre-write
      snapshot. Parquet rollback is the caller's responsibility (the pipeline
      handles it via ``_ParquetCleanupGuard``).

    Normal exit without ``flush_json`` logs a warning — indicates a caller
    bug (forgot to flush) but leaves the JSON untouched.
    """

    def __init__(
        self,
        catalog_path: Path,
        symbol: str,
        snapshot: _FundingCacheSnapshot,
        storage: Any | None = None,
    ) -> None:
        self._catalog_path = catalog_path
        self.symbol = symbol
        self._storage = storage
        self._snapshot = snapshot
        self._pending: list[dict[str, Any]] = []
        self._flushed = False
        self._written_parquet = False

    @property
    def flushed(self) -> bool:
        return self._flushed

    @property
    def wrote_parquet(self) -> bool:
        return self._written_parquet

    def write_parquet(self, records: list) -> Path:
        """Write funding-rate records to the primary Parquet and stage JSON updates."""
        def _cache_record(record: Any) -> dict[str, Any]:
            funding_time_ms = getattr(record, "funding_time_ms", None)
            if funding_time_ms is None:
                funding_time_ms = int(getattr(record, "ts_event")) // 1_000_000
            funding_rate = getattr(record, "funding_rate", None)
            if funding_rate is None:
                funding_rate = getattr(record, "rate")
            return {
                "funding_time_ms": int(funding_time_ms),
                "funding_rate": float(funding_rate),
                "mark_price": 0,
            }

        cache_records = [_cache_record(r) for r in records]
        parquet_path = write_funding_rate_parquet(
            records=records,
            symbol=self.symbol,
            catalog_root=self._catalog_path,
            storage=self._storage,
        )
        self._pending.extend(cache_records)
        self._written_parquet = True
        logger.info(
            "Wrote %d funding rate records for %s (Parquet primary; JSON pending)",
            len(records),
            self.symbol,
        )
        return parquet_path

    def flush_json(self) -> None:
        """Merge staged records into the JSON cache. Call after a successful DB commit."""
        from tinohelm.data.funding_cache import _load_cache, _save_cache

        if not self._pending:
            self._flushed = True
            return
        by_time: dict[int, dict[str, Any]] = {}
        for row in _load_cache(self.symbol):
            if isinstance(row, dict) and isinstance(row.get("funding_time_ms"), (int, float)):
                by_time[int(row["funding_time_ms"])] = row
        for row in self._pending:
            by_time[int(row["funding_time_ms"])] = row
        _save_cache(self.symbol, [by_time[key] for key in sorted(by_time)])
        self._flushed = True

    def restore(self) -> None:
        """Write the JSON snapshot back, or remove the file if it didn't exist.

        If the snapshot recorded ``existed=True`` but its payload could not be
        captured at snapshot time, leave whatever is currently on disk alone
        rather than overwriting with empty bytes — a best-effort recovery that
        refuses to destroy data it couldn't read.
        """
        snapshot = self._snapshot
        try:
            if snapshot.existed:
                if snapshot.payload is None:
                    logger.warning(
                        "Skipping funding cache restore for %s — snapshot payload was unavailable",
                        snapshot.path,
                    )
                    return
                snapshot.path.parent.mkdir(parents=True, exist_ok=True)
                snapshot.path.write_bytes(snapshot.payload)
            else:
                snapshot.path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to restore funding cache %s", snapshot.path, exc_info=True)


@dataclass(frozen=True)
class ScanEntry:
    """One catalog-row candidate produced by ``session.scan_bars`` / ``scan_ticks``.

    Fields line up with ``DataCatalog`` columns so the route can upsert by
    ``(symbol, data_type, interval, source_type)`` without re-deriving anything.
    ``file_path`` is the canonical catalog root (source-aware when available,
    base path otherwise) so consumers can resolve later parquet reads.
    """

    symbol: str
    data_type: str
    interval: str
    source_type: str
    start_date: Any
    end_date: Any
    size_bytes: int
    record_count: int | None
    file_path: str


@dataclass(frozen=True)
class ScanResult:
    entries: list[ScanEntry]
    scanned: int


@dataclass(frozen=True)
class LiveCatalogSummary:
    entries: list[ScanEntry]
    scanned: int


_BAR_SOURCE_TYPES: tuple[str, ...] = (
    "klines",
    "markPriceKlines",
    "indexPriceKlines",
    "premiumIndexKlines",
)
_TICK_SCAN_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("trade_tick", "trade_tick", ("aggTrades", "trades")),
    ("quote_tick", "quote_tick", ("bookTicker",)),
)
_UPDATE_SCAN_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("mark_price", "mark_price_update", "markPriceKlines", "tick"),
    ("index_price", "index_price_update", "indexPriceKlines", "tick"),
)
# Single-file-per-symbol Parquet categories: one parquet sitting in a shared
# parent dir, named ``{symbol.lower()}.parquet``. Interval is a sentinel
# string because these categories don't have a time-bucket: funding rates
# land on Binance's 8h schedule, metrics / bookDepth are per-tick snapshots.
_SINGLE_FILE_SCAN_SPECS: tuple[tuple[str, str, str], ...] = (
    ("funding_rate", "fundingRate", "8h"),
    ("metrics", "metrics", "tick"),
    ("order_book_delta", "bookDepth", "tick"),
)


class CatalogSession:
    """Entry point for Catalog CRUD (see Issue #156).

    Wraps a ``catalog_path`` plus an optional ``CatalogStorageProvider`` so
    callers do not need to thread both values through every Catalog operation.
    All methods are sync — async/await lives at the route/pipeline layer and
    bridges via :func:`asyncio.to_thread`.
    """

    def __init__(
        self,
        catalog_path: str | Path,
        storage: Any | None = None,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self._storage = storage

    @property
    def storage(self) -> Any:
        """Return the backing storage provider, lazily resolving a local default."""
        if self._storage is None:
            from tinohelm.data.storage import get_catalog_storage

            self._storage = get_catalog_storage(catalog_root=self.catalog_path)
        return self._storage

    def resolve_catalog_path(self, source_type: str | None) -> Path:
        """Resolve a source-aware catalog root (see :func:`resolve_catalog_path`)."""
        return resolve_catalog_path(self.catalog_path, source_type)

    def delete_storage(
        self,
        symbol: str,
        data_type: str,
        interval: str,
        *,
        source_type: str | None = None,
    ) -> tuple[int, int]:
        """Delete storage files for a catalog entry.

        Returns ``(deleted_files, freed_bytes)``. Unknown ``data_type`` values
        return ``(0, 0)`` and emit a warning — the DB row is the caller's
        responsibility to remove regardless.

        Semantics:

        - ``bar`` / ``trade_tick`` / ``quote_tick``: remove every parquet file in
          the resolved directory. When ``source_type`` is the legacy default for
          the category (e.g. ``klines`` for ``bar``), the base path (flat layout)
          is also scanned and removed — this preserves the double-delete behaviour
          introduced when we migrated from flat to source-aware layouts.
        - ``metrics`` / ``order_book_delta``: single parquet file at the canonical
          per-symbol path.
        - ``funding_rate``: both the primary parquet and the read-side JSON cache
          at ``~/.tino/data/funding_rates/{symbol}.json``.
        """
        storage = self.storage
        if data_type == "bar":
            return self._delete_parquet_dirs(
                self._bar_target_dirs(symbol, interval, source_type)
            )
        if data_type == "trade_tick":
            return self._delete_parquet_dirs(
                self._tick_target_dirs(symbol, "trade_tick", source_type)
            )
        if data_type == "quote_tick":
            return self._delete_parquet_dirs(
                self._tick_target_dirs(symbol, "quote_tick", source_type)
            )
        if data_type == "metrics":
            return self._delete_parquet_files([metrics_parquet_path(symbol, self.catalog_path)])
        if data_type == "order_book_delta":
            return self._delete_parquet_files([book_depth_parquet_path(symbol, self.catalog_path)])
        if data_type == "funding_rate":
            return self._delete_funding_rate(symbol)
        logger.warning("No storage handler for data_type=%r, removing DB row only", data_type)
        _ = storage  # touch to keep lazy-init semantics consistent across branches
        return (0, 0)

    def _target_roots(self, category: str, source_type: str | None) -> list[Path]:
        base = self.catalog_path
        if not source_type:
            return [base]
        resolved = self.resolve_catalog_path(source_type)
        if source_type == _LEGACY_DEFAULT_SOURCE.get(category):
            return [resolved, base]
        if resolved == base:
            return []
        return [resolved]

    def _bar_target_dirs(
        self,
        symbol: str,
        interval: str,
        source_type: str | None,
    ) -> list[Path]:
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        dir_name = make_bar_type_str(symbol, interval)
        return [root / "data" / "bar" / dir_name for root in self._target_roots("bar", source_type)]

    def _tick_target_dirs(
        self,
        symbol: str,
        category: str,
        source_type: str | None,
    ) -> list[Path]:
        from tinohelm.strategy.loader_helpers import normalize_symbol

        nt_sym = normalize_symbol(symbol)
        return [root / "data" / category / nt_sym for root in self._target_roots(category, source_type)]

    def _delete_parquet_dirs(self, target_dirs: list[Path]) -> tuple[int, int]:
        from tinohelm.data.storage import delete_prefix, stage_prefix_for_local_consumer

        storage = self.storage
        deleted_files = 0
        freed_bytes = 0
        seen: set[Path] = set()
        for target_dir in target_dirs:
            if target_dir in seen:
                continue
            seen.add(target_dir)
            if getattr(storage, "provider", "local") != "local":
                remote_deleted, remote_freed = delete_prefix(storage, target_dir)
                deleted_files += remote_deleted
                freed_bytes += remote_freed
                continue
            stage_prefix_for_local_consumer(storage, target_dir)
            if not target_dir.exists():
                continue
            files = list(target_dir.glob("*.parquet"))
            freed_bytes += sum(f.stat().st_size for f in files)
            for f in files:
                f.unlink(missing_ok=True)
            if target_dir.exists() and not list(target_dir.iterdir()):
                target_dir.rmdir()
            deleted_files += len(files)
        return deleted_files, freed_bytes

    def _delete_parquet_files(self, target_files: list[Path]) -> tuple[int, int]:
        from tinohelm.data.storage import delete_prefix

        storage = self.storage
        deleted_files = 0
        freed_bytes = 0
        seen: set[Path] = set()
        for target_file in target_files:
            if target_file in seen:
                continue
            seen.add(target_file)
            if getattr(storage, "provider", "local") != "local":
                remote_deleted, remote_freed = delete_prefix(storage, target_file)
                deleted_files += remote_deleted
                freed_bytes += remote_freed
                continue
            storage.materialize_path(target_file)
            if not target_file.exists():
                continue
            size = target_file.stat().st_size
            target_file.unlink()
            deleted_files += 1
            freed_bytes += size
        return deleted_files, freed_bytes

    def parquet_size_for(
        self,
        symbol: str,
        interval: str,
        *,
        source_type: str | None = None,
    ) -> int:
        """Sum on-disk sizes of all bar parquet files for ``(symbol, interval)``.

        Matches the now-retired ``routes.data._parquet_size_for`` — raises
        ``ValueError`` on a malformed interval so callers see the same
        failure mode they used to. Defaults ``source_type`` to the legacy
        bar default (``klines``) so callers that only know the interval
        keep working.
        """
        # Validate interval shape eagerly: ``make_bar_type_str`` falls back to
        # ``1-MINUTE`` on garbage inputs, but the route layer raised here.
        interval_to_nt_suffix(interval)
        effective_source = source_type or _LEGACY_DEFAULT_SOURCE_FOR_BAR
        total = 0
        for obj in self._parquet_objects_for(symbol, "bar", interval, effective_source):
            size = getattr(obj, "size", None)
            if size is not None:
                total += int(size)
                continue
            try:
                total += Path(obj.path).stat().st_size
            except OSError:
                pass
        return total

    def aggregate_parquet_stats(
        self,
        symbol: str,
        data_type: str,
        interval: str,
        *,
        source_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Aggregate row count, date coverage, and size over a catalog item's parquet files.

        Output matches the route-level ``_aggregate_parquet_object_stats``
        exactly, so callers can switch without regenerating snapshots.
        """
        objects = list(self._parquet_objects_for(symbol, data_type, interval, source_type))
        if not objects:
            return None
        return _aggregate_parquet_object_stats(objects, self.storage)

    def merged_bar_stats(
        self,
        symbol: str,
        interval: str,
        *,
        source_type: str,
    ) -> dict[str, Any] | None:
        """Return merged stats across source-aware + legacy flat bar layouts.

        ``_run_compact`` only rewrites one side (whichever ``resolve_bar_catalog_path``
        picked), but the DB row in ``data_catalog`` still describes the union
        of both layouts — so after compacting klines we must re-sum both the
        source-aware root and any legacy flat copy before updating
        ``size_bytes`` / ``record_count``. Only the legacy default source
        (``klines``) has a flat fallback; other sources stay scoped.
        """
        interval_to_nt_suffix(interval)  # eager validation — see parquet_size_for
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        bar_type_dir_name = make_bar_type_str(symbol, interval)
        resolved_root = self.resolve_catalog_path(source_type)

        roots: list[Path] = [resolved_root / "data" / "bar" / bar_type_dir_name]
        if (
            source_type == _LEGACY_DEFAULT_SOURCE_FOR_BAR
            and resolved_root != self.catalog_path
        ):
            roots.append(
                self.catalog_path / "data" / "bar" / bar_type_dir_name
            )

        collected: list[dict[str, Any]] = []
        storage = self.storage
        for root in roots:
            objects = list(storage.iter_files(root, suffix=".parquet", recursive=False))
            if not objects:
                continue
            stats = _aggregate_parquet_object_stats(objects, storage)
            if stats is not None:
                collected.append(stats)

        if not collected:
            return None

        size_bytes = sum(int(s["size_bytes"]) for s in collected)
        all_rows_known = all(s["all_record_counts_known"] for s in collected)
        record_count = (
            sum(int(s["record_count"]) for s in collected) if all_rows_known else None
        )
        start_date = min(s["start_date"] for s in collected)
        end_date = max(s["end_date"] for s in collected)
        return {
            "size_bytes": size_bytes,
            "record_count": record_count,
            "all_record_counts_known": all_rows_known,
            "start_date": start_date,
            "end_date": end_date,
        }

    def scan_bars(self) -> ScanResult:
        """Discover bar parquet files on disk and collapse to catalog entries.

        One entry per ``(symbol, interval, source_type)``. Source-aware and
        legacy flat layouts are merged when both coexist — source-aware wins
        the reported ``file_path`` to steer future reads to the new layout.
        """
        bar_type_pattern = re.compile(r"^(.+\.BINANCE)-(\d+-\w+)-LAST-EXTERNAL$")
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        scanned = 0

        for source_type in _BAR_SOURCE_TYPES:
            resolved_root = self.resolve_catalog_path(source_type)
            bar_root = resolved_root / "data" / "bar"
            is_source_aware = resolved_root != self.catalog_path
            scanned += self._collect_bar_entries_for_root(
                bar_root=bar_root,
                cat_root=resolved_root,
                source_type=source_type,
                is_source_aware=is_source_aware,
                merged=merged,
                pattern=bar_type_pattern,
            )

        legacy_root = self.catalog_path / "data" / "bar"
        already_scanned_legacy = any(
            self.resolve_catalog_path(src) == self.catalog_path
            for src in _BAR_SOURCE_TYPES
        )
        if not already_scanned_legacy:
            scanned += self._collect_bar_entries_for_root(
                bar_root=legacy_root,
                cat_root=self.catalog_path,
                source_type="klines",
                is_source_aware=False,
                merged=merged,
                pattern=bar_type_pattern,
            )

        entries = [self._build_scan_entry(key, stats) for key, stats in merged.items()]
        return ScanResult(entries=entries, scanned=scanned)

    def scan_ticks(self) -> ScanResult:
        """Discover trade_tick / quote_tick parquet files and collapse to entries."""
        sym_pattern = re.compile(r"^(.+)\.BINANCE$")
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        scanned = 0

        for data_type, dir_name, source_types in _TICK_SCAN_SPECS:
            for source_type in source_types:
                resolved_root = self.resolve_catalog_path(source_type)
                tick_root = resolved_root / "data" / dir_name
                is_source_aware = resolved_root != self.catalog_path
                scanned += self._collect_tick_entries_for_root(
                    tick_root=tick_root,
                    cat_root=resolved_root,
                    data_type=data_type,
                    source_type=source_type,
                    is_source_aware=is_source_aware,
                    merged=merged,
                    pattern=sym_pattern,
                )
            legacy_root = self.catalog_path / "data" / dir_name
            legacy_already_scanned = any(
                self.resolve_catalog_path(src) == self.catalog_path
                for src in source_types
            )
            if not legacy_already_scanned:
                scanned += self._collect_tick_entries_for_root(
                    tick_root=legacy_root,
                    cat_root=self.catalog_path,
                    data_type=data_type,
                    source_type=source_types[0],
                    is_source_aware=False,
                    merged=merged,
                    pattern=sym_pattern,
                )

        entries = [self._build_scan_entry(key, stats) for key, stats in merged.items()]
        return ScanResult(entries=entries, scanned=scanned)

    def scan_single_files(self) -> ScanResult:
        """Discover per-symbol Parquet files for single-file categories.

        Covers ``funding_rate`` / ``metrics`` / ``order_book_delta`` — each
        lives at a canonical path keyed by ``symbol.lower()``. These were
        silently missed by the pre-PR2 route scan; lifting the discovery into
        the session closes that gap without re-scattering data-type knowledge
        across call sites.
        """
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        scanned = 0
        for data_type, source_type, interval in _SINGLE_FILE_SCAN_SPECS:
            parent_dir = self._single_file_parent_dir(data_type)
            if parent_dir is None:
                continue
            storage = self.storage
            for obj in storage.iter_files(parent_dir, suffix=".parquet", recursive=False):
                symbol = self._symbol_from_single_file(obj.path)
                if symbol is None:
                    continue
                stats = _aggregate_parquet_object_stats([obj], storage)
                if stats is None:
                    continue
                scanned += 1
                self._merge_scan_stats(
                    merged,
                    key=(symbol, data_type, interval, source_type),
                    stats=stats,
                    cat_root=self.catalog_path,
                    is_source_aware=False,
                )
        entries = [self._build_scan_entry(key, stats) for key, stats in merged.items()]
        return ScanResult(entries=entries, scanned=scanned)

    def live_summary(self) -> LiveCatalogSummary:
        """Return a live catalog summary derived from current NT catalog files."""
        bar_result = self.scan_bars()
        tick_result = self.scan_ticks()
        single_file_result = self.scan_single_files()
        funding_result = self.scan_funding_rate_updates()
        direct_update_result = self.scan_direct_updates()

        merged_entries: dict[tuple[str, str, str, str], ScanEntry] = {}
        for entry in [
            *bar_result.entries,
            *tick_result.entries,
            *single_file_result.entries,
            *funding_result.entries,
            *direct_update_result.entries,
        ]:
            merged_entries[(entry.symbol, entry.data_type, entry.interval, entry.source_type)] = entry

        return LiveCatalogSummary(
            entries=list(merged_entries.values()),
            scanned=(
                bar_result.scanned
                + tick_result.scanned
                + single_file_result.scanned
                + funding_result.scanned
                + direct_update_result.scanned
            ),
        )

    def _single_file_parent_dir(self, data_type: str) -> Path | None:
        """Return the directory that holds one parquet per symbol for ``data_type``."""
        sentinel = "scan"
        if data_type == "funding_rate":
            return funding_rate_parquet_path(sentinel, self.catalog_path).parent
        if data_type == "metrics":
            return metrics_parquet_path(sentinel, self.catalog_path).parent
        if data_type == "order_book_delta":
            return book_depth_parquet_path(sentinel, self.catalog_path).parent
        return None

    @staticmethod
    def _symbol_from_single_file(path: Path) -> str | None:
        """Recover ``symbol`` from a ``{symbol.lower()}.parquet`` filename."""
        name = path.stem
        if not name:
            return None
        return name.upper()

    def scan_funding_rate_updates(self) -> ScanResult:
        """Discover NT-native FundingRateUpdate parquet files and collapse to entries."""
        sym_pattern = re.compile(r"^(.+)\.BINANCE$")
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        scanned = self._collect_tick_entries_for_root(
            tick_root=self.catalog_path / "data" / "funding_rate_update",
            cat_root=self.catalog_path,
            data_type="funding_rate",
            source_type="fundingRate",
            interval="8h",
            is_source_aware=False,
            merged=merged,
            pattern=sym_pattern,
        )
        entries = [self._build_scan_entry(key, stats) for key, stats in merged.items()]
        return ScanResult(entries=entries, scanned=scanned)

    def scan_direct_updates(self) -> ScanResult:
        """Discover NT-native direct-update parquet files for auxiliary prices."""
        sym_pattern = re.compile(r"^(.+)\.BINANCE$")
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        scanned = 0
        for data_type, dir_name, source_type, interval in _UPDATE_SCAN_SPECS:
            scanned += self._collect_tick_entries_for_root(
                tick_root=self.catalog_path / "data" / dir_name,
                cat_root=self.catalog_path,
                data_type=data_type,
                source_type=source_type,
                interval=interval,
                is_source_aware=False,
                merged=merged,
                pattern=sym_pattern,
            )
        entries = [self._build_scan_entry(key, stats) for key, stats in merged.items()]
        return ScanResult(entries=entries, scanned=scanned)

    def _collect_bar_entries_for_root(
        self,
        *,
        bar_root: Path,
        cat_root: Path,
        source_type: str,
        is_source_aware: bool,
        merged: dict[tuple[str, str, str, str], dict[str, Any]],
        pattern: "re.Pattern[str]",
    ) -> int:
        grouped = self._group_parquet_objects_by_child_dir(bar_root)
        scanned_here = 0
        for entry_dir, parquet_objects in sorted(
            grouped.items(), key=lambda item: item[0].name
        ):
            match = pattern.match(entry_dir.name)
            if not match:
                continue
            interval = nt_suffix_to_interval(match.group(2))
            if interval is None:
                logger.warning(
                    "Scan: unknown interval %s in %s, skipping",
                    match.group(2),
                    entry_dir.name,
                )
                continue
            symbol = match.group(1).removesuffix(".BINANCE")
            stats = _aggregate_parquet_object_stats(parquet_objects, self.storage)
            if stats is None:
                logger.warning(
                    "Scan: no timestamp range readable for %s, skipping", entry_dir.name
                )
                continue
            scanned_here += 1
            self._merge_scan_stats(
                merged,
                key=(symbol, "bar", interval, source_type),
                stats=stats,
                cat_root=cat_root,
                is_source_aware=is_source_aware,
            )
            logger.info(
                "Scan: %s %s %s [%s..%s] %d bytes",
                source_type,
                symbol,
                interval,
                stats["start_date"],
                stats["end_date"],
                stats["size_bytes"],
            )
        return scanned_here

    def _collect_tick_entries_for_root(
        self,
        *,
        tick_root: Path,
        cat_root: Path,
        data_type: str,
        source_type: str,
        interval: str = "tick",
        is_source_aware: bool,
        merged: dict[tuple[str, str, str, str], dict[str, Any]],
        pattern: "re.Pattern[str]",
    ) -> int:
        grouped = self._group_parquet_objects_by_child_dir(tick_root)
        scanned_here = 0
        for entry_dir, parquet_objects in sorted(
            grouped.items(), key=lambda item: item[0].name
        ):
            match = pattern.match(entry_dir.name)
            if not match:
                continue
            symbol = match.group(1)
            stats = _aggregate_parquet_object_stats(parquet_objects, self.storage)
            if stats is None:
                continue
            scanned_here += 1
            self._merge_scan_stats(
                merged,
                key=(symbol, data_type, interval, source_type),
                stats=stats,
                cat_root=cat_root,
                is_source_aware=is_source_aware,
            )
        return scanned_here

    @staticmethod
    def _merge_scan_stats(
        merged: dict[tuple[str, str, str, str], dict[str, Any]],
        *,
        key: tuple[str, str, str, str],
        stats: dict[str, Any],
        cat_root: Path,
        is_source_aware: bool,
    ) -> None:
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "start_date": stats["start_date"],
                "end_date": stats["end_date"],
                "size_bytes": int(stats["size_bytes"]),
                "record_count": stats["record_count"],
                "all_record_counts_known": stats["record_count"] is not None,
                "file_path": str(cat_root),
                "has_source_aware": is_source_aware,
            }
            return
        existing["start_date"] = min(existing["start_date"], stats["start_date"])
        existing["end_date"] = max(existing["end_date"], stats["end_date"])
        existing["size_bytes"] += int(stats["size_bytes"])
        rc = stats["record_count"]
        if rc is None:
            existing["all_record_counts_known"] = False
            existing["record_count"] = None
        elif existing["all_record_counts_known"]:
            existing["record_count"] = (existing["record_count"] or 0) + int(rc)
        if is_source_aware and not existing["has_source_aware"]:
            existing["file_path"] = str(cat_root)
            existing["has_source_aware"] = True

    @staticmethod
    def _build_scan_entry(
        key: tuple[str, str, str, str],
        stats: dict[str, Any],
    ) -> ScanEntry:
        symbol, data_type, interval, source_type = key
        return ScanEntry(
            symbol=symbol,
            data_type=data_type,
            interval=interval,
            source_type=source_type,
            start_date=stats["start_date"],
            end_date=stats["end_date"],
            size_bytes=int(stats["size_bytes"]),
            record_count=stats["record_count"],
            file_path=stats["file_path"],
        )

    def _group_parquet_objects_by_child_dir(
        self, root: Path
    ) -> dict[Path, list]:
        """Group parquet objects under ``root/<child>/*.parquet`` by child dir.

        Mirrors the route-level helper removed in PR2 — placed here so scan
        logic stays behind a single boundary, not split between session + route.
        """
        groups: dict[Path, list] = {}
        storage = self.storage
        for obj in storage.iter_files(root, suffix=".parquet", recursive=True):
            try:
                rel = obj.path.relative_to(root)
            except ValueError:
                continue
            if len(rel.parts) < 2:
                continue
            groups.setdefault(root / rel.parts[0], []).append(obj)
        return groups

    def _parquet_objects_for(
        self,
        symbol: str,
        data_type: str,
        interval: str,
        source_type: str | None,
    ):
        """Yield storage objects for one catalog row's parquet files.

        For directory-backed categories (bar/trade_tick/quote_tick) this walks
        the target dir. For single-file categories (metrics / order_book_delta /
        funding_rate) the target is already a concrete parquet path shared
        between symbols in its parent dir, so we only return that one object.
        """
        storage = self.storage
        target = self._parquet_dir_for(symbol, data_type, interval, source_type)
        if target is None:
            return []
        if str(target).endswith(".parquet"):
            try:
                if not storage.exists(target):
                    return []
            except Exception:
                return []
            size = None
            if getattr(storage, "provider", "local") == "local":
                try:
                    size = Path(target).stat().st_size
                except OSError:
                    size = None
            from tinohelm.data.storage import StorageObject

            return [StorageObject(key=str(target), path=Path(target), size=size)]
        recursive = data_type == "funding_rate"
        return list(storage.iter_files(target, suffix=".parquet", recursive=recursive))

    def _parquet_dir_for(
        self,
        symbol: str,
        data_type: str,
        interval: str,
        source_type: str | None,
    ) -> Path | None:
        """Return the directory (dir-backed) OR concrete file (single-file) path.

        Single-file categories return the parquet path itself so callers can
        scope stats/iteration to a specific symbol rather than bleeding across
        every symbol that shares the parent directory.
        """
        from tinohelm.strategy.loader_helpers import make_bar_type_str, normalize_symbol

        if data_type == "bar":
            resolved = self.resolve_bar_catalog_path(source_type or _LEGACY_DEFAULT_SOURCE_FOR_BAR, symbol, interval)
            return resolved / "data" / "bar" / make_bar_type_str(symbol, interval)
        if data_type in {"trade_tick", "quote_tick"}:
            resolved = self.resolve_catalog_path(source_type)
            return resolved / "data" / data_type / normalize_symbol(symbol)
        if data_type == "metrics":
            return metrics_parquet_path(symbol, self.catalog_path)
        if data_type == "order_book_delta":
            return book_depth_parquet_path(symbol, self.catalog_path)
        if data_type == "funding_rate":
            update_dir = funding_rate_update_dir(symbol, self.catalog_path)
            if _iter_catalog_files(self.storage, update_dir, recursive=True):
                return update_dir
            return funding_rate_parquet_path(symbol, self.catalog_path)
        return None

    @contextmanager
    def funding_rate_transaction(self, symbol: str) -> Iterator[FundingRateTxn]:
        """Yield a :class:`FundingRateTxn` for ``symbol``.

        Behaviour:

        * The JSON snapshot is captured on entry.
        * Exit with an exception restores the snapshot and re-raises.
        * Normal exit without ``flush_json`` logs a warning — the Parquet is
          already written and the JSON is stale; callers must flush after
          their own DB commit or skip writing entirely.
        """
        snapshot = self._take_funding_snapshot(symbol)
        # Resolve lazily here so remote-configured sessions built without an
        # explicit storage argument still route the parquet write through
        # their backend, not a silent ``None`` that defaults to local.
        txn = FundingRateTxn(
            catalog_path=self.catalog_path,
            symbol=symbol,
            snapshot=snapshot,
            storage=self.storage,
        )
        try:
            yield txn
        except BaseException:
            txn.restore()
            raise
        else:
            if txn.wrote_parquet and not txn.flushed:
                logger.warning(
                    "FundingRateTxn for %s left the JSON cache unflushed — "
                    "Parquet was written but legacy JSON readers will be stale",
                    symbol,
                )

    def create_funding_txn(self, symbol: str) -> FundingRateTxn:
        """Create an unmanaged :class:`FundingRateTxn` for callers that need
        to control flush/restore timing across async boundaries (e.g. pipeline
        ingest where DB commit happens between write and flush).

        The caller is responsible for calling ``txn.flush_json()`` on success
        and ``txn.restore()`` on failure.
        """
        snapshot = self._take_funding_snapshot(symbol)
        return FundingRateTxn(
            catalog_path=self.catalog_path,
            symbol=symbol,
            snapshot=snapshot,
            storage=self.storage,
        )

    @staticmethod
    def _take_funding_snapshot(symbol: str) -> _FundingCacheSnapshot:
        from tinohelm.core.paths import paths

        path = paths.get("funding_rates") / f"{symbol.lower()}.json"
        if not path.exists():
            return _FundingCacheSnapshot(path=path, existed=False, payload=None)
        # Preserve ``existed=True`` even if the body cannot be read: rollback
        # must NOT unlink a real file it simply failed to snapshot. Leaving
        # ``payload=None`` means restore will rewrite the file with an empty
        # body, which matches the fail-safe bias (a best-effort recovery
        # beats silent data loss).
        try:
            payload = path.read_bytes()
        except OSError:
            logger.warning("Failed to read funding cache %s; snapshot payload unavailable", path, exc_info=True)
            return _FundingCacheSnapshot(path=path, existed=True, payload=None)
        return _FundingCacheSnapshot(path=path, existed=True, payload=payload)

    def load_funding_rates(
        self,
        symbol: str,
        start,
        end,
    ) -> list[dict[str, Any]]:
        """Return cached funding-rate records intersecting ``[start, end]``.

        Thin delegate over :func:`tinohelm.data.funding_cache.load_funding_rates`;
        exists so callers never need to know whether the read-side is JSON or
        Parquet.
        """
        from tinohelm.data.funding_cache import load_funding_rates

        return load_funding_rates(symbol, start, end)

    def funding_cache_covers(self, symbol: str, start, end) -> bool:
        """Return ``True`` iff the JSON funding cache fully spans ``[start, end]``."""
        from datetime import datetime as _dt, time as _time, timezone as _tz

        from tinohelm.data.funding_cache import _load_cache
        from tinohelm.data.funding_cache_helpers import compute_fetch_start

        cached = _load_cache(symbol)
        cached_times = [
            int(r["funding_time_ms"])
            for r in cached
            if isinstance(r, dict) and isinstance(r.get("funding_time_ms"), (int, float))
        ]
        if not cached_times:
            return False
        start_dt = _dt.combine(start, _time.min, tzinfo=_tz.utc)
        end_dt = _dt.combine(end, _time.max, tzinfo=_tz.utc)
        return compute_fetch_start(cached_times, start=start_dt, end=end_dt) is None

    def funding_rate_parquet_path(self, symbol: str) -> Path:
        """Return the primary funding-rate Parquet path for ``symbol``."""
        return funding_rate_parquet_path(symbol, self.catalog_path)

    def funding_parquet_covers(self, symbol: str, start, end) -> bool:
        """Return ``True`` iff the primary funding-rate Parquet spans ``[start, end]``."""
        from datetime import UTC, datetime

        target_dir = funding_rate_update_dir(symbol, self.catalog_path)
        storage = self.storage
        try:
            objects = list(storage.iter_files(target_dir, suffix=".parquet", recursive=True))
        except Exception:
            logger.warning(
                "Funding-rate update parquet is not readable for %s", symbol, exc_info=True
            )
            return False
        if objects:
            min_ts: int | None = None
            max_ts: int | None = None
            for obj in objects:
                try:
                    time_range = _funding_parquet_time_range(obj.path, storage)
                except Exception:
                    logger.warning(
                        "Funding-rate update parquet is not readable for %s", symbol, exc_info=True
                    )
                    return False
                if time_range is None:
                    return False
                obj_min, obj_max = time_range
                min_ts = obj_min if min_ts is None else min(min_ts, obj_min)
                max_ts = obj_max if max_ts is None else max(max_ts, obj_max)
            if min_ts is None or max_ts is None:
                return False
        else:
            path = funding_rate_parquet_path(symbol, self.catalog_path)
            try:
                if not storage.exists(path):
                    return False
            except Exception:
                logger.warning(
                    "Funding-rate primary parquet is not readable for %s", symbol, exc_info=True
                )
                return False
            try:
                time_range = _funding_parquet_time_range(path, storage)
            except Exception:
                logger.warning(
                    "Funding-rate primary parquet is not readable for %s", symbol, exc_info=True
                )
                return False
            if time_range is None:
                return False
            min_ts, max_ts = time_range
        min_date = datetime.fromtimestamp(min_ts // 1_000_000_000, UTC).date()
        max_date = datetime.fromtimestamp(max_ts // 1_000_000_000, UTC).date()
        return min_date <= start and max_date >= end

    def compact_bars(self, symbol: str, interval: str) -> dict:
        """Compact multiple bar parquet files into one for ``(symbol, interval)``.

        Local and remote providers return the same dict shape:
        ``{files_before, files_after, bars_count, size_before, size_after}``.
        """
        storage = self.storage
        if getattr(storage, "provider", "local") == "local":
            return compact_bars(
                symbol=symbol, interval=interval, catalog_path=self.catalog_path
            )
        return self._compact_bars_remote(symbol, interval)

    def _compact_bars_remote(self, symbol: str, interval: str) -> dict:
        """Remote storage compaction: read bars → dedupe → write to temp → promote."""
        from tinohelm.data.catalog_helpers import dedupe_by_ts
        from tinohelm.data.storage import delete_prefix, promote_objects_with_rollback

        storage = self.storage
        instrument = _make_instrument(symbol)
        bar_type = _make_bar_type(instrument.id, interval)
        bar_dir = self.catalog_path / "data" / "bar" / str(bar_type)
        existing_objects = list(
            storage.iter_files(bar_dir, suffix=".parquet", recursive=False)
        )
        files_before = len(existing_objects)
        size_before = sum(int(obj.size or 0) for obj in existing_objects)

        if files_before <= 1:
            return {
                "files_before": files_before,
                "files_after": files_before,
                "bars_count": 0,
                "size_before": size_before,
                "size_after": size_before,
            }

        catalog = _catalog_for_root(self.catalog_path, storage)
        bars = catalog.bars(bar_types=[str(bar_type)])
        if not bars:
            return {
                "files_before": files_before,
                "files_after": files_before,
                "bars_count": 0,
                "size_before": size_before,
                "size_after": size_before,
            }

        bars = dedupe_by_ts(bars)
        logger.info(
            "Compacting remote %s %s: %d files -> %d bars",
            symbol, interval, files_before, len(bars),
        )

        temp_catalog_path = self.catalog_path / ".compaction" / f"{bar_type}-{uuid4().hex}"
        rollback_prefix = self.catalog_path / ".compaction-rollback" / f"{bar_type}-{uuid4().hex}"
        try:
            temp_catalog = _catalog_for_root(temp_catalog_path, storage)
            temp_catalog.write_data([instrument])
            temp_catalog.write_data(bars)

            temp_bar_dir = temp_catalog_path / "data" / "bar" / str(bar_type)
            temp_objects = list(
                storage.iter_files(temp_bar_dir, suffix=".parquet", recursive=False)
            )
            if not temp_objects:
                raise RuntimeError(
                    f"Remote compaction produced no parquet files for {symbol} {interval}"
                )

            promote_objects_with_rollback(
                storage,
                temp_objects,
                bar_dir,
                existing_objects,
                rollback_prefix=rollback_prefix,
            )
        finally:
            try:
                delete_prefix(storage, temp_catalog_path)
            except Exception:
                logger.warning(
                    "Failed to clean temporary remote compaction prefix %s",
                    temp_catalog_path,
                    exc_info=True,
                )

        new_objects = list(
            storage.iter_files(bar_dir, suffix=".parquet", recursive=False)
        )
        if not new_objects:
            raise RuntimeError(
                f"Remote compaction produced no parquet files for {symbol} {interval}"
            )
        size_after = sum(int(obj.size or 0) for obj in new_objects)

        return {
            "files_before": files_before,
            "files_after": len(new_objects),
            "bars_count": len(bars),
            "size_before": size_before,
            "size_after": size_after,
        }

    def _delete_funding_rate(self, symbol: str) -> tuple[int, int]:
        from tinohelm.core.paths import paths

        deleted_files, freed_bytes = self._delete_parquet_dirs(
            [funding_rate_update_dir(symbol, self.catalog_path)]
        )
        legacy_deleted, legacy_freed = self._delete_parquet_files(
            [funding_rate_parquet_path(symbol, self.catalog_path)]
        )
        deleted_files += legacy_deleted
        freed_bytes += legacy_freed
        json_path = paths.get("funding_rates") / f"{symbol.lower()}.json"
        if json_path.exists():
            freed_bytes += json_path.stat().st_size
            json_path.unlink()
            deleted_files += 1
        return deleted_files, freed_bytes

    def resolve_bar_catalog_path(
        self,
        source_type: str,
        symbol: str,
        interval: str,
    ) -> Path:
        """Resolve the bar catalog root, falling back to legacy flat-layout files.

        Before source-aware layouts existed, bars were written directly under
        ``{base}/data/bar/<bar_type>/``. The migration to ``{base}/bar/<source_type>``
        kept the old files readable by falling back to the base path only when:
        (a) the requested source is the legacy default for bars (``klines``); and
        (b) the new layout contains no parquet files but the legacy layout does.
        """
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        resolved = self.resolve_catalog_path(source_type)
        if source_type != _LEGACY_DEFAULT_SOURCE_FOR_BAR:
            return resolved
        bar_type_dir_name = make_bar_type_str(symbol, interval)
        new_layout_dir = resolved / "data" / "bar" / bar_type_dir_name
        legacy_layout_dir = self.catalog_path / "data" / "bar" / bar_type_dir_name
        new_has_files = bool(_iter_catalog_files(self.storage, new_layout_dir, recursive=False))
        if new_has_files:
            return resolved
        legacy_has_files = bool(_iter_catalog_files(self.storage, legacy_layout_dir, recursive=False))
        if legacy_has_files:
            return self.catalog_path
        return resolved


def _is_remote_storage(storage: Any | None) -> bool:
    return storage is not None and getattr(storage, "provider", "local") != "local"


def _remote_catalog_constructor_args(catalog_root: Path, storage: Any) -> tuple[str, str]:
    """Return ``(path, protocol)`` for NT's constructor without URI-parsed host leakage."""
    uri_for_root = getattr(storage, "uri_for_catalog_root", None)
    if not callable(uri_for_root):
        raise ValueError("Remote catalog storage must expose uri_for_catalog_root()")
    uri = str(uri_for_root(catalog_root))
    parsed = urlparse(uri)
    if not parsed.scheme:
        return uri, getattr(storage, "fs_protocol", "file")
    if parsed.scheme == "file":
        return parsed.path, "file"
    return f"{parsed.netloc}{parsed.path}".lstrip("/"), parsed.scheme


_NT_UPDATE_QUERY_SUPPORT_READY = False


def _ensure_nt_update_query_support() -> None:
    """Patch NT 1.225.0 to read IndexPriceUpdate via PyArrow decoder.

    NT can write IndexPriceUpdate parquet, but its default query path routes the
    type through the PyArrow deserializer without a registered decoder. We add
    the missing decoder once per process so catalog.query(IndexPriceUpdate, ...)
    works like MarkPriceUpdate/FundingRateUpdate.
    """
    global _NT_UPDATE_QUERY_SUPPORT_READY

    if _NT_UPDATE_QUERY_SUPPORT_READY:
        return

    try:
        from nautilus_trader.model.data import IndexPriceUpdate
        from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer, register_arrow
        from nautilus_trader.serialization.arrow.schema import NAUTILUS_ARROW_SCHEMA
    except Exception:
        return

    register_arrow(
        data_cls=IndexPriceUpdate,
        schema=NAUTILUS_ARROW_SCHEMA[IndexPriceUpdate],
        decoder=make_dict_deserializer(IndexPriceUpdate),
    )
    _NT_UPDATE_QUERY_SUPPORT_READY = True


def _catalog_for_root(catalog_root: str | Path, storage: Any | None = None):
    """Create an NT catalog for a logical root, using object storage directly when active."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    _ensure_nt_update_query_support()
    root = Path(catalog_root)
    if _is_remote_storage(storage):
        remote_path, fs_protocol = _remote_catalog_constructor_args(root, storage)
        return ParquetDataCatalog(
            remote_path,
            fs_protocol=fs_protocol,
            fs_storage_options=getattr(storage, "fs_storage_options", None),
            fs_rust_storage_options=getattr(storage, "fs_rust_storage_options", None),
        )
    return ParquetDataCatalog(str(root))


def _iter_catalog_files(storage: Any | None, path: Path, *, suffix: str = ".parquet", recursive: bool = False) -> list[Path]:
    if _is_remote_storage(storage):
        return [obj.path for obj in storage.iter_files(path, suffix=suffix, recursive=recursive)]
    if path.is_file():
        return [path] if path.name.endswith(suffix) else []
    return list(path.rglob(f"*{suffix}") if recursive else path.glob(f"*{suffix}")) if path.exists() else []


def _catalog_write_lock_path(out_path: Path, storage: Any | None = None) -> Path:
    if _is_remote_storage(storage):
        import hashlib
        import tempfile

        lock_root = Path(tempfile.gettempdir()) / "tinohelm-remote-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(out_path).encode("utf-8")).hexdigest()
        return lock_root / f"{digest}.lock"
    return out_path.with_suffix(out_path.suffix + ".lock")


def validate_bars(
    symbol: str,
    interval: str,
    catalog_path: str | Path,
    *,
    storage=None,
) -> dict:
    """Validate data integrity for a symbol/interval.

    Returns a dict with:
    - total_bars: int - total number of bars
    - date_range: {start: str, end: str} - actual date range in data
    - duplicates: int - number of duplicate timestamps found
    - gaps: list[{start: str, end: str, missing_bars: int}] - missing time periods
    - file_count: int - number of parquet files
    - size_bytes: int - total size on disk
    - status: "ok" | "warnings" | "errors"
    - issues: list[str] - human-readable issue descriptions
    """
    # Validates the interval and reuses the same error message shape the
    # helper exposes to every caller that does interval-string lookup.
    expected_step_ns = interval_to_nanoseconds(interval)

    catalog_path = Path(catalog_path)
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    # Count files and size in the active backing store.
    storage_provider = storage
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    if storage_provider is not None:
        parquet_files = list(storage_provider.iter_files(bar_dir, suffix=".parquet", recursive=False))
    else:
        parquet_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    file_count = len(parquet_files)
    size_bytes = sum(
        int(obj.size) if getattr(obj, "size", None) is not None else Path(obj.path if hasattr(obj, "path") else obj).stat().st_size
        for obj in parquet_files
    )

    # Read bars from catalog
    catalog = _catalog_for_root(catalog_path, storage_provider)
    try:
        bars = catalog.bars(bar_types=[str(bar_type)])
    except Exception as exc:
        return {
            "total_bars": 0,
            "date_range": {"start": "", "end": ""},
            "duplicates": 0,
            "gaps": [],
            "file_count": file_count,
            "size_bytes": size_bytes,
            "status": "errors",
            "read_error": True,
            "issues": [f"Failed to read bars from catalog: {exc}"],
        }

    if not bars:
        return {
            "total_bars": 0,
            "date_range": {"start": "", "end": ""},
            "duplicates": 0,
            "gaps": [],
            "file_count": file_count,
            "size_bytes": size_bytes,
            "status": "errors",
            "issues": ["No bars found in catalog"],
        }

    # Extract and sort timestamps (nanoseconds since epoch)
    timestamps = sorted(b.ts_event for b in bars)
    total_bars = len(timestamps)

    date_range = {"start": ns_to_iso(timestamps[0]), "end": ns_to_iso(timestamps[-1])}

    duplicates = count_duplicates(timestamps)
    sorted_unique = sorted(set(timestamps))
    gaps = find_gaps(sorted_unique, expected_step_ns)

    # OHLC relationship + volume + price-jump scan (single pass)
    ohlc_violations = 0
    zero_volume_bars = 0
    jump_threshold = 0.10  # 10% price change between consecutive bars

    closes_with_ts: list[tuple[int, float]] = []
    for bar in bars:
        o = float(bar.open)
        h = float(bar.high)
        low = float(bar.low)
        c = float(bar.close)
        v = float(bar.volume)

        if not is_ohlc_valid(o, h, low, c):
            ohlc_violations += 1
        if v == 0:
            zero_volume_bars += 1
        closes_with_ts.append((bar.ts_event, c))

    price_jumps = detect_price_jumps(closes_with_ts, threshold=jump_threshold)

    issues = build_validation_issues(
        duplicates=duplicates,
        gaps=gaps,
        ohlc_violations=ohlc_violations,
        zero_volume_bars=zero_volume_bars,
        price_jumps=price_jumps,
        jump_threshold=jump_threshold,
    )
    status = classify_status(
        has_errors=bool(gaps) or ohlc_violations > 0,
        has_warnings=duplicates > 0 or zero_volume_bars > 0 or bool(price_jumps),
    )

    return {
        "total_bars": total_bars,
        "date_range": date_range,
        "duplicates": duplicates,
        "gaps": gaps,
        "ohlc_violations": ohlc_violations,
        "zero_volume_bars": zero_volume_bars,
        "price_jumps": price_jumps[:20],  # cap to avoid huge output
        "file_count": file_count,
        "size_bytes": size_bytes,
        "status": status,
        "issues": issues,
    }


def write_bars(
    bars: list,
    symbol: str,
    interval: str,
    catalog_path: str | Path,
    merge: bool = True,
    source_type: str | None = None,
    storage: Any | None = None,
) -> list[Path]:
    """Write pre-converted NT Bar objects to Parquet catalog.

    When *merge* is True (default), reads existing bars, deduplicates,
    deletes old parquet files, then writes everything in one write_data()
    call — satisfying NT's disjoint-interval constraint.

    When *merge* is False (streaming mode), writes directly without
    reading existing data.  Caller must clean old files beforehand.

    Returns list of written file paths.
    """
    if not bars:
        return []

    catalog_path = Path(resolve_catalog_path(catalog_path, source_type))
    if not _is_remote_storage(storage):
        catalog_path = ensure_catalog_dirs(catalog_path)
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)

    existing_files_to_delete: list[Path] = []
    existing_objects_to_delete: list[Any] = []
    merged_existing_bars = False
    if merge:
        # Merge with existing bars if present (incremental update case)
        try:
            if _is_remote_storage(storage):
                existing_objects_to_delete = list(storage.iter_files(bar_dir, suffix=".parquet", recursive=False))
                existing_files_to_delete = [obj.path for obj in existing_objects_to_delete]
                existing_bars = []
                if existing_objects_to_delete:
                    catalog = _catalog_for_root(catalog_path, storage)
                    existing_bars = catalog.bars(bar_types=[str(bar_type)])
                    if not existing_bars:
                        raise RuntimeError(
                            f"Remote catalog has parquet objects but no readable bars for {symbol} {interval}"
                        )
            else:
                catalog = _catalog_for_root(catalog_path, storage)
                existing_bars = catalog.bars(bar_types=[str(bar_type)])
            if existing_bars:
                # Track old files — they will be deleted AFTER the merged write
                # succeeds, so a crash between write and delete leaves the old
                # data intact (at worst we have duplicate bars, not missing ones).
                if not _is_remote_storage(storage):
                    existing_files_to_delete = _iter_catalog_files(storage, bar_dir, recursive=False)

                existing_count = len(existing_bars)
                bars = merge_bars(existing_bars, bars)
                merged_existing_bars = True
                logger.info("Merged %d existing + %d new bars = %d total",
                            existing_count, len(bars) - existing_count, len(bars))
        except Exception:
            if _is_remote_storage(storage):
                raise
            logger.warning("Failed to read existing bars for %s %s, writing fresh", symbol, interval, exc_info=True)

    if _is_remote_storage(storage) and merged_existing_bars:
        from uuid import uuid4

        from tinohelm.data.storage import delete_prefix, promote_objects_with_rollback

        catalog = _catalog_for_root(catalog_path, storage)
        catalog.write_data([instrument])

        temp_catalog_path = catalog_path / ".merge" / f"{bar_type}-{uuid4().hex}"
        rollback_prefix = catalog_path / ".merge-rollback" / f"{bar_type}-{uuid4().hex}"
        try:
            temp_catalog = _catalog_for_root(temp_catalog_path, storage)
            temp_catalog.write_data([instrument])
            temp_catalog.write_data(bars, skip_disjoint_check=True)
            temp_bar_dir = temp_catalog_path / "data" / "bar" / str(bar_type)
            temp_objects = list(storage.iter_files(temp_bar_dir, suffix=".parquet", recursive=False))
            if not temp_objects:
                raise RuntimeError(f"Merged write produced no parquet files for {symbol} {interval}")

            promote_objects_with_rollback(
                storage,
                temp_objects,
                bar_dir,
                existing_objects_to_delete,
                rollback_prefix=rollback_prefix,
            )
        finally:
            try:
                delete_prefix(storage, temp_catalog_path)
            except Exception:
                logger.warning("Failed to clean temporary remote merge prefix %s", temp_catalog_path, exc_info=True)

        written_files = _iter_catalog_files(storage, bar_dir, recursive=False)
        if not written_files:
            raise RuntimeError(f"Merged write produced no parquet files for {symbol} {interval}")
        logger.info("Wrote %d bars to remote catalog at %s", len(bars), catalog_path)
        return written_files

    catalog = _catalog_for_root(catalog_path, storage)
    catalog.write_data([instrument])
    # When merge=False (streaming mode), multiple files accumulate in the same
    # directory within one ingest session.  NT uses P.closed() intervals so two
    # files sharing a boundary nanosecond are treated as overlapping.  TinoHelm
    # manages file lifecycle via _clean_overlapping_parquet, so it is safe to
    # skip the disjoint check for append writes.
    # When merge=True, skip_disjoint_check=True so the write succeeds even
    # while old files still exist on disk (they are deleted afterwards).
    catalog.write_data(bars, skip_disjoint_check=True)
    logger.info("Wrote %d bars to catalog at %s", len(bars), catalog_path)

    current_files = set(_iter_catalog_files(storage, bar_dir, recursive=False))
    if not current_files:
        raise RuntimeError(f"Merged write produced no parquet files for {symbol} {interval}")

    # Delete old parquet files AFTER the merged write has succeeded.  Skip any
    # same-name output path; deleting by path after a rewrite can remove the
    # freshly written parquet.
    for old_file in existing_files_to_delete:
        if old_file in current_files:
            continue
        if _is_remote_storage(storage):
            storage.delete_path(old_file)
        elif old_file.exists():
            old_file.unlink()

    return _iter_catalog_files(storage, bar_dir, recursive=False)


def compact_bars(symbol: str, interval: str, catalog_path: str | Path) -> dict:
    """Compact multiple Parquet files for a symbol/interval into a single file.

    Reads all bars, deduplicates by ts_event (keeping last), then stages the
    compacted catalog under a temporary prefix and promotes the new files only
    after the write succeeds.  That keeps the old catalog intact if anything
    fails before promotion.

    Returns summary dict with files_before, files_after, bars_count,
    size_before, size_after.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from uuid import uuid4
    import shutil

    catalog_path = Path(catalog_path)

    # 1. Create instrument and bar_type using existing helpers
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)

    # 2. Count files before compaction
    existing_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    files_before = len(existing_files)
    size_before = sum(f.stat().st_size for f in existing_files)

    if files_before <= 1:
        logger.info("Compaction skipped for %s %s — only %d file(s)", symbol, interval, files_before)
        return {
            "files_before": files_before,
            "files_after": files_before,
            "bars_count": 0,
            "size_before": size_before,
            "size_after": size_before,
        }

    # 3. Read ALL bars via catalog
    catalog = ParquetDataCatalog(str(catalog_path))
    bars = catalog.bars(bar_types=[str(bar_type)])
    if not bars:
        logger.warning("No bars found for %s %s during compaction", symbol, interval)
        return {
            "files_before": files_before,
            "files_after": files_before,
            "bars_count": 0,
            "size_before": size_before,
            "size_after": size_before,
        }

    # 4. Deduplicate by ts_event (keep last) and sort
    bars = dedupe_by_ts(bars)
    logger.info("Compacting %s %s: %d files -> %d bars (deduped)", symbol, interval, files_before, len(bars))

    temp_catalog_path = catalog_path / ".compaction" / f"{bar_type}-{uuid4().hex}"
    rollback_path = catalog_path / ".compaction-rollback" / f"{bar_type}-{uuid4().hex}"
    backup_paths: dict[Path, Path] = {}
    promoted_files: set[Path] = set()
    rollback_created = False
    cleanup_rollback = True
    try:
        temp_bar_dir = temp_catalog_path / "data" / "bar" / str(bar_type)
        temp_catalog = ParquetDataCatalog(str(temp_catalog_path))
        temp_catalog.write_data([instrument])
        temp_catalog.write_data(bars)

        temp_files = list(temp_bar_dir.glob("*.parquet")) if temp_bar_dir.exists() else []
        if not temp_files:
            raise RuntimeError(f"Local compaction produced no parquet files for {symbol} {interval}")

        rollback_path.mkdir(parents=True, exist_ok=True)
        rollback_created = True
        for old_file in existing_files:
            backup_path = rollback_path / old_file.name
            shutil.copy2(old_file, backup_path)
            backup_paths[old_file] = backup_path

        for temp_file in temp_files:
            # NT encodes the interval in the parquet basename. Preserve that exact
            # name during promotion so downstream directory scans keep working.
            final_path = bar_dir / temp_file.name
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file.replace(final_path)
            promoted_files.add(final_path)

        for f in existing_files:
            if f in promoted_files:
                continue
            f.unlink(missing_ok=True)
    except Exception:
        for promoted_file in reversed(list(promoted_files)):
            try:
                promoted_file.unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to rollback promoted local compaction file %s", promoted_file, exc_info=True)
        for old_file, backup_path in backup_paths.items():
            try:
                old_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, old_file)
            except Exception:
                cleanup_rollback = False
                logger.warning("Failed to restore local compaction backup %s", old_file, exc_info=True)
        raise
    finally:
        shutil.rmtree(temp_catalog_path, ignore_errors=True)
        if rollback_created and cleanup_rollback:
            shutil.rmtree(rollback_path, ignore_errors=True)

    # 5. Compute summary
    new_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    files_after = len(new_files)
    size_after = sum(f.stat().st_size for f in new_files)

    logger.info(
        "Compaction complete for %s %s: %d -> %d files, %d -> %d bytes, %d bars",
        symbol, interval, files_before, files_after, size_before, size_after, len(bars),
    )

    return {
        "files_before": files_before,
        "files_after": files_after,
        "bars_count": len(bars),
        "size_before": size_before,
        "size_after": size_after,
    }


# ---------------------------------------------------------------------------
# TradeTick support
# ---------------------------------------------------------------------------

def agg_trades_to_trade_ticks(
    agg_trades: list[dict[str, Any]],
    symbol: str,
) -> list:
    """Convert Binance aggregate trades to NT TradeTick objects.

    Args:
        agg_trades: list of dicts from ``fetch_agg_trades()``
            (keys: agg_id, price, quantity, timestamp_ms, is_buyer_maker)
        symbol: TinoHelm symbol (e.g. ``BTCUSDT-PERP``)

    Returns:
        list of ``TradeTick`` objects sorted by ts_event.
    """
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.enums import AggressorSide
    from nautilus_trader.model.identifiers import TradeId

    from tinohelm.data.instruments import make_instrument

    instrument = make_instrument(symbol)
    inst_id = instrument.id

    ticks: list[TradeTick] = []
    for t in agg_trades:
        ts_ns = int(t["timestamp_ms"]) * 1_000_000  # ms → ns
        ticks.append(TradeTick(
            instrument_id=inst_id,
            price=instrument.make_price(float(t["price"])),
            size=instrument.make_qty(float(t["quantity"])),
            aggressor_side=AggressorSide.SELLER if t["is_buyer_maker"] else AggressorSide.BUYER,
            trade_id=TradeId(str(t["agg_id"])),
            ts_event=ts_ns,
            ts_init=ts_ns,
        ))

    ticks.sort(key=lambda x: x.ts_event)
    logger.info("Converted %d aggregate trades to TradeTick for %s", len(ticks), symbol)
    return ticks


def write_trade_ticks(
    ticks: list,
    symbol: str,
    catalog_path: str | Path,
    source_type: str | None = None,
    storage: Any | None = None,
) -> list[str]:
    """Write TradeTick objects to the Parquet catalog.

    Returns list of written Parquet file paths.
    """
    catalog_path = Path(resolve_catalog_path(catalog_path, source_type))
    if not _is_remote_storage(storage):
        ensure_catalog_dirs(catalog_path)
    catalog = _catalog_for_root(catalog_path, storage)

    from tinohelm.data.instruments import make_instrument
    instrument = make_instrument(symbol)
    tick_dir = catalog_path / "data" / "trade_tick" / str(instrument.id)

    # Snapshot existing files before write to return only new ones
    existing = {str(p) for p in _iter_catalog_files(storage, tick_dir, recursive=False)}

    catalog.write_data(ticks, skip_disjoint_check=True)

    # Return newly created files when possible. Remote/object-store writes may
    # replace an existing key in place, so fall back to the current directory
    # contents as the success signal instead of requiring a path delta.
    current = sorted({str(p) for p in _iter_catalog_files(storage, tick_dir, recursive=False)})
    written = sorted(set(current) - existing)
    if ticks and not current:
        raise RuntimeError(
            f"TradeTick write for {symbol} produced no parquet files under {tick_dir}"
        )
    result = written or current
    logger.info("Wrote %d TradeTick to %d file(s) for %s", len(ticks), len(result), symbol)
    return result


def write_quote_ticks(
    ticks: list,
    symbol: str,
    catalog_path: str | Path,
    source_type: str | None = None,
    storage: Any | None = None,
) -> list[str]:
    """Write QuoteTick objects to a source-aware Parquet catalog root."""
    catalog_path = Path(resolve_catalog_path(catalog_path, source_type))
    if not _is_remote_storage(storage):
        ensure_catalog_dirs(catalog_path)
    catalog = _catalog_for_root(catalog_path, storage)

    from tinohelm.data.instruments import make_instrument
    instrument = make_instrument(symbol)
    tick_dir = catalog_path / "data" / "quote_tick" / str(instrument.id)

    existing = {str(p) for p in _iter_catalog_files(storage, tick_dir, recursive=False)}
    catalog.write_data(ticks, skip_disjoint_check=True)
    current = {str(p) for p in _iter_catalog_files(storage, tick_dir, recursive=False)}
    written = sorted(current - existing)
    if ticks and not written:
        raise RuntimeError(
            f"QuoteTick write for {symbol} produced no parquet files under {tick_dir}"
        )
    logger.info("Wrote %d QuoteTick to %d file(s) for %s", len(ticks), len(written), symbol)
    return written


def metrics_parquet_path(symbol: str, catalog_root: str | Path) -> Path:
    return Path(catalog_root) / "metrics" / "metrics" / "data" / "metrics" / f"{symbol.lower()}.parquet"


def book_depth_parquet_path(symbol: str, catalog_root: str | Path) -> Path:
    return Path(catalog_root) / "book_depth" / "bookDepth" / "data" / "book_depth" / f"{symbol.lower()}.parquet"


def _write_raw_records_parquet(
    records: list,
    out_path: Path,
    rows: list[dict[str, Any]],
    dedupe_subset: list[str],
    storage: Any | None = None,
) -> Path:
    import fcntl
    import os
    import tempfile
    from io import BytesIO

    import polars as pl

    frame = pl.DataFrame(rows)
    if not _is_remote_storage(storage):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _catalog_write_lock_path(out_path, storage)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if _is_remote_storage(storage):
                try:
                    exists = storage.exists(out_path)
                except FileNotFoundError:
                    exists = False
                if exists:
                    try:
                        existing_bytes = storage.read_bytes(out_path)
                    except FileNotFoundError:
                        existing_bytes = None
                    else:
                        frame = pl.concat([
                            pl.read_parquet(BytesIO(existing_bytes)),
                            frame,
                        ], how="diagonal_relaxed")
                frame = frame.sort(dedupe_subset).unique(
                    subset=dedupe_subset,
                    keep="last",
                    maintain_order=True,
                )
                buf = BytesIO()
                frame.write_parquet(buf)
                storage.upload_bytes(out_path, buf.getvalue())
                logger.info("Wrote %d raw rows to remote %s", len(records), out_path)
                return out_path

            if out_path.exists():
                try:
                    frame = pl.concat([pl.read_parquet(out_path), frame], how="diagonal_relaxed")
                except Exception as exc:
                    raise RuntimeError(f"failed to read existing raw parquet at {out_path}") from exc
            frame = frame.sort(dedupe_subset).unique(
                subset=dedupe_subset,
                keep="last",
                maintain_order=True,
            )
            fd, tmp_name = tempfile.mkstemp(prefix=f".{out_path.name}.", suffix=".tmp", dir=out_path.parent)
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                frame.write_parquet(tmp_path)
                os.replace(tmp_path, out_path)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    logger.info("Wrote %d raw rows to %s", len(records), out_path)
    return out_path


def write_metrics_parquet(records: list, symbol: str, catalog_root: str | Path, storage: Any | None = None) -> Path:
    """Write BinanceMetrics dataclass records as source-aware raw Parquet."""
    rows: list[dict[str, Any]] = []
    for record in records:
        open_interest = float(getattr(record, "open_interest"))
        rows.append({
            "symbol": str(getattr(record, "symbol", symbol)),
            "ts_event": int(getattr(record, "ts_event")),
            "ts_init": int(getattr(record, "ts_init", getattr(record, "ts_event"))),
            "open_interest": open_interest,
            "sum_open_interest": open_interest,
            "open_interest_value": float(getattr(record, "open_interest_value")),
            "toptrader_long_short_ratio_count": float(getattr(record, "toptrader_long_short_ratio_count", 0.0)),
            "toptrader_long_short_ratio_sum": float(getattr(record, "toptrader_long_short_ratio_sum", 0.0)),
            "global_long_short_ratio": float(getattr(record, "global_long_short_ratio", 0.0)),
            "taker_long_short_vol_ratio": float(getattr(record, "taker_long_short_vol_ratio", 0.0)),
        })
    return _write_raw_records_parquet(records, metrics_parquet_path(symbol, catalog_root), rows, ["ts_event"], storage=storage)


def write_book_depth_parquet(records: list, symbol: str, catalog_root: str | Path, storage: Any | None = None) -> Path:
    """Write BinanceBookDepth dataclass records as source-aware raw Parquet."""
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append({
            "symbol": str(getattr(record, "symbol", symbol)),
            "ts_event": int(getattr(record, "ts_event")),
            "ts_init": int(getattr(record, "ts_init", getattr(record, "ts_event"))),
            "percentage": float(getattr(record, "percentage")),
            "depth": float(getattr(record, "depth")),
            "notional": float(getattr(record, "notional")),
        })
    return _write_raw_records_parquet(records, book_depth_parquet_path(symbol, catalog_root), rows, ["ts_event", "percentage"], storage=storage)


def read_metrics_parquet(symbol: str, catalog_root: str | Path) -> "pl.DataFrame | None":
    import polars as pl

    path = metrics_parquet_path(symbol, catalog_root)
    return pl.read_parquet(path) if path.exists() else None


def read_book_depth_parquet(symbol: str, catalog_root: str | Path) -> "pl.DataFrame | None":
    import polars as pl

    path = book_depth_parquet_path(symbol, catalog_root)
    return pl.read_parquet(path) if path.exists() else None


# ---------------------------------------------------------------------------
# Direct-update / funding Parquet support
# ---------------------------------------------------------------------------

def _direct_update_dir(symbol: str, catalog_root: str | Path, update_type: str) -> Path:
    from tinohelm.strategy.loader_helpers import normalize_symbol

    return Path(catalog_root) / "data" / update_type / normalize_symbol(symbol)


def mark_price_update_dir(symbol: str, catalog_root: str | Path) -> Path:
    return _direct_update_dir(symbol, catalog_root, "mark_price_update")


def index_price_update_dir(symbol: str, catalog_root: str | Path) -> Path:
    return _direct_update_dir(symbol, catalog_root, "index_price_update")


def funding_rate_update_dir(symbol: str, catalog_root: str | Path) -> Path:
    return _direct_update_dir(symbol, catalog_root, "funding_rate_update")


def funding_rate_parquet_path(symbol: str, catalog_root: str | Path) -> Path:
    """Return the legacy single-file Parquet path for a symbol's funding-rate data.

    The new NT-native funding update layout lives under
    ``data/funding_rate_update/<instrument_id>/``. This helper is kept for the
    legacy JSON/single-file compatibility path.
    """
    return Path(catalog_root) / "data" / "funding_rate" / f"{symbol.lower()}.parquet"


def write_funding_rate_parquet(
    records: list,
    symbol: str,
    catalog_root: str | Path,
    storage: Any | None = None,
) -> Path:
    """Write BinanceFundingRate records to a single Parquet file per symbol.

    Schema
    ------
    - ``ts_event`` : int64 — funding time in nanoseconds since epoch
    - ``funding_rate`` : float64

    Records from ``records`` may be :class:`BinanceFundingRate` dataclass
    instances (with ``.ts_event`` and ``.funding_rate`` attrs) **or** plain
    dicts with ``funding_time_ms`` / ``funding_rate`` keys (JSON cache format).

    Existing data is merged (deduped by ``ts_event``, keeping latest) before
    writing so incremental ingestion is safe.

    Returns the path of the written Parquet file.
    """
    import fcntl
    import pyarrow as pa
    import pyarrow.parquet as pq
    from io import BytesIO

    out_path = funding_rate_parquet_path(symbol, catalog_root)
    if not _is_remote_storage(storage):
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalise records → list of (ts_event_ns, funding_rate)
    def _to_tuple(r):
        if hasattr(r, "ts_event"):
            rate = getattr(r, "funding_rate", None)
            if rate is None:
                rate = getattr(r, "rate")
            return int(r.ts_event), float(rate)
        # Plain dict (JSON cache format)
        return int(r["funding_time_ms"]) * 1_000_000, float(r["funding_rate"])

    new_rows: dict[int, float] = {}
    for r in records:
        ts_ns, rate = _to_tuple(r)
        new_rows[ts_ns] = rate

    lock_path = _catalog_write_lock_path(out_path, storage)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            # Merge with existing Parquet if present
            if _is_remote_storage(storage):
                try:
                    exists = storage.exists(out_path)
                except FileNotFoundError:
                    exists = False
                if exists:
                    try:
                        existing_payload = storage.read_bytes(out_path)
                    except FileNotFoundError:
                        existing_payload = None
                    else:
                        existing_table = pq.read_table(BytesIO(existing_payload))
                        for ts_ns, rate in zip(
                            existing_table["ts_event"].to_pylist(),
                            existing_table["funding_rate"].to_pylist(),
                        ):
                            # New records take precedence (overwrite existing by ts_event key)
                            if ts_ns not in new_rows:
                                new_rows[ts_ns] = float(rate)
            elif out_path.exists():
                existing_table = pq.read_table(str(out_path))
                for ts_ns, rate in zip(
                    existing_table["ts_event"].to_pylist(),
                    existing_table["funding_rate"].to_pylist(),
                ):
                    # New records take precedence (overwrite existing by ts_event key)
                    if ts_ns not in new_rows:
                        new_rows[ts_ns] = float(rate)

            # Sort by ts_event and build Arrow table
            sorted_ts = sorted(new_rows)
            table = pa.table(
                {
                    "ts_event": pa.array(sorted_ts, type=pa.int64()),
                    "funding_rate": pa.array([new_rows[t] for t in sorted_ts], type=pa.float64()),
                }
            )
            if _is_remote_storage(storage):
                buf = BytesIO()
                pq.write_table(table, buf)
                storage.upload_bytes(out_path, buf.getvalue())
            else:
                pq.write_table(table, str(out_path))
            logger.info(
                "Wrote %d funding-rate rows to %s", len(sorted_ts), out_path
            )
            return out_path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_funding_rate_parquet(
    symbol: str,
    catalog_root: str | Path,
) -> "pd.DataFrame | None":
    """Read a funding-rate Parquet file and return a DataFrame or None.

    Columns: ``ts_event`` (int64 ns), ``funding_rate`` (float64).
    Returns ``None`` when the file does not exist.
    """
    import pyarrow.parquet as pq

    path = funding_rate_parquet_path(symbol, catalog_root)
    if not path.exists():
        return None
    table = pq.read_table(str(path))
    # Return as pandas DataFrame (caller handles conversion to Series)
    return table.to_pandas()
