"""Backtest result extraction from NautilusTrader BacktestEngine."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine

from tinohelm.backtest.result.sections import (
    build_equity_curve,
    compute_annual_returns,
    compute_benchmark_daily_returns,
    compute_benchmark_equity_curve,
    compute_benchmark_relative_metrics,
    compute_cumulative_trade_pnl,
    compute_drawdown_periods,
    compute_extended_statistics,
    compute_holding_time_distribution,
    compute_long_vs_short,
    compute_mae_mfe,
    compute_per_instrument_advanced,
    compute_per_instrument_basic,
    compute_periodic_returns,
    compute_qq_plot_data,
    compute_return_by_dow,
    compute_return_by_hour,
    compute_returns_distribution,
    compute_robustness as _compute_robustness_block,
    compute_streak_sequence,
    compute_trade_pnl_distribution,
    compute_trade_pnl_scatter,
    compute_trade_scalar_metrics,
    recompute_risk_metrics_from_equity_curve,
)
from tinohelm.backtest.result.statistics import (
    _compute_rolling_series,
    _compute_streaks,
    _format_duration_ns,
    _format_ns_timestamp,
    _format_order_side,
    _make_rolling_beta_fn,
    _parse_realized_pnl,
    _rolling_cumret_fn,
    _rolling_sharpe_fn,
    _rolling_sortino_fn,
    _rolling_volatility_fn,
    _safe_float,
)

logger = logging.getLogger(__name__)


def _collect_bars_by_instrument(
    engine: BacktestEngine,
) -> dict[str, list[tuple[int, float, float]]]:
    """Flatten NT bar cache into primitive ``{instrument: [(ts_init, high, low), ...]}`` tuples.

    All bar types for a given instrument are merged.  Used as input to the
    pure ``compute_mae_mfe`` helper so that MAE/MFE logic is NT-independent
    and unit-testable.
    """
    inst_bars: dict[str, list[tuple[int, float, float]]] = {}
    for bt in engine.cache.bar_types():
        bars = engine.cache.bars(bt)
        if not bars:
            continue
        inst_key = str(bt.instrument_id)
        target = inst_bars.setdefault(inst_key, [])
        for bar in bars:
            target.append((int(bar.ts_init), float(bar.high), float(bar.low)))
    return inst_bars


def _build_inst_daily_close_from_cache(
    engine: BacktestEngine,
    per_instrument: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Reconstruct ``{instrument: {date: close}}`` from the engine bar cache.

    Used as a fallback when the runner did not pre-compute benchmark daily
    closes.  May be incomplete for long backtests due to cache eviction.
    """
    inst_daily_close: dict[str, dict[str, float]] = {}
    instruments = list(per_instrument.keys()) if per_instrument else []
    if not instruments:
        instruments = [str(iid) for iid in engine.cache.instrument_ids()]
    for inst_str in instruments:
        target_bt = None
        for bt in engine.cache.bar_types():
            if str(bt.instrument_id) == inst_str:
                target_bt = bt
                break
        if target_bt is None:
            continue
        bars = engine.cache.bars(target_bt)
        if not bars:
            continue
        daily: dict[str, float] = {}
        for bar in bars:
            ts = pd.Timestamp(bar.ts_init, unit="ns")
            daily[ts.strftime("%Y-%m-%d")] = float(bar.close)
        inst_daily_close[inst_str] = daily
    return inst_daily_close


def extract_backtest_results(
    engine: BacktestEngine,
    starting_balance: float = 10000,
    benchmark_daily_closes: dict[str, dict[str, float]] | None = None,
    compute_robustness: bool = True,
) -> dict[str, Any]:
    """Extract comprehensive results from a completed BacktestEngine.

    Uses the portfolio analyzer for performance stats and generates
    equity curve, trade log, and 30+ metrics.
    """
    analyzer = engine.portfolio.analyzer

    # Fetch returns series once; reused across equity curve, periodic
    # returns, drawdown analysis, and fallback risk metrics.
    try:
        returns_series: pd.Series = analyzer.returns()
    except Exception:
        returns_series = None
        logger.warning("Failed to get returns series from analyzer", exc_info=True)

    # ------------------------------------------------------------------
    # 1. Performance stats from analyzer
    # ------------------------------------------------------------------
    pnl_stats: dict[str, Any] = {}
    returns_stats: dict[str, Any] = {}
    general_stats: dict[str, Any] = {}

    try:
        pnl_stats = analyzer.get_performance_stats_pnls()
    except Exception:
        logger.warning("Failed to get PnL stats from analyzer", exc_info=True)

    try:
        returns_stats = analyzer.get_performance_stats_returns()
    except Exception:
        logger.warning("Failed to get returns stats from analyzer", exc_info=True)

    try:
        general_stats = analyzer.get_performance_stats_general()
    except Exception:
        logger.warning("Failed to get general stats from analyzer", exc_info=True)

    # ------------------------------------------------------------------
    # 2. Extract individual metrics (safe)
    # ------------------------------------------------------------------
    total_pnl = _safe_float(pnl_stats.get("PnL (total)"), 0.0)
    # total_return_pct is computed later from equity curve for consistency;
    # fallback to PnL-based calculation if returns_series is unavailable.
    total_return_pct = round(total_pnl / starting_balance * 100, 4) if starting_balance else 0.0

    sharpe = _safe_float(returns_stats.get("Sharpe Ratio (252 days)"))
    sortino = _safe_float(returns_stats.get("Sortino Ratio (252 days)"))
    calmar = _safe_float(returns_stats.get("Calmar Ratio (252 days)"))
    max_drawdown = _safe_float(returns_stats.get("Max Drawdown"))
    cagr = _safe_float(returns_stats.get("CAGR (252 days)"))
    returns_volatility = _safe_float(returns_stats.get("Returns Volatility (252 days)"))

    # Fallback: compute max_drawdown / CAGR / Calmar from returns series
    # when NT custom statistics registration fails or key names mismatch.
    try:
        if returns_series is not None and len(returns_series) > 0:
            if max_drawdown is None:
                cum = (1 + returns_series).cumprod()
                peak = cum.cummax()
                dd = (cum - peak) / peak
                max_drawdown = _safe_float(dd.min())
            if cagr is None:
                cum_all = (1 + returns_series).cumprod()
                total_ret = float(cum_all.iloc[-1])
                n_days = len(returns_series)
                if n_days > 0 and total_ret > 0:
                    cagr = _safe_float(total_ret ** (252.0 / n_days) - 1.0)
            if calmar is None and cagr is not None and max_drawdown is not None and max_drawdown != 0:
                calmar = _safe_float(cagr / abs(max_drawdown))
    except Exception:
        logger.warning("Failed to compute fallback risk metrics", exc_info=True)

    win_rate = _safe_float(pnl_stats.get("Win Rate"), 0.0)
    # general_stats may also have Win Rate; prefer pnl_stats
    if win_rate == 0.0:
        win_rate = _safe_float(general_stats.get("Win Rate"), 0.0)

    profit_factor = _safe_float(returns_stats.get("Profit Factor"))
    # profit_factor can be Inf when gross_loss==0; _safe_float converts that
    # to None.  We compute a fallback after positions are tallied (section 3).
    expectancy = _safe_float(pnl_stats.get("Expectancy"))

    largest_win = _safe_float(pnl_stats.get("Max Winner"))
    largest_loss = _safe_float(pnl_stats.get("Max Loser"))
    avg_win = _safe_float(pnl_stats.get("Avg Winner"))
    avg_loss = _safe_float(pnl_stats.get("Min Loser"))  # analyzer reports avg losing as "Min Loser"

    long_pct = _safe_float(general_stats.get("Long Ratio"))
    short_pct = round(1.0 - long_pct, 4) if long_pct is not None else None

    avg_win_loss_ratio = None
    if avg_win is not None and avg_loss is not None and avg_loss != 0:
        avg_win_loss_ratio = round(abs(avg_win / avg_loss), 4)

    # ------------------------------------------------------------------
    # 3. Positions & orders from cache
    # ------------------------------------------------------------------
    positions = engine.cache.positions()
    closed_positions = [p for p in positions if p.is_closed]
    open_pos = [p for p in positions if p.is_open]

    orders = engine.cache.orders()
    from nautilus_trader.model.enums import OrderStatus
    filled = [o for o in orders if o.status == OrderStatus.FILLED]

    total_trades = len(closed_positions)
    winning_trades = 0
    losing_trades = 0
    gross_profit = 0.0
    gross_loss = 0.0

    for p in closed_positions:
        pnl_val = _parse_realized_pnl(p.realized_pnl)
        if pnl_val > 0:
            winning_trades += 1
            gross_profit += pnl_val
        elif pnl_val < 0:
            losing_trades += 1
            gross_loss += abs(pnl_val)
        # pnl_val == 0 counts as neither win nor loss

    # Fallback: compute profit_factor from manual gross_profit / gross_loss
    # when the analyzer didn't provide it (NaN, Inf, or missing key).
    if profit_factor is None and gross_loss > 0:
        profit_factor = gross_profit / gross_loss

    # ------------------------------------------------------------------
    # 4. Total fees from fills report
    # ------------------------------------------------------------------
    total_fees = 0.0
    try:
        fills_report = engine.trader.generate_fills_report()
        if fills_report is not None and not fills_report.empty and "commission" in fills_report.columns:
            total_fees = float(fills_report["commission"].map(_parse_realized_pnl).sum())
    except Exception:
        logger.warning("Failed to extract total fees from fills report", exc_info=True)

    # ------------------------------------------------------------------
    # 5. Holding times from positions report
    # ------------------------------------------------------------------
    avg_holding_time: str | None = None
    avg_winning_holding_time: str | None = None
    avg_losing_holding_time: str | None = None

    try:
        pos_report = engine.trader.generate_positions_report()
        if pos_report is not None and not pos_report.empty and "duration_ns" in pos_report.columns:
            durations = pos_report["duration_ns"].astype(float)
            valid = durations[durations > 0]
            if len(valid) > 0:
                avg_holding_time = _format_duration_ns(valid.mean())

            # Split by realized_pnl if available
            if "realized_pnl" in pos_report.columns:
                pnl_col = pos_report["realized_pnl"].map(_parse_realized_pnl)
                win_mask = (pnl_col > 0) & (durations > 0)
                lose_mask = (pnl_col <= 0) & (durations > 0)
                if win_mask.any():
                    avg_winning_holding_time = _format_duration_ns(durations[win_mask].mean())
                if lose_mask.any():
                    avg_losing_holding_time = _format_duration_ns(durations[lose_mask].mean())
    except Exception:
        logger.warning("Failed to compute holding times", exc_info=True)

    # ------------------------------------------------------------------
    # 6. Account final balance
    # ------------------------------------------------------------------
    final_balance: str | None = None
    try:
        accounts = engine.cache.accounts()
        if accounts:
            balances = accounts[0].balances()
            if balances:
                final_balance = str(list(balances.values())[0].total)
    except Exception:
        logger.warning("Failed to get final balance", exc_info=True)

    # ------------------------------------------------------------------
    # 7. Build trade log
    # ------------------------------------------------------------------
    trade_log: list[dict[str, Any]] = []
    pnl_sequence: list[float] = []

    for p in closed_positions:
        pnl_val = _parse_realized_pnl(p.realized_pnl)
        pnl_sequence.append(pnl_val)

        duration_ns = getattr(p, "duration_ns", None)
        duration_str = _format_duration_ns(duration_ns) if duration_ns else None

        trade_log.append({
            "instrument": str(p.instrument_id),
            "side": _format_order_side(p.entry),
            "quantity": str(p.peak_qty),
            "avg_open": str(p.avg_px_open),
            "avg_close": str(p.avg_px_close),
            "realized_pnl": round(pnl_val, 4),
            "duration": duration_str,
            "opened_at": _format_ns_timestamp(getattr(p, "ts_opened", None)),
            "closed_at": _format_ns_timestamp(getattr(p, "ts_closed", None)),
        })

    winning_streak, losing_streak = _compute_streaks(pnl_sequence)

    # ------------------------------------------------------------------
    # 8. Equity curve
    # ------------------------------------------------------------------
    # Build equity from closed-position PnL accumulated by close date.
    # This avoids the analyzer.returns() cumprod issue where leveraged
    # futures accounts produce inflated daily returns that diverge from
    # the actual account PnL (gross_profit - gross_loss).
    equity_curve: list[dict[str, Any]] = []
    try:
        trade_closes = [
            (getattr(p, "ts_closed", 0) or 0, _parse_realized_pnl(p.realized_pnl))
            for p in closed_positions
        ]
        equity_curve = build_equity_curve(trade_closes, starting_balance)
    except Exception:
        logger.warning("Failed to build equity curve", exc_info=True)

    # Function-scope variables for daily returns (used by sections 8b, 8c, 11b-11f)
    _daily_rets = None  # numpy array, set in section 8b
    _n_days = 0
    # Timestamps extracted from equity curve for rolling calculations (11c-11j)
    _eq_timestamps: list[str] = [pt["timestamp"] for pt in equity_curve] if equity_curve else []

    # Extended statistics (set in section 8c, used in section 13 result dict)
    best_day = worst_day = positive_days_pct = None
    skewness = kurtosis_val = tail_ratio = stability = None
    omega_ratio = var_95 = var_99 = cvar_95 = downside_dev = ulcer_index = max_daily_loss = None
    normal_dist_mean = normal_dist_std = None
    positive_months_pct = None

    # Benchmark-relative metrics (set in section 11k)
    alpha = beta_val = r_squared = information_ratio = None
    benchmark_type = "zero_line"
    benchmark_daily_returns = None
    rolling_sharpe: list[dict[str, Any]] = []
    rolling_sortino: list[dict[str, Any]] = []
    rolling_volatility: list[dict[str, Any]] = []
    rolling_beta: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 8b. Recompute risk metrics from equity curve (industry standard)
    # ------------------------------------------------------------------
    # NT's analyzer.returns() uses per-trade notional returns
    # (realized_pnl / position_cost) which inflates metrics for leveraged
    # futures. We recompute all risk metrics from the actual dollar equity
    # curve built in section 8 — this matches industry-standard portfolio
    # return definitions.
    mean_ret = 0.0
    std_ret = 0.0
    dd_arr = np.empty(0, dtype=float)
    try:
        metrics = recompute_risk_metrics_from_equity_curve(equity_curve, starting_balance)
        if metrics is not None:
            _daily_rets = metrics["daily_rets"]
            _n_days = metrics["n_days"]
            dd_arr = metrics["dd_arr"]
            mean_ret = metrics["mean_ret"]
            std_ret = metrics["std_ret"]
            max_drawdown = metrics["max_drawdown"]
            cagr = metrics["cagr"]
            sharpe = metrics["sharpe"]
            sortino = metrics["sortino"]
            calmar = metrics["calmar"]
            returns_volatility = metrics["returns_volatility"]
            total_return_pct = metrics["total_return_pct"]
            logger.info(
                "Risk metrics recomputed from equity curve: max_dd=%.4f, sharpe=%.4f, cagr=%.4f (%d calendar days, %d data points)",
                max_drawdown, sharpe or 0, cagr or 0, metrics["calendar_days"], _n_days,
            )

            # ----------------------------------------------------------
            # 8c. Extended statistics for returns analytics
            # ----------------------------------------------------------
            try:
                ext = compute_extended_statistics(_daily_rets, dd_arr, mean_ret, std_ret)
                best_day = ext.get("best_day")
                worst_day = ext.get("worst_day")
                positive_days_pct = ext.get("positive_days_pct")
                skewness = ext.get("skewness")
                kurtosis_val = ext.get("kurtosis")
                tail_ratio = ext.get("tail_ratio")
                stability = ext.get("stability")
                omega_ratio = ext.get("omega_ratio")
                var_95 = ext.get("var_95")
                var_99 = ext.get("var_99")
                cvar_95 = ext.get("cvar_95")
                downside_dev = ext.get("downside_dev")
                ulcer_index = ext.get("ulcer_index")
                max_daily_loss = ext.get("max_daily_loss")
                normal_dist_mean = ext.get("normal_dist_mean")
                normal_dist_std = ext.get("normal_dist_std")
            except Exception:
                logger.warning("Failed to compute extended statistics (8c)", exc_info=True)
    except Exception:
        logger.warning("Failed to recompute risk metrics from equity curve", exc_info=True)

    # ------------------------------------------------------------------
    # 9. Per-instrument breakdown
    # ------------------------------------------------------------------
    per_instrument: dict[str, dict[str, Any]] = {}
    try:
        trade_records = [
            {"instrument": str(p.instrument_id), "pnl": _parse_realized_pnl(p.realized_pnl)}
            for p in closed_positions
        ]
        per_instrument = compute_per_instrument_basic(trade_records, starting_balance)
    except Exception:
        logger.warning("Failed to compute per-instrument breakdown", exc_info=True)

    # ------------------------------------------------------------------
    # 9b. Advanced per-instrument analytics
    # ------------------------------------------------------------------
    instrument_cumulative_pnl: dict[str, list] = {}
    instrument_correlation: dict[str, dict[str, float]] = {}
    monthly_pnl_heatmap: list[dict[str, Any]] = []
    portfolio_analytics: dict[str, Any] = {}

    try:
        closed_trades_adv = [
            {
                "instrument": str(p.instrument_id),
                "ts_closed": getattr(p, "ts_closed", 0) or 0,
                "pnl": _parse_realized_pnl(p.realized_pnl),
            }
            for p in closed_positions
        ]
        adv = compute_per_instrument_advanced(closed_trades_adv, per_instrument, starting_balance)
        for inst, updates in adv["per_instrument_updates"].items():
            per_instrument[inst].update(updates)
        instrument_cumulative_pnl = adv["instrument_cumulative_pnl"]
        instrument_correlation = adv["instrument_correlation"]
        monthly_pnl_heatmap = adv["monthly_pnl_heatmap"]
        portfolio_analytics = adv["portfolio_analytics"]
    except Exception:
        logger.warning("Failed to compute advanced per-instrument analytics", exc_info=True)

    # ------------------------------------------------------------------
    # 10. Monthly & weekly returns
    # ------------------------------------------------------------------
    monthly_returns: list[dict[str, Any]] = []
    weekly_returns: list[dict[str, Any]] = []
    try:
        # Use equity-curve-derived daily PnL for periodic returns (consistent
        # with portfolio-level metrics, avoids per-trade notional return issue).
        if equity_curve and len(equity_curve) >= 2:
            monthly_returns, weekly_returns = compute_periodic_returns(
                equity_curve, starting_balance,
            )
        elif returns_series is not None and len(returns_series) > 0:
            # Fallback to analyzer returns if equity curve unavailable
            rs = returns_series.copy()
            rs.index = pd.to_datetime(rs.index)
            monthly = (1 + rs).resample("ME").prod() - 1
            for ts, ret in monthly.items():
                monthly_returns.append({
                    "period": ts.strftime("%Y-%m"),
                    "return_pct": round(float(ret) * 100, 4),
                })
            weekly = (1 + rs).resample("W").prod() - 1
            for ts, ret in weekly.items():
                weekly_returns.append({
                    "period": ts.strftime("%Y-%m-%d"),
                    "return_pct": round(float(ret) * 100, 4),
                })
    except Exception:
        logger.warning("Failed to compute periodic returns", exc_info=True)

    # Best/worst month from monthly returns
    best_month = max((m["return_pct"] for m in monthly_returns), default=None) if monthly_returns else None
    worst_month = min((m["return_pct"] for m in monthly_returns), default=None) if monthly_returns else None

    # Positive months percentage
    if monthly_returns:
        _pos_m = sum(1 for m in monthly_returns if m["return_pct"] > 0)
        positive_months_pct = round(_pos_m / len(monthly_returns) * 100, 2)

    # ------------------------------------------------------------------
    # 11. Drawdown analysis (periods with duration)
    # ------------------------------------------------------------------
    drawdown_periods: list[dict[str, Any]] = []
    try:
        drawdown_periods = compute_drawdown_periods(equity_curve)
    except Exception:
        logger.warning("Failed to compute drawdown periods", exc_info=True)

    # ------------------------------------------------------------------
    # 11b. Annual returns (compound formula)
    # ------------------------------------------------------------------
    annual_returns: list[dict[str, Any]] = []
    try:
        annual_returns = compute_annual_returns(equity_curve, starting_balance)
    except Exception:
        logger.warning("Failed to compute annual returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11c. Rolling returns (3m/6m/12m)
    # ------------------------------------------------------------------
    rolling_returns: list[dict[str, Any]] = []
    try:
        if _daily_rets is not None and len(_daily_rets) >= 2:
            rolling_returns = _compute_rolling_series(
                _daily_rets, _eq_timestamps,
                {"rolling_3m": 63, "rolling_6m": 126, "rolling_12m": 252},
                _rolling_cumret_fn,
            )
    except Exception:
        logger.warning("Failed to compute rolling returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11d. Returns distribution (histogram bins)
    # ------------------------------------------------------------------
    returns_distribution: list[dict[str, Any]] = []
    try:
        if _daily_rets is not None:
            returns_distribution = compute_returns_distribution(_daily_rets)
    except Exception:
        logger.warning("Failed to compute returns distribution", exc_info=True)

    # ------------------------------------------------------------------
    # 11e. QQ plot data (theoretical vs empirical quantiles)
    # ------------------------------------------------------------------
    qq_plot_data: list[dict[str, float]] = []
    try:
        if _daily_rets is not None:
            qq_plot_data = compute_qq_plot_data(_daily_rets)
    except Exception:
        logger.warning("Failed to compute QQ plot data", exc_info=True)

    # ------------------------------------------------------------------
    # 11f. Benchmark equity curve (Buy & Hold)
    # ------------------------------------------------------------------
    # Uses pre-computed daily closes from runner (raw bar data captured
    # before engine.run(), avoiding cache capacity eviction).
    # Falls back to engine.cache.bars() if pre-computed data unavailable.
    benchmark_equity_curve: list[dict[str, Any]] = []
    try:
        inst_daily_close: dict[str, dict[str, float]] = {}
        if benchmark_daily_closes:
            inst_daily_close = benchmark_daily_closes
        else:
            inst_daily_close = _build_inst_daily_close_from_cache(engine, per_instrument)

        benchmark_equity_curve = compute_benchmark_equity_curve(
            equity_curve, inst_daily_close, starting_balance,
        )
    except Exception:
        logger.warning("Failed to compute benchmark equity curve", exc_info=True)

    # Determine benchmark type
    try:
        instruments_list = list(per_instrument.keys()) if per_instrument else []
        if not instruments_list:
            instruments_list = [str(iid) for iid in engine.cache.instrument_ids()]
        if instruments_list:
            benchmark_type = "single_bh" if len(instruments_list) == 1 else "basket_bh"
    except Exception:
        pass  # keeps default "zero_line"

    # Compute benchmark daily returns for rolling beta / benchmark-relative metrics
    try:
        benchmark_daily_returns = compute_benchmark_daily_returns(
            benchmark_equity_curve, starting_balance,
        )
    except Exception:
        logger.warning("Failed to compute benchmark daily returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11g. Rolling Sharpe Ratio (3m/6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 63:
            rolling_sharpe = _compute_rolling_series(
                _daily_rets, _eq_timestamps,
                {"rolling_3m": 63, "rolling_6m": 126, "rolling_12m": 252},
                _rolling_sharpe_fn,
            )
    except Exception:
        logger.warning("Failed to compute rolling Sharpe", exc_info=True)

    # ------------------------------------------------------------------
    # 11h. Rolling Sortino Ratio (6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 126:
            rolling_sortino = _compute_rolling_series(
                _daily_rets, _eq_timestamps,
                {"rolling_6m": 126, "rolling_12m": 252},
                _rolling_sortino_fn,
            )
    except Exception:
        logger.warning("Failed to compute rolling Sortino", exc_info=True)

    # ------------------------------------------------------------------
    # 11i. Rolling Volatility (6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 126:
            rolling_volatility = _compute_rolling_series(
                _daily_rets, _eq_timestamps,
                {"rolling_6m": 126, "rolling_12m": 252},
                _rolling_volatility_fn,
            )
    except Exception:
        logger.warning("Failed to compute rolling volatility", exc_info=True)

    # ------------------------------------------------------------------
    # 11j. Rolling Beta (6m/12m) — requires benchmark returns
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and benchmark_daily_returns is not None and len(_daily_rets) >= 126:
            _rb_min_len = min(len(_daily_rets), len(benchmark_daily_returns))
            rolling_beta = _compute_rolling_series(
                _daily_rets, _eq_timestamps[:_rb_min_len],
                {"rolling_6m": 126, "rolling_12m": 252},
                _make_rolling_beta_fn(benchmark_daily_returns),
            )
    except Exception:
        logger.warning("Failed to compute rolling beta", exc_info=True)

    # ------------------------------------------------------------------
    # 11k. Benchmark-relative metrics (Alpha, Beta, R², Information Ratio)
    # ------------------------------------------------------------------
    try:
        bm_metrics = compute_benchmark_relative_metrics(_daily_rets, benchmark_daily_returns)
        alpha = bm_metrics["alpha"]
        beta_val = bm_metrics["beta"]
        r_squared = bm_metrics["r_squared"]
        information_ratio = bm_metrics["information_ratio"]
    except Exception:
        logger.warning("Failed to compute benchmark-relative metrics", exc_info=True)

    # Persist raw daily returns for future analytics endpoint migration
    daily_returns_pct: list[float] = []
    if _daily_rets is not None:
        daily_returns_pct = [round(float(r) * 100, 4) for r in _daily_rets]

    # ------------------------------------------------------------------
    # 12. Slippage statistics
    # ------------------------------------------------------------------
    slippage_stats: dict[str, Any] = {}
    try:
        fills_report_slip = engine.trader.generate_fills_report()
        if fills_report_slip is not None and not fills_report_slip.empty:
            has_slippage = "slippage" in fills_report_slip.columns
            if has_slippage:
                slip = fills_report_slip["slippage"].astype(float)
                slippage_stats = {
                    "total_fills": len(slip),
                    "fills_with_slippage": int((slip != 0).sum()),
                    "slippage_pct": round(float((slip != 0).sum() / len(slip) * 100), 2),
                    "avg_slippage": round(float(slip.mean()), 6),
                    "max_slippage": round(float(slip.max()), 6),
                    "min_slippage": round(float(slip.min()), 6),
                    "total_slippage_cost": round(float(slip.sum()), 4),
                }
            else:
                # Estimate from last_px vs avg_px if available
                has_last = "last_px" in fills_report_slip.columns
                has_avg = "avg_px" in fills_report_slip.columns
                if has_last and has_avg:
                    last_px = fills_report_slip["last_px"].astype(float)
                    avg_px = fills_report_slip["avg_px"].astype(float)
                    slip_est = (avg_px - last_px).abs()
                    slippage_stats = {
                        "total_fills": len(slip_est),
                        "estimated": True,
                        "avg_price_diff": round(float(slip_est.mean()), 6),
                        "max_price_diff": round(float(slip_est.max()), 6),
                    }
                else:
                    slippage_stats = {"total_fills": len(fills_report_slip), "available": False}
    except Exception:
        logger.warning("Failed to compute slippage stats", exc_info=True)

    # ------------------------------------------------------------------
    # 12b. Trades analytics (scalar metrics + chart data arrays)
    # ------------------------------------------------------------------
    # All calculation lives in ``sections.py``; extract.py only reshapes NT
    # objects into the primitive inputs each helper expects.
    scalar_keys = (
        "median_trade_pnl", "std_trade_pnl", "fill_rate",
        "avg_trades_per_day", "recovery_factor", "sqn",
        "kelly_criterion", "k_ratio", "expectancy_r",
    )
    scalar_metrics: dict[str, Any] = {k: None for k in scalar_keys}

    trade_pnl_distribution: list[dict[str, Any]] = []
    cumulative_trade_pnl: list[dict[str, Any]] = []
    trade_pnl_scatter: list[dict[str, Any]] = []
    mae_mfe: list[dict[str, Any]] = []
    holding_time_distribution: list[dict[str, Any]] = []
    streak_sequence: list[dict[str, Any]] = []
    long_vs_short: dict[str, Any] = {}
    return_by_dow: list[dict[str, Any]] = []
    return_by_hour: list[dict[str, Any]] = []

    pnls = [_parse_realized_pnl(p.realized_pnl) for p in closed_positions]

    try:
        scalar_metrics = compute_trade_scalar_metrics(
            pnls,
            n_orders=len(orders),
            n_filled_orders=len(filled),
            n_returns_periods=len(returns_series) if returns_series is not None else 0,
            total_trades=total_trades,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            starting_balance=starting_balance,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
        )
    except Exception:
        logger.warning("Failed to compute trade scalar metrics", exc_info=True)

    try:
        trade_pnl_distribution = compute_trade_pnl_distribution(pnls)
    except Exception:
        logger.warning("Failed to compute trade PnL distribution", exc_info=True)

    try:
        cumulative_trade_pnl = compute_cumulative_trade_pnl(pnls)
    except Exception:
        logger.warning("Failed to compute cumulative trade PnL", exc_info=True)

    try:
        scatter_inputs = [
            {
                "ts_closed": getattr(p, "ts_closed", None),
                "pnl": _parse_realized_pnl(p.realized_pnl),
                "side": _format_order_side(p.entry),
                "instrument": str(p.instrument_id),
            }
            for p in closed_positions
        ]
        trade_pnl_scatter = compute_trade_pnl_scatter(scatter_inputs)
    except Exception:
        logger.warning("Failed to compute trade PnL scatter", exc_info=True)

    try:
        bars_by_inst = _collect_bars_by_instrument(engine)
        mae_positions = [
            {
                "instrument": str(p.instrument_id),
                "ts_opened": getattr(p, "ts_opened", 0) or 0,
                "ts_closed": getattr(p, "ts_closed", 0) or 0,
                "entry_price": float(p.avg_px_open),
                "side": _format_order_side(p.entry),
                "pnl": _parse_realized_pnl(p.realized_pnl),
            }
            for p in closed_positions
        ]
        mae_mfe = compute_mae_mfe(mae_positions, bars_by_inst)
    except Exception:
        logger.warning("Failed to compute MAE/MFE", exc_info=True)

    try:
        durations = [getattr(p, "duration_ns", None) for p in closed_positions]
        holding_time_distribution = compute_holding_time_distribution(
            [d for d in durations if d is not None],
        )
    except Exception:
        logger.warning("Failed to compute holding time distribution", exc_info=True)

    try:
        streak_sequence = compute_streak_sequence(pnls)
    except Exception:
        logger.warning("Failed to compute streak sequence", exc_info=True)

    try:
        trade_sides = [
            (_format_order_side(p.entry), _parse_realized_pnl(p.realized_pnl))
            for p in closed_positions
        ]
        long_vs_short = compute_long_vs_short(trade_sides)
    except Exception:
        logger.warning("Failed to compute long vs short comparison", exc_info=True)

    try:
        trade_times = [
            (getattr(p, "ts_closed", 0) or 0, _parse_realized_pnl(p.realized_pnl))
            for p in closed_positions
        ]
        return_by_dow = compute_return_by_dow(trade_times)
        return_by_hour = compute_return_by_hour(trade_times)
    except Exception:
        logger.warning("Failed to compute return by day-of-week/hour", exc_info=True)

    # ------------------------------------------------------------------
    # 13. Assemble final result
    # ------------------------------------------------------------------
    _result = {
        "statistics": {
            "total_pnl": round(total_pnl, 4),
            "total_return_pct": round(total_return_pct, 4),
            "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
            "sortino_ratio": round(sortino, 4) if sortino is not None else None,
            "calmar_ratio": round(calmar, 4) if calmar is not None else None,
            "max_drawdown": round(max_drawdown, 4) if max_drawdown is not None else None,
            "annual_return": round(cagr, 4) if cagr is not None else None,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
            "expectancy": round(expectancy, 4) if expectancy is not None else None,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "largest_win": round(largest_win, 4) if largest_win is not None else None,
            "largest_loss": round(largest_loss, 4) if largest_loss is not None else None,
            "avg_win": round(avg_win, 4) if avg_win is not None else None,
            "avg_loss": round(avg_loss, 4) if avg_loss is not None else None,
            "avg_win_loss_ratio": avg_win_loss_ratio,
            "winning_streak": winning_streak,
            "losing_streak": losing_streak,
            "long_pct": round(long_pct, 4) if long_pct is not None else None,
            "short_pct": round(short_pct, 4) if short_pct is not None else None,
            "avg_holding_time": avg_holding_time,
            "avg_winning_holding_time": avg_winning_holding_time,
            "avg_losing_holding_time": avg_losing_holding_time,
            "total_fees": round(total_fees, 4),
            "gross_profit": round(gross_profit, 4),
            "gross_loss": round(gross_loss, 4),
            "open_positions": len(open_pos),
            "total_orders": len(orders),
            "filled_orders": len(filled),
            "final_balance": final_balance,
            "returns_volatility": round(returns_volatility, 4) if returns_volatility is not None else None,
            # Extended statistics (section 8c)
            "best_day": best_day,
            "worst_day": worst_day,
            "best_month": best_month,
            "worst_month": worst_month,
            "positive_days_pct": positive_days_pct,
            "skewness": skewness,
            "kurtosis": kurtosis_val,
            "tail_ratio": tail_ratio,
            "stability": stability,
            # Performance tab extended metrics (section 8c-ext)
            "omega_ratio": _safe_float(omega_ratio),
            "var_95": _safe_float(var_95),
            "var_99": _safe_float(var_99),
            "cvar_95": _safe_float(cvar_95),
            "downside_deviation": _safe_float(downside_dev),
            "ulcer_index": _safe_float(ulcer_index),
            "max_daily_loss": _safe_float(max_daily_loss),
            "positive_months_pct": _safe_float(positive_months_pct),
            "normal_dist_mean": _safe_float(normal_dist_mean),
            "normal_dist_std": _safe_float(normal_dist_std),
            # Benchmark-relative metrics (section 11k)
            "alpha": _safe_float(alpha),
            "beta": _safe_float(beta_val),
            "r_squared": _safe_float(r_squared),
            "information_ratio": _safe_float(information_ratio),
            # Trades analytics scalar metrics (section 12b)
            "median_trade_pnl": scalar_metrics["median_trade_pnl"],
            "std_trade_pnl": scalar_metrics["std_trade_pnl"],
            "fill_rate": scalar_metrics["fill_rate"],
            "avg_trades_per_day": scalar_metrics["avg_trades_per_day"],
            "recovery_factor": scalar_metrics["recovery_factor"],
            "sqn": scalar_metrics["sqn"],
            "kelly_criterion": scalar_metrics["kelly_criterion"],
            "k_ratio": scalar_metrics["k_ratio"],
            "expectancy_r": scalar_metrics["expectancy_r"],
        },
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "per_instrument": per_instrument,
        "monthly_returns": monthly_returns,
        "weekly_returns": weekly_returns,
        "drawdown_periods": drawdown_periods,
        "slippage_stats": slippage_stats,
        "instrument_cumulative_pnl": instrument_cumulative_pnl,
        "instrument_correlation": instrument_correlation,
        "monthly_pnl_heatmap": monthly_pnl_heatmap,
        "portfolio_analytics": portfolio_analytics,
        "annual_returns": annual_returns,
        "rolling_returns": rolling_returns,
        "returns_distribution": returns_distribution,
        "qq_plot_data": qq_plot_data,
        "benchmark_equity_curve": benchmark_equity_curve,
        "daily_returns": daily_returns_pct,
        # Performance tab rolling analytics (sections 11g-11j)
        "rolling_sharpe": rolling_sharpe,
        "rolling_sortino": rolling_sortino,
        "rolling_volatility": rolling_volatility,
        "rolling_beta": rolling_beta,
        "benchmark_type": benchmark_type,
        # Trades analytics chart data (section 12b)
        "trade_pnl_distribution": trade_pnl_distribution,
        "cumulative_trade_pnl": cumulative_trade_pnl,
        "trade_pnl_scatter": trade_pnl_scatter,
        "mae_mfe": mae_mfe,
        "holding_time_distribution": holding_time_distribution,
        "streak_sequence": streak_sequence,
        "long_vs_short": long_vs_short,
        "return_by_dow": return_by_dow,
        "return_by_hour": return_by_hour,
    }

    # ------------------------------------------------------------------
    # 14. Robustness metrics (Layer 1 — any backtest)
    # ------------------------------------------------------------------
    robustness = None
    if compute_robustness:
        try:
            daily_sharpe = (mean_ret / std_ret) if std_ret and std_ret > 1e-12 else None
            trade_pnl_list = [
                float(t["realized_pnl"])
                for t in trade_log
                if isinstance(t.get("realized_pnl"), (int, float))
            ]
            robustness = _compute_robustness_block(
                trade_pnl_list,
                starting_balance,
                daily_sharpe=daily_sharpe,
                n_days=_n_days if _n_days else 0,
                skewness=skewness,
                kurtosis=kurtosis_val,
            )
        except Exception:
            logger.warning("Robustness computation failed", exc_info=True)

    _result["robustness"] = robustness

    return _result
