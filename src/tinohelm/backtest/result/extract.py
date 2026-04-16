"""Backtest result extraction from NautilusTrader BacktestEngine."""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine

from tinohelm.backtest.result.sections import (
    build_equity_curve,
    compute_annual_returns,
    compute_benchmark_relative_metrics,
    compute_drawdown_periods,
    compute_extended_statistics,
    compute_long_vs_short,
    compute_per_instrument_basic,
    compute_periodic_returns,
    compute_qq_plot_data,
    compute_return_by_dow,
    compute_return_by_hour,
    compute_returns_distribution,
    compute_streak_sequence,
    recompute_risk_metrics_from_equity_curve,
)
from tinohelm.backtest.result.statistics import (
    _compute_monte_carlo,
    _compute_psr,
    _compute_min_backtest_length,
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
        if len(per_instrument) > 1:
            from collections import defaultdict as _defaultdict
            from datetime import datetime as _dt, timezone as _tz

            # Build per-instrument daily PnL matrix (grouped by close date)
            daily_pnl_map: dict[str, dict[str, float]] = _defaultdict(lambda: _defaultdict(float))
            all_dates: set[str] = set()
            for p in closed_positions:
                inst = str(p.instrument_id)
                close_date = _dt.fromtimestamp(p.ts_closed / 1e9, tz=_tz.utc).strftime("%Y-%m-%d")
                daily_pnl_map[inst][close_date] += _parse_realized_pnl(p.realized_pnl)
                all_dates.add(close_date)

            sorted_dates = sorted(all_dates)
            instruments = sorted(per_instrument.keys())
            n_dates = len(sorted_dates)
            n_inst = len(instruments)

            # Cumulative PnL per instrument (for stacked area chart)
            for inst in instruments:
                cum = 0.0
                curve = []
                for d in sorted_dates:
                    cum += daily_pnl_map[inst].get(d, 0.0)
                    curve.append({"date": d, "cum_pnl": round(cum, 2)})
                instrument_cumulative_pnl[inst] = curve

            # Daily returns matrix (n_dates x n_inst) for correlation & risk
            returns_matrix = np.zeros((n_dates, n_inst))
            for j, inst in enumerate(instruments):
                for i, d in enumerate(sorted_dates):
                    returns_matrix[i, j] = daily_pnl_map[inst].get(d, 0.0) / starting_balance

            # Correlation matrix (pairwise Pearson)
            if n_dates >= 10:
                corr = np.corrcoef(returns_matrix.T)
                for i, inst_i in enumerate(instruments):
                    instrument_correlation[inst_i] = {}
                    for j, inst_j in enumerate(instruments):
                        if i != j:
                            val = corr[i, j]
                            if not (np.isnan(val) or np.isinf(val)):
                                instrument_correlation[inst_i][inst_j] = round(float(val), 4)

            # Per-instrument Sharpe, Sortino, MaxDD, Recovery Factor
            def _safe_round(v, n=4):
                return round(v, n) if v is not None and not (np.isnan(v) or np.isinf(v)) else None

            for idx, inst in enumerate(instruments):
                arr = returns_matrix[:, idx]
                mean_ret = float(arr.mean())
                std_ret = float(arr.std(ddof=1)) if n_dates > 1 else 0.0

                # Sharpe (annualized 365 for crypto)
                inst_sharpe = (mean_ret / std_ret * np.sqrt(365)) if std_ret > 1e-12 else None

                # Sortino (downside deviation only)
                downside = arr[arr < 0]
                ds_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
                inst_sortino = (mean_ret / ds_std * np.sqrt(365)) if ds_std > 1e-12 else None

                # Max drawdown from cumulative PnL
                cum_pnl = np.cumsum([daily_pnl_map[inst].get(d, 0.0) for d in sorted_dates])
                running_max = np.maximum.accumulate(cum_pnl)
                dd = cum_pnl - running_max
                max_dd = float(dd.min()) if len(dd) > 0 else 0.0
                max_dd_pct = max_dd / starting_balance if starting_balance > 0 else 0.0

                # Recovery factor = |total_pnl / max_drawdown|
                inst_total_pnl = per_instrument[inst]["total_pnl"]
                recovery = abs(inst_total_pnl / max_dd) if max_dd < -0.01 else None

                per_instrument[inst]["sharpe_ratio"] = _safe_round(inst_sharpe)
                per_instrument[inst]["sortino_ratio"] = _safe_round(inst_sortino)
                per_instrument[inst]["max_drawdown"] = _safe_round(max_dd_pct)
                per_instrument[inst]["recovery_factor"] = _safe_round(recovery)

            # Monthly PnL heatmap (instrument x month)
            monthly_map: dict[str, dict[str, float]] = _defaultdict(lambda: _defaultdict(float))
            for p in closed_positions:
                inst = str(p.instrument_id)
                close_month = _dt.fromtimestamp(p.ts_closed / 1e9, tz=_tz.utc).strftime("%Y-%m")
                monthly_map[inst][close_month] += _parse_realized_pnl(p.realized_pnl)

            all_months = sorted({m for mm in monthly_map.values() for m in mm})
            for inst in instruments:
                for month in all_months:
                    monthly_pnl_heatmap.append({
                        "instrument": inst,
                        "month": month,
                        "pnl": round(monthly_map[inst].get(month, 0.0), 2),
                    })

            # Diversification ratio (equal-weight assumption)
            if n_dates >= 10 and n_inst >= 2:
                weights = np.ones(n_inst) / n_inst
                inst_vols = np.array([returns_matrix[:, j].std(ddof=1) for j in range(n_inst)])
                cov = np.cov(returns_matrix.T)
                port_vol = float(np.sqrt(weights @ cov @ weights))
                wav = float(np.dot(weights, inst_vols))
                if port_vol > 1e-12 and wav > 1e-12:
                    portfolio_analytics["diversification_ratio"] = round(wav / port_vol, 4)
                    portfolio_analytics["diversification_benefit_pct"] = round((1.0 - port_vol / wav) * 100, 2)

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
        if equity_curve and len(equity_curve) >= 2:
            # Build inst_daily_close from pre-computed data or cache fallback
            inst_daily_close: dict[str, dict[str, float]] = {}

            if benchmark_daily_closes:
                # Pre-computed by runner — complete data, no cache eviction
                inst_daily_close = benchmark_daily_closes
            else:
                # Fallback: read from engine cache (may be incomplete for long backtests)
                instruments = list(per_instrument.keys()) if per_instrument else []
                if not instruments:
                    instruments = [str(iid) for iid in engine.cache.instrument_ids()]
                for inst_str in instruments:
                    bar_types = engine.cache.bar_types()
                    target_bt = None
                    for bt in bar_types:
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
                        day_key = ts.strftime("%Y-%m-%d")
                        daily[day_key] = float(bar.close)
                    inst_daily_close[inst_str] = daily

            if inst_daily_close:
                eq_dates = [pt["timestamp"] for pt in equity_curve]
                alloc_per_inst = starting_balance / len(inst_daily_close)

                inst_units: dict[str, float] = {}
                for inst, closes in inst_daily_close.items():
                    for d in eq_dates:
                        if d in closes and closes[d] > 0:
                            inst_units[inst] = alloc_per_inst / closes[d]
                            break

                last_price: dict[str, float] = {}
                for d in eq_dates:
                    bm_equity = 0.0
                    for inst, units in inst_units.items():
                        closes = inst_daily_close.get(inst, {})
                        price = closes.get(d)
                        if price is not None:
                            last_price[inst] = price
                        elif inst in last_price:
                            price = last_price[inst]
                        else:
                            price = alloc_per_inst / units if units > 0 else 0
                        bm_equity += units * price
                    benchmark_equity_curve.append({
                        "timestamp": d,
                        "equity": round(bm_equity, 4),
                    })
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
        if benchmark_equity_curve and len(benchmark_equity_curve) >= 2:
            _bm_values = [starting_balance] + [pt["equity"] for pt in benchmark_equity_curve]
            _bm_arr = np.array(_bm_values, dtype=float)
            benchmark_daily_returns = np.diff(_bm_arr) / _bm_arr[:-1]
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
    # Defaults so the final dict always has these keys
    median_trade_pnl = None
    std_trade_pnl = None
    fill_rate = None
    avg_trades_per_day = None
    recovery_factor = None
    sqn = None
    kelly_criterion = None
    k_ratio = None
    expectancy_r = None

    trade_pnl_distribution: list[dict[str, Any]] = []
    cumulative_trade_pnl: list[dict[str, Any]] = []
    trade_pnl_scatter: list[dict[str, Any]] = []
    mae_mfe: list[dict[str, Any]] = []
    holding_time_distribution: list[dict[str, Any]] = []
    streak_sequence: list[dict[str, Any]] = []
    long_vs_short: dict[str, Any] = {}
    return_by_dow: list[dict[str, Any]] = []
    return_by_hour: list[dict[str, Any]] = []

    try:
        pnls = [_parse_realized_pnl(p.realized_pnl) for p in closed_positions]

        if pnls:
            _pnl_arr = np.array(pnls, dtype=float)

            # --- Scalar metrics ---
            median_trade_pnl = _safe_float(round(float(np.median(_pnl_arr)), 4))
            std_trade_pnl = _safe_float(round(float(np.std(_pnl_arr, ddof=1)), 4)) if len(pnls) > 1 else None

            if orders and len(orders) > 0:
                fill_rate = _safe_float(round(len(filled) / len(orders) * 100, 4))

            if returns_series is not None and len(returns_series) > 0:
                avg_trades_per_day = _safe_float(round(total_trades / len(returns_series), 4))

            if max_drawdown is not None and abs(max_drawdown) > 1e-12 and starting_balance > 0:
                # max_drawdown is a ratio (e.g. -0.01 = 1%); convert total_pnl to ratio too
                net_return = total_pnl / starting_balance
                recovery_factor = _safe_float(round(net_return / abs(max_drawdown), 4))

            # SQN: sqrt(N) * mean(pnls) / std(pnls)
            _pnl_std = float(np.std(_pnl_arr, ddof=1)) if len(pnls) > 1 else 0.0
            _pnl_mean = float(np.mean(_pnl_arr))
            if _pnl_std > 1e-12:
                sqn = _safe_float(round(float(np.sqrt(len(pnls)) * _pnl_mean / _pnl_std), 4))

            # Kelly Criterion: win_rate - (1 - win_rate) / R
            if avg_win is not None and avg_loss is not None and abs(avg_loss) > 1e-12:
                _R = abs(avg_win / avg_loss)
                _wr = win_rate if win_rate is not None else 0.0
                kelly_criterion = _safe_float(round((_wr - (1 - _wr) / _R) * 100, 4))

            # K-Ratio: slope of OLS on log(cumulative equity) / std_err(slope)
            try:
                _cum_equity = starting_balance + np.cumsum(_pnl_arr)
                _pos_mask = _cum_equity > 0
                if _pos_mask.sum() >= 2:
                    _log_eq = np.log(_cum_equity[_pos_mask])
                    _x = np.arange(len(_log_eq), dtype=float)
                    _n_k = len(_x)
                    _coeffs = np.polyfit(_x, _log_eq, 1)
                    _slope = _coeffs[0]
                    _y_pred = _slope * _x + _coeffs[1]
                    _residuals = _log_eq - _y_pred
                    _mse = float(np.sum(_residuals ** 2) / (_n_k - 2)) if _n_k > 2 else 0.0
                    _x_var = float(np.sum((_x - _x.mean()) ** 2))
                    if _x_var > 1e-12 and _mse > 0:
                        _std_err = float(np.sqrt(_mse / _x_var))
                        if _std_err > 1e-12:
                            # Kestner 2003: normalize by sqrt(N) for scale independence
                            k_ratio = _safe_float(round(float(_slope / (_std_err * np.sqrt(_n_k))), 4))
            except Exception:
                logger.warning("Failed to compute K-Ratio", exc_info=True)

            # Expectancy in R-multiples
            if expectancy is not None and avg_loss is not None and abs(avg_loss) > 1e-12:
                expectancy_r = _safe_float(round(expectancy / abs(avg_loss), 4))

            # --- Chart data arrays ---

            # 1. Trade PnL distribution (histogram)
            try:
                _n_bins = min(30, max(10, len(pnls) // 5))
                _counts, _bin_edges = np.histogram(_pnl_arr, bins=_n_bins)
                for j in range(len(_counts)):
                    trade_pnl_distribution.append({
                        "bin_start": round(float(_bin_edges[j]), 4),
                        "bin_end": round(float(_bin_edges[j + 1]), 4),
                        "count": int(_counts[j]),
                    })
            except Exception:
                logger.warning("Failed to compute trade PnL distribution", exc_info=True)

            # 2. Cumulative trade PnL
            try:
                _cum_pnl = np.cumsum(_pnl_arr)
                for idx_t, cpnl in enumerate(_cum_pnl):
                    cumulative_trade_pnl.append({
                        "trade_num": idx_t + 1,
                        "cumulative_pnl": round(float(cpnl), 4),
                    })
            except Exception:
                logger.warning("Failed to compute cumulative trade PnL", exc_info=True)

            # 3. Trade PnL scatter
            try:
                for p in closed_positions:
                    _pnl_v = _parse_realized_pnl(p.realized_pnl)
                    _ts_c = getattr(p, "ts_closed", None)
                    trade_pnl_scatter.append({
                        "timestamp": _format_ns_timestamp(_ts_c) if _ts_c else None,
                        "pnl": round(_pnl_v, 4),
                        "side": _format_order_side(p.entry),
                        "instrument": str(p.instrument_id),
                    })
            except Exception:
                logger.warning("Failed to compute trade PnL scatter", exc_info=True)

            # 4. MAE/MFE (max adverse/favorable excursion)
            try:
                _bar_type_list = engine.cache.bar_types()
                # Build a dict: instrument_id_str -> list of bars (sorted by ts_init)
                _inst_bars: dict[str, list] = {}
                for _bt in _bar_type_list:
                    _bars = engine.cache.bars(_bt)
                    if _bars:
                        _inst_key = str(_bt.instrument_id)
                        if _inst_key not in _inst_bars:
                            _inst_bars[_inst_key] = list(_bars)
                        else:
                            _inst_bars[_inst_key].extend(_bars)

                for p in closed_positions:
                    _inst_str = str(p.instrument_id)
                    if _inst_str not in _inst_bars:
                        continue
                    _ts_o = getattr(p, "ts_opened", 0)
                    _ts_c = getattr(p, "ts_closed", 0)
                    if not _ts_o or not _ts_c:
                        continue

                    _entry_px = float(p.avg_px_open)
                    _side_str = _format_order_side(p.entry)
                    _p_pnl = _parse_realized_pnl(p.realized_pnl)

                    # Find bars within holding period
                    _highs = []
                    _lows = []
                    for bar in _inst_bars[_inst_str]:
                        if bar.ts_init >= _ts_o and bar.ts_init <= _ts_c:
                            _highs.append(float(bar.high))
                            _lows.append(float(bar.low))

                    if not _highs:
                        continue

                    _max_high = max(_highs)
                    _min_low = min(_lows)

                    if _side_str == "BUY":
                        _mae = _entry_px - _min_low
                        _mfe = _max_high - _entry_px
                    else:
                        _mae = _max_high - _entry_px
                        _mfe = _entry_px - _min_low

                    mae_mfe.append({
                        "pnl": round(_p_pnl, 4),
                        "mae": round(_mae, 4),
                        "mfe": round(_mfe, 4),
                        "side": _side_str,
                    })
            except Exception:
                logger.warning("Failed to compute MAE/MFE", exc_info=True)

            # 5. Holding time distribution (histogram, in hours)
            try:
                _durations_h = []
                for p in closed_positions:
                    _dur = getattr(p, "duration_ns", None)
                    if _dur and _dur > 0:
                        _durations_h.append(int(_dur) / 3.6e12)  # ns -> hours
                if _durations_h:
                    _dur_arr = np.array(_durations_h, dtype=float)
                    _n_bins_h = min(30, max(10, len(_durations_h) // 5))
                    _h_counts, _h_edges = np.histogram(_dur_arr, bins=_n_bins_h)
                    for j in range(len(_h_counts)):
                        holding_time_distribution.append({
                            "bin_start": round(float(_h_edges[j]), 4),
                            "bin_end": round(float(_h_edges[j + 1]), 4),
                            "count": int(_h_counts[j]),
                        })
            except Exception:
                logger.warning("Failed to compute holding time distribution", exc_info=True)

            # 6. Streak sequence
            try:
                streak_sequence = compute_streak_sequence(pnls)
            except Exception:
                logger.warning("Failed to compute streak sequence", exc_info=True)

            # 7. Long vs Short comparison
            try:
                trade_sides = [
                    (_format_order_side(p.entry), _parse_realized_pnl(p.realized_pnl))
                    for p in closed_positions
                ]
                long_vs_short = compute_long_vs_short(trade_sides)
            except Exception:
                logger.warning("Failed to compute long vs short comparison", exc_info=True)

            # 8/9. Return by day-of-week / hour
            try:
                trade_times = [
                    (getattr(p, "ts_closed", 0) or 0, _parse_realized_pnl(p.realized_pnl))
                    for p in closed_positions
                ]
                return_by_dow = compute_return_by_dow(trade_times)
                return_by_hour = compute_return_by_hour(trade_times)
            except Exception:
                logger.warning("Failed to compute return by day-of-week/hour", exc_info=True)

    except Exception:
        logger.warning("Failed to compute trades analytics (section 12b)", exc_info=True)

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
            "median_trade_pnl": _safe_float(median_trade_pnl),
            "std_trade_pnl": _safe_float(std_trade_pnl),
            "fill_rate": _safe_float(fill_rate),
            "avg_trades_per_day": _safe_float(avg_trades_per_day),
            "recovery_factor": _safe_float(recovery_factor),
            "sqn": _safe_float(sqn),
            "kelly_criterion": _safe_float(kelly_criterion),
            "k_ratio": _safe_float(k_ratio),
            "expectancy_r": _safe_float(expectancy_r),
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
            _r_daily_sr = (mean_ret / std_ret) if std_ret and std_ret > 1e-12 else None
            _r_sk = skewness if skewness is not None else 0.0
            _r_ku = kurtosis_val if kurtosis_val is not None else 0.0
            _r_nobs = _n_days if _n_days else 0

            psr_val = _compute_psr(_r_daily_sr, _r_nobs, _r_sk, _r_ku)
            mbl_val = _compute_min_backtest_length(_r_daily_sr, _r_sk, _r_ku)

            # MC from trade PnLs
            trade_pnl_list = [
                float(t["realized_pnl"])
                for t in trade_log
                if isinstance(t.get("realized_pnl"), (int, float))
            ]
            mc_result = _compute_monte_carlo(trade_pnl_list, starting_balance)

            robustness = {
                "psr": psr_val,
                "min_backtest_length_days": mbl_val,
                "actual_backtest_length_days": _r_nobs,
                "backtest_length_sufficient": (
                    (_r_nobs >= mbl_val) if mbl_val is not None else None
                ),
            }
            if mc_result:
                robustness["mc_equity_cone"] = {
                    "percentiles": mc_result["percentiles"],
                    "curves": mc_result["curves"],
                    "original": mc_result["original"],
                    "x_labels": mc_result["x_labels"],
                }
                robustness["mc_probability_of_loss"] = mc_result["mc_probability_of_loss"]
                robustness["mc_5th_percentile_return"] = mc_result["mc_5th_percentile_return"]
                robustness["mc_median_max_drawdown"] = mc_result["mc_median_max_drawdown"]
                robustness["mc_median_final_return"] = mc_result["mc_median_final_return"]
                robustness["mc_num_simulations"] = mc_result["mc_num_simulations"]
        except Exception:
            logger.warning("Robustness computation failed", exc_info=True)

    _result["robustness"] = robustness

    return _result
