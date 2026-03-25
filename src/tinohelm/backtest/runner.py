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

from tinohelm.portfolio.loader import (
    create_strategies,
    create_actors,
    _normalize_symbol,
    _make_bar_type_str,
    _INTERVAL_MAP,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress reporter — lightweight actor for bar-level progress tracking
# ---------------------------------------------------------------------------

class _ProgressReporterConfig(ActorConfig, frozen=True):
    component_id: str = "ProgressReporter-001"


class _ProgressReporter(Actor):
    """Counts processed bars and periodically writes progress to Redis.

    Uses class-level attributes set by BacktestRunner before engine.run().
    This is safe because the worker processes one backtest at a time.
    """

    _redis = None       # sync redis.Redis client
    _run_id: str = ""
    _total_bars: int = 0
    _bar_count: int = 0
    _start_time: float = 0.0
    _report_every: int = 2000
    _bar_type_strs: list = []  # Set before engine.run() — explicit bar type strings

    def on_start(self) -> None:
        from nautilus_trader.model.data import BarType

        cls = self.__class__
        cls._bar_count = 0
        cls._start_time = time.monotonic()
        # Subscribe to explicitly provided bar types (cache may be empty at this point)
        for bt_str in cls._bar_type_strs:
            try:
                self.subscribe_bars(BarType.from_str(bt_str))
            except Exception:
                pass

    def on_bar(self, bar) -> None:
        cls = self.__class__
        cls._bar_count += 1
        if (
            cls._bar_count % cls._report_every == 0
            and cls._redis is not None
            and cls._total_bars > 0
        ):
            # Map bar progress to 10-90% range (leaving 0-10 for setup, 90-100 for post)
            pct = min(int(cls._bar_count / cls._total_bars * 80) + 10, 90)
            elapsed = round(time.monotonic() - cls._start_time, 1)
            try:
                cls._redis.setex(
                    f"tino:backtest:progress:{cls._run_id}", 86400, str(pct),
                )
                payload = json.dumps({
                    "type": "backtest.progress",
                    "run_id": cls._run_id,
                    "pct": pct,
                    "elapsed_secs": elapsed,
                })
                cls._redis.publish(
                    f"tino:backtest:progress:{cls._run_id}", payload,
                )
            except Exception:
                pass  # Never let Redis errors crash the backtest


class BacktestRunner:
    """Runs a backtest using NautilusTrader BacktestEngine.

    Supports both:
    - Portfolio mode: pass a ``PortfolioConfig`` directly
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
        portfolio_config: Any | None = None,
    ) -> None:
        self.strategy_path = strategy_path
        self.config_path = config_path
        self.strategy_params = strategy_params or {}
        self.catalog_path = Path(catalog_path) if catalog_path else Path()

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
        self.artifacts_dir: Path | None = None
        self._engine: BacktestEngine | None = None
        self._redis_client = None  # sync Redis for progress reporting
        self._run_id: str = ""

        # Portfolio config (explicit or auto-wrapped from legacy params)
        self._portfolio_config = portfolio_config

    # Timeframes ordered from lowest to highest for composite source resolution
    _TIMEFRAME_PRIORITY: list[str] = [
        "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d",
    ]

    def _resolve_bars(
        self,
        catalog: ParquetDataCatalog,
        sym: str,
        nt_sym: str,
        ivl: str,
    ) -> tuple[list | None, str, str | None]:
        """Resolve bar data for a (symbol, interval) pair.

        Strategy:
          1. Try loading target interval directly from catalog.
          2. Find the lowest available timeframe below target for composite aggregation.
          3. If nothing local, download target interval from Binance and write to catalog.

        Returns:
            (bars, bar_type_str, source_bar_type_str_or_None)
            - bars: list of NT Bar objects, or None if all attempts fail
            - bar_type_str: the bar type string the strategy should subscribe to
            - source_bar_type_str: the actually loaded source bar type (for progress), None if direct
        """
        # 1. Direct load: target interval exists in catalog
        bar_type_str = _make_bar_type_str(sym, ivl)
        bars = catalog.bars(bar_types=[bar_type_str], start=self.start, end=self.end)
        if bars:
            logger.info("Loaded %d %s bars for %s directly", len(bars), ivl, sym)
            return bars, bar_type_str, None

        # 2. Composite: find lowest available source timeframe
        try:
            target_idx = self._TIMEFRAME_PRIORITY.index(ivl)
        except ValueError:
            target_idx = 0  # Unknown interval, skip composite search

        source_ivl: str | None = None
        source_bars = None
        for candidate in self._TIMEFRAME_PRIORITY[:target_idx]:
            candidate_bt = _make_bar_type_str(sym, candidate)
            source_bars = catalog.bars(bar_types=[candidate_bt], start=self.start, end=self.end)
            if source_bars:
                source_ivl = candidate
                break

        if source_bars and source_ivl:
            source_bt_str = _make_bar_type_str(sym, source_ivl)
            source_interval_part = _INTERVAL_MAP.get(source_ivl, "1-MINUTE")
            target_interval_part = _INTERVAL_MAP.get(ivl, "1-MINUTE")
            composite_bt_str = (
                f"{nt_sym}-{target_interval_part}-LAST-INTERNAL@{source_interval_part}-EXTERNAL"
            )
            logger.info(
                "No %s data for %s, using %s composite aggregation (%d bars): %s",
                ivl, sym, source_ivl, len(source_bars), composite_bt_str,
            )
            return source_bars, composite_bt_str, source_bt_str

        # 3. Auto-download: fetch target interval from Binance and write to catalog
        logger.info("No local data for %s %s, downloading from Binance...", sym, ivl)
        bars = self._download_bars(sym, ivl, catalog)
        if bars:
            bar_type_str = _make_bar_type_str(sym, ivl)
            logger.info("Downloaded and loaded %d %s bars for %s", len(bars), ivl, sym)
            return bars, bar_type_str, None

        return None, bar_type_str, None

    def _download_bars(
        self,
        sym: str,
        ivl: str,
        catalog: ParquetDataCatalog,
    ) -> list | None:
        """Synchronously download bars from Binance and write to catalog.

        Returns the loaded Bar objects, or None on failure.
        """
        import asyncio
        from tinohelm.data.providers.binance import fetch_klines
        from tinohelm.data.catalog import klines_to_bars, write_bars

        try:
            klines = asyncio.run(fetch_klines(
                symbol=sym,
                interval=ivl,
                start=self.start,
                end=self.end,
            ))
            if not klines:
                logger.warning("Binance returned no data for %s %s", sym, ivl)
                return None

            bars = klines_to_bars(klines, sym, ivl)
            if not bars:
                logger.warning("Failed to convert klines to bars for %s %s", sym, ivl)
                return None

            write_bars(bars, sym, ivl, str(catalog.path))
            logger.info("Wrote %d bars to catalog for %s %s", len(bars), sym, ivl)

            # Reload from catalog to get properly indexed data
            bar_type_str = _make_bar_type_str(sym, ivl)
            return catalog.bars(bar_types=[bar_type_str], start=self.start, end=self.end)
        except Exception:
            logger.warning("Failed to download bars for %s %s from Binance", sym, ivl, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Fill model & latency model builders
    # ------------------------------------------------------------------

    _FILL_MODEL_CLASSES: dict[str, str] = {
        "default": "FillModel",
        "best_price": "BestPriceFillModel",
        "one_tick_slippage": "OneTickSlippageFillModel",
        "three_tier": "ThreeTierFillModel",
    }

    @staticmethod
    def _build_fill_model(config: dict[str, Any]) -> FillModel:
        """Build a FillModel from config dict.

        Supports ``fill_model_type`` key to select model class:
          - ``"default"`` → FillModel (probabilistic)
          - ``"best_price"`` → BestPriceFillModel (fills inside spread)
          - ``"one_tick_slippage"`` → OneTickSlippageFillModel
          - ``"three_tier"`` → ThreeTierFillModel (50/30/20 distribution)
        """
        model_type = config.get("fill_model_type", "default")

        if model_type == "best_price":
            from nautilus_trader.backtest.models import BestPriceFillModel
            return BestPriceFillModel()
        elif model_type == "one_tick_slippage":
            from nautilus_trader.backtest.models import OneTickSlippageFillModel
            return OneTickSlippageFillModel()
        elif model_type == "three_tier":
            from nautilus_trader.backtest.models import ThreeTierFillModel
            return ThreeTierFillModel()
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

    def _load_auxiliary_price_data(
        self, engine: BacktestEngine, nt_symbols: list[str],
    ) -> None:
        """Load mark price and index price data for all symbols.

        Fetches from Binance API and injects as MarkPriceUpdate / IndexPriceUpdate
        so strategies can subscribe via ``subscribe_data(DataType(MarkPriceUpdate))``.
        Uses the same interval as the primary bar data.
        """
        import asyncio
        from tinohelm.data.providers.binance import (
            fetch_mark_price_klines,
            fetch_index_price_klines,
        )

        ivl = self.intervals[0] if self.intervals else "1h"
        total_mark = 0
        total_index = 0

        for sym, nt_sym in zip(self.symbols, nt_symbols):
            # Mark price
            try:
                mark_klines = asyncio.run(fetch_mark_price_klines(
                    symbol=sym, interval=ivl, start=self.start, end=self.end,
                ))
                if mark_klines:
                    updates = self._build_mark_price_updates(mark_klines, nt_sym)
                    if updates:
                        engine.add_data(updates, sort=False)
                        total_mark += len(updates)
            except Exception:
                logger.debug("Failed to load mark price for %s", sym, exc_info=True)

            # Index price
            try:
                index_klines = asyncio.run(fetch_index_price_klines(
                    symbol=sym, interval=ivl, start=self.start, end=self.end,
                ))
                if index_klines:
                    updates = self._build_index_price_updates(index_klines, nt_sym)
                    if updates:
                        engine.add_data(updates, sort=False)
                        total_index += len(updates)
            except Exception:
                logger.debug("Failed to load index price for %s", sym, exc_info=True)

        if total_mark or total_index:
            logger.info(
                "Auxiliary price data loaded: %d MarkPriceUpdate, %d IndexPriceUpdate",
                total_mark, total_index,
            )

    @staticmethod
    def _build_mark_price_updates(klines: list[dict], nt_symbol: str) -> list:
        """Convert mark price klines to NT MarkPriceUpdate objects."""
        try:
            from nautilus_trader.model.data import MarkPriceUpdate
            from nautilus_trader.model.identifiers import InstrumentId
            from nautilus_trader.model.objects import Price

            inst_id = InstrumentId.from_str(nt_symbol)
            updates = []
            for k in klines:
                close_time_ns = int(k["close_time"]) * 1_000_000  # ms → ns
                updates.append(MarkPriceUpdate(
                    instrument_id=inst_id,
                    value=Price.from_str(str(k["close"])),
                    ts_event=close_time_ns,
                    ts_init=close_time_ns,
                ))
            return updates
        except Exception:
            logger.warning("Failed to build MarkPriceUpdate objects", exc_info=True)
            return []

    @staticmethod
    def _build_index_price_updates(klines: list[dict], nt_symbol: str) -> list:
        """Convert index price klines to NT IndexPriceUpdate objects."""
        try:
            from nautilus_trader.model.data import IndexPriceUpdate
            from nautilus_trader.model.identifiers import InstrumentId
            from nautilus_trader.model.objects import Price

            inst_id = InstrumentId.from_str(nt_symbol)
            updates = []
            for k in klines:
                close_time_ns = int(k["close_time"]) * 1_000_000
                updates.append(IndexPriceUpdate(
                    instrument_id=inst_id,
                    value=Price.from_str(str(k["close"])),
                    ts_event=close_time_ns,
                    ts_init=close_time_ns,
                ))
            return updates
        except Exception:
            logger.warning("Failed to build IndexPriceUpdate objects", exc_info=True)
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
                ))
            return updates
        except Exception:
            logger.warning("Failed to build FundingRateUpdate objects", exc_info=True)
            return []

    def _load_funding_rates(self, nt_symbols: list[str]) -> list[dict]:
        """Load historical funding rates for all symbols with local caching.

        Uses ``funding_cache`` for persistent storage and incremental updates —
        only fetches new data from Binance when the cache doesn't cover the
        requested date range.

        Returns a list of funding events sorted by timestamp_ns, ready for the
        FundingCostTracker actor.
        """
        from datetime import datetime as _dt, timezone
        from tinohelm.data.funding_cache import load_funding_rates

        all_events: list[dict] = []
        for sym, nt_sym in zip(self.symbols, nt_symbols):
            try:
                rates = load_funding_rates(
                    symbol=sym,
                    start=self.start,
                    end=self.end,
                )
                for r in rates:
                    ts_ms = r["funding_time_ms"]
                    mark_price = r["mark_price"]
                    # If mark_price is missing/zero, skip (can't calculate notional)
                    if not mark_price:
                        continue
                    ts_iso = _dt.fromtimestamp(
                        ts_ms / 1000, tz=timezone.utc,
                    ).isoformat()
                    all_events.append({
                        "timestamp_ns": ts_ms * 1_000_000,  # ms → ns
                        "timestamp_iso": ts_iso,
                        "symbol": nt_sym,
                        "rate": r["funding_rate"],
                        "mark_price": mark_price,
                    })
                logger.info(
                    "Loaded %d funding rate events for %s", len(rates), sym,
                )
            except Exception:
                logger.warning(
                    "Failed to load funding rates for %s, skipping", sym,
                    exc_info=True,
                )

        # Sort by timestamp for sequential processing in the actor
        all_events.sort(key=lambda e: e["timestamp_ns"])
        return all_events

    def _build_portfolio_config(self):
        """Build a PortfolioConfig from legacy constructor params if not provided."""
        if self._portfolio_config is not None:
            return self._portfolio_config

        from tinohelm.portfolio.config import PortfolioConfig, AccountSettings, load_portfolio_config

        starting_balance = self.strategy_params.get("starting_balance", 10000)
        leverage = self.strategy_params.get("leverage", 1)

        # Detect portfolio strategy (strategy_path points to portfolio.yaml)
        file_part = self.strategy_path.rsplit(":", 1)[0] if self.strategy_path else ""
        if file_part.endswith("portfolio.yaml"):
            folder = Path(file_part).parent
            config = load_portfolio_config(str(folder))
            # Override account settings from job params
            config.account = AccountSettings(
                starting_balance=starting_balance,
                currency=config.account.currency,
                leverage=leverage,
            )
            # Merge additional strategy params from the job (override yaml defaults)
            for k, v in self.strategy_params.items():
                if k not in ("starting_balance", "leverage"):
                    config.params[k] = v
            # Override symbols/interval from job if provided
            if self.symbols:
                config.symbols = self.symbols
            if self.intervals:
                config.interval = self.intervals[0]
            return config

        # For legacy mode, strategy_path is "file:ClassName", config_path is "file:ConfigName"
        return PortfolioConfig(
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

    def run(self) -> dict[str, Any]:
        """Execute the backtest and return results dict."""
        logger.info("Starting backtest: %s on %s", self.strategy_path, self.symbols)

        portfolio_config = self._build_portfolio_config()

        # Sync symbols/intervals from portfolio config when not provided by the job
        # (portfolio strategies define their own symbols/interval in portfolio.yaml)
        if not self.symbols and portfolio_config.symbols:
            self.symbols = portfolio_config.symbols
            self.symbol = self.symbols[0] if self.symbols else ""
        if not self.intervals and portfolio_config.interval:
            self.intervals = [portfolio_config.interval]
            self.interval = portfolio_config.interval

        # Configure engine
        engine_config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="WARNING"),
        )

        engine = BacktestEngine(config=engine_config)
        self._engine = engine

        # Build FillModel if config provided
        fill_model_obj: FillModel | None = None
        if self.fill_model_config is not None:
            fill_model_obj = self._build_fill_model(self.fill_model_config)

        # Build LatencyModel — default 30ms base latency for realistic simulation
        latency_config = self.fill_model_config or {}
        latency_model_obj = self._build_latency_model(latency_config)

        # Add venue — currency is assumed USDT for Binance futures
        from nautilus_trader.model.currencies import USDT
        from nautilus_trader.model.identifiers import Venue
        from nautilus_trader.model.objects import Money

        starting_balance = portfolio_config.account.starting_balance
        leverage = portfolio_config.account.leverage
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
        engine.add_venue(**venue_kwargs)

        # Load data from catalog
        catalog = ParquetDataCatalog(str(self.catalog_path))

        # Load instruments for each symbol
        nt_symbols: list[str] = []
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            nt_symbols.append(nt_sym)
            instruments = catalog.instruments(instrument_ids=[nt_sym])
            if instruments:
                for inst in instruments:
                    engine.add_instrument(inst)

        # Load bar data for each (symbol, interval) combination
        all_bar_type_strs: list[str] = []
        loaded_bar_type_strs: list[str] = []  # bar types with actual data (for progress)
        total_bar_count: int = 0
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            for ivl in self.intervals:
                bars, bt_str, source_bt_str = self._resolve_bars(
                    catalog, sym, nt_sym, ivl,
                )
                if bars:
                    total_bar_count += len(bars)
                    loaded_bar_type_strs.append(source_bt_str or bt_str)
                    engine.add_data(bars, sort=False)
                    all_bar_type_strs.append(bt_str)
                else:
                    logger.warning("No bar data available for %s at %s", sym, ivl)

        # Single efficient sort after all data is loaded (avoids O(n*k) re-sorting)
        if all_bar_type_strs:
            engine.sort_data()

        # Inject NT-format params for strategy config resolution
        self.strategy_params.setdefault("instrument_id", nt_symbols[0] if nt_symbols else "")
        self.strategy_params.setdefault("instrument_ids", nt_symbols)
        self.strategy_params.setdefault("bar_type", all_bar_type_strs[0] if all_bar_type_strs else "")
        self.strategy_params.setdefault("bar_types", all_bar_type_strs)

        # Build per-symbol bar_type map so the portfolio loader can pick up
        # composite bar types (e.g. INTERNAL@1-MINUTE-EXTERNAL) instead of
        # building plain EXTERNAL bar types that don't match loaded data.
        bar_type_map: dict[str, str] = {}
        for bt_str in all_bar_type_strs:
            # Extract NT symbol from bar_type string (everything before the interval part)
            # e.g. "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
            for ns in nt_symbols:
                if bt_str.startswith(ns):
                    bar_type_map[ns] = bt_str
                    break
        portfolio_config.params["_bar_type_map"] = bar_type_map

        # Create strategy instances via portfolio_loader
        strategy_instances = create_strategies(portfolio_config)
        for strategy_instance in strategy_instances:
            engine.add_strategy(strategy_instance)
        logger.info("Added %d strategy instance(s) to engine", len(strategy_instances))

        # Create and add actor instances via portfolio_loader
        actor_instances = create_actors(portfolio_config)
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

        # Add funding cost tracker + inject FundingRateUpdate data for perpetual futures
        self._funding_enabled = False
        if self.symbols and self.start and self.end:
            funding_events = self._load_funding_rates(nt_symbols)
            if funding_events:
                from tinohelm.backtest.funding import (
                    _FundingCostTracker,
                    _FundingCostTrackerConfig,
                )
                _FundingCostTracker._funding_events = funding_events
                _FundingCostTracker._bar_type_strs = loaded_bar_type_strs
                tracker = _FundingCostTracker(config=_FundingCostTrackerConfig())
                engine.add_actor(tracker)
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

        # Load mark price and index price data for strategies
        if self.symbols and self.start and self.end:
            self._load_auxiliary_price_data(engine, nt_symbols)

        # Add progress reporter actor for bar-level progress tracking
        if self._redis_client and self._run_id and total_bar_count > 0:
            _ProgressReporter._redis = self._redis_client
            _ProgressReporter._run_id = self._run_id
            _ProgressReporter._total_bars = total_bar_count
            _ProgressReporter._bar_count = 0
            _ProgressReporter._bar_type_strs = loaded_bar_type_strs
            reporter = _ProgressReporter(config=_ProgressReporterConfig())
            engine.add_actor(reporter)
            logger.info(
                "Progress reporter enabled: %d total bars, run_id=%s",
                total_bar_count, self._run_id[:8],
            )

        # Final sort — funding/mark/index data may have been added after initial sort
        engine.sort_data()

        # Run
        engine.run()

        # Extract results
        results = self._extract_results(engine, starting_balance)

        # Merge funding cost data into results
        if self._funding_enabled:
            from tinohelm.backtest.funding import _FundingCostTracker
            funding_data = _FundingCostTracker.get_results()
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
            self._generate_tearsheet(engine, all_bar_type_strs)
            self._enhance_tearsheet(results)

        # Cleanup
        engine.dispose()

        logger.info("Backtest complete: %s", self.strategy_path)
        return results

    # ------------------------------------------------------------------
    # Engine reuse API (for Optuna optimization — avoids reloading data)
    # ------------------------------------------------------------------

    def prepare_engine(self) -> tuple[BacktestEngine, Any, float]:
        """Create and configure engine with data loaded, but do NOT run.

        This is the "heavy" setup step — loads instruments, bar data, and
        configures the venue. Call once, then use :meth:`run_trial` per
        parameter set with ``engine.reset()`` between trials.

        Returns:
            (engine, portfolio_config, starting_balance)
        """
        portfolio_config = self._build_portfolio_config()

        if not self.symbols and portfolio_config.symbols:
            self.symbols = portfolio_config.symbols
            self.symbol = self.symbols[0] if self.symbols else ""
        if not self.intervals and portfolio_config.interval:
            self.intervals = [portfolio_config.interval]
            self.interval = portfolio_config.interval

        engine_config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="WARNING"),
        )
        engine = BacktestEngine(config=engine_config)
        self._engine = engine

        # Build FillModel / LatencyModel
        fill_model_obj: FillModel | None = None
        if self.fill_model_config is not None:
            fill_model_obj = self._build_fill_model(self.fill_model_config)
        latency_config = self.fill_model_config or {}
        latency_model_obj = self._build_latency_model(latency_config)

        from nautilus_trader.model.currencies import USDT
        from nautilus_trader.model.identifiers import Venue
        from nautilus_trader.model.objects import Money

        starting_balance = portfolio_config.account.starting_balance
        leverage = portfolio_config.account.leverage
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
        engine.add_venue(**venue_kwargs)

        # Load instruments + bar data
        catalog = ParquetDataCatalog(str(self.catalog_path))
        nt_symbols: list[str] = []
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            nt_symbols.append(nt_sym)
            instruments = catalog.instruments(instrument_ids=[nt_sym])
            if instruments:
                for inst in instruments:
                    engine.add_instrument(inst)

        all_bar_type_strs: list[str] = []
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            for ivl in self.intervals:
                bars, bt_str, source_bt_str = self._resolve_bars(
                    catalog, sym, nt_sym, ivl,
                )
                if bars:
                    engine.add_data(bars, sort=False)
                    all_bar_type_strs.append(bt_str)

        if all_bar_type_strs:
            engine.sort_data()

        # Store metadata for run_trial()
        self._nt_symbols = nt_symbols
        self._all_bar_type_strs = all_bar_type_strs

        # Inject NT-format defaults into strategy_params
        self.strategy_params.setdefault("instrument_id", nt_symbols[0] if nt_symbols else "")
        self.strategy_params.setdefault("instrument_ids", nt_symbols)
        self.strategy_params.setdefault("bar_type", all_bar_type_strs[0] if all_bar_type_strs else "")
        self.strategy_params.setdefault("bar_types", all_bar_type_strs)

        # Build bar_type_map
        bar_type_map: dict[str, str] = {}
        for bt_str in all_bar_type_strs:
            for ns in nt_symbols:
                if bt_str.startswith(ns):
                    bar_type_map[ns] = bt_str
                    break
        portfolio_config.params["_bar_type_map"] = bar_type_map

        logger.info(
            "Engine prepared: %d symbols, %d bar types",
            len(nt_symbols), len(all_bar_type_strs),
        )
        return engine, portfolio_config, starting_balance

    def run_trial(
        self,
        engine: BacktestEngine,
        portfolio_config: Any,
        starting_balance: float,
        trial_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a single trial on a prepared engine.

        Resets the engine, adds strategies with *trial_params*, runs, and
        extracts results.  Does NOT dispose the engine — caller owns that.
        """
        from copy import deepcopy

        engine.reset()

        # Merge trial params into portfolio config
        if trial_params:
            pc = deepcopy(portfolio_config)
            pc.params.update(trial_params)
        else:
            pc = portfolio_config

        # Ensure bar_type_map survives the copy
        if "_bar_type_map" not in pc.params and hasattr(self, "_all_bar_type_strs"):
            bar_type_map: dict[str, str] = {}
            for bt_str in self._all_bar_type_strs:
                for ns in self._nt_symbols:
                    if bt_str.startswith(ns):
                        bar_type_map[ns] = bt_str
                        break
            pc.params["_bar_type_map"] = bar_type_map

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
        return self._extract_results(engine, starting_balance)

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
            TearsheetConfig,
            TearsheetBarsWithFillsChart,
            TearsheetDrawdownChart,
            TearsheetEquityChart,
            TearsheetRunInfoChart,
            TearsheetStatsTableChart,
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
                theme="plotly_dark",
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

    def _enhance_tearsheet(self, results: dict[str, Any]) -> None:
        """Inject per-instrument performance breakdown into the tearsheet HTML.

        Adds a Plotly horizontal bar chart and a detailed summary table
        showing PnL, return %, win rate, and other per-symbol metrics.
        Only activates for multi-instrument (portfolio) backtests.
        """
        tearsheet_path = self.artifacts_dir / "tearsheet.html"
        if not tearsheet_path.exists():
            return

        per_instrument = results.get("per_instrument", {})
        if len(per_instrument) <= 1:
            return

        import json

        sorted_items = sorted(
            per_instrument.items(),
            key=lambda x: x[1].get("total_pnl", 0),
            reverse=True,
        )

        # Plotly data (reversed for bottom-to-top horizontal bar display)
        symbols = [k.replace(".BINANCE", "") for k, _ in reversed(sorted_items)]
        pnls = [round(v.get("total_pnl", 0), 2) for _, v in reversed(sorted_items)]
        colors = ["#00963c" if p >= 0 else "#c62828" for p in pnls]
        chart_height = max(300, len(sorted_items) * 35 + 100)

        trace = json.dumps([{
            "type": "bar", "orientation": "h",
            "y": symbols, "x": pnls,
            "marker": {"color": colors},
            "text": [f"{p:+.2f}" for p in pnls],
            "textposition": "outside",
            "hovertemplate": "%{y}: %{x:+.2f} USDT<extra></extra>",
        }])
        chart_layout = json.dumps({
            "title": {"text": "PnL by Instrument (USDT)", "font": {"size": 16}},
            "xaxis": {"title": "PnL", "zeroline": True, "zerolinecolor": "#ddd", "gridcolor": "#eee"},
            "yaxis": {"automargin": True},
            "margin": {"l": 130, "r": 80, "t": 50, "b": 40},
            "template": "plotly_white",
            "height": chart_height,
        })

        # Build HTML table rows (extended with Sharpe, MaxDD, Recovery)
        rows = []
        for inst_id, data in sorted_items:
            short = inst_id.replace(".BINANCE", "")
            pnl = data.get("total_pnl", 0)
            ret = data.get("return_pct", 0)
            trades = data.get("total_trades", 0)
            wr = data.get("win_rate", 0) * 100
            pf = data.get("profit_factor")
            lg_w = data.get("largest_win")
            lg_l = data.get("largest_loss")
            avg = data.get("avg_pnl")
            sr = data.get("sharpe_ratio")
            mdd = data.get("max_drawdown")
            rf = data.get("recovery_factor")

            pc = "pos" if pnl >= 0 else "neg"
            rc = "pos" if ret >= 0 else "neg"
            _d = "\u2013"
            pf_s = f"{pf:.2f}" if pf is not None else _d
            lw_s = f"{lg_w:+.2f}" if lg_w is not None else _d
            ll_s = f"{lg_l:.2f}" if lg_l is not None else _d
            avg_s = f"{avg:+.2f}" if avg is not None else _d
            sr_s = f"{sr:.2f}" if sr is not None else _d
            mdd_s = f"{mdd * 100:.1f}%" if mdd is not None else _d
            rf_s = f"{rf:.1f}" if rf is not None else _d

            rows.append(
                f'<tr><td class="sym">{short}</td>'
                f'<td class="{pc}">{pnl:+.2f}</td>'
                f'<td class="{rc}">{ret:+.2f}%</td>'
                f'<td>{trades}</td><td>{wr:.1f}%</td>'
                f'<td>{pf_s}</td><td>{sr_s}</td>'
                f'<td>{mdd_s}</td><td>{rf_s}</td>'
                f'<td class="pos">{lw_s}</td>'
                f'<td class="neg">{ll_s}</td>'
                f'<td>{avg_s}</td></tr>'
            )

        # ── Additional charts ──

        # Chart 2: Cumulative PnL stacked area
        cum_chart_html = ""
        cum_pnl_data = results.get("instrument_cumulative_pnl", {})
        if cum_pnl_data:
            traces = []
            for inst_id in sorted(cum_pnl_data.keys()):
                curve = cum_pnl_data[inst_id]
                short = inst_id.replace(".BINANCE", "")
                traces.append({
                    "type": "scatter", "mode": "lines",
                    "name": short,
                    "x": [p["date"] for p in curve],
                    "y": [p["cum_pnl"] for p in curve],
                    "stackgroup": "one",
                    "hovertemplate": f"{short}: %{{y:+.2f}} USDT<extra></extra>",
                })
            cum_trace = json.dumps(traces)
            cum_layout = json.dumps({
                "title": {"text": "Cumulative PnL by Instrument", "font": {"size": 16}},
                "xaxis": {"title": "Date"},
                "yaxis": {"title": "Cumulative PnL (USDT)"},
                "template": "plotly_white", "height": 400,
                "margin": {"l": 80, "r": 40, "t": 50, "b": 50},
            })
            cum_chart_html = (
                f'<div id="th-cum-pnl" style="width:100%;height:400px;margin-top:30px"></div>'
                f'<script>Plotly.newPlot("th-cum-pnl",{cum_trace},{cum_layout},{{responsive:true}})</script>'
            )

        # Chart 3: Correlation heatmap
        corr_chart_html = ""
        corr_data = results.get("instrument_correlation", {})
        if corr_data and len(corr_data) >= 2:
            insts = sorted(corr_data.keys())
            short_names = [i.replace(".BINANCE", "") for i in insts]
            z, text_m = [], []
            for inst_i in insts:
                row, trow = [], []
                for inst_j in insts:
                    val = 1.0 if inst_i == inst_j else corr_data.get(inst_i, {}).get(inst_j, 0)
                    row.append(val)
                    trow.append(f"{val:.2f}")
                z.append(row)
                text_m.append(trow)
            ch = max(350, len(insts) * 50 + 150)
            corr_trace = json.dumps([{
                "type": "heatmap", "z": z, "x": short_names, "y": short_names,
                "colorscale": "RdBu", "zmid": 0, "zmin": -1, "zmax": 1,
                "text": text_m, "texttemplate": "%{text}",
                "colorbar": {"title": "Corr"},
                "hovertemplate": "%{x} vs %{y}: %{z:.4f}<extra></extra>",
            }])
            corr_layout = json.dumps({
                "title": {"text": "Return Correlation Matrix", "font": {"size": 16}},
                "template": "plotly_white", "height": ch,
                "margin": {"l": 130, "r": 60, "t": 50, "b": 100},
            })
            corr_chart_html = (
                f'<div id="th-corr" style="width:100%;height:{ch}px;margin-top:30px"></div>'
                f'<script>Plotly.newPlot("th-corr",{corr_trace},{corr_layout},{{responsive:true}})</script>'
            )

        # Chart 4: Monthly PnL heatmap (instrument x month)
        heat_chart_html = ""
        heatmap_data = results.get("monthly_pnl_heatmap", [])
        if heatmap_data:
            h_insts = sorted({d["instrument"] for d in heatmap_data})
            h_months = sorted({d["month"] for d in heatmap_data})
            h_short = [i.replace(".BINANCE", "") for i in h_insts]
            lookup = {(d["instrument"], d["month"]): d["pnl"] for d in heatmap_data}
            z, text_m = [], []
            for inst in h_insts:
                row, trow = [], []
                for month in h_months:
                    val = lookup.get((inst, month), 0)
                    row.append(val)
                    trow.append(f"{val:+.0f}")
                z.append(row)
                text_m.append(trow)
            hh = max(300, len(h_insts) * 40 + 150)
            heat_trace = json.dumps([{
                "type": "heatmap", "z": z, "x": h_months, "y": h_short,
                "colorscale": "RdYlGn", "zmid": 0,
                "text": text_m, "texttemplate": "%{text}",
                "hovertemplate": "%{y} %{x}: %{z:+.2f} USDT<extra></extra>",
            }])
            heat_layout = json.dumps({
                "title": {"text": "Monthly PnL Heatmap (Instrument \u00d7 Month)", "font": {"size": 16}},
                "xaxis": {"title": "Month"},
                "yaxis": {"automargin": True},
                "template": "plotly_white", "height": hh,
                "margin": {"l": 130, "r": 60, "t": 50, "b": 60},
            })
            heat_chart_html = (
                f'<div id="th-monthly-heat" style="width:100%;height:{hh}px;margin-top:30px"></div>'
                f'<script>Plotly.newPlot("th-monthly-heat",{heat_trace},{heat_layout},{{responsive:true}})</script>'
            )

        # Chart 5: PnL Treemap (proportional boxes by contribution)
        treemap_html = ""
        if len(sorted_items) >= 2:
            tm_labels = [k.replace(".BINANCE", "") for k, _ in sorted_items]
            tm_pnls = [round(v.get("total_pnl", 0), 2) for _, v in sorted_items]
            tm_abs = [abs(p) for p in tm_pnls]
            tm_parents = ["Portfolio"] * len(tm_labels)
            tm_text = [f"{p:+.2f}" for p in tm_pnls]
            tm_colors = ["#00963c" if p >= 0 else "#c62828" for p in tm_pnls]
            treemap_trace = json.dumps([{
                "type": "treemap",
                "labels": tm_labels,
                "parents": tm_parents,
                "values": tm_abs,
                "text": tm_text,
                "texttemplate": "<b>%{label}</b><br>%{text} USDT",
                "marker": {"colors": tm_colors},
                "hovertemplate": "%{label}: %{text} USDT<extra></extra>",
            }])
            treemap_layout = json.dumps({
                "title": {"text": "PnL Contribution Treemap", "font": {"size": 16}},
                "template": "plotly_white", "height": 400,
                "margin": {"l": 10, "r": 10, "t": 50, "b": 10},
            })
            treemap_html = (
                f'<div id="th-treemap" style="width:100%;height:400px;margin-top:30px"></div>'
                f'<script>Plotly.newPlot("th-treemap",{treemap_trace},{treemap_layout},{{responsive:true}})</script>'
            )

        # Portfolio analytics summary
        pa = results.get("portfolio_analytics", {})
        analytics_html = ""
        if pa:
            dr = pa.get("diversification_ratio")
            db = pa.get("diversification_benefit_pct")
            parts = []
            if dr is not None:
                parts.append(f"<b>Diversification Ratio:</b> {dr:.2f}")
            if db is not None:
                parts.append(f"<b>Diversification Benefit:</b> {db:.1f}%")
            if parts:
                analytics_html = (
                    '<div style="margin-top:20px;padding:16px;background:#f9f9f9;'
                    'border-radius:8px;font-size:14px;">'
                    + " &nbsp;\u2502&nbsp; ".join(parts)
                    + "</div>"
                )

        section = f"""
<!-- TinoHelm: Per-Instrument Breakdown -->
<style>
.th-inst {{ max-width:1200px; margin:40px auto; padding:0 20px;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
.th-inst h2 {{ color:#333; border-bottom:3px solid #ffb000; padding-bottom:10px; font-size:20px; }}
.th-inst h3 {{ color:#555; margin-top:30px; font-size:16px; }}
.th-inst table {{ width:100%; border-collapse:collapse; margin-top:16px; font-size:13px; }}
.th-inst th {{ padding:10px 12px; text-align:left; border-bottom:2px solid #ffb000;
  font-weight:600; color:#555; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
.th-inst td {{ padding:8px 12px; border-bottom:1px solid #eee; }}
.th-inst tbody tr:hover {{ background:#f7f7f7; }}
.th-inst .sym {{ font-weight:600; color:#2c6fbb; }}
.th-inst .pos {{ color:#00963c; font-weight:600; }}
.th-inst .neg {{ color:#c62828; font-weight:600; }}
</style>
<div class="th-inst">
<h2>Per-Instrument Performance</h2>
{analytics_html}
<div id="th-inst-chart" style="width:100%;height:{chart_height}px"></div>
<script>Plotly.newPlot('th-inst-chart',{trace},{chart_layout},{{responsive:true}})</script>
<table><thead><tr>
<th>Symbol</th><th>PnL</th><th>Return</th><th>Trades</th><th>Win Rate</th>
<th>PF</th><th>Sharpe</th><th>MaxDD</th><th>Recovery</th>
<th>Best</th><th>Worst</th><th>Avg PnL</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table>
{cum_chart_html}
{treemap_html}
{corr_chart_html}
{heat_chart_html}
</div>
"""
        try:
            html = tearsheet_path.read_text(encoding="utf-8")
            html = html.replace("</body>", section + "\n</body>")
            tearsheet_path.write_text(html, encoding="utf-8")
            logger.info(
                "Enhanced tearsheet with per-instrument breakdown (%d instruments, %d charts)",
                len(sorted_items),
                1 + bool(cum_chart_html) + bool(corr_chart_html) + bool(heat_chart_html),
            )
        except Exception:
            logger.warning("Failed to enhance tearsheet", exc_info=True)

    def _extract_results(self, engine: BacktestEngine, starting_balance: float = 10000) -> dict[str, Any]:
        """Extract results from completed backtest engine."""
        from tinohelm.backtest.result import extract_backtest_results
        return extract_backtest_results(engine, starting_balance=starting_balance)
