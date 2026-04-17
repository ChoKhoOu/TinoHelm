"""ParquetDataCatalog manager for TinoHelm — writes NautilusTrader native format."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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


def validate_bars(symbol: str, interval: str, catalog_path: str | Path) -> dict:
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

    # Count files and size on disk
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    parquet_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    file_count = len(parquet_files)
    size_bytes = sum(f.stat().st_size for f in parquet_files)

    # Read bars from catalog
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
) -> list[Path]:
    """Write pre-converted NT Bar objects to Parquet catalog.

    When *merge* is True (default), reads existing bars, deduplicates,
    deletes old parquet files, then writes everything in one write_data()
    call — satisfying NT's disjoint-interval constraint.

    When *merge* is False (streaming mode), writes directly without
    reading existing data.  Caller must clean old files beforehand.

    Returns list of written file paths.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    if not bars:
        return []

    catalog_path = ensure_catalog_dirs(resolve_catalog_path(catalog_path, source_type))
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    existing_files_to_delete: list[Path] = []
    if merge:
        # Merge with existing bars if present (incremental update case)
        try:
            catalog = ParquetDataCatalog(str(catalog_path))
            existing_bars = catalog.bars(bar_types=[str(bar_type)])
            if existing_bars:
                # Track old files — they will be deleted AFTER the merged write
                # succeeds, so a crash between write and delete leaves the old
                # data intact (at worst we have duplicate bars, not missing ones).
                bar_dir = catalog_path / "data" / "bar" / str(bar_type)
                if bar_dir.exists():
                    existing_files_to_delete = list(bar_dir.glob("*.parquet"))

                existing_count = len(existing_bars)
                bars = merge_bars(existing_bars, bars)
                logger.info("Merged %d existing + %d new bars = %d total",
                            existing_count, len(bars) - existing_count, len(bars))
        except Exception:
            logger.warning("Failed to read existing bars for %s %s, writing fresh", symbol, interval, exc_info=True)

    catalog = ParquetDataCatalog(str(catalog_path))
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
        if old_file.exists():
            old_file.unlink()

    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    return list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []


def compact_bars(symbol: str, interval: str, catalog_path: str | Path) -> dict:
    """Compact multiple Parquet files for a symbol/interval into a single file.

    Reads all bars, deduplicates by ts_event (keeping last), deletes existing
    parquet files, then writes back as a single sorted file.

    Returns summary dict with files_before, files_after, bars_count,
    size_before, size_after.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

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

    # 5. Delete all existing parquet files in the bar_type directory
    for f in existing_files:
        f.unlink()
    logger.info("Deleted %d old parquet files from %s", files_before, bar_dir)

    # 6. Write all bars back as a single sorted file
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])
    catalog.write_data(bars)

    # 7. Compute summary
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
) -> list[str]:
    """Write TradeTick objects to the Parquet catalog.

    Returns list of written Parquet file paths.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog_path = resolve_catalog_path(catalog_path, source_type)
    catalog_path = Path(catalog_path)
    catalog = ParquetDataCatalog(str(catalog_path))

    from tinohelm.data.instruments import make_instrument
    instrument = make_instrument(symbol)
    tick_dir = catalog_path / "data" / "trade_tick" / str(instrument.id)

    # Snapshot existing files before write to return only new ones
    existing = set(str(f) for f in tick_dir.glob("*.parquet")) if tick_dir.exists() else set()

    catalog.write_data(ticks, skip_disjoint_check=True)

    # Return only newly created files
    current = set(str(f) for f in tick_dir.glob("*.parquet")) if tick_dir.exists() else set()
    written = sorted(current - existing)
    logger.info("Wrote %d TradeTick to %d file(s) for %s", len(ticks), len(written), symbol)
    return written
