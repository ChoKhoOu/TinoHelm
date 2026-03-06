"""Backtest runner wrapping NautilusTrader BacktestEngine."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
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

        # Portfolio config (explicit or auto-wrapped from legacy params)
        self._portfolio_config = portfolio_config

    def _build_portfolio_config(self):
        """Build a PortfolioConfig from legacy constructor params if not provided."""
        if self._portfolio_config is not None:
            return self._portfolio_config

        from tinohelm.portfolio.config import PortfolioConfig, AccountSettings

        starting_balance = self.strategy_params.get("starting_balance", 10000)
        leverage = self.strategy_params.get("leverage", 1)

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
            fill_model_obj = FillModel(
                prob_fill_on_limit=self.fill_model_config.get("prob_fill_on_limit", 1.0),
                prob_fill_on_stop=self.fill_model_config.get("prob_fill_on_stop", 1.0),
                prob_slippage=self.fill_model_config.get("prob_slippage", 0.0),
                random_seed=self.fill_model_config.get("random_seed", None),
            )

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
        }
        if fill_model_obj is not None:
            venue_kwargs["fill_model"] = fill_model_obj
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
        for sym in self.symbols:
            nt_sym = _normalize_symbol(sym)
            for ivl in self.intervals:
                bar_type_str = _make_bar_type_str(sym, ivl)

                # Try loading direct bar data first
                bars = catalog.bars(
                    bar_types=[bar_type_str],
                    start=self.start,
                    end=self.end,
                )
                if bars:
                    engine.add_data(bars)
                    all_bar_type_strs.append(bar_type_str)
                elif ivl != "1m":
                    # Fall back to 1m and use NT composite aggregation
                    source_bar_type_str = _make_bar_type_str(sym, "1m")
                    bars = catalog.bars(
                        bar_types=[source_bar_type_str],
                        start=self.start,
                        end=self.end,
                    )
                    if bars:
                        engine.add_data(bars)
                        interval_part = _INTERVAL_MAP.get(ivl, "1-MINUTE")
                        composite_bar_type_str = (
                            f"{nt_sym}-{interval_part}-LAST-INTERNAL@1-MINUTE-EXTERNAL"
                        )
                        all_bar_type_strs.append(composite_bar_type_str)
                        logger.info(
                            "No %s data found for %s, using 1m composite aggregation: %s",
                            ivl, sym, composite_bar_type_str,
                        )
                    else:
                        logger.warning(
                            "No bar data found for %s (tried %s and 1m)", sym, ivl,
                        )
                else:
                    logger.warning("No bar data found for %s at %s", sym, ivl)

        # Inject NT-format params for strategy config resolution
        self.strategy_params.setdefault("instrument_id", nt_symbols[0] if nt_symbols else "")
        self.strategy_params.setdefault("instrument_ids", nt_symbols)
        self.strategy_params.setdefault("bar_type", all_bar_type_strs[0] if all_bar_type_strs else "")
        self.strategy_params.setdefault("bar_types", all_bar_type_strs)

        # Create strategy instances via portfolio_loader
        strategy_instances = create_strategies(portfolio_config)
        for strategy_instance in strategy_instances:
            engine.add_strategy(strategy_instance)
        logger.info("Added %d strategy instance(s) to engine", len(strategy_instances))

        # Create and add actor instances via portfolio_loader
        actor_instances = create_actors(portfolio_config)
        for actor_instance in actor_instances:
            engine.add_actor(actor_instance)
        if actor_instances:
            logger.info("Added %d actor(s) to engine", len(actor_instances))

        # Register additional statistics BEFORE running
        try:
            from nautilus_trader.analysis.statistics.max_drawdown import MaxDrawdown
            from nautilus_trader.analysis.statistics.calmar_ratio import CalmarRatio
            from nautilus_trader.analysis.statistics.cagr import CAGR

            engine.portfolio.analyzer.register_statistic(MaxDrawdown())
            engine.portfolio.analyzer.register_statistic(CalmarRatio())
            engine.portfolio.analyzer.register_statistic(CAGR())
        except Exception:
            logger.warning("Failed to register extra analyzer statistics", exc_info=True)

        # Run
        engine.run()

        # Extract results
        results = self._extract_results(engine, starting_balance)

        # Export raw reports before dispose (if artifacts_dir is set)
        if self.artifacts_dir is not None:
            self._export_reports(engine)
            self._generate_tearsheet(engine, all_bar_type_strs)

        # Cleanup
        engine.dispose()

        logger.info("Backtest complete: %s", self.strategy_path)
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
        try:
            from nautilus_trader.analysis import (
                create_tearsheet,
                TearsheetConfig,
                TearsheetBarsWithFillsChart,
                TearsheetDrawdownChart,
                TearsheetEquityChart,
                TearsheetRunInfoChart,
                TearsheetStatsTableChart,
            )
        except ImportError:
            logger.info("Tearsheet skipped: plotly not installed (pip install plotly>=6.3.1)")
            return

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
                theme="plotly_white",
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

    def _extract_results(self, engine: BacktestEngine, starting_balance: float = 10000) -> dict[str, Any]:
        """Extract results from completed backtest engine."""
        from tinohelm.backtest.result import extract_backtest_results
        return extract_backtest_results(engine, starting_balance=starting_balance)
