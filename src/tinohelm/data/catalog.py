"""ParquetDataCatalog manager for TinoHelm — writes NautilusTrader native format."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    interval_to_step_unit,
    is_ohlc_valid,
    merge_bars,
    ns_to_iso,
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


def ensure_catalog_dirs(catalog_path: str | Path) -> Path:
    """Ensure catalog directory exists."""
    path = Path(catalog_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_remote_storage(storage: Any | None) -> bool:
    return storage is not None and getattr(storage, "provider", "local") != "local"


def _catalog_for_root(catalog_root: str | Path, storage: Any | None = None):
    """Create an NT catalog for a logical root, using object storage directly when active."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    root = Path(catalog_root)
    if _is_remote_storage(storage):
        uri_for_root = getattr(storage, "uri_for_catalog_root", None)
        if not callable(uri_for_root):
            raise ValueError("Remote catalog storage must expose uri_for_catalog_root()")
        return ParquetDataCatalog.from_uri(
            uri_for_root(root),
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
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

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
        catalog_uri_for_root = getattr(storage_provider, "uri_for_catalog_root", None)
        catalog_uri = catalog_uri_for_root(catalog_path) if callable(catalog_uri_for_root) else str(catalog_path)
    else:
        parquet_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
        catalog_uri = str(catalog_path)
    file_count = len(parquet_files)
    size_bytes = sum(
        int(obj.size) if getattr(obj, "size", None) is not None else Path(obj.path if hasattr(obj, "path") else obj).stat().st_size
        for obj in parquet_files
    )

    # Read bars from catalog
    if storage_provider is not None and getattr(storage_provider, "provider", "local") != "local":
        catalog = ParquetDataCatalog.from_uri(
            catalog_uri,
            fs_storage_options=getattr(storage_provider, "fs_storage_options", None),
            fs_rust_storage_options=getattr(storage_provider, "fs_rust_storage_options", None),
        )
    else:
        catalog = ParquetDataCatalog(str(catalog_path))
    try:
        bars = catalog.bars(bar_types=[str(bar_type)])
    except Exception:
        bars = []

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

    existing_files_to_delete: list[Path] = []
    if merge:
        # Merge with existing bars if present (incremental update case)
        try:
            catalog = _catalog_for_root(catalog_path, storage)
            existing_bars = catalog.bars(bar_types=[str(bar_type)])
            if existing_bars:
                # Track old files — they will be deleted AFTER the merged write
                # succeeds, so a crash between write and delete leaves the old
                # data intact (at worst we have duplicate bars, not missing ones).
                bar_dir = catalog_path / "data" / "bar" / str(bar_type)
                existing_files_to_delete = _iter_catalog_files(storage, bar_dir, recursive=False)

                existing_count = len(existing_bars)
                bars = merge_bars(existing_bars, bars)
                logger.info("Merged %d existing + %d new bars = %d total",
                            existing_count, len(bars) - existing_count, len(bars))
        except Exception:
            logger.warning("Failed to read existing bars for %s %s, writing fresh", symbol, interval, exc_info=True)

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

    # Delete old parquet files AFTER the merged write has succeeded.  This
    # order prevents data loss: if the process crashes between delete and
    # write (the previous order) all data would be gone.  Now the worst case
    # is stale duplicates that compact_bars can clean up.
    for old_file in existing_files_to_delete:
        if _is_remote_storage(storage):
            storage.delete_path(old_file)
        elif old_file.exists():
            old_file.unlink()

    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
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
    temp_bar_dir = temp_catalog_path / "data" / "bar" / str(bar_type)
    temp_catalog = ParquetDataCatalog(str(temp_catalog_path))
    temp_catalog.write_data([instrument])
    temp_catalog.write_data(bars)

    temp_files = list(temp_bar_dir.glob("*.parquet")) if temp_bar_dir.exists() else []
    if not temp_files:
        raise RuntimeError(f"Local compaction produced no parquet files for {symbol} {interval}")

    promotion_token = uuid4().hex
    for temp_file in temp_files:
        final_path = bar_dir / f"{promotion_token}-{temp_file.name}"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file.replace(final_path)

    try:
        for f in existing_files:
            f.unlink(missing_ok=True)
    finally:
        shutil.rmtree(temp_catalog_path, ignore_errors=True)

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

    # Return only newly created files
    current = {str(p) for p in _iter_catalog_files(storage, tick_dir, recursive=False)}
    written = sorted(current - existing)
    logger.info("Wrote %d TradeTick to %d file(s) for %s", len(ticks), len(written), symbol)
    return written


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
                except Exception:
                    logger.warning("Failed to merge existing raw parquet at %s", out_path, exc_info=True)
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
# FundingRate Parquet support
# ---------------------------------------------------------------------------

def funding_rate_parquet_path(symbol: str, catalog_root: str | Path) -> Path:
    """Return the canonical Parquet path for a symbol's funding-rate data.

    Convention: ``{catalog_root}/data/funding_rate/{symbol.lower()}.parquet``.
    This matches the ``funding_rate`` write-category in WRITE_CATEGORY and the
    catalog root resolved by ``DataLayer._resolve_catalog_root`` (see
    ``tinohelm.factor.data_layer``).
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
            # BinanceFundingRate dataclass
            return int(r.ts_event), float(r.funding_rate)
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
