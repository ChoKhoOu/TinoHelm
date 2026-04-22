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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from tinohelm.data.converters import get_converter
from tinohelm.data.downloader import VisionDownloader, _KLINES_TYPES
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

# Backwards-compat aliases (kept so external imports keep working):
# ``api/routes/data.py`` historically reaches into ``_WRITE_CATEGORY``;
# the canonical name is now :data:`WRITE_CATEGORY` exported from
# ``pipeline_helpers``.
_REST_FALLBACK_TYPES = REST_FALLBACK_TYPES
_WRITE_CATEGORY = WRITE_CATEGORY
_INTERVAL_CONVENTION = INTERVAL_CONVENTION

# Default chunk size for large-file converters
_CHUNK_SIZE = 10_000_000


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
        cfg = get_settings()
        self.catalog_path = str(catalog_path)
        self.downloader = VisionDownloader(
            raw_dir=raw_dir, concurrency=cfg.data.download_concurrency,
        )
        self._convert_workers = cfg.data.convert_workers

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

        csv_queue: asyncio.Queue[Path | None] = asyncio.Queue()
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

                # Thread-safe chunk callback: interpolates within current file's range
                _cd = convert_done  # snapshot before executor starts
                def _chunk_cb(objects_so_far):
                    chunks = max(1, objects_so_far // _CHUNK_SIZE)
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
                    logger.warning("Convert failed for %s", csv_path, exc_info=True)
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
        written_size = sum(
            Path(p).stat().st_size for p in unique_paths
            if Path(p).exists()
        ) if unique_paths else None
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
    def _detect_header(path: Path) -> int | None:
        """Return 0 if the CSV has a text header row, else None (no header).

        Thin I/O wrapper around :func:`pipeline_helpers.csv_has_header`.
        """
        with open(path) as f:
            first = f.readline()
        return 0 if csv_has_header(first) else None

    def _convert_one_file(
        self, csv_path, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
        chunk_cb=None,
    ) -> tuple[int, list[str]]:
        """Convert a single CSV file (sync, runs in executor thread)."""
        try:
            hdr = self._detect_header(csv_path)
        except Exception:
            logger.warning("Failed to read CSV header %s", csv_path, exc_info=True)
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
        csv_paths: list[Path],
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
            try:
                hdr = self._detect_header(csv_path)
            except Exception:
                logger.warning("Failed to read CSV header %s", csv_path, exc_info=True)
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
        self, csv_path, hdr, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
        chunk_cb=None,
    ) -> tuple[int, list[str]]:
        """Read one CSV in chunks, convert, flush periodically."""
        try:
            reader = pd.read_csv(csv_path, header=hdr, chunksize=_CHUNK_SIZE)
        except Exception:
            logger.warning("Failed to open CSV %s", csv_path, exc_info=True)
            return 0, []

        count = 0
        fps: list[str] = []
        chunk_objects: list = []

        for chunk in reader:
            if hdr is None:
                chunk.columns = range(len(chunk.columns))
            if not schema_validated:
                converter.validate_schema(chunk)
                schema_validated = True

            objs = converter.convert_chunk(chunk, instrument, **kwargs)
            chunk_objects.extend(objs)

            if len(chunk_objects) >= _CHUNK_SIZE:
                fp = self._write_objects(
                    chunk_objects, symbol, data_type, interval, merge=False,
                )
                count += len(chunk_objects)
                fps.extend(fp)
                chunk_objects = []
                if chunk_cb:
                    chunk_cb(count)

        if chunk_objects:
            fp = self._write_objects(
                chunk_objects, symbol, data_type, interval, merge=False,
            )
            count += len(chunk_objects)
            fps.extend(fp)
            if chunk_cb:
                chunk_cb(count)

        return count, fps

    def _stream_full_file(
        self, csv_path, hdr, converter, instrument, kwargs,
        symbol, data_type, interval, schema_validated,
    ) -> tuple[int, list[str]]:
        """Read one full CSV, convert, write."""
        try:
            df = pd.read_csv(csv_path, header=hdr)
            if hdr is None:
                df.columns = range(len(df.columns))
        except Exception:
            logger.warning("Failed to read CSV %s", csv_path, exc_info=True)
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
            )
            return [str(p) for p in paths]

        elif category == "trade_tick":
            from tinohelm.data.catalog import write_trade_ticks
            paths = write_trade_ticks(
                ticks=objects, symbol=symbol,
                catalog_path=self.catalog_path,
                source_type=data_type,
            )
            return paths

        elif category == "funding_rate":
            # Write to both Parquet (primary) and JSON (backward-compat fallback)
            parquet_path = self._write_funding_rates(objects, symbol)
            return [parquet_path] if parquet_path else []

        else:
            logger.warning(
                "No catalog writer for data_type=%r (category=%r), skipping write",
                data_type, category,
            )
            return []

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
            paths = write_bars(bars, symbol, interval, self.catalog_path, merge=False, source_type=data_type)
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
            paths = write_trade_ticks(ticks, symbol, self.catalog_path, source_type="aggTrades")
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
        elif category == "trade_tick":
            from tinohelm.data.catalog import resolve_catalog_path
            inst = self._get_instrument(symbol)
            resolved = resolve_catalog_path(self.catalog_path, data_type)
            target_dir = Path(resolved) / "data" / "trade_tick" / str(inst.id)
        else:
            return  # funding_rate etc. use JSON, no parquet cleanup needed

        if not target_dir.exists():
            return

        start_ns = date_start_ns(start)
        end_ns = date_end_ns(end)

        deleted = 0
        for fpath in list(target_dir.glob("*.parquet")):
            time_range = self._parquet_time_range(fpath)
            if time_range is None:
                # Cannot determine range — delete to be safe (will be re-fetched)
                fpath.unlink()
                deleted += 1
                continue
            file_min, file_max = time_range
            if file_max >= start_ns and file_min < end_ns:
                fpath.unlink()
                deleted += 1

        if deleted:
            logger.info(
                "Cleaned %d overlapping parquet file(s) in %s for [%s, %s]",
                deleted, target_dir, start, end,
            )

    @staticmethod
    def _parquet_time_range(path: Path) -> tuple[int, int] | None:
        """Read min/max timestamp from parquet row-group statistics.

        Returns ``(min_ts_ns, max_ts_ns)`` or ``None`` if unavailable.
        Only reads file metadata — zero row data loaded.
        """
        try:
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(str(path))
            schema = pf.schema_arrow
            col_idx = None
            for name in ("ts_event", "ts_init"):
                try:
                    col_idx = schema.get_field_index(name)
                    break
                except KeyError:
                    continue
            if col_idx is None:
                return None

            min_ts: int | None = None
            max_ts: int | None = None
            for i in range(pf.metadata.num_row_groups):
                stats = pf.metadata.row_group(i).column(col_idx).statistics
                if stats is None or not stats.has_min_max:
                    return None  # incomplete statistics
                if min_ts is None or stats.min < min_ts:
                    min_ts = stats.min
                if max_ts is None or stats.max > max_ts:
                    max_ts = stats.max

            return (int(min_ts), int(max_ts)) if min_ts is not None else None
        except Exception:
            return None

    @staticmethod
    def _cleanup_raw_file(csv_path: Path) -> None:
        """Remove processed CSV and its ZIP to free disk space."""
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
                existing.start_date = min(existing.start_date, start)
                existing.end_date = max(existing.end_date, end)
                existing.file_path = effective_path
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
                    file_path=effective_path,
                    size_bytes=size_bytes or 0,
                    record_count=record_count,
                    source_type=source_type,
                ))

            await session.commit()
