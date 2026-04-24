"""Backtest runner wrapping NautilusTrader BacktestEngine."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.common.actor import Actor, ActorConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model import TraderId
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

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


# ---------------------------------------------------------------------------
# Progress reporter — lightweight actor for bar-level progress tracking
# ---------------------------------------------------------------------------

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
    ) -> None:
        self.strategy_path = strategy_path
        self.config_path = config_path
        self.strategy_params = strategy_params or {}
        self.catalog_path = Path(catalog_path) if catalog_path else Path()
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

        # Backward compat aliases
        self.symbol = self.symbols[0] if self.symbols else ""
        self.interval = self.intervals[0] if self.intervals else ""

        self.start = start
        self.end = end
        self.fill_model_config = fill_model
        self.warmup_bars = warmup_bars
        self.tags = tags
        self.data_type = data_type
        self.artifacts_dir: Path | None = None
        self._engine: BacktestEngine | None = None
        self._redis_client = None  # sync Redis for progress reporting
        self._run_id: str = ""
        self._job_start_time: float = 0.0  # set by worker for elapsed tracking

        # Strategy bundle (explicit or auto-wrapped from legacy params)
        self._strategy_bundle = strategy_bundle

    # Timeframes ordered from lowest to highest for composite source resolution.
    # Sourced from ``runner_helpers.TIMEFRAME_PRIORITY`` (single source of truth).
    _TIMEFRAME_PRIORITY: list[str] = list(_TIMEFRAME_PRIORITY_TUPLE)

    def _try_load_bars(self, bar_type_str: str, source_type: str = "klines") -> list | None:
        """Load bars from the resolved catalog path for a specific source_type.

        Only searches the exact source_type path + legacy base path.
        Different kline types (klines, markPriceKlines, indexPriceKlines,
        premiumIndexKlines) are distinct datasets and must NOT be mixed.
        """
        from tinohelm.data.catalog import resolve_catalog_path

        # 1. Resolved source_type path (new layout)
        resolved = str(resolve_catalog_path(self.catalog_path, source_type))
        try:
            cat = ParquetDataCatalog(resolved)
            bars = cat.bars(bar_types=[bar_type_str], start=self.start, end=self.end)
            if bars:
                return bars
        except Exception:
            pass

        # 2. Legacy base path fallback
        try:
            cat = ParquetDataCatalog(str(self.catalog_path))
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
        effective_start = start_override if start_override is not None else self.start
        start_date = effective_start.date() if isinstance(effective_start, datetime) else effective_start
        end_date = self.end.date() if isinstance(self.end, datetime) else self.end

        try:
            # Create job in DB
            async with factory() as session:
                session.add(DataFetchJob(
                    job_id=job_id,
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

            # Enqueue to Redis
            rds = self._redis_client
            if rds is None:
                logger.warning("No Redis client; cannot enqueue data fetch job")
                return False
            rds.lpush("tino:data:queue", job_id)

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
                mark_updates = await self._load_or_fetch_aux_updates(
                    sym, nt_sym, ivl,
                    source_type="markPriceKlines",
                    build_fn=self._build_mark_price_updates_from_bars,
                )
                if mark_updates:
                    engine.add_data(mark_updates, sort=False)
                    total_mark += len(mark_updates)
            except Exception:
                logger.debug("Failed to load mark price for %s", sym, exc_info=True)

            try:
                index_updates = await self._load_or_fetch_aux_updates(
                    sym, nt_sym, ivl,
                    source_type="indexPriceKlines",
                    build_fn=self._build_index_price_updates_from_bars,
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

    async def _load_or_fetch_aux_updates(
        self,
        sym: str,
        nt_sym: str,
        ivl: str,
        *,
        source_type: str,
        build_fn,
    ) -> list:
        """Load aux (mark/index) bars from catalog, fetching via job queue if missing.

        Parquet for mark/index lives under ``{catalog}/bar/{source_type}/``
        (see :func:`resolve_catalog_path`). We reuse ``_try_load_bars`` with
        the correct ``source_type`` so we don't mix klines with mark/index.
        """
        bar_type_str = _make_bar_type_str(sym, ivl)
        bars = self._try_load_bars(bar_type_str, source_type=source_type)
        if bars:
            return build_fn(bars, nt_sym)

        # Missing — enqueue fetch job and retry catalog read once it's done.
        if self._redis_client:
            success = await self._submit_and_wait_fetch(sym, ivl, data_type=source_type)
            if success:
                bars = self._try_load_bars(bar_type_str, source_type=source_type)

        if not bars:
            return []
        return build_fn(bars, nt_sym)

    @staticmethod
    def _build_mark_price_updates_from_bars(bars: list, nt_symbol: str) -> list:
        """Convert NT Bar objects (mark price klines) into MarkPriceUpdate objects."""
        try:
            from nautilus_trader.model.data import MarkPriceUpdate
            from nautilus_trader.model.identifiers import InstrumentId

            inst_id = InstrumentId.from_str(nt_symbol)
            updates = []
            for b in bars:
                updates.append(MarkPriceUpdate(
                    instrument_id=inst_id,
                    value=b.close,
                    ts_event=b.ts_event,
                    ts_init=b.ts_init,
                ))
            return updates
        except Exception:
            logger.warning("Failed to build MarkPriceUpdate from bars", exc_info=True)
            return []

    @staticmethod
    def _build_index_price_updates_from_bars(bars: list, nt_symbol: str) -> list:
        """Convert NT Bar objects (index price klines) into IndexPriceUpdate objects."""
        try:
            from nautilus_trader.model.data import IndexPriceUpdate
            from nautilus_trader.model.identifiers import InstrumentId

            inst_id = InstrumentId.from_str(nt_symbol)
            updates = []
            for b in bars:
                updates.append(IndexPriceUpdate(
                    instrument_id=inst_id,
                    value=b.close,
                    ts_event=b.ts_event,
                    ts_init=b.ts_init,
                ))
            return updates
        except Exception:
            logger.warning("Failed to build IndexPriceUpdate from bars", exc_info=True)
            return []

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
        """Load historical funding rates for all symbols via data catalog queue.

        Submits DataFetchJobs for fundingRate data, waits for completion,
        then loads from the local JSON cache.

        Symbols whose JSON cache already covers ``[self.start, self.end]``
        are skipped entirely — no DataFetchJob row is created. Symbols with
        only a partial tail gap get a job whose ``start_date`` is the first
        uncovered timestamp, keeping incremental fetches narrow and making
        the second run of an identical backtest a no-op.

        Returns a list of funding events sorted by timestamp_ns, ready for the
        FundingCostTracker actor.
        """
        from tinohelm.data.funding_cache import _load_cache, load_funding_rates
        from tinohelm.data.funding_cache_helpers import compute_fetch_start
        from tinohelm.data.instruments import fetch_funding_info, strip_to_binance_api_symbol

        # Fetch funding rate data via job queue, but only for symbols whose
        # local JSON cache does not already span [self.start, self.end].
        if self._redis_client and self.start and self.end:
            for sym in self.symbols:
                cached = _load_cache(sym)
                cached_times = [
                    int(r["funding_time_ms"])
                    for r in cached
                    if isinstance(r, dict)
                    and isinstance(r.get("funding_time_ms"), (int, float))
                ]
                fetch_start = compute_fetch_start(
                    cached_times, start=self.start, end=self.end,
                )
                if fetch_start is None:
                    logger.info(
                        "Funding rate cache already covers %s [%s..%s], "
                        "skipping fetch job", sym, self.start, self.end,
                    )
                    continue
                self._report_progress(5, message=f"Fetching funding rates: {sym}...")
                await self._submit_and_wait_fetch(
                    sym, None, data_type="fundingRate",
                    start_override=fetch_start,
                )

        # Load per-symbol funding interval (hours) from Binance
        funding_info = fetch_funding_info()

        # Gather raw rates + per-symbol interval into primitive dicts, then
        # hand off to the pure assembler helper.
        rates_by_symbol: dict[str, list[dict]] = {}
        nt_symbols_by_symbol: dict[str, str] = dict(zip(self.symbols, nt_symbols))
        interval_minutes_by_symbol: dict[str, int] = {}
        for sym in self.symbols:
            api_sym = strip_to_binance_api_symbol(sym)
            interval_minutes_by_symbol[sym] = funding_info.get(api_sym, 8) * 60
            try:
                rates_by_symbol[sym] = load_funding_rates(
                    symbol=sym, start=self.start, end=self.end,
                )
                logger.info(
                    "Loaded %d funding rate events for %s",
                    len(rates_by_symbol[sym]), sym,
                )
            except Exception:
                rates_by_symbol[sym] = []
                logger.warning(
                    "Failed to load funding rates for %s, skipping", sym,
                    exc_info=True,
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
        catalog = ParquetDataCatalog(str(self.catalog_path))

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
        pending_bars: list[list] = []
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            for ivl in self.intervals:
                bars, bt_str, source_bt_str = await self._resolve_bars(
                    catalog, sym, nt_sym, ivl,
                )
                if bars:
                    total_bar_count += len(bars)
                    loaded_bar_type_strs.append(source_bt_str or bt_str)
                    pending_bars.append(bars)
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
                resolved = str(resolve_catalog_path(self.catalog_path, self.data_type))
                try:
                    resolved_cat = ParquetDataCatalog(resolved)
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
        for bars in pending_bars:
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

        # Export raw reports before dispose (if artifacts_dir is set)
        if self.artifacts_dir is not None:
            self._export_reports(engine)
            self._generate_tearsheet(engine, self._loaded_bar_type_strs)
            from tinohelm.backtest.tearsheet import enhance_tearsheet
            enhance_tearsheet(self.artifacts_dir, results)

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
        engine, strategy_bundle, starting_balance = await self._setup_engine()

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
