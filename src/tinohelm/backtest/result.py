"""Backtest result extraction from NautilusTrader BacktestEngine."""
from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely convert a value to float, returning *default* on failure.

    Returns *default* for NaN and Infinity to ensure JSON compatibility.
    """
    if value is None:
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _format_duration_ns(ns: int | float) -> str | None:
    """Convert nanoseconds to a human-readable duration string."""
    if ns is None or ns <= 0:
        return None
    total_seconds = int(ns) // 1_000_000_000
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _parse_realized_pnl(pnl_obj: Any) -> float:
    """Extract float value from NT Money/realized_pnl.

    NT Money objects stringify as ``"114.60 USDT"``.  ``Decimal(...)`` chokes
    on the currency suffix, so we strip it first.  Also handles ``as_double()``
    when available.
    """
    if pnl_obj is None:
        return 0.0
    try:
        # Prefer .as_double() if the object supports it (Money)
        if hasattr(pnl_obj, "as_double"):
            return float(pnl_obj.as_double())
        s = str(pnl_obj).strip()
        # Strip trailing currency like "USDT", "USD", "BTC"
        parts = s.split()
        return float(parts[0]) if parts else 0.0
    except (ValueError, TypeError, IndexError):
        return 0.0


def _format_order_side(entry: Any) -> str:
    """Convert NT OrderSide enum to readable string."""
    try:
        if hasattr(entry, "name"):
            return entry.name  # e.g. "BUY", "SELL"
        val = int(entry)
        return {1: "BUY", 2: "SELL"}.get(val, str(entry))
    except (ValueError, TypeError):
        return str(entry)


def _format_ns_timestamp(ts_ns: Any) -> str | None:
    """Convert nanosecond timestamp to ISO string."""
    if ts_ns is None or ts_ns == 0:
        return None
    try:
        from datetime import datetime, timezone
        ts_sec = int(ts_ns) / 1e9
        return datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _compute_streaks(pnl_values: list[float]) -> tuple[int, int]:
    """Compute the longest winning and losing streaks from a PnL sequence."""
    max_win_streak = 0
    max_lose_streak = 0
    cur_win = 0
    cur_lose = 0
    for pnl in pnl_values:
        if pnl > 0:
            cur_win += 1
            cur_lose = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif pnl < 0:
            cur_lose += 1
            cur_win = 0
            max_lose_streak = max(max_lose_streak, cur_lose)
    return max_win_streak, max_lose_streak


def extract_backtest_results(
    engine: BacktestEngine,
    starting_balance: float = 10000,
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
        if returns_series is not None and len(returns_series) > 0:
            rs = returns_series.copy()
            rs.index = pd.to_datetime(rs.index)

            # Monthly
            monthly = (1 + rs).resample("ME").prod() - 1
            for ts, ret in monthly.items():
                monthly_returns.append({
                    "period": ts.strftime("%Y-%m"),
                    "return_pct": round(float(ret) * 100, 4),
                })

            # Weekly
            weekly = (1 + rs).resample("W").prod() - 1
            for ts, ret in weekly.items():
                weekly_returns.append({
                    "period": ts.strftime("%Y-%m-%d"),
                    "return_pct": round(float(ret) * 100, 4),
                })
    except Exception:
        logger.warning("Failed to compute periodic returns", exc_info=True)

    # ------------------------------------------------------------------
    # 11. Drawdown analysis (periods with duration)
    # ------------------------------------------------------------------
    drawdown_periods: list[dict[str, Any]] = []
    try:
        if returns_series is not None and len(returns_series) > 0:
            rs_dd = returns_series.copy()
            rs_dd.index = pd.to_datetime(rs_dd.index)
            cum = (1 + rs_dd).cumprod()
            peak = cum.cummax()
            dd = (cum - peak) / peak

            in_drawdown = False
            dd_start = None
            dd_trough = 0.0
            dd_trough_ts = None

            for ts, dd_val in dd.items():
                if dd_val < 0:
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
                            "start": str(dd_start),
                            "trough_date": str(dd_trough_ts),
                            "recovery_date": str(ts),
                            "max_drawdown_pct": round(float(dd_trough) * 100, 4),
                            "duration_days": (ts - dd_start).days,
                            "recovery_days": (ts - dd_trough_ts).days,
                        })
                        in_drawdown = False

            # Handle ongoing drawdown at end
            if in_drawdown and dd_start is not None:
                drawdown_periods.append({
                    "start": str(dd_start),
                    "trough_date": str(dd_trough_ts),
                    "recovery_date": None,
                    "max_drawdown_pct": round(float(dd_trough) * 100, 4),
                    "duration_days": (rs_dd.index[-1] - dd_start).days,
                    "recovery_days": None,
                })

            # Sort by severity, keep top 10
            drawdown_periods.sort(key=lambda x: x["max_drawdown_pct"])
            drawdown_periods = drawdown_periods[:10]
    except Exception:
        logger.warning("Failed to compute drawdown periods", exc_info=True)

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
    # 13. Assemble final result
    # ------------------------------------------------------------------
    return {
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
    }
