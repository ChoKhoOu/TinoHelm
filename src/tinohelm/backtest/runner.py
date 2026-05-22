"""Backtest runner using NautilusTrader BacktestNode."""
from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from nautilus_trader.backtest.engine import BacktestEngine
    _NT_AVAILABLE = True
    _NT_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    BacktestEngine = None  # type: ignore[assignment]
    _NT_AVAILABLE = False
    _NT_IMPORT_ERROR = exc

from tinohelm.strategy.loader import normalize_symbol as _normalize_symbol

logger = logging.getLogger(__name__)


def _require_nt() -> None:
    if not _NT_AVAILABLE:
        raise RuntimeError("nautilus_trader is required for backtest execution") from _NT_IMPORT_ERROR


def _ordered_unique_extra(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


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
        get_settings()
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
        self.strategy_bundle = strategy_bundle
        self.fill_model_config = fill_model
        self.warmup_bars = warmup_bars
        self.tags = tags
        self.data_type = data_type
        self.extra_data_types = list(extra_data_types or [])
        self.artifacts_dir: Path | None = None
        self._before_artifact_export: Callable[[], None] | None = None
        self._engine: BacktestEngine | None = None
        self._redis_client = None
        self._run_id: str = ""
        self._job_start_time: float = 0.0
        self._funding_tracker = None

    async def run(self) -> dict[str, Any]:
        """Execute the backtest and return results dict."""
        logger.info("Starting backtest: %s on %s", self.strategy_path, self.symbols)
        return self._run_via_backtest_node()

    def _build_strategy_bundle(self):
        from tinohelm.portfolio.config import AccountSettings, StrategyBundle

        if self.strategy_bundle is not None:
            return self.strategy_bundle

        return StrategyBundle(
            strategy_class=self.strategy_path,
            config_class=self.config_path,
            symbols=list(self.symbols),
            interval=self.interval,
            params=dict(self.strategy_params),
            account=AccountSettings(
                starting_balance=float(self.strategy_params.get("starting_balance", 10000)),
                leverage=int(self.strategy_params.get("leverage", 1)),
            ),
            implicit=True,
        )

    def _run_via_backtest_node(self) -> dict[str, Any]:
        from nautilus_trader.backtest.config import (
            BacktestDataConfig,
            BacktestRunConfig,
            BacktestVenueConfig,
        )
        from nautilus_trader.backtest.node import BacktestNode
        from nautilus_trader.config import BacktestEngineConfig, ImportableStrategyConfig, LoggingConfig
        from nautilus_trader.model.identifiers import TraderId

        nt_symbols = [_normalize_symbol(sym) for sym in self.symbols]
        starting_balance = float(self.strategy_params.get("starting_balance", 10000))
        leverage = float(self.strategy_params.get("leverage", 1))

        strategy_config = ImportableStrategyConfig(
            strategy_path=self.strategy_path,
            config_path=self.config_path,
            # Force interval empty so pure-NT strategies ignore any caller-supplied
            # timeframe override and derive their own composite subscriptions.
            config=dict(self.strategy_params) | {"symbols": self.symbols, "interval": ""},
        )
        venue_config = BacktestVenueConfig(
            name="BINANCE",
            oms_type="HEDGING",
            account_type="MARGIN",
            starting_balances=[f"{starting_balance} USDT"],
            default_leverage=leverage,
            bar_execution=True,
            trade_execution=True,
        )
        data_config = BacktestDataConfig(
            catalog_path=str(self.catalog_path),
            data_cls="nautilus_trader.model.data:Bar",
            instrument_ids=nt_symbols or None,
            bar_spec="1-MINUTE-LAST",
            start_time=self.start.isoformat() if self.start else None,
            end_time=self.end.isoformat() if self.end else None,
        )
        engine_config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="WARNING"),
            strategies=[strategy_config],
        )
        run_config = BacktestRunConfig(
            engine=engine_config,
            venues=[venue_config],
            data=[data_config],
            start=self.start.isoformat() if self.start else None,
            end=self.end.isoformat() if self.end else None,
        )
        node = BacktestNode(configs=[run_config])
        node.build()
        engine = node.get_engine(run_config.id)
        if engine is None:
            raise RuntimeError("BacktestNode did not produce an engine for result extraction")

        self._engine = engine
        self._nt_symbols = nt_symbols
        self._all_bar_type_strs = []
        self._loaded_bar_type_strs = []
        self._total_bar_count = 0
        self._benchmark_daily_closes = {}

        self._register_analyzer_statistics(engine)
        self._funding_tracker = self._prepare_funding_tracker(engine)

        node.run()

        results = self._extract_results(engine, starting_balance)
        results = self._merge_funding_results(results)
        if self._before_artifact_export is not None:
            self._before_artifact_export()
        if self.artifacts_dir is not None:
            self._export_reports(engine)
            self._generate_tearsheet(engine, self._loaded_bar_type_strs)
            from tinohelm.backtest.tearsheet import enhance_tearsheet
            enhance_tearsheet(self.artifacts_dir, results)
        return results

    def _register_analyzer_statistics(self, engine: BacktestEngine) -> None:
        analyzer = engine.portfolio.analyzer
        try:
            from nautilus_trader.analysis import MaxDrawdown, CalmarRatio, CAGR, ProfitFactor

            analyzer.register_statistic(MaxDrawdown())
            analyzer.register_statistic(CalmarRatio())
            analyzer.register_statistic(CAGR())
            analyzer.register_statistic(ProfitFactor())
        except Exception:
            logger.warning("Failed to register built-in backtest statistics", exc_info=True)

        try:
            from tinohelm.backtest.custom_statistics import register_custom_statistics

            register_custom_statistics(analyzer)
        except Exception:
            logger.warning("Failed to register custom statistics", exc_info=True)

    @staticmethod
    def _build_funding_events(nt_symbol: str, funding_rows: list[Any], mark_rows: list[Any]) -> list[dict[str, Any]]:
        if not funding_rows or not mark_rows:
            return []

        mark_times = [int(getattr(row, "ts_event", 0)) for row in mark_rows]
        mark_prices = [
            float(row.value.as_double()) if hasattr(row.value, "as_double") else float(row.value)
            for row in mark_rows
        ]
        events: list[dict[str, Any]] = []

        for row in funding_rows:
            ts_ns = int(getattr(row, "ts_event", 0))
            mark_idx = bisect_right(mark_times, ts_ns) - 1
            if mark_idx < 0:
                continue
            events.append({
                "timestamp_ns": ts_ns,
                "timestamp_iso": datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).isoformat(),
                "symbol": nt_symbol,
                "rate": float(getattr(row, "rate")),
                "mark_price": mark_prices[mark_idx],
                "funding_interval_minutes": getattr(row, "interval", None),
            })

        return events

    def _load_funding_rates(self, nt_symbols: list[str]) -> list[dict[str, Any]]:
        from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from tinohelm.data.catalog import _catalog_for_root

        if not self.start or not self.end:
            return []

        catalog = _catalog_for_root(self.catalog_path, self._storage)
        events: list[dict[str, Any]] = []

        for nt_symbol in nt_symbols:
            instrument_id = str(InstrumentId.from_str(nt_symbol))
            funding_rows = sorted(
                catalog.query(
                    FundingRateUpdate,
                    identifiers=[instrument_id],
                    start=self.start,
                    end=self.end,
                )
                or [],
                key=lambda row: int(getattr(row, "ts_event", 0)),
            )
            if not funding_rows:
                continue

            mark_lookback = 8 * 60
            first_interval = getattr(funding_rows[0], "interval", None)
            if first_interval is not None:
                try:
                    mark_lookback = int(first_interval)
                except Exception:
                    pass

            mark_rows = sorted(
                catalog.query(
                    MarkPriceUpdate,
                    identifiers=[instrument_id],
                    start=self.start - timedelta(minutes=mark_lookback),
                    end=self.end,
                )
                or [],
                key=lambda row: int(getattr(row, "ts_event", 0)),
            )
            if not mark_rows:
                logger.warning("Funding updates exist but mark prices are missing for %s", nt_symbol)
                continue

            events.extend(self._build_funding_events(nt_symbol, funding_rows, mark_rows))

        return events

    def _prepare_funding_tracker(self, engine: BacktestEngine):
        from tinohelm.backtest.funding import _FundingCostTracker, _FundingCostTrackerConfig

        funding_events = self._load_funding_rates(self._nt_symbols)
        if not funding_events:
            return None

        _FundingCostTracker._funding_events = funding_events
        _FundingCostTracker._bar_type_strs = self._loaded_bar_type_strs
        tracker = _FundingCostTracker(config=_FundingCostTrackerConfig())
        engine.add_actor(tracker)
        return tracker

    def _merge_funding_results(self, results: dict[str, Any]) -> dict[str, Any]:
        if self._funding_tracker is None:
            return results

        funding_data = self._funding_tracker.get_results()
        results["funding"] = funding_data
        stats = results.setdefault("statistics", {})
        funding_cost = funding_data["total_funding_cost"]
        stats["total_funding_cost"] = funding_cost
        stats["pnl_after_funding"] = round((stats.get("total_pnl", 0.0) or 0.0) - funding_cost, 4)
        return results

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
