"""Backtest result extraction from NautilusTrader BacktestEngine."""
from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine

from tinohelm.backtest.result.statistics import (
    _compute_monte_carlo,
    _compute_psr,
    _compute_min_backtest_length,
    _compute_streaks,
    _format_duration_ns,
    _format_ns_timestamp,
    _format_order_side,
    _norm_cdf,
    _norm_ppf,
    _parse_realized_pnl,
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
        from collections import defaultdict as _ec_defaultdict
        from datetime import datetime as _ec_dt, timezone as _ec_tz

        # Aggregate realized PnL by close date
        daily_pnl: dict[str, float] = _ec_defaultdict(float)
        for p in closed_positions:
            ts_closed = getattr(p, "ts_closed", None)
            if ts_closed and ts_closed > 0:
                close_date = _ec_dt.fromtimestamp(
                    int(ts_closed) / 1e9, tz=_ec_tz.utc
                ).strftime("%Y-%m-%d")
                daily_pnl[close_date] += _parse_realized_pnl(p.realized_pnl)

        if daily_pnl:
            sorted_dates = sorted(daily_pnl.keys())
            cum_pnl = 0.0
            peak_equity = starting_balance
            for d in sorted_dates:
                cum_pnl += daily_pnl[d]
                equity_val = starting_balance + cum_pnl
                peak_equity = max(peak_equity, equity_val)
                dd_pct = ((equity_val - peak_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
                ret_pct = (daily_pnl[d] / starting_balance * 100) if starting_balance > 0 else 0.0
                equity_curve.append({
                    "timestamp": d,
                    "equity": round(equity_val, 4),
                    "returns_pct": round(ret_pct, 4),
                    "drawdown_pct": round(dd_pct, 4),
                })
    except Exception:
        logger.warning("Failed to build equity curve", exc_info=True)

    # Downsample if too many points (> 2000)
    if len(equity_curve) > 2000:
        step = len(equity_curve) / 2000
        indices = [int(i * step) for i in range(2000)]
        indices[-1] = len(equity_curve) - 1  # Always include last point
        equity_curve = [equity_curve[i] for i in indices]

    # Function-scope variables for daily returns (used by sections 8b, 8c, 11b-11f)
    _daily_rets = None  # numpy array, set in section 8b
    _n_days = 0

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
    try:
        if len(equity_curve) >= 2:
            import numpy as _np
            from datetime import datetime as _eq_dt

            eq_values = [starting_balance] + [pt["equity"] for pt in equity_curve]
            eq_arr = _np.array(eq_values, dtype=float)

            # Daily portfolio returns: r_t = (equity_t - equity_{t-1}) / equity_{t-1}
            daily_rets = _np.diff(eq_arr) / eq_arr[:-1]

            # Hoist to function scope for downstream sections (8c, 11b-11f)
            _daily_rets = daily_rets
            _n_days = len(daily_rets)

            # Also fix returns_pct in equity_curve to use proper daily return
            for i, pt in enumerate(equity_curve):
                pt["returns_pct"] = round(float(daily_rets[i]) * 100, 4)

            n_days = len(daily_rets)
            mean_ret = float(daily_rets.mean())
            std_ret = float(daily_rets.std(ddof=1)) if n_days > 1 else 0.0

            # Calendar days for annualization (crypto = 365)
            first_date = _eq_dt.strptime(equity_curve[0]["timestamp"], "%Y-%m-%d")
            last_date = _eq_dt.strptime(equity_curve[-1]["timestamp"], "%Y-%m-%d")
            calendar_days = max((last_date - first_date).days, 1)
            ann_factor = 365  # crypto markets trade 365 days/year

            # Max Drawdown (peak-to-trough of equity)
            peak_arr = _np.maximum.accumulate(eq_arr[1:])  # skip prepended starting_balance
            dd_arr = (eq_arr[1:] - peak_arr) / peak_arr
            max_drawdown = round(float(dd_arr.min()), 6) if len(dd_arr) > 0 else 0.0

            # CAGR: (final / initial) ^ (365 / calendar_days) - 1
            final_eq = eq_arr[-1]
            if final_eq > 0 and starting_balance > 0:
                cagr = round((final_eq / starting_balance) ** (ann_factor / calendar_days) - 1, 6)
            else:
                cagr = None

            # Sharpe Ratio: mean(daily_ret) / std(daily_ret) * sqrt(365)
            if std_ret > 1e-12:
                sharpe = round(mean_ret / std_ret * _np.sqrt(ann_factor), 4)
            else:
                sharpe = None

            # Sortino Ratio: mean(daily_ret) / downside_std * sqrt(365)
            downside = daily_rets[daily_rets < 0]
            ds_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
            if ds_std > 1e-12:
                sortino = round(mean_ret / ds_std * _np.sqrt(ann_factor), 4)
            else:
                sortino = None

            # Calmar Ratio: CAGR / |max_drawdown|
            if cagr is not None and max_drawdown is not None and abs(max_drawdown) > 1e-12:
                calmar = round(cagr / abs(max_drawdown), 4)
            else:
                calmar = None

            # Volatility: std(daily_ret) * sqrt(365)
            returns_volatility = round(std_ret * _np.sqrt(ann_factor), 4) if std_ret > 1e-12 else None

            # Total return % (consistent with equity curve)
            total_return_pct = round((final_eq / starting_balance - 1) * 100, 4)

            logger.info(
                "Risk metrics recomputed from equity curve: max_dd=%.4f, sharpe=%.4f, cagr=%.4f (%d calendar days, %d data points)",
                max_drawdown, sharpe or 0, cagr or 0, calendar_days, n_days,
            )

            # ----------------------------------------------------------
            # 8c. Extended statistics for returns analytics
            # ----------------------------------------------------------
            try:
                best_day = round(float(daily_rets.max() * 100), 4)
                worst_day = round(float(daily_rets.min() * 100), 4)
                positive_days_pct = round(float((daily_rets > 0).sum() / n_days * 100), 2) if n_days > 0 else None

                # Skewness & Kurtosis
                try:
                    from scipy.stats import skew as _sp_skew, kurtosis as _sp_kurt
                    skewness = _safe_float(_sp_skew(daily_rets))
                    kurtosis_val = _safe_float(_sp_kurt(daily_rets, fisher=True))
                except ImportError:
                    skewness = _safe_float(float(_np.mean(((daily_rets - mean_ret) / std_ret) ** 3))) if std_ret > 1e-12 else None
                    kurtosis_val = _safe_float(float(_np.mean(((daily_rets - mean_ret) / std_ret) ** 4) - 3)) if std_ret > 1e-12 else None

                # Tail Ratio: 95th percentile / abs(5th percentile)
                p95 = float(_np.percentile(daily_rets, 95))
                p5 = float(_np.percentile(daily_rets, 5))
                tail_ratio = round(p95 / abs(p5), 4) if abs(p5) > 1e-12 else None

                # Stability: R² of cumulative returns linear regression
                cum_rets = _np.cumsum(daily_rets)
                x_idx = _np.arange(len(cum_rets))
                if len(x_idx) > 1:
                    slope, intercept = _np.polyfit(x_idx, cum_rets, 1)
                    y_pred = slope * x_idx + intercept
                    ss_res = float(_np.sum((cum_rets - y_pred) ** 2))
                    ss_tot = float(_np.sum((cum_rets - cum_rets.mean()) ** 2))
                    stability = round(1 - ss_res / ss_tot, 4) if ss_tot > 1e-12 else None
                else:
                    stability = None

                # ----------------------------------------------------------
                # 8c-ext. Additional risk metrics for Performance tab
                # ----------------------------------------------------------
                # Omega Ratio: sum(gains) / sum(|losses|)
                _gains = daily_rets[daily_rets > 0].sum()
                _losses_abs = abs(daily_rets[daily_rets < 0].sum())
                omega_ratio = round(float(_gains / _losses_abs), 4) if _losses_abs > 1e-12 else None

                # VaR (95% and 99%) — percentile of daily returns as %
                var_95 = round(float(_np.percentile(daily_rets, 5) * 100), 4)
                var_99 = round(float(_np.percentile(daily_rets, 1) * 100), 4)

                # CVaR / Expected Shortfall (mean of returns below VaR threshold)
                _var_thresh = _np.percentile(daily_rets, 5)
                _below_var = daily_rets[daily_rets <= _var_thresh]
                cvar_95 = round(float(_below_var.mean() * 100), 4) if len(_below_var) > 0 else None

                # Downside Deviation (annualized)
                _ds_rets = daily_rets[daily_rets < 0]
                downside_dev = round(float(_ds_rets.std(ddof=1) * _np.sqrt(365)), 4) if len(_ds_rets) > 1 else None

                # Ulcer Index: RMS of drawdown percentages
                ulcer_index = round(float(_np.sqrt(_np.mean(dd_arr ** 2))), 6) if len(dd_arr) > 0 else None

                # Max Daily Loss (percentage)
                max_daily_loss = round(float(daily_rets.min() * 100), 4) if n_days > 0 else None

                # Normal distribution parameters (for overlay curve in frontend)
                normal_dist_mean = round(float(mean_ret * 100), 6)
                normal_dist_std = round(float(std_ret * 100), 6)

            except Exception:
                logger.warning("Failed to compute extended statistics (8c)", exc_info=True)

    except Exception:
        logger.warning("Failed to recompute risk metrics from equity curve", exc_info=True)

    # ------------------------------------------------------------------
    # 9. Per-instrument breakdown
    # ------------------------------------------------------------------
    per_instrument: dict[str, dict[str, Any]] = {}
    try:
        from collections import defaultdict
        inst_positions: dict[str, list] = defaultdict(list)
        for p in closed_positions:
            inst_positions[str(p.instrument_id)].append(p)

        for inst_id, pos_list in inst_positions.items():
            inst_wins = 0
            inst_losses = 0
            inst_profit = 0.0
            inst_loss = 0.0
            inst_pnls: list[float] = []
            for p in pos_list:
                pv = _parse_realized_pnl(p.realized_pnl)
                inst_pnls.append(pv)
                if pv > 0:
                    inst_wins += 1
                    inst_profit += pv
                else:
                    inst_losses += 1
                    inst_loss += abs(pv)
            inst_total = inst_wins + inst_losses
            per_instrument[inst_id] = {
                "total_trades": inst_total,
                "winning_trades": inst_wins,
                "losing_trades": inst_losses,
                "win_rate": round(inst_wins / inst_total, 4) if inst_total > 0 else 0.0,
                "total_pnl": round(sum(inst_pnls), 4),
                "gross_profit": round(inst_profit, 4),
                "gross_loss": round(inst_loss, 4),
                "profit_factor": round(inst_profit / inst_loss, 4) if inst_loss > 0 else None,
                "largest_win": round(max(inst_pnls), 4) if inst_pnls else None,
                "largest_loss": round(min(inst_pnls), 4) if inst_pnls else None,
                "avg_pnl": round(sum(inst_pnls) / len(inst_pnls), 4) if inst_pnls else None,
                "return_pct": round(sum(inst_pnls) / starting_balance * 100, 4) if starting_balance > 0 else 0.0,
            }
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
            import numpy as np
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
            from collections import defaultdict as _pr_defaultdict

            monthly_pnl_agg: dict[str, float] = _pr_defaultdict(float)
            weekly_pnl_agg: dict[str, float] = _pr_defaultdict(float)

            prev_equity = starting_balance
            for pt in equity_curve:
                eq = pt["equity"]
                daily_ret = (eq - prev_equity) / prev_equity if prev_equity > 0 else 0.0
                month_key = pt["timestamp"][:7]  # "YYYY-MM"
                monthly_pnl_agg[month_key] += daily_ret

                # ISO week ending date
                from datetime import datetime as _pr_dt
                d = _pr_dt.strptime(pt["timestamp"], "%Y-%m-%d")
                # Use Monday-start week key
                import calendar as _cal_mod
                week_start = d - pd.Timedelta(days=d.weekday())
                week_key = (week_start + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
                weekly_pnl_agg[week_key] += daily_ret

                prev_equity = eq

            for period in sorted(monthly_pnl_agg.keys()):
                monthly_returns.append({
                    "period": period,
                    "return_pct": round(float(monthly_pnl_agg[period]) * 100, 4),
                })
            for period in sorted(weekly_pnl_agg.keys()):
                weekly_returns.append({
                    "period": period,
                    "return_pct": round(float(weekly_pnl_agg[period]) * 100, 4),
                })
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
        # Use equity-curve drawdown_pct for consistency with KPI and chart
        if equity_curve and len(equity_curve) >= 2:
            from datetime import datetime as _dd_dt

            in_drawdown = False
            dd_start = None
            dd_trough = 0.0
            dd_trough_ts = None

            for pt in equity_curve:
                dd_val = pt["drawdown_pct"] / 100.0  # convert from pct to fraction
                ts = _dd_dt.strptime(pt["timestamp"], "%Y-%m-%d")

                if dd_val < -1e-8:
                    if not in_drawdown:
                        in_drawdown = True
                        dd_start = ts
                        dd_trough = dd_val
                        dd_trough_ts = ts
                    elif dd_val < dd_trough:
                        dd_trough = dd_val
                        dd_trough_ts = ts
                else:
                    if in_drawdown:
                        drawdown_periods.append({
                            "start": str(dd_start.date()),
                            "trough_date": str(dd_trough_ts.date()),
                            "recovery_date": str(ts.date()),
                            "max_drawdown_pct": round(float(dd_trough) * 100, 4),
                            "duration_days": (ts - dd_start).days,
                            "recovery_days": (ts - dd_trough_ts).days,
                        })
                        in_drawdown = False

            # Handle ongoing drawdown at end
            if in_drawdown and dd_start is not None:
                last_ts = _dd_dt.strptime(equity_curve[-1]["timestamp"], "%Y-%m-%d")
                drawdown_periods.append({
                    "start": str(dd_start.date()),
                    "trough_date": str(dd_trough_ts.date()),
                    "recovery_date": None,
                    "max_drawdown_pct": round(float(dd_trough) * 100, 4),
                    "duration_days": (last_ts - dd_start).days,
                    "recovery_days": None,
                })

            # Sort by severity, keep top 10
            drawdown_periods.sort(key=lambda x: x["max_drawdown_pct"])
            drawdown_periods = drawdown_periods[:10]
    except Exception:
        logger.warning("Failed to compute drawdown periods", exc_info=True)

    # ------------------------------------------------------------------
    # 11b. Annual returns (compound formula)
    # ------------------------------------------------------------------
    annual_returns: list[dict[str, Any]] = []
    try:
        if equity_curve and len(equity_curve) >= 2:
            year_boundaries: dict[int, dict] = {}
            prev_eq = starting_balance
            for pt in equity_curve:
                year = int(pt["timestamp"][:4])
                eq = pt["equity"]
                if year not in year_boundaries:
                    year_boundaries[year] = {"first_eq": prev_eq, "last_eq": eq}
                else:
                    year_boundaries[year]["last_eq"] = eq
                prev_eq = eq
            for y in sorted(year_boundaries.keys()):
                b = year_boundaries[y]
                ret = (b["last_eq"] / b["first_eq"] - 1) * 100 if b["first_eq"] > 0 else 0.0
                annual_returns.append({"year": y, "return_pct": round(ret, 4)})
    except Exception:
        logger.warning("Failed to compute annual returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11c. Rolling returns (3m/6m/12m)
    # ------------------------------------------------------------------
    rolling_returns: list[dict[str, Any]] = []
    try:
        if _daily_rets is not None and len(_daily_rets) >= 2:
            import numpy as _np2  # may re-alias if _np not in scope here
            _np_rr = _np2 if '_np2' in dir() else __import__('numpy')
            windows = {"rolling_3m": 63, "rolling_6m": 126, "rolling_12m": 252}
            for i, pt in enumerate(equity_curve):
                entry: dict[str, Any] = {"timestamp": pt["timestamp"]}
                for key, w in windows.items():
                    if i + 1 >= w:
                        window_rets = _daily_rets[i + 1 - w:i + 1]
                        cum_ret = float(_np_rr.prod(1 + window_rets) - 1)
                        entry[key] = round(cum_ret * 100, 4)
                    else:
                        entry[key] = None
                rolling_returns.append(entry)

            # Downsample with uniform spacing
            if len(rolling_returns) > 500:
                indices = _np_rr.linspace(0, len(rolling_returns) - 1, 500, dtype=int)
                rolling_returns = [rolling_returns[i] for i in indices]
    except Exception:
        logger.warning("Failed to compute rolling returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11d. Returns distribution (histogram bins)
    # ------------------------------------------------------------------
    returns_distribution: list[dict[str, Any]] = []
    try:
        if _daily_rets is not None and len(_daily_rets) >= 2:
            import numpy as _np3
            dr_pct = _daily_rets * 100
            counts, bin_edges = _np3.histogram(dr_pct, bins=40)
            for j in range(len(counts)):
                returns_distribution.append({
                    "bin_start": round(float(bin_edges[j]), 4),
                    "bin_end": round(float(bin_edges[j + 1]), 4),
                    "count": int(counts[j]),
                })
    except Exception:
        logger.warning("Failed to compute returns distribution", exc_info=True)

    # ------------------------------------------------------------------
    # 11e. QQ plot data (theoretical vs empirical quantiles)
    # ------------------------------------------------------------------
    qq_plot_data: list[dict[str, float]] = []
    try:
        if _daily_rets is not None and len(_daily_rets) >= 2:
            import numpy as _np4
            n = len(_daily_rets)
            sorted_rets = _np4.sort(_daily_rets)

            # Theoretical normal quantiles
            try:
                from scipy.stats import norm as _norm
                probs = _np4.linspace(1 / (n + 1), n / (n + 1), n)
                theoretical = _norm.ppf(probs)
            except ImportError:
                probs = _np4.linspace(1 / (n + 1), n / (n + 1), n)
                theoretical = _np4.array([_norm_ppf(float(p)) for p in probs])

            # Downsample to ~200 points
            if n > 200:
                indices = _np4.linspace(0, n - 1, 200, dtype=int)
                sorted_rets = sorted_rets[indices]
                theoretical = theoretical[indices]

            for t, e in zip(theoretical, sorted_rets):
                qq_plot_data.append({
                    "theoretical": round(float(t), 6),
                    "empirical": round(float(e), 6),
                })
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
            import numpy as _np_bmdr
            _bm_values = [starting_balance] + [pt["equity"] for pt in benchmark_equity_curve]
            _bm_arr = _np_bmdr.array(_bm_values, dtype=float)
            benchmark_daily_returns = _np_bmdr.diff(_bm_arr) / _bm_arr[:-1]
    except Exception:
        logger.warning("Failed to compute benchmark daily returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11g. Rolling Sharpe Ratio (3m/6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 63:
            import numpy as _np_rs
            _rs_windows = {"rolling_3m": 63, "rolling_6m": 126, "rolling_12m": 252}
            for i, pt in enumerate(equity_curve):
                entry: dict[str, Any] = {"timestamp": pt["timestamp"]}
                for key, w in _rs_windows.items():
                    if i + 1 >= w:
                        wr = _daily_rets[i + 1 - w:i + 1]
                        m, s = float(wr.mean()), float(wr.std(ddof=1))
                        entry[key] = round(m / s * _np_rs.sqrt(365), 4) if s > 1e-12 else None
                    else:
                        entry[key] = None
                rolling_sharpe.append(entry)
            if len(rolling_sharpe) > 500:
                _idx = _np_rs.linspace(0, len(rolling_sharpe) - 1, 500, dtype=int)
                rolling_sharpe = [rolling_sharpe[int(i)] for i in _idx]
    except Exception:
        logger.warning("Failed to compute rolling Sharpe", exc_info=True)

    # ------------------------------------------------------------------
    # 11h. Rolling Sortino Ratio (6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 126:
            import numpy as _np_rso
            _rso_windows = {"rolling_6m": 126, "rolling_12m": 252}
            for i, pt in enumerate(equity_curve):
                entry = {"timestamp": pt["timestamp"]}
                for key, w in _rso_windows.items():
                    if i + 1 >= w:
                        wr = _daily_rets[i + 1 - w:i + 1]
                        _ds = wr[wr < 0]
                        ds_s = float(_ds.std(ddof=1)) if len(_ds) > 1 else 0.0
                        entry[key] = round(float(wr.mean()) / ds_s * _np_rso.sqrt(365), 4) if ds_s > 1e-12 else None
                    else:
                        entry[key] = None
                rolling_sortino.append(entry)
            if len(rolling_sortino) > 500:
                _idx = _np_rso.linspace(0, len(rolling_sortino) - 1, 500, dtype=int)
                rolling_sortino = [rolling_sortino[int(i)] for i in _idx]
    except Exception:
        logger.warning("Failed to compute rolling Sortino", exc_info=True)

    # ------------------------------------------------------------------
    # 11i. Rolling Volatility (6m/12m)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and len(_daily_rets) >= 126:
            import numpy as _np_rv
            _rv_windows = {"rolling_6m": 126, "rolling_12m": 252}
            for i, pt in enumerate(equity_curve):
                entry = {"timestamp": pt["timestamp"]}
                for key, w in _rv_windows.items():
                    if i + 1 >= w:
                        wr = _daily_rets[i + 1 - w:i + 1]
                        entry[key] = round(float(wr.std(ddof=1)) * _np_rv.sqrt(365), 4)
                    else:
                        entry[key] = None
                rolling_volatility.append(entry)
            if len(rolling_volatility) > 500:
                _idx = _np_rv.linspace(0, len(rolling_volatility) - 1, 500, dtype=int)
                rolling_volatility = [rolling_volatility[int(i)] for i in _idx]
    except Exception:
        logger.warning("Failed to compute rolling volatility", exc_info=True)

    # ------------------------------------------------------------------
    # 11j. Rolling Beta (6m/12m) — requires benchmark returns
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and benchmark_daily_returns is not None and len(_daily_rets) >= 126:
            import numpy as _np_rb
            _rb_windows = {"rolling_6m": 126, "rolling_12m": 252}
            _rb_min_len = min(len(_daily_rets), len(benchmark_daily_returns))
            for i, pt in enumerate(equity_curve[:_rb_min_len]):
                entry = {"timestamp": pt["timestamp"]}
                for key, w in _rb_windows.items():
                    if i + 1 >= w:
                        sr = _daily_rets[i + 1 - w:i + 1]
                        br = benchmark_daily_returns[i + 1 - w:i + 1]
                        _cov_val = float(_np_rb.cov(sr, br)[0, 1])
                        _var_bm = float(_np_rb.var(br, ddof=1))
                        entry[key] = round(_cov_val / _var_bm, 4) if _var_bm > 1e-12 else None
                    else:
                        entry[key] = None
                rolling_beta.append(entry)
            if len(rolling_beta) > 500:
                _idx = _np_rb.linspace(0, len(rolling_beta) - 1, 500, dtype=int)
                rolling_beta = [rolling_beta[int(i)] for i in _idx]
    except Exception:
        logger.warning("Failed to compute rolling beta", exc_info=True)

    # ------------------------------------------------------------------
    # 11k. Benchmark-relative metrics (Alpha, Beta, R², Information Ratio)
    # ------------------------------------------------------------------
    try:
        if _daily_rets is not None and benchmark_daily_returns is not None:
            import numpy as _np_bm
            _bm_min_len = min(len(_daily_rets), len(benchmark_daily_returns))
            if _bm_min_len >= 30:
                _sr = _daily_rets[:_bm_min_len]
                _br = benchmark_daily_returns[:_bm_min_len]
                _cov_sb = float(_np_bm.cov(_sr, _br)[0, 1])
                _var_b = float(_np_bm.var(_br, ddof=1))
                if _var_b > 1e-12:
                    beta_val = round(_cov_sb / _var_b, 4)
                    alpha = round((float(_sr.mean()) - beta_val * float(_br.mean())) * 365, 4)
                    _corr = float(_np_bm.corrcoef(_sr, _br)[0, 1])
                    r_squared = round(_corr ** 2, 4)
                    _excess = _sr - _br
                    _te = float(_excess.std(ddof=1))
                    if _te > 1e-12:
                        information_ratio = round(float(_excess.mean()) / _te * _np_bm.sqrt(365), 4)
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
        import numpy as np
        from datetime import datetime as _ta_dt, timezone as _ta_tz

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
                _streaks: list[dict[str, Any]] = []
                _cur_type: str | None = None
                _cur_count = 0
                _cur_pnl = 0.0
                for _pv in pnls:
                    _t = "win" if _pv > 0 else "loss"
                    if _pv == 0:
                        _t = "loss"  # breakeven counted as loss streak
                    if _t == _cur_type:
                        _cur_count += 1
                        _cur_pnl += _pv
                    else:
                        if _cur_type is not None:
                            _streaks.append({
                                "streak_num": len(_streaks) + 1,
                                "type": _cur_type,
                                "count": _cur_count,
                                "total_pnl": round(_cur_pnl, 4),
                            })
                        _cur_type = _t
                        _cur_count = 1
                        _cur_pnl = _pv
                if _cur_type is not None:
                    _streaks.append({
                        "streak_num": len(_streaks) + 1,
                        "type": _cur_type,
                        "count": _cur_count,
                        "total_pnl": round(_cur_pnl, 4),
                    })
                streak_sequence = _streaks
            except Exception:
                logger.warning("Failed to compute streak sequence", exc_info=True)

            # 7. Long vs Short comparison
            try:
                _long_trades = [p for p in closed_positions if _format_order_side(p.entry) == "BUY"]
                _short_trades = [p for p in closed_positions if _format_order_side(p.entry) == "SELL"]

                def _side_stats(trades: list) -> dict[str, Any]:
                    if not trades:
                        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}
                    _pnls_s = [_parse_realized_pnl(t.realized_pnl) for t in trades]
                    _wins = sum(1 for v in _pnls_s if v > 0)
                    _total = sum(_pnls_s)
                    return {
                        "trades": len(trades),
                        "total_pnl": round(_total, 4),
                        "avg_pnl": round(_total / len(trades), 4),
                        "win_rate": round(_wins / len(trades), 4),
                    }

                long_vs_short = {
                    "long": _side_stats(_long_trades),
                    "short": _side_stats(_short_trades),
                }
            except Exception:
                logger.warning("Failed to compute long vs short comparison", exc_info=True)

            # 8. Return by day-of-week
            try:
                _dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                _dow_map: dict[int, list[float]] = {i: [] for i in range(7)}
                for p in closed_positions:
                    _ts_c = getattr(p, "ts_closed", None)
                    if _ts_c and _ts_c > 0:
                        _dt_c = _ta_dt.fromtimestamp(int(_ts_c) / 1e9, tz=_ta_tz.utc)
                        _dow_map[_dt_c.weekday()].append(_parse_realized_pnl(p.realized_pnl))
                for _dow_i in range(7):
                    return_by_dow.append({
                        "dow": _dow_i,
                        "dow_name": _dow_names[_dow_i],
                        "values": [round(v, 4) for v in _dow_map[_dow_i]],
                    })
            except Exception:
                logger.warning("Failed to compute return by day-of-week", exc_info=True)

            # 9. Return by hour
            try:
                _hour_map: dict[int, list[float]] = {i: [] for i in range(24)}
                for p in closed_positions:
                    _ts_c = getattr(p, "ts_closed", None)
                    if _ts_c and _ts_c > 0:
                        _dt_c = _ta_dt.fromtimestamp(int(_ts_c) / 1e9, tz=_ta_tz.utc)
                        _hour_map[_dt_c.hour].append(_parse_realized_pnl(p.realized_pnl))
                for _h in range(24):
                    return_by_hour.append({
                        "hour": _h,
                        "values": [round(v, 4) for v in _hour_map[_h]],
                    })
            except Exception:
                logger.warning("Failed to compute return by hour", exc_info=True)

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
