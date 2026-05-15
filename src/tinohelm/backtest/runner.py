"""Backtest runner wrapping NautilusTrader BacktestEngine."""
from __future__ import annotations

import json
from bisect import bisect_right
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

try:
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.backtest.models import FillModel, LatencyModel
    from nautilus_trader.common.actor import Actor, ActorConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model import TraderId
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    _NT_AVAILABLE = True
    _NT_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    BacktestEngine = BacktestEngineConfig = FillModel = LatencyModel = None  # type: ignore[assignment]
    Actor = ActorConfig = LoggingConfig = TraderId = AccountType = OmsType = ParquetDataCatalog = None  # type: ignore[assignment]
    _NT_AVAILABLE = False
    _NT_IMPORT_ERROR = exc

from tinohelm.backtest.runner_helpers import (
    TIMEFRAME_PRIORITY as _TIMEFRAME_PRIORITY_TUPLE,
    assemble_funding_events,
    build_composite_bar_type_str,
    build_progress_payload,
    candidate_source_intervals,
    compute_bar_progress_fields,
    compute_warmup_adjusted_start,
    extract_benchmark_daily_closes,
    interval_to_minutes as _interval_to_minutes,
    resolve_symbols_intervals,
)
from tinohelm.strategy.loader import (
    create_strategies,
    create_actors,
    normalize_symbol as _normalize_symbol,
    make_bar_type_str as _make_bar_type_str,
    INTERVAL_MAP as _INTERVAL_MAP,
)

logger = logging.getLogger(__name__)


def _require_nt() -> None:
    if not _NT_AVAILABLE:
        raise RuntimeError("nautilus_trader is required for backtest execution") from _NT_IMPORT_ERROR


def _ordered_unique_extra(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


# ---------------------------------------------------------------------------
# Progress reporter — lightweight actor for bar-level progress tracking
# ---------------------------------------------------------------------------

if _NT_AVAILABLE:

    class _ProgressReporterConfig(ActorConfig, frozen=True):
        component_id: str = "ProgressReporter-001"


    class _ProgressReporter(Actor):
        """Counts processed bars and periodically writes progress to Redis.

        Instance attributes are set directly on the actor object by
        BacktestRunner between add_actor() and engine.run().
        """

        def on_start(self) -> None:
            from nautilus_trader.model.data import BarType

            self._bar_count = 0
            self._start_time = time.monotonic()
            # Subscribe to explicitly provided bar types (cache may be empty at this point)
            for bt_str in getattr(self, "_bar_type_strs", []):
                try:
                    self.subscribe_bars(BarType.from_str(bt_str))
                except Exception:
                    pass

        def on_bar(self, bar) -> None:
            self._bar_count += 1
            report_every = getattr(self, "_report_every", 2000)
            redis_client = getattr(self, "_redis", None)
            total_bars = getattr(self, "_total_bars", 0)
            run_id = getattr(self, "_run_id", "")

            if (
                self._bar_count % report_every == 0
                and redis_client is not None
                and total_bars > 0
            ):
                elapsed = time.monotonic() - self._start_time
                fields = compute_bar_progress_fields(
                    self._bar_count, total_bars, elapsed,
                )
                payload = build_progress_payload(
                    run_id,
                    pct=fields["pct"],
                    elapsed_secs=fields["elapsed_secs"],
                    eta_secs=fields["eta_secs"],
                    total_bars=total_bars,
                    processed_bars=self._bar_count,
                    bars_per_sec=fields["bars_per_sec"],
                )
                try:
                    redis_client.setex(
                        f"tino:backtest:progress:{run_id}", 86400, str(fields["pct"]),
                    )
                    redis_client.publish(
                        f"tino:backtest:progress:{run_id}", json.dumps(payload),
                    )
                except Exception:
                    pass  # Never let Redis errors crash the backtest

else:

    @dataclass(frozen=True)
    class _ProgressReporterConfig:
        component_id: str = "ProgressReporter-001"


    class _ProgressReporter:
        """Fallback stub used when NautilusTrader is not installed."""

        def __init__(self, config: _ProgressReporterConfig | None = None) -> None:
            self.config = config or _ProgressReporterConfig()

        def on_start(self) -> None:
            return None

        def on_bar(self, bar) -> None:
            return None


class BacktestRunner:
    """Runs a backtest using NautilusTrader BacktestEngine.

    Supports both:
    - Bundle mode: pass a ``StrategyBundle`` directly
    - Legacy mode: pass strategy_path/config_path/symbol/interval (auto-wrapped)
    """

    def __init__(
        self,
        strategy_path: str = "",
        config_path: str = "",
        strategy_params: dict[str, Any] | None = None,
        catalog_path: str | Path = "",
        symbol: str | list[str] | None = None,
        interval: str | list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        symbols: list[str] | None = None,
        intervals: list[str] | None = None,
        fill_model: dict | None = None,
        strategy_bundle: Any | None = None,
        maker_fee: str | None = None,
        taker_fee: str | None = None,
        warmup_bars: int | None = None,
        tags: str | None = None,
        data_type: str = "klines",
        extra_data_types: list[str] | None = None,
    ) -> None:
        self.strategy_path = strategy_path
        self.config_path = config_path
        self.strategy_params = strategy_params or {}
        from tinohelm.data.storage import get_catalog_storage
        from tinohelm.core.config import get_settings

        requested_catalog_path = Path(catalog_path) if catalog_path else None
        self._storage = get_catalog_storage(catalog_root=requested_catalog_path)
        self.catalog_path = self._storage.catalog_root
        cfg = get_settings()
        self.streaming_enabled = bool(cfg.backtest.streaming_enabled)
        self.stream_batch_size = int(cfg.backtest.stream_batch_size)
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        # Multi-instrument support: accept symbol (str/list) or symbols (list)
        if symbols is not None:
            self.symbols = symbols
        elif isinstance(symbol, list):
            self.symbols = symbol
        elif isinstance(symbol, str):
            self.symbols = [symbol]
        else:
            self.symbols = []

        # Multi-timeframe support: accept interval (str/list) or intervals (list)
        if intervals is not None:
            self.intervals = intervals
        elif isinstance(interval, list):
            self.intervals = interval
        elif isinstance(interval, str):
            self.intervals = [interval]
        else:
            self.intervals = []

        self.symbol = self.symbols[0] if self.symbols else ""
        self.interval = self.intervals[0] if self.intervals else ""

        self.start = start
        self.end = end
        self.fill_model_config = fill_model
        self.warmup_bars = warmup_bars
        self.tags = tags
        self.data_type = data_type
        self.extra_data_types = list(extra_data_types or [])
        self.artifacts_dir: Path | None = None
        self._before_artifact_export: Callable[[], None] | None = None
        self._engine: BacktestEngine | None = None
        self._redis_client = None  # sync Redis for progress reporting
        self._run_id: str = ""
        self._job_start_time: float = 0.0  # set by worker for elapsed tracking
        self._catalog_cache: dict[str, ParquetDataCatalog] = {}

        # Strategy bundle (explicit or auto-wrapped from legacy params)
        self._strategy_bundle = strategy_bundle

    # Timeframes ordered from lowest to highest for composite source resolution.
    # Sourced from ``runner_helpers.TIMEFRAME_PRIORITY`` (single source of truth).
    _TIMEFRAME_PRIORITY: list[str] = list(_TIMEFRAME_PRIORITY_TUPLE)

    def _catalog_uri_for_root(self, logical_root: Path | str) -> str:
        uri_for_root = getattr(self._storage, "uri_for_catalog_root", None)
        if callable(uri_for_root):
            return str(uri_for_root(logical_root))
        return str(logical_root)

    def _catalog_fs_storage_options(self) -> dict[str, Any] | None:
        return getattr(self._storage, "fs_storage_options", None)

    def _catalog_fs_rust_storage_options(self) -> dict[str, str] | None:
        return getattr(self._storage, "fs_rust_storage_options", None)

    def _catalog_for_path(self, path: str | Path) -> ParquetDataCatalog:
        """Return a cached ParquetDataCatalog for the runner lifetime."""
        _require_nt()
        from tinohelm.data.catalog import _ensure_nt_update_query_support

        _ensure_nt_update_query_support()
        uri = self._catalog_uri_for_root(path)
        key = json.dumps(
            {
                "uri": uri,
                "fs_storage_options": self._catalog_fs_storage_options() or {},
                "fs_rust_storage_options": self._catalog_fs_rust_storage_options() or {},
            },
            sort_keys=True,
            default=str,
        )
        catalog = self._catalog_cache.get(key)
        if catalog is None:
            if uri.startswith(("s3://", "gcs://", "abfs://", "az://")):
                from tinohelm.data.catalog import _remote_catalog_constructor_args

                remote_path, fs_protocol = _remote_catalog_constructor_args(
                    Path(path), self._storage,
                )
                catalog = ParquetDataCatalog(
                    remote_path,
                    fs_protocol=fs_protocol,
                    fs_storage_options=self._catalog_fs_storage_options(),
                    fs_rust_storage_options=self._catalog_fs_rust_storage_options(),
                )
            else:
                catalog = ParquetDataCatalog(uri)
            self._catalog_cache[key] = catalog
        return catalog

    def _logical_root_for_source(self, source_type: str) -> Path:
        return Path(self.catalog_path)

    def _catalog_for_logical_root(self, logical_root: Path) -> ParquetDataCatalog:
        return self._catalog_for_path(logical_root)

    def _find_bar_catalog_location(self, bar_type_str: str, source_type: str) -> tuple[Path, Path] | None:
        """Return the first catalog root containing parquet files for a bar type."""
        logical_root = self._logical_root_for_source(source_type)

        roots = [logical_root, self.catalog_path]
        seen: set[Path] = set()
        for root in roots:
            if root in seen:
                continue
            seen.add(root)
            bar_dir = root / "data" / "bar" / bar_type_str
            objects = list(self._storage.iter_files(bar_dir, suffix=".parquet"))
            if objects:
                return root, bar_dir
        return None

    def _count_parquet_rows(self, parquet_dir: Path) -> int:
        """Count rows from parquet metadata without loading row data."""
        try:
            import pyarrow.parquet as pq
        except Exception:
            return 0
        total = 0
        for obj in self._storage.iter_files(parquet_dir, suffix=".parquet"):
            try:
                with self._storage.open_input_file(obj) as fh:
                    total += int(pq.ParquetFile(fh, pre_buffer=False).metadata.num_rows)
            except Exception:
                continue
        return total

    def _bar_stream_windows(self, interval: str):
        """Yield half-open query windows sized by configured bar batch size."""
        if self.start is None or self.end is None:
            yield self.start, self.end
            return
        interval_minutes = max(1, _interval_to_minutes(interval) or 1)
        batch_size = max(1, int(self.stream_batch_size))
        step = timedelta(minutes=interval_minutes * batch_size)
        cur = self.start
        while cur < self.end:
            nxt = min(self.end, cur + step)
            yield cur, nxt
            if nxt <= cur:
                break
            cur = nxt

    def _bar_data_iterator(
        self,
        catalog_path: Path,
        bar_type_str: str,
        interval: str,
        *,
        benchmark_daily_closes: dict[str, float] | None = None,
    ):
        """Yield NT Bar batches from ParquetDataCatalog without holding the full range."""
        catalog = self._catalog_for_path(catalog_path)
        last_ts_init: int | None = None
        for start, end in self._bar_stream_windows(interval):
            try:
                batch = catalog.bars(bar_types=[bar_type_str], start=start, end=end) or []
            except Exception:
                logger.warning(
                    "Failed to stream bar batch %s [%s, %s)",
                    bar_type_str, start, end, exc_info=True,
                )
                continue
            if last_ts_init is not None:
                batch = [bar for bar in batch if int(getattr(bar, "ts_init", 0)) > last_ts_init]
            if not batch:
                continue
            last_ts_init = int(getattr(batch[-1], "ts_init", 0))
            if benchmark_daily_closes is not None:
                benchmark_daily_closes.update(
                    extract_benchmark_daily_closes(
                        (int(bar.ts_init), float(bar.close)) for bar in batch
                    )
                )
            yield batch

    def _resolve_bar_stream(
        self,
        sym: str,
        nt_sym: str,
        ivl: str,
        *,
        benchmark_daily_closes: dict[str, float] | None = None,
    ) -> tuple[Any | None, str, str | None, int]:
        """Resolve an engine data iterator for one symbol/interval without full materialization."""
        bar_type_str = _make_bar_type_str(sym, ivl)
        location = self._find_bar_catalog_location(bar_type_str, self.data_type)
        if location is not None:
            root, bar_dir = location
            return (
                self._bar_data_iterator(
                    root,
                    bar_type_str,
                    ivl,
                    benchmark_daily_closes=benchmark_daily_closes,
                ),
                bar_type_str,
                None,
                self._count_parquet_rows(bar_dir),
            )

        for candidate in candidate_source_intervals(ivl):
            candidate_bt = _make_bar_type_str(sym, candidate)
            location = self._find_bar_catalog_location(candidate_bt, self.data_type)
            if location is None:
                continue
            root, bar_dir = location
            composite_bt_str = build_composite_bar_type_str(nt_sym, candidate, ivl, _INTERVAL_MAP)
            return (
                self._bar_data_iterator(
                    root,
                    candidate_bt,
                    candidate,
                    benchmark_daily_closes=benchmark_daily_closes,
                ),
                composite_bt_str,
                candidate_bt,
                self._count_parquet_rows(bar_dir),
            )

        return None, bar_type_str, None, 0

    def _invalidate_catalog_cache_for_source(self, source_type: str) -> None:
        """Drop cached catalog handles that may be stale after a data fetch."""
        self._catalog_cache.clear()

    @staticmethod
    def _normalize_extra_data_type(data_type: str) -> str | None:
        token = str(data_type).strip()
        mapping = {
            "bookTicker": "bookTicker",
            "quote_tick": "bookTicker",
            "trades": "trades",
            "trade_tick": "trades",
        }
        return mapping.get(token)

    def _load_replay_data_from_catalog(self, symbol: str, source_type: str) -> list:
        """Cache-first load optional QuoteTick/TradeTick replay data."""
        from tinohelm.strategy.loader_helpers import normalize_symbol

        logical_root = self._logical_root_for_source(source_type)
        catalog = self._catalog_for_logical_root(logical_root)
        instrument_id = normalize_symbol(symbol)
        start = self.start
        end = self.end
        try:
            if source_type == "bookTicker":
                return catalog.quote_ticks(instrument_ids=[instrument_id], start=start, end=end) or []
            return catalog.trade_ticks(instrument_ids=[instrument_id], start=start, end=end) or []
        except Exception:
            logger.warning("Optional replay load failed for %s %s", symbol, source_type, exc_info=True)
            return []

    def _load_or_fetch_replay_data(self, symbol: str, source_type: str) -> list:
        """Load optional replay ticks, enqueueing a fetch once when possible."""
        ticks = self._load_replay_data_from_catalog(symbol, source_type)
        if ticks:
            return ticks
        if self._redis_client is None:
            logger.info("Optional replay data missing for %s %s; Redis unavailable, skipping", symbol, source_type)
            return []
        try:
            import asyncio
            success = asyncio.run(self._submit_and_wait_fetch(symbol, None, source_type))
        except RuntimeError:
            logger.warning("Optional replay fetch skipped for %s %s inside running event loop", symbol, source_type)
            return []
        except Exception:
            logger.warning("Optional replay fetch failed for %s %s", symbol, source_type, exc_info=True)
            return []
        if not success:
            return []
        self._invalidate_catalog_cache_for_source(source_type)
        return self._load_replay_data_from_catalog(symbol, source_type)

    async def _load_or_fetch_replay_data_async(self, symbol: str, source_type: str) -> list:
        """Async version used from BacktestRunner.run()."""
        ticks = self._load_replay_data_from_catalog(symbol, source_type)
        if ticks:
            return ticks
        if self._redis_client is None:
            logger.info("Optional replay data missing for %s %s; Redis unavailable, skipping", symbol, source_type)
            return []
        try:
            success = await self._submit_and_wait_fetch(symbol, None, source_type)
        except Exception:
            logger.warning("Optional replay fetch failed for %s %s", symbol, source_type, exc_info=True)
            return []
        if not success:
            return []
        self._invalidate_catalog_cache_for_source(source_type)
        return self._load_replay_data_from_catalog(symbol, source_type)

    def _inject_optional_replay_data(self, engine: BacktestEngine) -> None:
        """Inject requested quote/trade ticks without changing bar-only defaults."""
        if not self.extra_data_types:
            return
        source_types = _ordered_unique_extra(
            normalized for item in self.extra_data_types
            if (normalized := self._normalize_extra_data_type(item)) is not None
        )
        for symbol in self.symbols:
            for source_type in source_types:
                ticks = self._load_or_fetch_replay_data(symbol, source_type)
                if not ticks:
                    logger.info("No optional replay data for %s %s; continuing bar backtest", symbol, source_type)
                    continue
                engine.add_data(ticks, sort=False)
                logger.info("Injected %d optional replay ticks for %s %s", len(ticks), symbol, source_type)

    async def _inject_optional_replay_data_async(self, engine: BacktestEngine) -> None:
        if not self.extra_data_types:
            return
        source_types = _ordered_unique_extra(
            normalized for item in self.extra_data_types
            if (normalized := self._normalize_extra_data_type(item)) is not None
        )
        for symbol in self.symbols:
            for source_type in source_types:
                ticks = await self._load_or_fetch_replay_data_async(symbol, source_type)
                if not ticks:
                    logger.info("No optional replay data for %s %s; continuing bar backtest", symbol, source_type)
                    continue
                engine.add_data(ticks, sort=False)
                logger.info("Injected %d optional replay ticks for %s %s", len(ticks), symbol, source_type)

    def _try_load_bars(self, bar_type_str: str, source_type: str = "klines") -> list | None:
        """Load bars from the resolved catalog path for a specific source_type.

        Different kline types (klines, markPriceKlines, indexPriceKlines,
        premiumIndexKlines) are distinct datasets and must NOT be mixed.
        """
        logical_root = self._logical_root_for_source(source_type)
        try:
            cat = self._catalog_for_path(logical_root)
            bars = cat.bars(bar_types=[bar_type_str], start=self.start, end=self.end)
            if bars:
                return bars
        except Exception:
            pass

        return None

    async def _resolve_bars(
        self,
        catalog: ParquetDataCatalog,
        sym: str,
        nt_sym: str,
        ivl: str,
    ) -> tuple[list | None, str, str | None]:
        """Resolve bar data for a (symbol, interval) pair.

        Strategy:
          1. Try loading target interval from all catalog paths.
          2. Find the lowest available timeframe below target for composite aggregation.
          3. If nothing local, fetch via data job queue and wait for completion.

        Returns:
            (bars, bar_type_str, source_bar_type_str_or_None)
            - bars: list of NT Bar objects, or None if all attempts fail
            - bar_type_str: the bar type string the strategy should subscribe to
            - source_bar_type_str: the actually loaded source bar type (for progress), None if direct
        """
        # 1. Direct load: target interval exists in catalog
        bar_type_str = _make_bar_type_str(sym, ivl)
        bars = self._try_load_bars(bar_type_str, source_type=self.data_type)
        if bars:
            logger.info("Loaded %d %s bars for %s directly", len(bars), ivl, sym)
            return bars, bar_type_str, None

        # 2. Composite: find lowest available source timeframe
        source_ivl: str | None = None
        source_bars = None
        for candidate in candidate_source_intervals(ivl):
            candidate_bt = _make_bar_type_str(sym, candidate)
            source_bars = self._try_load_bars(candidate_bt, source_type=self.data_type)
            if source_bars:
                source_ivl = candidate
                break

        if source_bars and source_ivl:
            source_bt_str = _make_bar_type_str(sym, source_ivl)
            composite_bt_str = build_composite_bar_type_str(
                nt_sym, source_ivl, ivl, _INTERVAL_MAP,
            )
            logger.info(
                "No %s data for %s, using %s composite aggregation (%d bars): %s",
                ivl, sym, source_ivl, len(source_bars), composite_bt_str,
            )
            return source_bars, composite_bt_str, source_bt_str

        # 3. Fetch via data job queue
        logger.info("No local data for %s %s, submitting fetch job...", sym, ivl)
        bars = await self._download_bars(sym, ivl)
        if bars:
            logger.info("Fetched and loaded %d %s bars for %s", len(bars), ivl, sym)
            return bars, _make_bar_type_str(sym, ivl), None

        return None, bar_type_str, None

    async def _download_bars(
        self,
        sym: str,
        ivl: str,
    ) -> list | None:
        """Fetch bars via DataFetchJob queue and wait for completion.

        Creates a DataFetchJob record visible in the frontend,
        enqueues it to the data worker, and blocks until done.
        Returns the loaded Bar objects, or None on failure.
        """
        success = await self._submit_and_wait_fetch(sym, ivl)

        if not success:
            return None

        # Reload from catalog using the correct source_type path
        self._invalidate_catalog_cache_for_source(self.data_type)
        bar_type_str = _make_bar_type_str(sym, ivl)
        return self._try_load_bars(bar_type_str, source_type=self.data_type)

    async def _submit_and_wait_fetch(
        self,
        sym: str,
        ivl: str | None = None,
        data_type: str | None = None,
        *,
        start_override: datetime | None = None,
    ) -> bool:
        """Create a DataFetchJob, enqueue to Redis, and poll until done.

        ``start_override`` lets callers narrow the fetch window to only the
        uncovered tail (e.g. funding-rate incremental update) rather than
        re-downloading the full ``[self.start, self.end]`` range.
        """
        import uuid
        import asyncio as _aio
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from tinohelm.db.models import DataFetchJob
        from tinohelm.core.config import get_settings

        # Create a fresh engine for this event loop — the global singleton
        # from the parent process is bound to a different loop after fork.
        settings = get_settings()
        engine = create_async_engine(settings.database.url, echo=False, pool_size=2)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        effective_data_type = data_type or self.data_type
        job_id = str(uuid.uuid4())
        # A backtest-triggered fetch is its own single-job FetchBatch, so we
        # mint a fresh batch_id per call rather than reusing job_id.
        batch_id = str(uuid.uuid4())
        effective_start = start_override if start_override is not None else self.start
        start_date = effective_start.date() if isinstance(effective_start, datetime) else effective_start
        end_date = self.end.date() if isinstance(self.end, datetime) else self.end

        try:
            # Create job in DB
            async with factory() as session:
                session.add(DataFetchJob(
                    job_id=job_id,
                    batch_id=batch_id,
                    symbol=sym,
                    data_type=effective_data_type,
                    interval=ivl,
                    start_date=start_date,
                    end_date=end_date,
                    asset_class="um",
                    status="queued",
                    progress=0,
                    message="Queued by backtest",
                ))
                await session.commit()

            # Wake the data-fetch worker. After #164 Redis no longer holds
            # job_ids — the DB is the scheduling source of truth, so we push
            # the same opaque wake sentinel every other caller uses.
            rds = self._redis_client
            if rds is None:
                logger.warning("No Redis client; cannot enqueue data fetch job")
                return False
            from tinohelm.data.worker import QUEUE_KEY, WAKE_TOKEN

            rds.lpush(QUEUE_KEY, WAKE_TOKEN)

            logger.info(
                "Submitted data fetch job %s for %s %s [%s..%s]",
                job_id, sym, ivl, start_date, end_date,
            )

            # Poll for completion
            while True:
                async with factory() as session:
                    stmt = select(DataFetchJob).where(DataFetchJob.job_id == job_id)
                    job = (await session.execute(stmt)).scalar_one_or_none()
                    if not job:
                        logger.error("DataFetchJob %s disappeared", job_id)
                        return False

                    if job.status == "completed":
                        logger.info("Data fetch completed: %s %s — %s", sym, ivl, job.message)
                        return True
                    if job.status in ("failed", "cancelled"):
                        logger.warning("Data fetch %s: %s %s — %s", job.status, sym, ivl, job.error)
                        return False

                    self._report_progress(3, message=f"Fetching {sym} {ivl}: {job.progress}%")

                await _aio.sleep(2)
        finally:
            await engine.dispose()

    # ------------------------------------------------------------------
    # Fill model & latency model builders
    # ------------------------------------------------------------------

    _FILL_MODEL_CLASSES: dict[str, str] = {
        "default": "FillModel",
        "best_price": "BestPriceFillModel",
        "one_tick_slippage": "OneTickSlippageFillModel",
        "two_tier": "TwoTierFillModel",
        "three_tier": "ThreeTierFillModel",
        "probabilistic": "ProbabilisticFillModel",
        "size_aware": "SizeAwareFillModel",
        "volume_sensitive": "VolumeSensitiveFillModel",
        "competition_aware": "CompetitionAwareFillModel",
    }

    @staticmethod
    def _parse_fee(val: str) -> "Decimal":
        """Parse a fee string to Decimal.

        Accepts percentage strings like ``"0.02%"`` (divides by 100) or
        plain decimal strings like ``"0.0002"``.
        """
        val = val.strip()
        if val.endswith("%"):
            return Decimal(str(float(val[:-1]) / 100))
        return Decimal(val)

    def _build_fee_model(self) -> "Any | None":
        """Build a MakerTakerFeeModel if maker_fee or taker_fee is set."""
        if self.maker_fee is None and self.taker_fee is None:
            return None
        try:
            from nautilus_trader.backtest.models import MakerTakerFeeModel
            maker = self._parse_fee(self.maker_fee) if self.maker_fee else Decimal("0.0002")
            taker = self._parse_fee(self.taker_fee) if self.taker_fee else Decimal("0.0004")
            return MakerTakerFeeModel({"maker_fee": str(maker), "taker_fee": str(taker)})
        except Exception:
            logger.warning("Failed to build MakerTakerFeeModel, using default", exc_info=True)
            return None

    @staticmethod
    def _build_fill_model(config: dict[str, Any]) -> FillModel:
        """Build a FillModel from config dict.

        Supports ``fill_model_type`` key to select NT built-in model class:
          - ``"default"`` → FillModel (probabilistic)
          - ``"best_price"`` → BestPriceFillModel (fills inside spread)
          - ``"one_tick_slippage"`` → OneTickSlippageFillModel
          - ``"two_tier"`` → TwoTierFillModel (10 @ best, rest @ ±1 tick)
          - ``"three_tier"`` → ThreeTierFillModel (50/30/20 distribution)
          - ``"probabilistic"`` → ProbabilisticFillModel (50/50 best vs ±1 tick)
          - ``"size_aware"`` → SizeAwareFillModel (size-dependent impact)
          - ``"fixed_slippage"`` → OneTickSlippageFillModel (guaranteed 1-tick slippage)
          - ``"volume_impact"`` → VolumeSensitiveFillModel (volume-dependent liquidity)
          - ``"competition_aware"`` → CompetitionAwareFillModel (liquidity_factor)
        """
        model_type = config.get("fill_model_type", "default")

        if model_type == "best_price":
            from nautilus_trader.backtest.models import BestPriceFillModel
            return BestPriceFillModel()
        elif model_type in ("one_tick_slippage", "fixed_slippage"):
            # "fixed_slippage" from UI maps to NT's OneTickSlippageFillModel —
            # guaranteed 1-tick slippage per fill (NT has no bps-configurable model)
            from nautilus_trader.backtest.models import OneTickSlippageFillModel
            return OneTickSlippageFillModel()
        elif model_type == "two_tier":
            from nautilus_trader.backtest.models import TwoTierFillModel
            return TwoTierFillModel()
        elif model_type == "three_tier":
            from nautilus_trader.backtest.models import ThreeTierFillModel
            return ThreeTierFillModel()
        elif model_type == "probabilistic":
            from nautilus_trader.backtest.models import ProbabilisticFillModel
            return ProbabilisticFillModel()
        elif model_type == "size_aware":
            from nautilus_trader.backtest.models import SizeAwareFillModel
            return SizeAwareFillModel()
        elif model_type in ("volume_sensitive", "volume_impact"):
            # NT's VolumeSensitiveFillModel —
            # liquidity at best = max(1, int(recent_volume * 0.25)), rest at ±1 tick
            from nautilus_trader.backtest.models import VolumeSensitiveFillModel
            return VolumeSensitiveFillModel()
        elif model_type == "competition_aware":
            from nautilus_trader.backtest.models import CompetitionAwareFillModel
            return CompetitionAwareFillModel()
        else:
            return FillModel(
                prob_fill_on_limit=config.get("prob_fill_on_limit", 1.0),
                prob_slippage=config.get("prob_slippage", 0.0),
                random_seed=config.get("random_seed", None),
            )

    @staticmethod
    def _build_latency_model(config: dict[str, Any]) -> LatencyModel | None:
        """Build a LatencyModel from config dict.

        Reads ``latency_ms`` (base latency in milliseconds, default 30).
        Set to 0 to disable latency simulation.

        Advanced keys (nanoseconds, additive to base):
          - ``insert_latency_nanos``
          - ``update_latency_nanos``
          - ``cancel_latency_nanos``
        """
        latency_ms = config.get("latency_ms", 30)
        if latency_ms <= 0:
            return None
        base_nanos = int(latency_ms * 1_000_000)
        return LatencyModel(
            base_latency_nanos=base_nanos,
            insert_latency_nanos=int(config.get("insert_latency_nanos", 0)),
            update_latency_nanos=int(config.get("update_latency_nanos", 0)),
            cancel_latency_nanos=int(config.get("cancel_latency_nanos", 0)),
        )

    async def _load_auxiliary_price_data(
        self, engine: BacktestEngine, nt_symbols: list[str],
    ) -> None:
        """Load mark price and index price data for all symbols.

        Cache-first flow (mirrors the main klines ``_try_load_bars`` path):
          1. Try to load existing Parquet bars from the catalog
             (``{catalog}/bar/{markPriceKlines,indexPriceKlines}/``).
          2. For any symbol missing data, enqueue a DataFetchJob via
             ``_submit_and_wait_fetch`` so the pipeline downloads from
             Binance Vision + REST fallback and writes Parquet.
          3. Reload from catalog after the job completes.
          4. Convert the NT Bar objects into MarkPriceUpdate /
             IndexPriceUpdate and inject into the engine.

        Running the same backtest twice now reuses local Parquet and does
        not re-hit Binance. Individual per-symbol failures are silently
        downgraded (logger.debug) — a broken aux pull must not crash the
        whole run.

        Injection order into ``engine.add_data`` follows ``self.symbols``
        to keep backtests reproducible (mark before index, symbols in
        declared order) even though NT re-sorts internally.
        """
        ivl = self.intervals[0] if self.intervals else "1h"
        sym_pairs = list(zip(self.symbols, nt_symbols))
        if not sym_pairs:
            return

        self._report_progress(
            7,
            message=f"Loading auxiliary price data for {len(sym_pairs)} symbols",
        )

        total_mark = 0
        total_index = 0
        for sym, nt_sym in sym_pairs:
            try:
                from nautilus_trader.model.data import MarkPriceUpdate
                mark_updates = await self._load_or_fetch_aux_updates(
                    sym, nt_sym, ivl,
                    source_type="markPriceKlines",
                    data_cls=MarkPriceUpdate,
                )
                if mark_updates:
                    engine.add_data(mark_updates, sort=False)
                    total_mark += len(mark_updates)
            except Exception:
                logger.debug("Failed to load mark price for %s", sym, exc_info=True)

            try:
                from nautilus_trader.model.data import IndexPriceUpdate
                index_updates = await self._load_or_fetch_aux_updates(
                    sym, nt_sym, ivl,
                    source_type="indexPriceKlines",
                    data_cls=IndexPriceUpdate,
                )
                if index_updates:
                    engine.add_data(index_updates, sort=False)
                    total_index += len(index_updates)
            except Exception:
                logger.debug("Failed to load index price for %s", sym, exc_info=True)

        if total_mark or total_index:
            logger.info(
                "Auxiliary price data loaded: %d MarkPriceUpdate, %d IndexPriceUpdate",
                total_mark, total_index,
            )

    def _load_aux_updates(self, symbol: str, data_cls: type) -> list | None:
        from nautilus_trader.model.identifiers import InstrumentId

        catalog = self._catalog_for_path(self.catalog_path)
        instrument_id = str(InstrumentId.from_str(_normalize_symbol(symbol)))
        try:
            updates = catalog.query(
                data_cls,
                identifiers=[instrument_id],
                start=self.start,
                end=self.end,
            )
        except Exception:
            logger.warning("Failed to load aux updates for %s %s", symbol, data_cls.__name__, exc_info=True)
            return None
        return updates or None

    async def _load_or_fetch_aux_updates(
        self,
        sym: str,
        nt_sym: str,
        ivl: str,
        *,
        source_type: str,
        data_cls,
    ) -> list:
        """Load aux (mark/index) updates directly from the NT catalog."""
        _ = nt_sym
        updates = self._load_aux_updates(sym, data_cls)
        if updates:
            return updates

        if self._redis_client:
            success = await self._submit_and_wait_fetch(sym, ivl, data_type=source_type)
            if success:
                self._invalidate_catalog_cache_for_source(source_type)
                updates = self._load_aux_updates(sym, data_cls)

        return updates or []

    @staticmethod
    def _build_funding_rate_updates(funding_events: list[dict]) -> list:
        """Convert funding events to NT FundingRateUpdate objects.

        These are injected into the engine so strategies can subscribe via
        ``subscribe_data(DataType(FundingRateUpdate))`` and receive them
        in ``on_data()``.
        """
        try:
            from decimal import Decimal as _Decimal
            from nautilus_trader.model.data import FundingRateUpdate
            from nautilus_trader.model.identifiers import InstrumentId

            updates = []
            for ev in funding_events:
                updates.append(FundingRateUpdate(
                    instrument_id=InstrumentId.from_str(ev["symbol"]),
                    rate=_Decimal(str(ev["rate"])),
                    ts_event=ev["timestamp_ns"],
                    ts_init=ev["timestamp_ns"],
                    interval=ev.get("funding_interval_minutes"),
                ))
            return updates
        except Exception:
            logger.warning("Failed to build FundingRateUpdate objects", exc_info=True)
            return []

    async def _load_funding_rates(self, nt_symbols: list[str]) -> list[dict]:
        """Load historical funding rates from NT-native funding/mark updates.

        FundingRateUpdate provides the settlement rate, while MarkPriceUpdate
        provides the notional needed by the funding-cost tracker. The events are
        assembled directly from the catalog so backtests no longer depend on the
        legacy JSON cache path.
        """
        from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from tinohelm.data.funding_cache_helpers import compute_fetch_start
        from tinohelm.data.instruments import fetch_funding_info, strip_to_binance_api_symbol

        catalog = self._catalog_for_path(self.catalog_path)
        funding_info = fetch_funding_info()
        nt_symbols_by_symbol: dict[str, str] = dict(zip(self.symbols, nt_symbols))
        interval_minutes_by_symbol: dict[str, int] = {}

        def _instrument_id(symbol: str) -> str:
            return str(InstrumentId.from_str(_normalize_symbol(symbol)))

        def _query_updates(symbol: str, data_cls: type, *, start: datetime | None, end: datetime | None) -> list:
            try:
                return catalog.query(
                    data_cls,
                    identifiers=[_instrument_id(symbol)],
                    start=start,
                    end=end,
                ) or []
            except Exception:
                logger.warning(
                    "Failed to load %s updates for %s",
                    data_cls.__name__, symbol,
                    exc_info=True,
                )
                return []

        def _price_to_float(value: Any) -> float:
            if hasattr(value, "as_double"):
                return float(value.as_double())
            return float(value)

        def _timestamps_ms(rows: list) -> list[int]:
            out: list[int] = []
            for row in rows:
                ts_event = getattr(row, "ts_event", None)
                if ts_event is None:
                    continue
                out.append(int(ts_event) // 1_000_000)
            return out

        async def _ensure_mark_prices_for_funding(symbol: str) -> None:
            nonlocal catalog
            if not self._redis_client:
                return
            if self.start is None or self.end is None:
                return
            interval_minutes = interval_minutes_by_symbol[symbol]
            mark_start = self.start - timedelta(minutes=interval_minutes)
            mark_rows = _query_updates(symbol, MarkPriceUpdate, start=mark_start, end=self.end)
            if mark_rows:
                return
            logger.warning(
                "Missing mark price updates for funding assembly: %s [%s..%s]",
                symbol,
                mark_start,
                self.end,
            )
            success = await self._submit_and_wait_fetch(
                symbol,
                self.intervals[0] if self.intervals else "1h",
                data_type="markPriceKlines",
                start_override=mark_start,
            )
            if success:
                self._invalidate_catalog_cache_for_source("fundingRate")
                self._invalidate_catalog_cache_for_source("markPriceKlines")
                catalog = self._catalog_for_path(self.catalog_path)

        def _build_rates(symbol: str, start: datetime | None, end: datetime | None) -> list[dict]:
            funding_rows = sorted(
                _query_updates(symbol, FundingRateUpdate, start=start, end=end),
                key=lambda row: int(getattr(row, "ts_event", 0)),
            )
            mark_start = start - timedelta(minutes=interval_minutes_by_symbol[symbol]) if start is not None else None
            mark_rows = sorted(
                _query_updates(symbol, MarkPriceUpdate, start=mark_start, end=end),
                key=lambda row: int(getattr(row, "ts_event", 0)),
            )
            if funding_rows and not mark_rows:
                logger.warning(
                    "Funding updates exist but no mark price updates were found for %s [%s..%s]",
                    symbol,
                    mark_start,
                    end,
                )
            mark_times = [int(getattr(row, "ts_event", 0)) for row in mark_rows]
            mark_values = [_price_to_float(getattr(row, "value")) for row in mark_rows]
            rates: list[dict] = []
            skipped_missing_mark = 0
            for row in funding_rows:
                ts_ns = int(getattr(row, "ts_event", 0))
                mark_idx = bisect_right(mark_times, ts_ns) - 1
                if mark_idx < 0:
                    skipped_missing_mark += 1
                    continue
                mark_price = mark_values[mark_idx]
                if not mark_price:
                    skipped_missing_mark += 1
                    continue
                rates.append({
                    "funding_time_ms": ts_ns // 1_000_000,
                    "funding_rate": float(getattr(row, "rate")),
                    "mark_price": mark_price,
                })
            if skipped_missing_mark:
                logger.warning(
                    "Skipped %d funding event(s) without usable mark price for %s",
                    skipped_missing_mark,
                    symbol,
                )
            return rates

        if self._redis_client and self.start and self.end:
            for sym in self.symbols:
                api_sym = strip_to_binance_api_symbol(sym)
                interval_minutes_by_symbol[sym] = funding_info.get(api_sym, 8) * 60
                cached = _query_updates(sym, FundingRateUpdate, start=self.start, end=self.end)
                fetch_start = compute_fetch_start(
                    _timestamps_ms(cached), start=self.start, end=self.end,
                )
                if fetch_start is None:
                    logger.info(
                        "Funding updates already cover %s [%s..%s], skipping fetch job",
                        sym, self.start, self.end,
                    )
                    continue
                self._report_progress(5, message=f"Fetching funding rates: {sym}...")
                await self._submit_and_wait_fetch(
                    sym, None, data_type="fundingRate",
                    start_override=fetch_start,
                )
                self._invalidate_catalog_cache_for_source("fundingRate")
                catalog = self._catalog_for_path(self.catalog_path)
        else:
            for sym in self.symbols:
                api_sym = strip_to_binance_api_symbol(sym)
                interval_minutes_by_symbol[sym] = funding_info.get(api_sym, 8) * 60

        rates_by_symbol: dict[str, list[dict]] = {}
        for sym in self.symbols:
            await _ensure_mark_prices_for_funding(sym)
            rates_by_symbol[sym] = _build_rates(sym, self.start, self.end)
            logger.info(
                "Loaded %d funding rate events for %s",
                len(rates_by_symbol[sym]), sym,
            )

        return assemble_funding_events(
            rates_by_symbol, nt_symbols_by_symbol, interval_minutes_by_symbol,
        )

    def _build_strategy_bundle(self):
        """Build a StrategyBundle from legacy constructor params if not provided."""
        if self._strategy_bundle is not None:
            return self._strategy_bundle

        from tinohelm.portfolio.config import StrategyBundle, AccountSettings

        starting_balance = self.strategy_params.get("starting_balance", 10000)
        leverage = self.strategy_params.get("leverage", 1)

        return StrategyBundle(
            strategy_class=self.strategy_path,
            config_class=self.config_path,
            symbols=self.symbols,
            interval=self.intervals[0] if self.intervals else "1m",
            params=self.strategy_params,
            actors=[],
            account=AccountSettings(
                starting_balance=starting_balance,
                currency="USDT",
                leverage=leverage,
            ),
            source_path=Path(self.strategy_path.rsplit(":", 1)[0]).parent if self.strategy_path else None,
            implicit=True,
        )

    def _report_progress(self, pct: int, total_bars: int = 0, message: str | None = None) -> None:
        """Report setup-phase progress (0-10%) to Redis if available."""
        if not self._redis_client or not self._run_id:
            return
        try:
            elapsed = (
                round(time.monotonic() - self._job_start_time, 1)
                if self._job_start_time else 0
            )
            payload = build_progress_payload(
                self._run_id,
                pct=pct,
                elapsed_secs=elapsed,
                total_bars=total_bars if total_bars > 0 else None,
                message=message,
            )
            self._redis_client.setex(
                f"tino:backtest:progress:{self._run_id}", 86400, str(pct),
            )
            self._redis_client.publish(
                f"tino:backtest:progress:{self._run_id}",
                json.dumps(payload),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Shared engine setup (eliminates duplication between run/prepare)
    # ------------------------------------------------------------------

    async def _setup_engine(self) -> tuple["BacktestEngine", Any, float]:
        """Create and configure the BacktestEngine with all data loaded.

        This is the shared setup logic used by both :meth:`run` (single
        backtest) and :meth:`prepare_engine` (Optuna optimization).
        Stores resolved metadata on the instance for downstream use:

        - ``self._nt_symbols``
        - ``self._all_bar_type_strs``
        - ``self._loaded_bar_type_strs``
        - ``self._total_bar_count``
        - ``self._benchmark_daily_closes``

        Returns:
            (engine, strategy_bundle, starting_balance)
        """
        _require_nt()
        strategy_bundle = self._build_strategy_bundle()

        # Sync symbols/intervals from strategy bundle when runner-level lists are empty
        self.symbols, self.intervals = resolve_symbols_intervals(
            strategy_bundle.symbols, strategy_bundle.interval,
            self.symbols, self.intervals,
        )
        self.symbol = self.symbols[0] if self.symbols else ""
        self.interval = self.intervals[0] if self.intervals else ""

        # Extend data loading window for warmup bars (no-op when inputs disable it)
        if self.intervals:
            adjusted_start = compute_warmup_adjusted_start(
                self.start, self.intervals[0], self.warmup_bars,
            )
            if adjusted_start is not self.start:
                logger.info(
                    "Warmup: extended start by %d bars to %s",
                    self.warmup_bars, adjusted_start,
                )
                self.start = adjusted_start

        # Configure engine
        engine_config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="WARNING"),
        )
        engine = BacktestEngine(config=engine_config)
        self._engine = engine

        # Build fill/latency/fee models
        fill_model_obj: FillModel | None = None
        if self.fill_model_config is not None:
            fill_model_obj = self._build_fill_model(self.fill_model_config)
        latency_config = self.fill_model_config or {}
        latency_model_obj = self._build_latency_model(latency_config)

        # Add venue — currency is assumed USDT for Binance futures
        from nautilus_trader.model.currencies import USDT
        from nautilus_trader.model.identifiers import Venue
        from nautilus_trader.model.objects import Money

        starting_balance = strategy_bundle.account.starting_balance
        leverage = strategy_bundle.account.leverage
        venue_kwargs: dict[str, Any] = {
            "venue": Venue("BINANCE"),
            "oms_type": OmsType.HEDGING,
            "account_type": AccountType.MARGIN,
            "starting_balances": [Money(starting_balance, USDT)],
            "default_leverage": Decimal(str(leverage)),
            "bar_execution": True,
            "trade_execution": True,
        }
        if fill_model_obj is not None:
            venue_kwargs["fill_model"] = fill_model_obj
        if latency_model_obj is not None:
            venue_kwargs["latency_model"] = latency_model_obj
        fee_model_obj = self._build_fee_model()
        if fee_model_obj is not None:
            venue_kwargs["fee_model"] = fee_model_obj
        engine.add_venue(**venue_kwargs)

        # Load data from catalog
        from tinohelm.data.catalog import resolve_catalog_path

        catalog = self._catalog_for_path(self.catalog_path)

        # Load instruments for each symbol
        nt_symbols: list[str] = []
        missing_instrument_syms: list[tuple[str, str]] = []
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            nt_symbols.append(nt_sym)
            instruments = catalog.instruments(instrument_ids=[nt_sym])
            if instruments:
                for inst in instruments:
                    engine.add_instrument(inst)
            else:
                missing_instrument_syms.append((sym, nt_sym))

        # Load bar data for each (symbol, interval) combination
        all_bar_type_strs: list[str] = []
        loaded_bar_type_strs: list[str] = []
        total_bar_count: int = 0
        benchmark_daily_closes: dict[str, dict[str, float]] = {}
        pending_bars_by_loaded_type: dict[str, list] = {}
        use_streaming_bars = self.streaming_enabled and hasattr(engine, "add_data_iterator")
        added_stream_types: set[str] = set()
        benchmark_stream_symbols: set[str] = set()
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            for ivl in self.intervals:
                if use_streaming_bars:
                    daily_close_target = benchmark_daily_closes.get(nt_sym, {})
                    capture_benchmark = nt_sym not in benchmark_stream_symbols
                    stream, bt_str, source_bt_str, row_count = self._resolve_bar_stream(
                        sym,
                        nt_sym,
                        ivl,
                        benchmark_daily_closes=daily_close_target if capture_benchmark else None,
                    )
                    if stream is None:
                        logger.info("No local data for %s %s, submitting fetch job...", sym, ivl)
                        success = await self._submit_and_wait_fetch(sym, ivl)
                        if success:
                            self._invalidate_catalog_cache_for_source(self.data_type)
                            stream, bt_str, source_bt_str, row_count = self._resolve_bar_stream(
                                sym,
                                nt_sym,
                                ivl,
                                benchmark_daily_closes=daily_close_target if capture_benchmark else None,
                            )
                    if stream is not None:
                        loaded_bt_str = source_bt_str or bt_str
                        if loaded_bt_str not in added_stream_types:
                            engine.add_data_iterator(
                                data_name=f"bar:{loaded_bt_str}",
                                generator=stream,
                            )
                            added_stream_types.add(loaded_bt_str)
                            if capture_benchmark:
                                benchmark_stream_symbols.add(nt_sym)
                                benchmark_daily_closes[nt_sym] = daily_close_target
                            total_bar_count += row_count
                            loaded_bar_type_strs.append(loaded_bt_str)
                        all_bar_type_strs.append(bt_str)
                        continue
                    logger.warning("No bar data available for %s at %s", sym, ivl)
                    continue

                bars, bt_str, source_bt_str = await self._resolve_bars(
                    catalog, sym, nt_sym, ivl,
                )
                if bars:
                    loaded_bt_str = source_bt_str or bt_str
                    if loaded_bt_str not in pending_bars_by_loaded_type:
                        total_bar_count += len(bars)
                        loaded_bar_type_strs.append(loaded_bt_str)
                        pending_bars_by_loaded_type[loaded_bt_str] = bars
                    all_bar_type_strs.append(bt_str)
                    # Extract daily close prices for benchmark B&H (NT-free helper)
                    if nt_sym not in benchmark_daily_closes:
                        benchmark_daily_closes[nt_sym] = extract_benchmark_daily_closes(
                            (int(bar.ts_init), float(bar.close)) for bar in bars
                        )
                else:
                    logger.warning("No bar data available for %s at %s", sym, ivl)

        # Re-check instruments that were missing before bar auto-fetch
        if missing_instrument_syms:
            from tinohelm.data.catalog import resolve_catalog_path
            for raw_sym, nt_sym in missing_instrument_syms:
                resolved = Path(resolve_catalog_path(self.catalog_path, self.data_type))
                try:
                    resolved_cat = self._catalog_for_path(resolved)
                    instruments = resolved_cat.instruments(instrument_ids=[nt_sym])
                    if instruments:
                        for inst in instruments:
                            engine.add_instrument(inst)
                        logger.info("Loaded instrument %s from resolved catalog after auto-fetch", nt_sym)
                        continue
                except Exception:
                    pass
                instruments = catalog.instruments(instrument_ids=[nt_sym])
                if instruments:
                    for inst in instruments:
                        engine.add_instrument(inst)
                    logger.info("Loaded instrument %s from base catalog after auto-fetch", nt_sym)

        # Add bar data to engine (instruments are now guaranteed loaded)
        for bars in pending_bars_by_loaded_type.values():
            engine.add_data(bars, sort=False)
        if all_bar_type_strs:
            engine.sort_data()

        # Inject NT-format params for strategy config resolution
        self.strategy_params.setdefault("instrument_id", nt_symbols[0] if nt_symbols else "")
        self.strategy_params.setdefault("instrument_ids", nt_symbols)
        self.strategy_params.setdefault("bar_type", all_bar_type_strs[0] if all_bar_type_strs else "")
        self.strategy_params.setdefault("bar_types", all_bar_type_strs)

        # Inject resolved bar types for strategy on_start()
        strategy_bundle.resolved_bar_types = all_bar_type_strs

        # Store metadata on instance for downstream use
        self._nt_symbols = nt_symbols
        self._all_bar_type_strs = all_bar_type_strs
        self._loaded_bar_type_strs = loaded_bar_type_strs
        self._total_bar_count = total_bar_count
        self._benchmark_daily_closes = benchmark_daily_closes

        return engine, strategy_bundle, starting_balance

    async def run(self) -> dict[str, Any]:
        """Execute the backtest and return results dict."""
        logger.info("Starting backtest: %s on %s", self.strategy_path, self.symbols)

        engine, strategy_bundle, starting_balance = await self._setup_engine()

        self._report_progress(4, total_bars=self._total_bar_count)

        # Create strategy instances via loader
        strategy_instances = create_strategies(strategy_bundle)
        for strategy_instance in strategy_instances:
            engine.add_strategy(strategy_instance)
        logger.info("Added %d strategy instance(s) to engine", len(strategy_instances))

        # Create and add actor instances via strategy loader
        actor_instances = create_actors(strategy_bundle)
        for actor_instance in actor_instances:
            engine.add_actor(actor_instance)
            # Inject RiskEngine reference into RiskGuardActor for direct
            # TradingState enforcement (works without LifecycleController)
            from tinohelm.actors.risk_guard import RiskGuardActor
            if isinstance(actor_instance, RiskGuardActor):
                actor_instance._risk_engine = engine.kernel.risk_engine
                logger.info("Injected RiskEngine reference into RiskGuardActor")
        if actor_instances:
            logger.info("Added %d actor(s) to engine", len(actor_instances))

        # Register additional statistics BEFORE running
        # NT 1.224.0 defaults include Sharpe/Sortino/Volatility etc. but NOT
        # MaxDrawdown, CalmarRatio, CAGR — register them from the Rust builtins.
        try:
            from nautilus_trader.analysis import MaxDrawdown, CalmarRatio, CAGR, ProfitFactor
            engine.portfolio.analyzer.register_statistic(MaxDrawdown())
            engine.portfolio.analyzer.register_statistic(CalmarRatio())
            engine.portfolio.analyzer.register_statistic(CAGR())
            engine.portfolio.analyzer.register_statistic(ProfitFactor())
        except Exception:
            logger.warning("Failed to register MaxDrawdown/Calmar/CAGR statistics", exc_info=True)

        # Register custom Python statistics for richer tearsheet reports
        try:
            from tinohelm.backtest.custom_statistics import register_custom_statistics
            n = register_custom_statistics(engine.portfolio.analyzer)
            logger.info("Registered %d custom statistics with analyzer", n)
        except Exception:
            logger.warning("Failed to register custom statistics", exc_info=True)

        self._report_progress(5)  # Strategies loaded

        # Add funding cost tracker + inject FundingRateUpdate data for perpetual futures
        self._funding_enabled = False
        if self.symbols and self.start and self.end:
            funding_events = await self._load_funding_rates(self._nt_symbols)
            if funding_events:
                from tinohelm.backtest.funding import (
                    _FundingCostTracker,
                    _FundingCostTrackerConfig,
                )
                _FundingCostTracker._funding_events = funding_events
                _FundingCostTracker._bar_type_strs = self._loaded_bar_type_strs
                tracker = _FundingCostTracker(config=_FundingCostTrackerConfig())
                engine.add_actor(tracker)
                self._funding_tracker = tracker
                self._funding_enabled = True

                # Inject NT-native FundingRateUpdate so strategies can
                # subscribe via subscribe_data(DataType(FundingRateUpdate))
                funding_updates = self._build_funding_rate_updates(funding_events)
                if funding_updates:
                    engine.add_data(funding_updates, sort=False)

                logger.info(
                    "Funding cost tracker enabled: %d events, %d FundingRateUpdate injected",
                    len(funding_events), len(funding_updates),
                )

        self._report_progress(7)  # Funding rates loaded

        # Load mark price and index price data for strategies
        if self.symbols and self.start and self.end:
            await self._load_auxiliary_price_data(engine, self._nt_symbols)

        await self._inject_optional_replay_data_async(engine)

        self._report_progress(9)  # Auxiliary price data loaded

        # Add progress reporter actor for bar-level progress tracking
        if self._redis_client and self._run_id and self._total_bar_count > 0:
            reporter = _ProgressReporter(config=_ProgressReporterConfig())
            reporter._redis = self._redis_client
            reporter._run_id = self._run_id
            reporter._total_bars = self._total_bar_count
            reporter._report_every = 2000
            reporter._bar_type_strs = self._loaded_bar_type_strs
            engine.add_actor(reporter)
            logger.info(
                "Progress reporter enabled: %d total bars, run_id=%s",
                self._total_bar_count, self._run_id[:8],
            )

        # Final sort — funding/mark/index data may have been added after initial sort
        engine.sort_data()

        # Run
        engine.run()

        # Extract results
        results = self._extract_results(engine, starting_balance)

        # Merge funding cost data into results
        if self._funding_enabled:
            funding_data = self._funding_tracker.get_results()
            results["funding"] = funding_data
            # Adjust statistics to reflect funding cost
            stats = results.get("statistics", {})
            funding_cost = funding_data["total_funding_cost"]
            stats["total_funding_cost"] = funding_cost
            stats["pnl_after_funding"] = round(
                (stats.get("total_pnl", 0.0) or 0.0) - funding_cost, 4
            )

        # Export raw reports before dispose (if artifacts_dir is set).  Queue
        # mode uses _before_artifact_export to publish a terminalization marker
        # before any CSV/HTML artifact writes can be interrupted by late cancel.
        try:
            if self._before_artifact_export is not None:
                self._before_artifact_export()
            if self.artifacts_dir is not None:
                self._export_reports(engine)
                self._generate_tearsheet(engine, self._loaded_bar_type_strs)
                from tinohelm.backtest.tearsheet import enhance_tearsheet
                enhance_tearsheet(self.artifacts_dir, results)
        finally:
            # Cleanup
            engine.dispose()

        logger.info("Backtest complete: %s", self.strategy_path)
        return results

    # ------------------------------------------------------------------
    # Engine reuse API (for Optuna optimization — avoids reloading data)
    # ------------------------------------------------------------------

    async def prepare_engine(self) -> tuple[BacktestEngine, Any, float]:
        """Create and configure engine with data loaded, but do NOT run.

        This is the "heavy" setup step — loads instruments, bar data, and
        configures the venue. Call once, then use :meth:`run_trial` per
        parameter set with ``engine.reset()`` between trials.

        Returns:
            (engine, strategy_bundle, starting_balance)
        """
        was_streaming = self.streaming_enabled
        self.streaming_enabled = False
        try:
            engine, strategy_bundle, starting_balance = await self._setup_engine()
        finally:
            self.streaming_enabled = was_streaming

        logger.info(
            "Engine prepared: %d symbols, %d bar types",
            len(self._nt_symbols), len(self._all_bar_type_strs),
        )
        return engine, strategy_bundle, starting_balance

    def run_trial(
        self,
        engine: BacktestEngine,
        strategy_bundle: Any,
        starting_balance: float,
        trial_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a single trial on a prepared engine.

        Resets the engine, adds strategies with *trial_params*, runs, and
        extracts results.  Does NOT dispose the engine — caller owns that.
        """
        from copy import deepcopy

        engine.reset()

        # Merge trial params into strategy bundle
        if trial_params:
            pc = deepcopy(strategy_bundle)
            pc.params.update(trial_params)
        else:
            pc = strategy_bundle

        # Ensure resolved_bar_types survives the copy
        if not pc.resolved_bar_types and hasattr(self, "_all_bar_type_strs"):
            pc.resolved_bar_types = list(self._all_bar_type_strs)

        strategy_instances = create_strategies(pc)
        for s in strategy_instances:
            engine.add_strategy(s)

        actor_instances = create_actors(pc)
        for a in actor_instances:
            engine.add_actor(a)
            from tinohelm.actors.risk_guard import RiskGuardActor
            if isinstance(a, RiskGuardActor):
                a._risk_engine = engine.kernel.risk_engine

        engine.run()
        return self._extract_results(engine, starting_balance, compute_robustness=False)

    def _export_reports(self, engine: BacktestEngine) -> None:
        """Export all 5 raw reports from the engine to CSV before dispose."""
        report_methods = {
            "fills_report": "generate_fills_report",
            "orders_report": "generate_orders_report",
            "order_fills_report": "generate_order_fills_report",
            "positions_report": "generate_positions_report",
            "account_report": "generate_account_report",
        }
        for filename, method_name in report_methods.items():
            try:
                method = getattr(engine.trader, method_name, None)
                if method is None:
                    continue
                if method_name == "generate_account_report":
                    from nautilus_trader.model.identifiers import Venue
                    df = method(Venue("BINANCE"))
                else:
                    df = method()
                if df is not None and not df.empty:
                    csv_path = self.artifacts_dir / f"{filename}.csv"
                    df.to_csv(csv_path, index=True)
                    logger.info("Exported %s (%d rows) to %s", filename, len(df), csv_path)
            except Exception:
                logger.warning("Failed to export %s", filename, exc_info=True)

    def _generate_tearsheet(self, engine: BacktestEngine, bar_type_strs: list[str]) -> None:
        """Generate an interactive HTML tearsheet using NT's visualization module.

        Requires ``plotly>=6.3.1``. Silently skips if plotly is not installed.
        """
        from nautilus_trader.analysis import (
            create_tearsheet,
            register_theme,
            TearsheetConfig,
            TearsheetBarsWithFillsChart,
            TearsheetDrawdownChart,
            TearsheetEquityChart,
            TearsheetRunInfoChart,
            TearsheetStatsTableChart,
        )

        # Register QDS Warm light theme (based on docs/ui/qds-warm-theme.css)
        register_theme(
            name="qds_warm",
            template="plotly_white",
            colors={
                "primary": "#D97857",       # Burnt orange accent
                "positive": "#36884B",      # Success green
                "negative": "#8A2425",      # Danger red (light mode)
                "neutral": "#73726C",       # Text secondary
                "background": "#faf9f5",    # Body warm white
                "grid": "#dedbd3",          # Border default
                "table_section": "#eae8e0", # Tertiary bg
                "table_row_odd": "#f5f4ed", # Card bg (warm cream)
                "table_row_even": "#faf9f5",# Body bg
                "table_text": "#2C2C2A",    # Text primary
            },
        )

        try:
            # Build chart list: standard charts + bars_with_fills per bar type
            charts: list = [
                TearsheetRunInfoChart(),
                TearsheetStatsTableChart(),
                TearsheetEquityChart(),
                TearsheetDrawdownChart(),
            ]

            # Optional chart imports (may not exist in all NT versions)
            try:
                from nautilus_trader.analysis import TearsheetMonthlyReturnsChart
                charts.append(TearsheetMonthlyReturnsChart())
            except ImportError:
                pass
            try:
                from nautilus_trader.analysis import TearsheetDistributionChart
                charts.append(TearsheetDistributionChart())
            except ImportError:
                pass
            try:
                from nautilus_trader.analysis import TearsheetRollingSharpeChart
                charts.append(TearsheetRollingSharpeChart())
            except ImportError:
                pass
            try:
                from nautilus_trader.analysis import TearsheetYearlyReturnsChart
                charts.append(TearsheetYearlyReturnsChart())
            except ImportError:
                pass

            # Add bars_with_fills for each bar type (K-line + order fill markers)
            for bt_str in bar_type_strs:
                charts.append(TearsheetBarsWithFillsChart(bar_type=bt_str))

            config = TearsheetConfig(
                charts=charts,
                theme="qds_warm",
            )

            output_path = self.artifacts_dir / "tearsheet.html"
            create_tearsheet(
                engine=engine,
                output_path=str(output_path),
                config=config,
            )
            logger.info("Generated tearsheet: %s", output_path)
        except Exception:
            logger.warning("Failed to generate tearsheet", exc_info=True)

    def _extract_results(
        self, engine: BacktestEngine, starting_balance: float = 10000,
        compute_robustness: bool = True,
    ) -> dict[str, Any]:
        """Extract results from completed backtest engine."""
        from tinohelm.backtest.result import extract_backtest_results
        return extract_backtest_results(
            engine,
            starting_balance=starting_balance,
            benchmark_daily_closes=getattr(self, "_benchmark_daily_closes", None),
            compute_robustness=compute_robustness,
        )
