"""BinanceVisionPipeline — unified data ingestion orchestrator.

Three-layer architecture:
  Download (downloader.py) → Convert (converters/) → Store (catalog.py)

Provides both async and sync entry points for use in FastAPI routes
and BacktestRunner subprocesses respectively.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from tinohelm.data.converters import get_converter
from tinohelm.data.downloader import VisionDownloader, _KLINES_TYPES

logger = logging.getLogger(__name__)

# Data types that support REST API fallback for recent data
_REST_FALLBACK_TYPES = frozenset({
    "klines", "markPriceKlines", "indexPriceKlines",
    "premiumIndexKlines", "aggTrades", "trades",
})

# Mapping: data_type → catalog write category
_WRITE_CATEGORY: dict[str, str] = {
    "klines": "bar",
    "markPriceKlines": "bar",
    "indexPriceKlines": "bar",
    "premiumIndexKlines": "bar",
    "aggTrades": "trade_tick",
    "trades": "trade_tick",
    "bookTicker": "quote_tick",
    "fundingRate": "funding_rate",
    "bookDepth": "order_book_delta",
    "liquidationSnapshot": "liquidation",
    "metrics": "metrics",
}

# Mapping: data_type → DataCatalog.interval convention value
_INTERVAL_CONVENTION: dict[str, str] = {
    "aggTrades": "tick",
    "trades": "tick",
    "bookTicker": "tick",
    "fundingRate": "8h",
    "bookDepth": "tick",
    "liquidationSnapshot": "tick",
    "metrics": "5m",
}

# Default chunk size for large-file converters
_CHUNK_SIZE = 500_000


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
        self.catalog_path = str(catalog_path)
        self.downloader = VisionDownloader(raw_dir=raw_dir)

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

        # 2. Download + checksum + extract
        await _progress(5, f"Downloading {len(tasks)} file(s)...")
        csv_paths: list[Path] = []
        for i, task in enumerate(tasks):
            pct = 5 + int(70 * i / len(tasks))
            await _progress(pct, f"File {i+1}/{len(tasks)}: {task.zip_path.name}")
            try:
                csv_path = await self.downloader.execute_task(task)
                csv_paths.append(csv_path)
            except Exception as exc:
                logger.warning(
                    "Download failed for %s: %s — skipping", task.url, exc,
                )

        if not csv_paths:
            await _progress(100, "All downloads failed")
            return IngestResult(
                symbol=symbol, data_type=data_type,
                objects_count=0, files_written=0,
                start=start, end=end,
            )

        # 3. Convert + write
        await _progress(78, "Converting data...")
        instrument = self._get_instrument(symbol)
        converter = get_converter(data_type)
        kwargs = self._build_converter_kwargs(
            data_type, symbol, interval, instrument,
        )

        total_objects = 0
        all_file_paths: list[str] = []

        if converter.supports_chunked:
            total_objects, all_file_paths = await self._ingest_chunked(
                csv_paths, converter, instrument, kwargs,
                symbol, data_type, interval, _progress,
            )
        else:
            total_objects, all_file_paths = await self._ingest_full(
                csv_paths, converter, instrument, kwargs,
                symbol, data_type, interval, _progress,
            )

        # 4. REST API fallback for recent data gap
        rest_fallback_used = False
        rest_fallback_range = None

        if data_type in _REST_FALLBACK_TYPES:
            vision_end = self._detect_vision_coverage_end(tasks)
            if vision_end and vision_end < end:
                rest_start = vision_end + timedelta(days=1)
                await _progress(92, f"REST fallback: {rest_start} → {end}")
                try:
                    fb_count, fb_paths = await self._rest_fallback(
                        symbol, data_type, interval, rest_start, end, instrument,
                    )
                    if fb_count > 0:
                        total_objects += fb_count
                        all_file_paths.extend(fb_paths)
                        rest_fallback_used = True
                        rest_fallback_range = (rest_start, end)
                except Exception:
                    logger.warning(
                        "REST fallback failed for %s %s [%s..%s]",
                        symbol, data_type, rest_start, end, exc_info=True,
                    )

        # 5. Update DB catalog
        await _progress(96, "Updating catalog database...")
        # Compute size_bytes from written files
        written_size = sum(
            Path(p).stat().st_size for p in all_file_paths
            if Path(p).exists()
        ) if all_file_paths else None
        try:
            await self._update_db_catalog(
                symbol, data_type, interval, start, end,
                record_count=total_objects if total_objects > 0 else None,
                size_bytes=written_size,
                source_type=data_type,
            )
        except Exception:
            logger.warning("Failed to update DB catalog", exc_info=True)

        await _progress(100, f"Done: {total_objects} objects")

        return IngestResult(
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

    def ingest_sync(self, **kwargs) -> IngestResult:
        """Synchronous wrapper for :meth:`ingest`.

        Use from BacktestRunner subprocesses and other sync contexts.
        Creates a new event loop internally.
        """
        return asyncio.run(self.ingest(**kwargs))

    # ------------------------------------------------------------------
    # Conversion strategies
    # ------------------------------------------------------------------

    async def _ingest_full(
        self,
        csv_paths: list[Path],
        converter,
        instrument,
        kwargs: dict,
        symbol: str,
        data_type: str,
        interval: str | None,
        _progress,
    ) -> tuple[int, list[str]]:
        """Load all CSVs into one DataFrame, convert, write once."""
        dfs = []
        for p in csv_paths:
            try:
                df = pd.read_csv(p, header=None)
                dfs.append(df)
            except Exception:
                logger.warning("Failed to read CSV %s", p, exc_info=True)

        if not dfs:
            return 0, []

        merged = pd.concat(dfs, ignore_index=True)
        del dfs

        converter.validate_schema(merged)
        objects = converter.convert(merged, instrument, **kwargs)
        del merged

        if not objects:
            return 0, []

        await _progress(88, f"Writing {len(objects)} objects to catalog...")
        file_paths = self._write_objects(
            objects, symbol, data_type, interval,
        )
        return len(objects), file_paths

    async def _ingest_chunked(
        self,
        csv_paths: list[Path],
        converter,
        instrument,
        kwargs: dict,
        symbol: str,
        data_type: str,
        interval: str | None,
        _progress,
    ) -> tuple[int, list[str]]:
        """Process large CSVs in chunks to avoid OOM."""
        total_objects = 0
        all_file_paths: list[str] = []
        schema_validated = False

        for csv_idx, csv_path in enumerate(csv_paths):
            try:
                reader = pd.read_csv(csv_path, header=None, chunksize=_CHUNK_SIZE)
            except Exception:
                logger.warning("Failed to open CSV %s", csv_path, exc_info=True)
                continue

            chunk_objects: list = []
            for chunk in reader:
                if not schema_validated:
                    converter.validate_schema(chunk)
                    schema_validated = True

                objs = converter.convert_chunk(chunk, instrument, **kwargs)
                chunk_objects.extend(objs)

                # Flush periodically to control memory
                if len(chunk_objects) >= _CHUNK_SIZE:
                    fps = self._write_objects(
                        chunk_objects, symbol, data_type, interval,
                    )
                    total_objects += len(chunk_objects)
                    all_file_paths.extend(fps)
                    chunk_objects = []

            # Flush remainder
            if chunk_objects:
                fps = self._write_objects(
                    chunk_objects, symbol, data_type, interval,
                )
                total_objects += len(chunk_objects)
                all_file_paths.extend(fps)

            pct = 78 + int(12 * (csv_idx + 1) / len(csv_paths))
            await _progress(pct, f"Converted {csv_idx+1}/{len(csv_paths)} files ({total_objects} objects)")

        return total_objects, all_file_paths

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _write_objects(
        self,
        objects: list,
        symbol: str,
        data_type: str,
        interval: str | None,
    ) -> list[str]:
        """Write converted NT objects to the Parquet catalog."""
        if not objects:
            return []

        category = _WRITE_CATEGORY.get(data_type, "custom")

        if category == "bar":
            from tinohelm.data.catalog import write_bars
            if not interval:
                logger.warning("Cannot write bars without interval")
                return []
            paths = write_bars(
                bars=objects, symbol=symbol,
                interval=interval, catalog_path=self.catalog_path,
            )
            return [str(p) for p in paths]

        elif category == "trade_tick":
            from tinohelm.data.catalog import write_trade_ticks
            paths = write_trade_ticks(
                ticks=objects, symbol=symbol,
                catalog_path=self.catalog_path,
            )
            return paths

        elif category == "funding_rate":
            # Store as JSON in funding_cache format for backward compat
            self._write_funding_rates(objects, symbol)
            return []

        else:
            logger.warning(
                "No catalog writer for data_type=%r (category=%r), skipping write",
                data_type, category,
            )
            return []

    def _write_funding_rates(self, records: list, symbol: str) -> None:
        """Write funding rate records to the local JSON cache."""
        from tinohelm.data.funding_cache import _save_cache

        cache_records = [
            {
                "funding_time_ms": r.funding_time_ms,
                "funding_rate": r.funding_rate,
                "mark_price": 0,  # Not available in Vision data
            }
            for r in records
        ]
        _save_cache(symbol, cache_records)
        logger.info("Wrote %d funding rate records for %s", len(records), symbol)

    # ------------------------------------------------------------------
    # REST API fallback
    # ------------------------------------------------------------------

    def _detect_vision_coverage_end(
        self,
        tasks,
    ) -> date | None:
        """Detect the last date covered by Vision downloads."""
        if not tasks:
            return None

        last_task = tasks[-1]
        # Parse date from the task's date string in dest_path stem
        stem = last_task.dest_path.stem
        # Stems look like: BTCUSDT-klines-1m-2025-03-15 or BTCUSDT-aggTrades-2025-03
        parts = stem.split("-")

        # Try daily format (YYYY-MM-DD at the end)
        if last_task.granularity == "daily" and len(parts) >= 3:
            try:
                date_str = "-".join(parts[-3:])
                return date.fromisoformat(date_str)
            except ValueError:
                pass

        # Try monthly format (YYYY-MM at the end)
        if last_task.granularity == "monthly" and len(parts) >= 2:
            try:
                year = int(parts[-2])
                month = int(parts[-1])
                # Last day of the month
                if month == 12:
                    return date(year + 1, 1, 1) - timedelta(days=1)
                return date(year, month + 1, 1) - timedelta(days=1)
            except (ValueError, IndexError):
                pass

        return None

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
        start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(
            end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc,
        )

        # Klines-family: unified handler with per-type fetch function
        _klines_fetch_map = {
            "klines": "fetch_klines",
            "premiumIndexKlines": "fetch_klines",
            "markPriceKlines": "fetch_mark_price_klines",
            "indexPriceKlines": "fetch_index_price_klines",
        }
        if data_type in _klines_fetch_map:
            return await self._rest_fallback_klines(
                fetch_fn_name=_klines_fetch_map[data_type],
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
        from tinohelm.data import providers
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
            paths = write_bars(bars, symbol, interval, self.catalog_path)
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
            paths = write_trade_ticks(ticks, symbol, self.catalog_path)
            return len(ticks), paths
        return 0, []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
    ) -> None:
        """Upsert a DataCatalog row for the ingested data."""
        from sqlalchemy import select
        from tinohelm.db.models import DataCatalog
        from tinohelm.db.session import get_session_factory

        category = _WRITE_CATEGORY.get(data_type, data_type)
        db_interval = interval if interval else _INTERVAL_CONVENTION.get(data_type, "tick")

        factory = get_session_factory()
        async with factory() as session:
            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == category,
                DataCatalog.interval == db_interval,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.start_date = min(existing.start_date, start)
                existing.end_date = max(existing.end_date, end)
                if record_count is not None:
                    existing.record_count = record_count
                if size_bytes is not None:
                    existing.size_bytes = size_bytes
                if source_type is not None:
                    existing.source_type = source_type
            else:
                session.add(DataCatalog(
                    symbol=symbol,
                    data_type=category,
                    interval=db_interval,
                    start_date=start,
                    end_date=end,
                    file_path=self.catalog_path,
                    size_bytes=size_bytes or 0,
                    record_count=record_count,
                    source_type=source_type,
                ))

            await session.commit()
