"""Backtest management commands."""
from __future__ import annotations

import time
from typing import List, Optional

import typer

from tinohelm.cli._http import api_call, output, output_format
from tinohelm.cli._style import (
    C, Table, bold, dim, accent, muted,
    color_value, color_status, status_badge,
    header, divider, kv, kv_color, progress_bar, inline_progress,
)

app = typer.Typer(no_args_is_help=True)


# ── Param parsing ──────────────────────────────────────────────────────────

def _infer_type(value: str):
    """Infer Python type from a string value.

    Conversion order: None -> bool -> int -> float -> str (fallback).
    """
    if value.lower() in ("none", "null", "~"):
        return None
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_params(params: Optional[List[str]]) -> dict:
    """Parse key=value parameter pairs into a dict with smart type inference."""
    if not params:
        return {}
    result = {}
    for p in params:
        if "=" not in p:
            typer.echo(f"Warning: ignoring invalid param '{p}' (expected key=value)", err=True)
            continue
        k, v = p.split("=", 1)
        result[k.strip()] = _infer_type(v.strip())
    return result


def _parse_param_ranges(params: Optional[List[str]]) -> dict:
    """Parse param range specs (name:min:max[:step[:type]]) into a dict."""
    if not params:
        return {}
    result = {}
    for p in params:
        parts = p.split(":")
        if len(parts) < 3:
            typer.echo(f"Warning: ignoring invalid param range '{p}' (expected name:min:max[:step[:type]])", err=True)
            continue
        name = parts[0]
        try:
            min_val = float(parts[1])
            max_val = float(parts[2])
        except ValueError:
            typer.echo(f"Warning: ignoring param '{name}' -- min/max must be numeric", err=True)
            continue
        step = None
        param_type = "float"
        if len(parts) >= 4:
            try:
                step = float(parts[3])
            except ValueError:
                typer.echo(f"Warning: ignoring step for param '{name}' -- must be numeric", err=True)
        if len(parts) >= 5:
            param_type = parts[4] if parts[4] in ("int", "float") else "float"
        spec: dict = {"type": param_type, "min": min_val, "max": max_val}
        if step is not None:
            spec["step"] = step
        result[name] = spec
    return result


# ── Status card (reused by status + wait) ──────────────────────────────────

def _status_card(data: dict, run_id: str) -> None:
    """Display a formatted status card for a backtest run."""
    st = data.get("status", "unknown")
    progress = data.get("progress_pct", 0)
    error = data.get("error")

    typer.echo()
    typer.echo(f"  {status_badge(st)} Backtest {accent(run_id[:8])}  {color_status(st)}")

    if st in ("running", "queued"):
        typer.echo(f"      {progress_bar(progress)}")

    # Summary line for completed
    r = data.get("result", {})
    stats = r.get("statistics", {}) if isinstance(r, dict) else {}
    if stats and st == "completed":
        pnl = stats.get("total_pnl")
        ret = stats.get("total_return_pct")
        sharpe = stats.get("sharpe_ratio")
        trades = stats.get("total_trades")
        wr = stats.get("win_rate")

        parts = [
            f"PnL: {color_value(pnl)}",
            f"Return: {color_value(ret, '+.2f')}%" if ret is not None else "Return: " + muted("-"),
            f"Sharpe: {f'{sharpe:.2f}' if sharpe else muted('-')}",
            f"Trades: {trades or muted('-')}",
            f"WinRate: {f'{wr*100:.1f}%' if wr is not None else muted('-')}",
        ]
        typer.echo(f"      {'  '.join(parts)}")

    if error:
        typer.echo(f"      {typer.style('Error:', fg=C.NEGATIVE)} {error}")
    typer.echo()


# ── Commands ───────────────────────────────────────────────────────────────

@app.command()
def run(
    strategy: str = typer.Argument(..., help="Strategy name or portfolio folder"),
    symbol: Optional[List[str]] = typer.Option(None, "--symbol", "-s", help="Instrument symbol(s), repeat for multiple"),
    interval: List[str] = typer.Option(["1m"], "--interval", "-i", help="Bar interval(s), repeat for multiple"),
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)"),
    param: Optional[List[str]] = typer.Option(None, "--param", "-p", help="Strategy param as key=value"),
    slippage_prob: float = typer.Option(0.0, "--slippage-prob", help="Probability of slippage (0.0-1.0)"),
    random_seed: Optional[int] = typer.Option(None, "--random-seed", help="Random seed for FillModel"),
):
    """Run a backtest.

    For single-file strategies, --symbol is required.
    For portfolio strategies (with portfolio.yaml), symbols are read from config.
    """
    params = _parse_params(param)
    fill_model = None
    if slippage_prob > 0.0 or random_seed is not None:
        fill_model = {"prob_slippage": slippage_prob}
        if random_seed is not None:
            fill_model["random_seed"] = random_seed

    # Build request — symbols may be None for portfolio strategies
    request_body: dict = {
        "strategy": strategy,
        "intervals": interval,
        "start_date": start, "end_date": end,
        "params": params, "fill_model": fill_model,
    }
    if symbol:
        request_body["symbols"] = symbol

    data = api_call("POST", "/api/backtest/run", json=request_body)
    if output_format() == "json":
        output(data)
        return

    rid = data.get("run_id", "?")
    header("Backtest Submitted")
    kv("Run ID", accent(rid), 12)
    kv("Strategy", strategy, 12)
    kv("Symbol", ", ".join(symbol) if symbol else muted("(from portfolio)"), 12)
    kv("Interval", ", ".join(interval), 12)
    kv("Period", f"{start} ~ {end}", 12)
    if params:
        kv("Params", str(params), 12)
    typer.echo()
    typer.echo(f"    Track: {dim(f'tino backtest wait {rid}')}")
    typer.echo()


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Backtest run ID"),
):
    """Check backtest run status."""
    data = api_call("GET", f"/api/backtest/{run_id}/status")
    if output_format() == "json":
        output(data)
        return
    _status_card(data, run_id)


@app.command()
def wait(
    run_id: str = typer.Argument(..., help="Backtest run ID"),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Max seconds to wait"),
):
    """Wait for a backtest to complete, polling every 2 seconds."""
    elapsed = 0
    poll_interval = 2
    typer.echo()
    s = "unknown"
    while elapsed < timeout:
        data = api_call("GET", f"/api/backtest/{run_id}/status")
        s = data.get("status", "unknown")
        progress = data.get("progress_pct", 0)

        typer.echo(f"\r{inline_progress(progress, s, elapsed)}", nl=False)

        if s in ("completed", "failed", "error", "cancelled"):
            typer.echo()
            _status_card(data, run_id)
            if s != "completed":
                raise typer.Exit(1)
            return
        time.sleep(poll_interval)
        elapsed += poll_interval

    typer.echo()
    typer.echo(f"  {typer.style('Timeout', fg=C.NEGATIVE)} after {timeout}s. Last status: {s}", err=True)
    raise typer.Exit(1)


@app.command()
def result(
    run_id: str = typer.Argument(..., help="Backtest run ID"),
):
    """Get backtest results."""
    data = api_call("GET", f"/api/backtest/{run_id}/result")
    if output_format() == "json":
        output(data)
        return

    import re

    r = data.get("result", data) if isinstance(data, dict) else data
    stats = r.get("statistics", {}) if isinstance(r, dict) else {}
    if not stats:
        output(data)
        return

    equity_curve = r.get("equity_curve", []) if isinstance(r, dict) else []
    trade_log = r.get("trade_log", []) if isinstance(r, dict) else []
    monthly = r.get("monthly_returns", []) if isinstance(r, dict) else []
    drawdowns = r.get("drawdown_periods", []) if isinstance(r, dict) else []

    pnl = stats.get("total_pnl")
    ret = stats.get("total_return_pct")
    balance = stats.get("final_balance", "-")

    # ── Helpers ──
    def _v(val, fmt=".2f", suffix=""):
        if val is None:
            return muted("-")
        return f"{val:{fmt}}{suffix}"

    def _vc(val, fmt="+.2f"):
        """Format + color a value."""
        if val is None:
            return muted("-")
        return color_value(val, fmt)

    def _ansi_len(s: str) -> int:
        return len(re.sub(r'\033\[[0-9;]*m', '', s))

    def _rpad(s: str, w: int) -> str:
        return s + " " * max(0, w - _ansi_len(s))

    def _lpad(s: str, w: int) -> str:
        return " " * max(0, w - _ansi_len(s)) + s

    BOX_H = "─"
    BOX_V = "│"
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "┌", "┐", "└", "┘"
    BOX_LT, BOX_RT = "├", "┤"
    W = 62  # total box width

    def _box_top():
        typer.echo(f"  {BOX_TL}{BOX_H * W}{BOX_TR}")

    def _box_mid():
        typer.echo(f"  {BOX_LT}{BOX_H * W}{BOX_RT}")

    def _box_bot():
        typer.echo(f"  {BOX_BL}{BOX_H * W}{BOX_BR}")

    def _box_line(text: str):
        pad = W - _ansi_len(text)
        typer.echo(f"  {BOX_V} {text}{' ' * max(0, pad - 1)}{BOX_V}")

    def _box_pair(l1: str, v1: str, l2: str, v2: str):
        left = f"{l1:>14s}: {_rpad(v1, 12)}"
        right = f"{l2:>14s}: {v2}"
        combined = f"{left}  {right}"
        _box_line(combined)

    def _box_empty():
        _box_line("")

    # ══════════════════════════════════════════════════════════
    typer.echo()
    _box_top()

    # ── Header ──
    title = f"  BACKTEST REPORT  {accent(run_id[:8])}"
    _box_line(bold(title))
    _box_mid()

    # ── Hero: PnL ──
    pnl_s = _vc(pnl)
    ret_s = _vc(ret)
    _box_line(f"  PnL: {pnl_s} USDT  ({ret_s}%)     Balance: {balance}")
    _box_mid()

    # ── Risk Metrics (2-col) ──
    _box_line(bold("  Risk Metrics"))
    _box_pair("Sharpe", _v(stats.get("sharpe_ratio")),
              "Sortino", _v(stats.get("sortino_ratio")))
    _box_pair("Calmar", _v(stats.get("calmar_ratio")),
              "Max Drawdown", _v(stats.get("max_drawdown"), ".2f", "%"))
    _box_pair("Volatility", _v(stats.get("returns_volatility")),
              "Ann. Return", _v(stats.get("annual_return"), ".2f", "%"))
    _box_mid()

    # ── Trade Stats ──
    _box_line(bold("  Trade Statistics"))
    total_trades = stats.get("total_trades", 0)
    w_trades = stats.get("winning_trades", 0)
    l_trades = stats.get("losing_trades", 0)
    wr = stats.get("win_rate")

    # Win/Lose visual bar
    if total_trades > 0:
        w_pct = w_trades / total_trades
        bar_w = 28
        w_fill = max(0, int(bar_w * w_pct))
        l_fill = bar_w - w_fill
        bar = typer.style("█" * w_fill, fg=C.POSITIVE) + typer.style("█" * l_fill, fg=C.NEGATIVE)
        wr_str = f"{wr*100:.1f}%" if wr is not None else "-"
        _box_line(f"  [{bar}]  {w_trades}W {l_trades}L  WR: {wr_str}")
    else:
        _box_line(f"  {muted('No closed trades')}")

    _box_pair("Profit Factor", _v(stats.get("profit_factor")),
              "Expectancy", _vc(stats.get("expectancy")))
    _box_pair("Largest Win", _vc(stats.get("largest_win")),
              "Largest Loss", _vc(stats.get("largest_loss")))
    _box_pair("Avg Win", _v(stats.get("avg_win"), ".2f"),
              "Avg Loss", _v(stats.get("avg_loss"), ".2f"))
    _box_pair("W/L Ratio", _v(stats.get("avg_win_loss_ratio")),
              "Streaks", f"{stats.get('winning_streak', 0)}W / {stats.get('losing_streak', 0)}L")
    _box_mid()

    # ── Position ──
    _box_line(bold("  Position"))
    long_pct = stats.get("long_pct")
    short_pct = stats.get("short_pct")
    if long_pct is not None and short_pct is not None:
        bar_w = 28
        l_fill = max(0, int(bar_w * long_pct))
        s_fill = bar_w - l_fill
        dir_bar = typer.style("█" * l_fill, fg=C.ACCENT) + typer.style("█" * s_fill, fg=C.WARN)
        _box_line(f"  [{dir_bar}]  Long {long_pct*100:.0f}% / Short {short_pct*100:.0f}%")

    _box_pair("Avg Hold", str(stats.get("avg_holding_time", "-")),
              "Total Fees", _v(stats.get("total_fees"), ".4f"))
    _box_pair("Orders", str(stats.get("total_orders", "-")),
              "Filled", str(stats.get("filled_orders", "-")))

    # ── Equity Curve Sparkline ──
    if equity_curve and len(equity_curve) >= 2:
        _box_mid()
        _box_line(bold("  Equity Curve"))
        values = [p.get("equity", 0) for p in equity_curve if p.get("equity")]
        if values:
            mn, mx = min(values), max(values)
            rng = mx - mn if mx > mn else 1
            spark_chars = "▁▂▃▄▅▆▇█"
            # Downsample to fit box width
            target = min(len(values), W - 4)
            step = max(1, len(values) // target)
            sampled = values[::step][:target]
            spark = ""
            for v in sampled:
                idx = min(7, int((v - mn) / rng * 7))
                spark += spark_chars[idx]
            _box_line(f"  {typer.style(spark, fg=C.ACCENT)}")
            _box_line(f"  {muted(f'Low: {mn:.2f}')}{' ' * max(1, W - 30 - len(f'Low: {mn:.2f}') - len(f'High: {mx:.2f}'))}{muted(f'High: {mx:.2f}')}")

    # ── Monthly Returns ──
    if monthly:
        _box_mid()
        _box_line(bold("  Monthly Returns"))
        for m in monthly:
            period = m.get("period", "?")
            ret_m = m.get("return_pct")
            if ret_m is not None:
                bar_len = min(30, int(abs(ret_m) * 5))
                if ret_m >= 0:
                    bar_vis = typer.style("+" * bar_len, fg=C.POSITIVE)
                else:
                    bar_vis = typer.style("-" * bar_len, fg=C.NEGATIVE)
                _box_line(f"  {period:>10s}  {_vc(ret_m):>8s}%  {bar_vis}")

    # ── Drawdown Periods ──
    if drawdowns:
        _box_mid()
        _box_line(bold("  Notable Drawdowns"))
        for dd in drawdowns[:5]:
            dd_pct = dd.get("max_drawdown_pct")
            start_d = str(dd.get("start", ""))[:10]
            dur = dd.get("duration_days", "?")
            rec = dd.get("recovery_days")
            rec_s = f"rec {rec}d" if rec is not None else "no recovery"
            _box_line(f"  {start_d}  {_vc(dd_pct):>8s}%  {dur}d  {muted(rec_s)}")

    # ── Top Trades ──
    if trade_log and len(trade_log) > 1:
        _box_mid()
        _box_line(bold("  Top Trades (by PnL)"))

        def _trade_pnl(t):
            try:
                return float(str(t.get("realized_pnl", "0")).split()[0])
            except (ValueError, IndexError):
                return 0.0

        sorted_trades = sorted(trade_log, key=_trade_pnl, reverse=True)
        top_n = min(3, len(sorted_trades))

        # Best trades
        for t in sorted_trades[:top_n]:
            side = "LONG" if t.get("side") == "1" else "SHORT"
            pnl_t = _trade_pnl(t)
            qty = t.get("quantity", "?")
            _box_line(f"  {typer.style('+', fg=C.POSITIVE)} {side:5s}  qty={qty}  pnl={_vc(pnl_t)}")

        # Worst trades
        for t in sorted_trades[-top_n:]:
            side = "LONG" if t.get("side") == "1" else "SHORT"
            pnl_t = _trade_pnl(t)
            qty = t.get("quantity", "?")
            if pnl_t < 0:
                _box_line(f"  {typer.style('-', fg=C.NEGATIVE)} {side:5s}  qty={qty}  pnl={_vc(pnl_t)}")

    _box_bot()
    typer.echo()


@app.command("list")
def list_runs():
    """List all backtest runs."""
    data = api_call("GET", "/api/backtest/runs")
    runs = data.get("runs", data) if isinstance(data, dict) else data

    if not runs:
        typer.echo("No backtest runs found.")
        return

    t = Table([
        ("ID", 8, "left"), ("Strategy", 16, "left"), ("Symbol", 14, "left"),
        ("Ivl", 4, "right"), ("Period", 23, "left"), ("Status", 10, "right"),
        ("Trades", 6, "right"), ("PnL", 10, "right"), ("Ret%", 8, "right"),
        ("Sharpe", 7, "right"), ("WinRate", 7, "right"),
    ])
    t.header()

    for r in runs:
        run_id = r.get("run_id", "")[:8]
        strat = r.get("strategy_name", "?")[:16]
        sym = r.get("symbol", "?")[:14]
        ivl = r.get("interval", "?")
        start_d = r.get("start_date", "")[:10]
        end_d = r.get("end_date", "")[:10]
        period = f"{start_d} ~ {end_d}"
        st = r.get("status", "?")

        summary = r.get("result_summary") or {}
        trades = summary.get("total_trades")
        pnl = summary.get("total_pnl")
        ret_pct = summary.get("total_return_pct")
        sharpe = summary.get("sharpe_ratio")
        win_rate = summary.get("win_rate")

        t.row([
            run_id, strat, sym, ivl, period,
            color_status(st),
            str(trades) if trades is not None else muted("-"),
            color_value(pnl) if pnl is not None else muted("-"),
            color_value(ret_pct) if ret_pct is not None else muted("-"),
            f"{sharpe:.2f}" if sharpe is not None else muted("-"),
            f"{win_rate * 100:.1f}%" if win_rate is not None else muted("-"),
        ])

    t.footer()


# ── Optimization commands ──────────────────────────────────────────────────

def _display_optimize_result(data: dict) -> None:
    """Display formatted optimization result."""
    typer.echo()
    divider(50)
    typer.echo(f"  {bold('Optimization Result')}")
    divider(50)

    kv("ID", str(data.get("optimization_id", "-")), 12)
    kv("Status", color_status(str(data.get("status", "?"))), 12)
    kv("Objective", str(data.get("fitness_objective", "-")), 12)
    if data.get("sampler"):
        kv("Sampler", str(data["sampler"]), 12)
    if data.get("total_pruned"):
        kv("Pruned", str(data["total_pruned"]), 12)

    best_value = data.get("best_value")
    if best_value is not None:
        header("Best Fitness")
        typer.echo(f"    {color_value(best_value, '.6f')}")

    best_params = data.get("best_params")
    if best_params:
        header("Best Parameters")
        for k, v in sorted(best_params.items()):
            kv(k, str(v))

    importances = data.get("param_importances")
    if importances:
        header("Parameter Importances")
        sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
        for k, v in sorted_imp:
            bar = typer.style("#" * int(v * 40), fg=C.ACCENT)
            kv(k, f"{v:.4f} {bar}")

    wf_results = data.get("walk_forward_results")
    if wf_results:
        header("Walk-Forward Folds")
        for i, fold in enumerate(wf_results):
            if isinstance(fold, dict):
                tv = fold.get("test_value")
                typer.echo(f"    Fold {i+1}: {fold.get('test_start','')} ~ {fold.get('test_end','')}  "
                           f"value={color_value(tv, '.4f') if tv is not None else muted('-')}")

    test_metrics = data.get("test_metrics")
    if test_metrics and isinstance(test_metrics, dict):
        header("Validation (Test Period)")
        for k, v in test_metrics.items():
            kv(k, str(v))

    typer.echo()


@app.command()
def optimize(
    strategy: str = typer.Argument(..., help="Strategy name"),
    symbol: List[str] = typer.Option(..., "--symbol", "-s", help="Instrument symbol(s)"),
    interval: List[str] = typer.Option(["5m"], "--interval", "-i", help="Bar interval(s)"),
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)"),
    trials: int = typer.Option(100, "--trials", "-n", help="Number of Optuna trials"),
    fitness: str = typer.Option("sharpe", "--fitness", "-f", help="Objective: sharpe/calmar/sortino/profit"),
    train_pct: float = typer.Option(85.0, "--train-pct", help="Train percentage (50-99)"),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel trial workers"),
    walk_forward: int = typer.Option(0, "--walk-forward", "--wf", help="Walk-forward folds (0=disabled)"),
    sampler: str = typer.Option("tpe", "--sampler", help="Sampler: tpe/cmaes/random"),
    patience: int = typer.Option(0, "--patience", help="Early stopping patience (0=disabled)"),
    no_pruning: bool = typer.Option(False, "--no-pruning", help="Disable trial pruning"),
    param: Optional[List[str]] = typer.Option(None, "--param", "-p", help="Param range as name:min:max[:step[:type]]"),
    initial_capital: float = typer.Option(10000, "--capital", "-c", help="Initial capital"),
    leverage: float = typer.Option(1, "--leverage", "-l", help="Leverage"),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Auto-poll progress until done"),
):
    """Run hyperparameter optimization."""
    param_ranges = _parse_param_ranges(param)

    payload: dict = {
        "strategy": strategy, "symbols": symbol, "intervals": interval,
        "start_date": start, "end_date": end, "n_trials": trials,
        "fitness_objective": fitness, "train_pct": train_pct,
        "initial_capital": initial_capital, "leverage": leverage,
        "n_workers": workers, "walk_forward_folds": walk_forward,
        "pruning": not no_pruning, "sampler": sampler, "patience": patience,
    }
    if param_ranges:
        payload["param_ranges"] = param_ranges

    data = api_call("POST", "/api/backtest/optimize", json=payload)
    opt_id = data.get("optimization_id")

    header("Optimization Started")
    kv("ID", accent(str(opt_id)), 12)
    kv("Trials", str(trials), 12)
    kv("Fitness", fitness, 12)
    kv("Sampler", sampler, 12)
    if walk_forward > 0:
        kv("Walk-Forward", f"{walk_forward} folds", 12)
    typer.echo()

    if not poll:
        return

    # Poll progress
    while True:
        time.sleep(3)
        sd = api_call("GET", f"/api/backtest/optimize/{opt_id}/status")
        s = sd.get("status", "unknown")
        done = sd.get("trials_completed", 0)
        total = sd.get("total_trials", trials)
        best = sd.get("best_value")
        pruned = sd.get("pruned_trials", 0)

        pct = int(done / total * 100) if total > 0 else 0
        bar_w = 30
        filled = int(bar_w * done / total) if total > 0 else 0
        bar = "=" * filled + "-" * (bar_w - filled)

        best_s = f"  best={color_value(best, '.4f')}" if best is not None else ""
        pruned_s = f"  pruned={pruned}" if pruned > 0 else ""
        typer.echo(f"\r  [{bar}] {done}/{total} ({pct}%){best_s}{pruned_s}", nl=False)

        if s in ("completed", "failed", "error"):
            typer.echo()
            if s != "completed":
                typer.echo(f"  {typer.style('FAILED', fg=C.NEGATIVE, bold=True)}")
                output(sd)
                raise typer.Exit(1)
            break

    result_data = api_call("GET", f"/api/backtest/optimize/{opt_id}/result")
    _display_optimize_result(result_data)


@app.command("optimize-status")
def optimize_status(opt_id: int = typer.Argument(..., help="Optimization run ID")):
    """Check optimization run status."""
    data = api_call("GET", f"/api/backtest/optimize/{opt_id}/status")
    if output_format() == "json":
        output(data)
        return
    st = data.get("status", "unknown")
    done = data.get("trials_completed", 0)
    total = data.get("total_trials", 0)
    best = data.get("best_value")
    typer.echo()
    typer.echo(f"  {status_badge(st)} Optimization {accent(str(opt_id))}  {color_status(st)}")
    typer.echo(f"      Trials: {done}/{total}  Best: {color_value(best, '.4f') if best is not None else muted('-')}")
    typer.echo()


@app.command("optimize-result")
def optimize_result(opt_id: int = typer.Argument(..., help="Optimization run ID")):
    """Get full optimization results."""
    data = api_call("GET", f"/api/backtest/optimize/{opt_id}/result")
    if output_format() == "json":
        output(data)
        return
    _display_optimize_result(data)


@app.command("optimize-list")
def optimize_list(
    limit: int = typer.Option(20, "--limit"),
    strategy: Optional[str] = typer.Option(None, "--strategy"),
):
    """List optimization runs."""
    params: dict = {"limit": limit}
    if strategy:
        params["strategy"] = strategy
    data = api_call("GET", "/api/backtest/optimize/runs", params=params)
    if output_format() == "json":
        output(data)
        return
    runs = data if isinstance(data, list) else data.get("runs", [])
    if not runs:
        typer.echo("No optimization runs found.")
        return

    t = Table([
        ("ID", 6, "right"), ("Strategy", 16, "left"), ("Symbol", 14, "left"),
        ("Trials", 8, "right"), ("Fitness", 8, "left"), ("Status", 10, "right"),
        ("Best", 10, "right"), ("Done", 6, "right"),
    ])
    t.header()
    for r in runs:
        t.row([
            str(r.get("optimization_id", "")),
            str(r.get("strategy_name", "?"))[:16],
            str(r.get("symbol", "?"))[:14],
            str(r.get("n_trials", "-")),
            str(r.get("fitness_objective", "-")),
            color_status(str(r.get("status", "?"))),
            color_value(r.get("best_value"), ".4f") if r.get("best_value") is not None else muted("-"),
            str(r.get("trials_completed", "-")),
        ])
    t.footer()
