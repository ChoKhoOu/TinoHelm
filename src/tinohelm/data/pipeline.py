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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from tinohelm.data.converters import get_converter
from tinohelm.data.downloader import VisionCsvPayload, VisionDownloader, _KLINES_TYPES
from tinohelm.data.pipeline_helpers import (
    DOWNLOAD_PROGRESS_BASE,
    classify_download_failures,
    compute_chunk_subprogress,
    compute_stage_pct,
    csv_has_header,
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
    partial: bool = False
    last_available_date: date | None = None



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
        self._tick_chunk_rows = max(1, cfg.data.tick_chunk_rows)
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
            Vision data type key (e.g. ``"klines"``, ``"trades"``).
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

        if data_type == "fundingRate":
            from tinohelm.data.catalog import CatalogSession

            _funding_session = CatalogSession(self.catalog_path, storage=self._storage)
            if _funding_session.funding_parquet_covers(symbol, start, end):
                await _progress(100, "Funding rate parquet already covers range")
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

        try:
            await _progress(
                DOWNLOAD_PROGRESS_BASE,
                f"Downloading {len(tasks)} file(s) (×{dl_concurrency}, convert ×{n_converters})...",
            )
        except asyncio.CancelledError:
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
            raise

        is_partial = False
        partial_last_date: date | None = None
        if download_errors or convert_errors:
            # Check if failures qualify as partial completion (#190):
            # Only download-only failures with trailing 404s in UTC last 3 days
            if download_errors and not convert_errors and total_objects > 0:
                task_dates = self._extract_task_dates(tasks)
                classification = classify_download_failures(
                    failed_indices=download_failed_indices,
                    success_indices=download_success_indices,
                    task_dates=task_dates,
                    tolerance_days=3,
                )
                if classification.is_partial:
                    is_partial = True
                    partial_last_date = classification.last_success_date
                    logger.info(
                        "Data-fetch partial completion for %s %s: "
                        "trailing %d day(s) unavailable (within tolerance), "
                        "last available = %s",
                        symbol, data_type,
                        len(classification.tolerated_dates),
                        partial_last_date,
                    )

            if not is_partial:
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
            await _progress(100, "Failed")
            raise RuntimeError(
                f"{symbol} {data_type} converted 0 objects from {len(tasks)} downloaded file(s)"
            )

        # 4. Consolidate + Deduplicate + Organize by period
        try:
            await _progress(90, "Consolidating and deduplicating...")
        except asyncio.CancelledError:
            raise
        try:
            await asyncio.to_thread(
                self._consolidate_catalog_data, symbol, data_type, interval
            )
        except Exception as consolidation_exc:
            logger.error(
                "Consolidation failed for %s %s; data is written but fragmented/duplicated",
                symbol, data_type, exc_info=True,
            )
            await _progress(100, f"Consolidation failed: {consolidation_exc}")
            raise RuntimeError(
                f"Data written successfully but consolidation failed for {symbol} {data_type}: {consolidation_exc}"
            ) from consolidation_exc

        # 5. Gap detection + auto-backfill (runs after consolidation so
        #    file-boundary artifacts don't produce false-positive gaps)
        try:
            await _progress(93, "Checking for data gaps...")
        except asyncio.CancelledError:
            raise
        gap_ranges = await asyncio.to_thread(
            self._detect_gaps_for_backfill, symbol, data_type, interval
        )
        if gap_ranges:
            logger.info(
                "Detected %d gap range(s) for %s %s, triggering backfill",
                len(gap_ranges), symbol, data_type,
            )
            for gap_start, gap_end in gap_ranges:
                gap_tasks = self.downloader.plan_downloads(
                    data_type=data_type,
                    symbol=symbol,
                    asset_class=asset_class,
                    start=gap_start,
                    end=gap_end,
                    interval=interval,
                )
                if not gap_tasks:
                    continue
                for task in gap_tasks:
                    try:
                        csv_path = await self.downloader.execute_task(task)
                    except Exception as dl_exc:
                        logger.warning(
                            "Gap backfill download failed: %s", dl_exc,
                        )
                        continue
                    try:
                        n, fps = self._convert_one_file(
                            csv_path, converter, instrument, kwargs,
                            symbol, data_type, interval, True,
                        )
                        total_objects += n
                    except Exception as conv_exc:
                        logger.warning(
                            "Gap backfill convert failed: %s", conv_exc,
                        )
                    self._cleanup_raw_file(csv_path)

            # Re-consolidate after backfill writes
            try:
                await asyncio.to_thread(
                    self._consolidate_catalog_data, symbol, data_type, interval
                )
            except Exception:
                logger.warning("Post-backfill consolidation failed", exc_info=True)

            remaining_gaps = await asyncio.to_thread(
                self._detect_gaps_for_backfill, symbol, data_type, interval
            )
            if remaining_gaps:
                logger.warning(
                    "%d gap(s) remain after backfill for %s %s (source data may be missing)",
                    len(remaining_gaps), symbol, data_type,
                )

        # 6. Update DB catalog
        try:
            await _progress(96, "Updating catalog database...")
        except asyncio.CancelledError:
            raise
        try:
            record_count_from_disk, written_size = self._catalog_storage_stats(
                symbol, data_type, interval, data_type
            )
        except Exception:
            logger.warning("Failed to stat consolidated catalog files", exc_info=True)
            written_size = None
        record_count = total_objects if total_objects > 0 else None
        ingest_run_id = uuid4().hex
        post_commit_was_cancelled = False
        catalog_end = partial_last_date if is_partial and partial_last_date else end
        update_task = asyncio.create_task(self._update_db_catalog(
            symbol, data_type, interval, start, catalog_end,
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
                await _progress(100, "Failed")
                logger.exception("DB catalog update was cancelled before commit")
                raise
            except Exception:
                await _progress(100, "Failed")
                logger.exception("Failed to update DB catalog")
                raise
        except Exception:
            await _progress(100, "Failed")
            logger.exception("Failed to update DB catalog")
            raise

        if post_commit_was_cancelled:
            logger.info(
                "Cancellation arrived after DB catalog commit for %s %s; treating committed ingest as success",
                symbol,
                data_type,
            )

        try:
            if is_partial:
                await _progress(100, f"Partial: {total_objects} objects (tail unavailable, last={partial_last_date})")
            else:
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

        effective_end = partial_last_date if is_partial and partial_last_date else end
        result = IngestResult(
            symbol=symbol,
            data_type=data_type,
            objects_count=total_objects,
            files_written=len(all_file_paths),
            file_paths=all_file_paths,
            start=start,
            end=effective_end,
            partial=is_partial,
            last_available_date=partial_last_date if is_partial else None,
        )
        if post_commit_was_cancelled:
            _recancel_current_task()
        return result



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
        if data_type == "trades":
            return self._tick_chunk_rows
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

        else:
            raise RuntimeError(
                f"No catalog writer for data_type={data_type!r} (category={category!r})"
            )



    def _resolve_data_cls_and_identifier(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
    ) -> tuple[type, str] | None:
        """Resolve the NT data class and catalog identifier for a data type.

        Returns None for unsupported/custom categories.
        """
        from tinohelm.data.pipeline_helpers import resolve_write_category

        category = resolve_write_category(data_type)
        if category == "custom":
            return None

        if category == "bar":
            from nautilus_trader.model.data import Bar
            from tinohelm.data.catalog import _make_bar_type, _make_instrument

            inst = _make_instrument(symbol)
            bar_type = _make_bar_type(inst.id, interval)
            return (Bar, str(bar_type))
        elif category == "trade_tick":
            from nautilus_trader.model.data import TradeTick
            from tinohelm.strategy.loader_helpers import normalize_symbol

            return (TradeTick, normalize_symbol(symbol))
        elif category == "quote_tick":
            from nautilus_trader.model.data import QuoteTick
            from tinohelm.strategy.loader_helpers import normalize_symbol

            return (QuoteTick, normalize_symbol(symbol))
        elif category == "mark_price":
            from nautilus_trader.model.data import MarkPriceUpdate
            from tinohelm.strategy.loader_helpers import normalize_symbol

            return (MarkPriceUpdate, normalize_symbol(symbol))
        elif category == "index_price":
            from nautilus_trader.model.data import IndexPriceUpdate
            from tinohelm.strategy.loader_helpers import normalize_symbol

            return (IndexPriceUpdate, normalize_symbol(symbol))
        elif category == "funding_rate":
            from nautilus_trader.model.data import FundingRateUpdate
            from tinohelm.strategy.loader_helpers import normalize_symbol

            return (FundingRateUpdate, normalize_symbol(symbol))
        return None

    def _detect_gaps_for_backfill(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
    ) -> list[tuple[date, date]]:
        """Detect gaps in the catalog and return day-aligned ranges for backfill.

        Returns an empty list when data is contiguous or the data type is unsupported.
        """
        from tinohelm.data.catalog import _catalog_for_root
        from tinohelm.data.pipeline_helpers import detect_gaps, expand_gaps_to_days

        resolved = self._resolve_data_cls_and_identifier(symbol, data_type, interval)
        if resolved is None:
            return []

        data_cls, identifier = resolved
        catalog = _catalog_for_root(self.catalog_path, self._storage)
        intervals = catalog.get_intervals(data_cls, identifier)
        if not intervals:
            return []

        gaps = detect_gaps(intervals)
        if not gaps:
            return []

        return expand_gaps_to_days(gaps)

    def _consolidate_catalog_data(
        self,
        symbol: str,
        data_type: str,
        interval: str | None,
    ) -> None:
        """Run NT-native consolidation after streaming writes complete.

        All NT-native data types (bar, trade_tick, quote_tick, mark_price,
        index_price, funding_rate) are consolidated.
        """
        from tinohelm.data.catalog import _catalog_for_root, consolidate_and_organize

        resolved = self._resolve_data_cls_and_identifier(symbol, data_type, interval)
        if resolved is None:
            return

        data_cls, identifier = resolved
        catalog = _catalog_for_root(self.catalog_path, self._storage)
        consolidate_and_organize(catalog, data_cls, identifier)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    @staticmethod
    def _extract_task_dates(tasks) -> list[date | None]:
        """Extract the coverage date from each DownloadTask for failure classification."""
        from tinohelm.data.pipeline_helpers import parse_vision_coverage_end

        dates: list[date | None] = []
        for task in tasks:
            stem = task.dest_path.stem if hasattr(task, "dest_path") else ""
            d = parse_vision_coverage_end(task.granularity, stem)
            dates.append(d)
        return dates

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
                        write_category == "funding_rate"
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
