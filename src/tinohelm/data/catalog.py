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

    # OHLC relationship validation
    ohlc_violations = 0
    zero_volume_bars = 0
    price_jumps: list[dict] = []

    prev_close: float | None = None
    jump_threshold = 0.10  # 10% price change between consecutive bars

    for bar in bars:
        o = float(bar.open)
        h = float(bar.high)
        l = float(bar.low)  # noqa: E741
        c = float(bar.close)
        v = float(bar.volume)

        # OHLC invariant: high >= max(open, close), low <= min(open, close)
        if h < max(o, c) - 1e-10 or l > min(o, c) + 1e-10 or h < l:
            ohlc_violations += 1

        # Zero volume detection
        if v == 0:
            zero_volume_bars += 1

        # Price jump detection (consecutive bars)
        if prev_close is not None and prev_close > 0:
            change_pct = abs(c - prev_close) / prev_close
            if change_pct > jump_threshold:
                price_jumps.append({
                    "timestamp": _ns_to_iso(bar.ts_event),
                    "prev_close": round(prev_close, 4),
                    "current_close": round(c, 4),
                    "change_pct": round(change_pct * 100, 2),
                })
        prev_close = c

    if ohlc_violations > 0:
        issues.append(f"Found {ohlc_violations} bar(s) with invalid OHLC relationship (high < max(O,C) or low > min(O,C))")
    if zero_volume_bars > 0:
        issues.append(f"Found {zero_volume_bars} zero-volume bar(s)")
    if price_jumps:
        issues.append(f"Found {len(price_jumps)} price jump(s) exceeding {jump_threshold*100:.0f}%")

    # Determine status
    has_errors = gaps or ohlc_violations > 0
    has_warnings = duplicates > 0 or zero_volume_bars > 0 or len(price_jumps) > 0
    if has_errors:
        status = "errors"
    elif has_warnings:
        status = "warnings"
    else:
        status = "ok"

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
    existing_files: list[Path] = []
    try:
        catalog = ParquetDataCatalog(str(catalog_path))
        existing_bars = catalog.bars(bar_types=[str(bar_type)])
        if existing_bars:
            # Track old files before writing new data
            bar_dir = catalog_path / "data" / "bar" / str(bar_type)
            if bar_dir.exists():
                existing_files = list(bar_dir.glob("*.parquet"))

            seen: dict[int, Any] = {b.ts_event: b for b in existing_bars}
            for b in bars:
                seen[b.ts_event] = b
            bars = sorted(seen.values(), key=lambda b: b.ts_event)
            logger.info("Merged %d existing + %d new bars = %d total",
                        len(existing_bars), len(bars) - len(existing_bars), len(bars))
    except Exception:
        logger.warning("Failed to read existing bars for %s %s, writing fresh", symbol, interval, exc_info=True)

    # Delete old parquet files BEFORE writing merged data to avoid
    # NT's non-disjoint interval error (merged bars are already in memory)
    for old_file in existing_files:
        if old_file.exists():
            old_file.unlink()

    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])
    catalog.write_data(bars)
    logger.info("Wrote %d bars to catalog at %s", len(bars), catalog_path)

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
    from nautilus_trader.model.identifiers import InstrumentId, TradeId
    from nautilus_trader.model.objects import Price, Quantity

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
) -> list[str]:
    """Write TradeTick objects to the Parquet catalog.

    Returns list of written Parquet file paths.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog_path = Path(catalog_path)
    catalog = ParquetDataCatalog(str(catalog_path))

    catalog.write_data(ticks)

    # Find written files
    from tinohelm.data.instruments import make_instrument
    instrument = make_instrument(symbol)
    tick_dir = catalog_path / "data" / "trade_tick" / str(instrument.id)
    written = [str(f) for f in tick_dir.glob("*.parquet")] if tick_dir.exists() else []
    logger.info("Wrote %d TradeTick to %d file(s) for %s", len(ticks), len(written), symbol)
    return written
