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
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

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

        # Early exit: funding-rate JSON cache already covers [start, end].
        # Defense-in-depth — protects every caller (not just BacktestRunner)
        # from re-downloading an already-cached range.
        if data_type == "fundingRate" and self._funding_cache_covers(symbol, start, end):
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
        self._clean_overlapping_parquet(symbol, data_type, interval, start, end)

        # 3. Pipelined download → convert (overlap via asyncio.Queue)
        dl_concurrency = self.downloader.concurrency
        n_converters = self._convert_workers
        sem = asyncio.Semaphore(dl_concurrency)
        total_tasks = len(tasks)
        await _progress(
            DOWNLOAD_PROGRESS_BASE,
            f"Downloading {len(tasks)} file(s) (×{dl_concurrency}, convert ×{n_converters})...",
        )

        csv_queue: asyncio.Queue[CsvSource | None] = asyncio.Queue(maxsize=self._csv_queue_maxsize)
        download_done = 0
        convert_done = 0
        total_objects = 0
        all_file_paths: list[str] = []
        download_failed = 0

        async def _download_one(task):
            nonlocal download_done, download_failed
            async with sem:
                try:
                    csv_path = await self.downloader.execute_task(task)
                    if csv_path:
                        await csv_queue.put(csv_path)
                    else:
                        download_failed += 1
                except Exception as exc:
                    logger.warning(
                        "Download failed for %s: %s — skipping", task.url, exc,
                    )
                    download_failed += 1
                download_done += 1
                await _progress(
                    compute_stage_pct(convert_done, total_tasks),
                    f"下载 {download_done}/{total_tasks}",
                )

        async def _download_all():
            await asyncio.gather(*[_download_one(t) for t in tasks])
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

                try:
                    n, fps = await loop.run_in_executor(
                        None, self._convert_one_file,
                        csv_path, converter, instrument, kwargs,
                        symbol, data_type, interval, schema_validated,
                        _chunk_cb,
                    )
                    schema_validated = True
                    total_objects += n
                    all_file_paths.extend(fps)
                except Exception:
                    logger.warning("Convert failed for %s", csv_name, exc_info=True)
                self._cleanup_raw_file(csv_path)
                convert_done += 1
                pct = compute_stage_pct(convert_done, total_tasks)
                await _progress(
                    pct,
                    f"已完成 {convert_done}/{total_tasks} ({total_objects:,} objects)",
                )

        consumers = [_convert_consumer() for _ in range(n_converters)]
        await asyncio.gather(_download_all(), *consumers)

        if total_objects == 0 and download_failed == total_tasks:
            await _progress(100, "All downloads failed")
            return IngestResult(
                symbol=symbol, data_type=data_type,
                objects_count=0, files_written=0,
                start=start, end=end,
            )

        # 4. REST API fallback for recent data gap
        rest_fallback_used = False
        rest_fallback_range = None

        if is_rest_fallback_supported(data_type):
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
        unique_paths = set(all_file_paths)
        written_size = self._written_file_size(unique_paths) if unique_paths else None
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
        csv_name = self._csv_display_name(csv_path)
        try:
            hdr = self._detect_header(csv_path)
        except Exception:
            logger.warning("Failed to read CSV header %s", csv_name, exc_info=True)
            return 0, []

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
                continue

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
            return 0, []

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
            return 0, []

        if not schema_validated:
            converter.validate_schema(df)

        objects = converter.convert(df, instrument, **kwargs)
        del df

        if not objects:
            return 0, []

        fps = self._write_objects(
            objects, symbol, data_type, interval, merge=False,
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

        category = resolve_write_category(data_type)

        if category == "bar":
            from tinohelm.data.catalog import write_bars
            if not interval:
                logger.warning("Cannot write bars without interval")
                return []
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

        elif category == "funding_rate":
            # Write to both Parquet (primary) and JSON (backward-compat fallback)
            parquet_path = self._write_funding_rates(objects, symbol)
            return [parquet_path] if parquet_path else []

        elif category == "metrics":
            from tinohelm.data.catalog import write_metrics_parquet
            path = write_metrics_parquet(objects, symbol, self.catalog_path, storage=self._storage)
            return [str(path)]

        elif category == "order_book_delta":
            from tinohelm.data.catalog import write_book_depth_parquet
            path = write_book_depth_parquet(objects, symbol, self.catalog_path, storage=self._storage)
            return [str(path)]

        else:
            logger.warning(
                "No catalog writer for data_type=%r (category=%r), skipping write",
                data_type, category,
            )
            return []

    @staticmethod
    def _funding_cache_covers(symbol: str, start: date, end: date) -> bool:
        """Return True iff the JSON funding-rate cache fully spans ``[start, end]``.

        Reuses :func:`funding_cache_helpers.compute_fetch_start` — the same
        decision the ``load_funding_rates`` orchestrator uses. A return value
        of ``None`` from that helper means "no API call needed", which is
        exactly our skip condition.
        """
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
        # Pipeline callers pass ``date``; promote to UTC datetime for the
        # helper (start-of-day, end-of-day).
        start_dt = _dt.combine(start, _time.min, tzinfo=_tz.utc)
        end_dt = _dt.combine(end, _time.max, tzinfo=_tz.utc)
        return compute_fetch_start(cached_times, start=start_dt, end=end_dt) is None

    def _write_funding_rates(self, records: list, symbol: str) -> str | None:
        """Write funding rate records to Parquet (primary) and JSON (fallback cache).

        Returns the Parquet file path string if written, else None.
        """
        from tinohelm.data.funding_cache import _save_cache
        from tinohelm.data.catalog import write_funding_rate_parquet

        cache_records = [
            {
                "funding_time_ms": r.funding_time_ms,
                "funding_rate": r.funding_rate,
                "mark_price": 0,  # Not available in Vision data
            }
            for r in records
        ]
        # JSON write (backward compat — preserves existing deployments)
        _save_cache(symbol, cache_records)

        # Parquet write (new primary path)
        try:
            parquet_path = write_funding_rate_parquet(
                records=records,
                symbol=symbol,
                catalog_root=self.catalog_path,
                storage=self._storage,
            )
            logger.info(
                "Wrote %d funding rate records for %s (Parquet + JSON)",
                len(records), symbol,
            )
            return str(parquet_path)
        except Exception:
            logger.warning(
                "Failed to write funding rates as Parquet for %s — JSON cache written",
                symbol, exc_info=True,
            )
            return None

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
    ) -> None:
        """Delete parquet files whose time range overlaps with [start, end].

        Uses parquet row-group statistics to check each file's timestamp
        range **without loading data into memory**.  Files whose range
        cannot be determined are deleted conservatively.
        Non-overlapping files from other date ranges are preserved.
        """
        category = resolve_write_category(data_type)
        if category == "bar" and interval:
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
        else:
            return  # merged raw datasets clean themselves via their writers

        from tinohelm.data.storage import delete_prefix

        parquet_objects = list(self._storage.iter_files(target_dir, suffix=".parquet", recursive=False))
        if not parquet_objects:
            return

        start_ns = date_start_ns(start)
        end_ns = date_end_ns(end)

        deleted = 0
        for obj in parquet_objects:
            try:
                time_range = self._parquet_time_range(obj, storage=self._storage)
            except TypeError:
                # Backward-compat for tests/callers monkeypatching the old
                # one-argument helper signature.
                time_range = self._parquet_time_range(obj.path)
            if time_range is None:
                # Cannot determine range — delete to be safe (will be re-fetched)
                delete_prefix(self._storage, obj.path)
                deleted += 1
                continue
            file_min, file_max = time_range
            if file_max >= start_ns and file_min < end_ns:
                delete_prefix(self._storage, obj.path)
                deleted += 1

        if deleted:
            logger.info(
                "Cleaned %d overlapping parquet file(s) in %s for [%s, %s]",
                deleted, target_dir, start, end,
            )

    @staticmethod
    def _parquet_time_range(path_or_object, storage=None) -> tuple[int, int] | None:
        """Read min/max timestamp from parquet row-group statistics.

        Returns ``(min_ts_ns, max_ts_ns)`` or ``None`` if unavailable.
        Only reads file metadata — zero row data loaded.  Remote storage objects
        are opened through the provider instead of relying on local ``Path``
        materialization.
        """
        try:
            import pyarrow.parquet as pq

            if storage is not None and getattr(storage, "provider", "local") != "local":
                with storage.open_input_file(path_or_object) as fh:
                    pf = pq.ParquetFile(fh)
                    return BinanceVisionPipeline._parquet_file_time_range(pf)

            path = path_or_object.path if hasattr(path_or_object, "path") else path_or_object
            pf = pq.ParquetFile(str(Path(path)))
            return BinanceVisionPipeline._parquet_file_time_range(pf)
        except Exception:
            return None

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
            return sum(path.stat().st_size for path in unique_paths if path.exists())

        total = 0
        for path in unique_paths:
            for obj in self._storage.iter_files(path, suffix=".parquet", recursive=False):
                total += int(obj.size or 0)
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
            from tinohelm.data.catalog import funding_rate_parquet_path

            return funding_rate_parquet_path(symbol, self.catalog_path)

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

        from tinohelm.data.catalog import resolve_catalog_path

        category = resolve_db_category(data_type)
        db_interval = resolve_db_interval(data_type, interval)
        effective_path = str(resolve_catalog_path(self.catalog_path, source_type))

        factory = get_session_factory()
        async with factory() as session:
            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == category,
                DataCatalog.interval == db_interval,
                DataCatalog.source_type == source_type,
            )
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
                    if write_category in {"metrics", "order_book_delta", "funding_rate"}:
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
                ))

            await session.commit()
