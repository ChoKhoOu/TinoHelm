"""Backtest runner using NautilusTrader BacktestNode."""
from __future__ import annotations

import logging
from datetime import datetime
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

    async def run(self) -> dict[str, Any]:
        """Execute the backtest and return results dict."""
        logger.info("Starting backtest: %s on %s", self.strategy_path, self.symbols)
        return self._run_via_backtest_node()

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
        node.run()
        engine = node.get_engine(run_config.id)
        if engine is None:
            raise RuntimeError("BacktestNode did not produce an engine for result extraction")
        self._nt_symbols = nt_symbols
        self._all_bar_type_strs = []
        self._loaded_bar_type_strs = []
        self._total_bar_count = 0
        self._benchmark_daily_closes = {}
        results = self._extract_results(engine, starting_balance)
        if self._before_artifact_export is not None:
            self._before_artifact_export()
        if self.artifacts_dir is not None:
            self._export_reports(engine)
            self._generate_tearsheet(engine, self._loaded_bar_type_strs)
            from tinohelm.backtest.tearsheet import enhance_tearsheet
            enhance_tearsheet(self.artifacts_dir, results)
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
