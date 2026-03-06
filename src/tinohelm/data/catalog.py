"""ParquetDataCatalog manager for TinoHelm — writes NautilusTrader native format."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Interval string to (step, BarAggregation) mapping
_INTERVAL_MAP: dict[str, tuple[int, str]] = {
    "1m": (1, "MINUTE"), "3m": (3, "MINUTE"), "5m": (5, "MINUTE"),
    "15m": (15, "MINUTE"), "30m": (30, "MINUTE"),
    "1h": (1, "HOUR"), "2h": (2, "HOUR"), "4h": (4, "HOUR"),
    "6h": (6, "HOUR"), "8h": (8, "HOUR"), "12h": (12, "HOUR"),
    "1d": (1, "DAY"),
}


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

    if interval not in _INTERVAL_MAP:
        raise ValueError(f"Unsupported interval '{interval}'. Supported: {list(_INTERVAL_MAP.keys())}")

    step, agg_name = _INTERVAL_MAP[interval]
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


def _interval_to_nanoseconds(interval: str) -> int:
    """Convert an interval string like '5m' or '1h' to nanoseconds."""
    step, agg_name = _INTERVAL_MAP[interval]
    multipliers = {"MINUTE": 60, "HOUR": 3600, "DAY": 86400}
    return step * multipliers[agg_name] * 1_000_000_000


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
    from datetime import datetime, timezone

    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    if interval not in _INTERVAL_MAP:
        raise ValueError(f"Unsupported interval '{interval}'. Supported: {list(_INTERVAL_MAP.keys())}")

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

    # Date range
    def _ns_to_iso(ns: int) -> str:
        return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()

    date_range = {"start": _ns_to_iso(timestamps[0]), "end": _ns_to_iso(timestamps[-1])}

    issues: list[str] = []

    # Check duplicates
    unique_ts = set(timestamps)
    duplicates = total_bars - len(unique_ts)
    if duplicates > 0:
        issues.append(f"Found {duplicates} duplicate timestamp(s)")

    # Check gaps (use deduplicated sorted timestamps)
    sorted_unique = sorted(unique_ts)
    expected_step_ns = _interval_to_nanoseconds(interval)
    tolerance_ns = int(expected_step_ns * 1.5)
    gaps: list[dict] = []

    for i in range(1, len(sorted_unique)):
        diff = sorted_unique[i] - sorted_unique[i - 1]
        if diff > tolerance_ns:
            missing_bars = int(diff / expected_step_ns) - 1
            gaps.append({
                "start": _ns_to_iso(sorted_unique[i - 1]),
                "end": _ns_to_iso(sorted_unique[i]),
                "missing_bars": missing_bars,
            })

    if gaps:
        total_missing = sum(g["missing_bars"] for g in gaps)
        issues.append(f"Found {len(gaps)} gap(s) with ~{total_missing} missing bar(s)")

    # Determine status
    if gaps:
        status = "errors"
    elif duplicates > 0:
        status = "warnings"
    else:
        status = "ok"

    return {
        "total_bars": total_bars,
        "date_range": date_range,
        "duplicates": duplicates,
        "gaps": gaps,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "status": status,
        "issues": issues,
    }


def klines_to_parquet(
    klines: list[dict[str, Any]],
    symbol: str,
    interval: str,
    catalog_path: str | Path,
) -> list[Path]:
    """Convert raw Binance klines to NautilusTrader native Parquet format.

    Uses BarDataWrangler + ParquetDataCatalog.write_data() so that
    the data can be read back via catalog.bars() / catalog.instruments().

    Returns list of written file paths (approximate — catalog manages files internally).
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required for Parquet operations: pip install pandas pyarrow")
        return []

    if not klines:
        logger.warning("No klines to write for %s %s", symbol, interval)
        return []

    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.persistence.wranglers import BarDataWrangler

    catalog_path = ensure_catalog_dirs(catalog_path)

    # 1. Create instrument and bar type
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    # 2. Build DataFrame with DatetimeIndex from raw klines
    df = pd.DataFrame(klines)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = "timestamp"
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna()

    if df.empty:
        logger.warning("DataFrame empty after cleaning for %s %s", symbol, interval)
        return []

    # 3. Wrangle into Bar objects
    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    bars = wrangler.process(df)
    logger.info("Wrangled %d bars for %s %s", len(bars), symbol, interval)

    # 4. Write to catalog
    catalog = ParquetDataCatalog(str(catalog_path))

    # Write instrument (idempotent — catalog handles duplicates)
    catalog.write_data([instrument])

    # Merge with existing bars to deduplicate by timestamp (keep latest)
    try:
        existing_bars = catalog.bars(bar_types=[str(bar_type)])
        if existing_bars:
            seen: dict[int, Any] = {b.ts_event: b for b in existing_bars}
            for b in bars:
                seen[b.ts_event] = b  # newer overwrites older
            bars = sorted(seen.values(), key=lambda b: b.ts_event)
            logger.info("Merged with %d existing bars, total %d after dedup", len(existing_bars), len(bars))

            # Remove old parquet files before rewriting (NT requires disjoint intervals)
            bar_dir = catalog_path / "data" / "bar" / str(bar_type)
            if bar_dir.exists():
                for old_file in bar_dir.glob("*.parquet"):
                    old_file.unlink()
                logger.info("Cleared old parquet files in %s before rewrite", bar_dir)
    except Exception:
        logger.debug("No existing bars for %s %s, writing fresh", symbol, interval)

    # Write bars (must be sorted by ts_init — wrangler ensures this)
    catalog.write_data(bars)

    logger.info("Wrote %d bars to catalog at %s", len(bars), catalog_path)

    # Return file paths (NT catalog stores under data/bar/)
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    written = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    return written


def klines_to_bars(
    klines: list[dict[str, Any]],
    symbol: str,
    interval: str,
) -> list:
    """Convert raw Binance klines to NT Bar objects without any I/O.

    Use this to process monthly chunks incrementally. Accumulate the returned
    Bar objects across chunks, then call write_bars() once at the end.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required: pip install pandas pyarrow")
        return []

    if not klines:
        return []

    from nautilus_trader.persistence.wranglers import BarDataWrangler

    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    df = pd.DataFrame(klines)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = "timestamp"
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna()

    if df.empty:
        return []

    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    return wrangler.process(df)


def write_bars(
    bars: list,
    symbol: str,
    interval: str,
    catalog_path: str | Path,
) -> list[Path]:
    """Write pre-converted NT Bar objects to Parquet catalog.

    Merges with any existing bars (for incremental updates), deletes old
    parquet files, then writes everything as a single file in one write_data()
    call — satisfying NT's disjoint-interval constraint.

    Returns list of written file paths.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    if not bars:
        return []

    catalog_path = ensure_catalog_dirs(catalog_path)
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    # Merge with existing bars if present (incremental update case)
    try:
        catalog = ParquetDataCatalog(str(catalog_path))
        existing_bars = catalog.bars(bar_types=[str(bar_type)])
        if existing_bars:
            seen: dict[int, Any] = {b.ts_event: b for b in existing_bars}
            for b in bars:
                seen[b.ts_event] = b
            bars = sorted(seen.values(), key=lambda b: b.ts_event)
            logger.info("Merged %d existing + %d new bars = %d total",
                        len(existing_bars), len(bars) - len(existing_bars), len(bars))

            # Remove old parquet files (NT requires disjoint intervals)
            bar_dir = catalog_path / "data" / "bar" / str(bar_type)
            if bar_dir.exists():
                for old_file in bar_dir.glob("*.parquet"):
                    old_file.unlink()
    except Exception:
        logger.debug("No existing bars for %s %s, writing fresh", symbol, interval)

    # Write all bars in a single call to satisfy NT's disjoint constraint
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])
    catalog.write_data(bars)
    logger.info("Wrote %d bars to catalog at %s", len(bars), catalog_path)

    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    return list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []


def write_klines_chunk(
    klines: list[dict[str, Any]],
    symbol: str,
    interval: str,
    catalog_path: str | Path,
) -> int:
    """Write a single chunk of klines to Parquet without loading existing data.

    Unlike klines_to_parquet(), this does NOT read/merge/delete existing bars —
    it just converts and appends. Call compact_bars() afterward to merge all
    chunk files into one deduplicated file.

    Returns the number of bars written.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required: pip install pandas pyarrow")
        return 0

    if not klines:
        return 0

    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.persistence.wranglers import BarDataWrangler

    catalog_path = ensure_catalog_dirs(catalog_path)
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)

    df = pd.DataFrame(klines)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = "timestamp"
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna()

    if df.empty:
        return 0

    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    bars = wrangler.process(df)

    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])
    catalog.write_data(bars)

    logger.info("Chunk: wrote %d bars for %s %s", len(bars), symbol, interval)
    return len(bars)


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
    seen: dict[int, Any] = {}
    for b in bars:
        seen[b.ts_event] = b
    bars = sorted(seen.values(), key=lambda b: b.ts_event)
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
