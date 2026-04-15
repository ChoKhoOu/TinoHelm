"""Full diagnostic report — orchestrates all analysis modules."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tinohelm.research.loader import load_data
from tinohelm.research.factors import compute_factor
from tinohelm.research.analysis import (
    forward_returns, compute_ic_series, compute_ic_summary, compute_rating,
    compute_ic_decay, compute_half_life, compute_quantile_returns,
    compute_distribution, compute_turnover,
)
from tinohelm.research.robustness import shuffle_test, subsample_ic, cross_symbol_ic
from tinohelm.research.param_scan import sweep_1d, sweep_2d
from tinohelm.research.cost import edge_waterfall

logger = logging.getLogger(__name__)

def _reports_dir() -> Path:
    from tinohelm.core.config import get_settings
    return get_settings().paths.research / "reports"


def _judge_signal_profile(stats: dict) -> str:
    """Pass/warn/fail for signal profile."""
    std = stats.get("std", 0)
    zero_pct = stats.get("zero_pct", 0)
    skew = abs(stats.get("skew", 0))
    acf = stats.get("autocorr_1", 0)

    if std == 0 or zero_pct > 0.5:
        return "fail"
    if skew > 5 or acf > 0.999:
        return "warn"
    return "pass"


def _judge_predictive_power(summary: dict) -> str:
    """Pass/warn/fail for predictive power."""
    tstat = abs(summary.get("ic_tstat", 0))
    ir = abs(summary.get("ir", 0))
    pct = summary.get("ic_positive_pct", 0)

    if tstat < 2.0:
        return "fail"
    if ir < 0.5 or pct < 0.55:
        return "warn"
    return "pass"


def _judge_robustness(shuffle_result: dict, subsample_result: list, cross_result: list) -> str:
    """Pass/warn/fail for robustness."""
    if not shuffle_result.get("significant", False):
        return "fail"

    if subsample_result:
        neg_pct = sum(1 for s in subsample_result if s.get("ic", 0) < 0) / len(subsample_result)
        if neg_pct > 0.4:
            return "warn"

    if cross_result:
        pos_count = sum(1 for c in cross_result if c.get("ic", 0) > 0)
        if pos_count < len(cross_result) * 0.5:
            return "fail"

    return "pass"


def _judge_cost_params(waterfall: dict, heatmap: dict | None) -> str:
    """Pass/warn/fail for cost & params."""
    net = waterfall.get("net_edge_bps", 0)
    gross = waterfall.get("gross_edge_bps", 1)

    if net <= 0:
        return "fail"
    if gross > 0 and net / gross < 0.3:
        return "warn"
    return "pass"


def generate_report(
    job_id: str,
    factor_name: str,
    symbol: str,
    data_type: str,
    interval: str,
    start_date: str,
    end_date: str,
    factor_params: dict,
    forward_periods: list[int] | None = None,
    n_quantiles: int = 5,
    shuffle_iterations: int = 1000,
    cross_symbols: list[str] | None = None,
    param_scan_config: dict | None = None,
    fee_rate: float = 0.0004,
    slippage_bps: float = 1.0,
    catalog_path: str | None = None,
    progress_cb: Any = None,
) -> dict:
    """Generate full diagnostic report.

    progress_cb: callable(pct: int, msg: str) or None — must be a sync callable.
        The caller is responsible for bridging async callbacks to sync
        (e.g. via asyncio.run_coroutine_threadsafe) before passing them here,
        since generate_report runs in a worker thread via asyncio.to_thread().
    """
    if forward_periods is None:
        forward_periods = [5, 15, 30]

    report: dict[str, Any] = {
        "job_id": job_id,
        "factor_name": factor_name,
        "symbol": symbol,
        "interval": interval,
        "created_at": datetime.utcnow().isoformat(),
        "status": "completed",
    }

    def _progress(pct: int, msg: str):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    # 1. Load data
    _progress(5, "加载数据...")
    df = load_data(symbol, data_type, interval, start_date, end_date, catalog_path)
    logger.info("Loaded %d rows (%s) for %s", len(df), data_type, symbol)

    # 2. Compute factor
    _progress(10, "计算因子...")
    factor = compute_factor(factor_name, df, factor_params)
    primary_fwd = forward_returns(df["close"], forward_periods[0])

    # 3. Signal Profile (Tab 1)
    _progress(15, "信号特征分析...")
    dist = compute_distribution(factor)

    # ACF
    clean = factor.dropna()
    clean = clean[np.isfinite(clean)]
    acf_values = []
    for lag in [1, 2, 3, 5, 8, 13, 21, 34, 55]:
        ac = float(pd.Series(clean.values).autocorr(lag=lag)) if len(clean) > lag else 0
        acf_values.append({"lag": lag, "value": round(ac, 4) if np.isfinite(ac) else 0})

    # RV correlation
    rv = df["close"].pct_change().rolling(20).std()
    rv_corr = float(factor.corr(rv)) if len(factor.dropna()) > 30 else 0

    profile_stats = {
        **dist["stats"],
        "rv_corr": round(rv_corr, 4) if np.isfinite(rv_corr) else 0,
    }

    report["signal_profile"] = {
        "stats": profile_stats,
        "distribution": dist,
        "acf": acf_values,
    }

    # 4. Predictive Power (Tab 2)
    _progress(25, "预测力分析...")
    horizons_data = []
    for h in forward_periods:
        fwd = forward_returns(df["close"], h)
        ic_ser = compute_ic_series(factor, fwd)
        summ = compute_ic_summary(ic_ser)
        horizons_data.append({"forward_period": h, **summ})

    primary_ic_ser = compute_ic_series(factor, primary_fwd)
    primary_summary = compute_ic_summary(primary_ic_ser)
    primary_summary["rating"] = compute_rating(primary_summary)

    decay = compute_ic_decay(factor, df["close"])
    quantiles = compute_quantile_returns(factor, primary_fwd, n_quantiles)

    report["predictive_power"] = {
        "horizons": horizons_data,
        "ic_series": primary_ic_ser.to_dict("records") if len(primary_ic_ser) > 0 else [],
        "ic_decay": decay,
        "half_life": compute_half_life(decay),
        "quantile_avg_returns": quantiles.get("avg_returns", {}),
        "quantile_cum_returns": quantiles.get("cum_returns", {}),
        "is_monotonic": quantiles.get("is_monotonic", False),
    }

    # 5. Robustness (Tab 3)
    _progress(40, f"Shuffle test ({shuffle_iterations}次)...")
    shuffle_result = shuffle_test(factor, primary_fwd, n_iter=shuffle_iterations)

    _progress(65, "分段稳定性...")
    subsample_result = subsample_ic(factor, primary_fwd)

    cross_result = []
    if cross_symbols:
        _progress(70, f"跨品种 IC ({len(cross_symbols)} 个)...")
        cross_result = cross_symbol_ic(
            factor_name, factor_params, cross_symbols,
            interval, start_date, end_date, forward_periods[0], catalog_path,
        )

    report["robustness"] = {
        "shuffle_test": shuffle_result,
        "subsample_ic": subsample_result,
        "positive_period_pct": round(
            sum(1 for s in subsample_result if s.get("ic", 0) > 0) / max(len(subsample_result), 1), 4
        ),
        "cross_symbol": cross_result,
    }

    # 6. Cost & Params (Tab 4)
    _progress(80, "成本分析...")
    turnover = compute_turnover(factor, primary_fwd, n_quantiles, fee_rate)
    waterfall = edge_waterfall(
        primary_summary.get("ic_mean", 0), turnover.get("daily", 0), fee_rate, slippage_bps,
    )

    heatmap = None
    sweep = None
    if param_scan_config:
        scan_type = param_scan_config.get("type", "1d")
        if scan_type == "2d" and "param1" in param_scan_config and "param2" in param_scan_config:
            _progress(85, "参数热力图...")
            p1 = param_scan_config["param1"]
            p2 = param_scan_config["param2"]
            heatmap = sweep_2d(
                factor_name, df,
                p1["name"], p1["values"], p2["name"], p2["values"],
                factor_params, forward_periods[0],
            )

        if "param1" in param_scan_config:
            _progress(90, "单参数扫描...")
            p1 = param_scan_config["param1"]
            sweep = sweep_1d(
                factor_name, df, p1["name"], p1["values"],
                factor_params, forward_periods[0],
            )

    report["cost_params"] = {
        "waterfall": waterfall,
        "turnover": turnover,
        "param_heatmap": heatmap,
        "param_sweep": sweep,
    }

    # 7. Verdicts
    report["verdict"] = {
        "signal_profile": _judge_signal_profile(profile_stats),
        "predictive_power": _judge_predictive_power(primary_summary),
        "robustness": _judge_robustness(shuffle_result, subsample_result, cross_result),
        "cost_params": _judge_cost_params(waterfall, heatmap),
    }
    report["summary"] = primary_summary

    # 8. Save to disk
    _progress(95, "保存报告...")
    reports_dir = _reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{job_id}.json"

    with open(report_path, "w") as f:
        json.dump(report, f, default=str, ensure_ascii=False)

    _progress(100, "完成")
    logger.info("Report saved: %s", report_path)

    return {"path": str(report_path), "report": report}
