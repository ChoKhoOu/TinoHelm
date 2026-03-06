"""Sandbox / Live node control commands."""
from __future__ import annotations

import json
from typing import List

import typer

from tinohelm.cli._http import api_call, output, output_format
from tinohelm.cli._style import (
    C, Table, bold, dim, accent, muted,
    color_status, header, divider, kv, kv_color,
)

app = typer.Typer(no_args_is_help=True)

# ── Node-state color mapping ─────────────────────────────────────────────
_NODE_STATUS = {
    "running": C.POSITIVE,
    "starting": C.WARN,
    "stopped": C.NEGATIVE,
    "error": C.NEGATIVE,
}

_NODE_ICON = {
    "running": "+",
    "starting": "~",
    "stopped": "x",
    "error": "!",
}


def _color_node_status(st: str) -> str:
    fg = _NODE_STATUS.get(st, C.NEUTRAL)
    return typer.style(st, fg=fg, bold=True)


def _node_badge(st: str) -> str:
    fg = _NODE_STATUS.get(st, C.NEUTRAL)
    icon = _NODE_ICON.get(st, "?")
    return typer.style(f"[{icon}]", fg=fg)


def _mode_label(mode: str) -> str:
    """Return a styled mode label, with WARN color for live."""
    if mode == "live":
        return typer.style("LIVE", fg=C.WARN, bold=True)
    return typer.style("SANDBOX", fg=C.ACCENT, bold=True)


def _resolve_mode(ctx: typer.Context) -> str | None:
    """Determine node mode from the parent typer command name.

    Returns None when invoked via ``tino node`` (unified view).
    """
    info = ctx.parent
    if info and info.info_name in ("sandbox", "live"):
        return info.info_name
    if info and info.info_name == "node":
        return None
    return "sandbox"


# ── Commands ──────────────────────────────────────────────────────────────

@app.command()
def start(
    ctx: typer.Context,
    strategy: List[str] = typer.Option(..., "--strategy", "-s", help="Strategy name(s) to run"),
):
    """Start a trading node."""
    mode = _resolve_mode(ctx)
    data = api_call(
        "POST",
        "/api/node/start",
        json={"mode": mode, "strategies": strategy},
    )
    if output_format() == "json":
        output(data)
        return

    header(f"Node Starting  {_mode_label(mode)}")
    divider()
    kv("Mode", _mode_label(mode), 14)
    kv("Status", _color_node_status("starting"), 14)
    typer.echo()
    typer.echo(f"    {bold('Strategies:')}")
    for s in strategy:
        typer.echo(f"      {typer.style('-', fg=C.ACCENT)} {s}")
    typer.echo()


@app.command()
def stop(ctx: typer.Context):
    """Gracefully stop the trading node."""
    mode = _resolve_mode(ctx)
    data = api_call("POST", "/api/node/stop", json={"mode": mode})
    if output_format() == "json":
        output(data)
        return

    header(f"Node Stopping  {_mode_label(mode)}")
    divider()
    kv("Mode", _mode_label(mode), 14)
    kv("Status", typer.style("graceful stop", fg=C.WARN, bold=True), 14)
    typer.echo()


@app.command()
def kill(
    ctx: typer.Context,
    level: int = typer.Option(3, "--level", "-l", help="Kill escalation level (1-5)"),
):
    """Force-kill the trading node."""
    mode = _resolve_mode(ctx)
    data = api_call("POST", "/api/node/kill", json={"mode": mode, "level": level})
    if output_format() == "json":
        output(data)
        return

    level_color = C.WARN if level < 3 else C.NEGATIVE
    header(f"Kill Switch  {_mode_label(mode)}")
    divider()
    kv("Mode", _mode_label(mode), 14)
    kv("Level", typer.style(str(level), fg=level_color, bold=True), 14)
    typer.echo()


@app.command()
def status(ctx: typer.Context):
    """Show trading node status."""
    mode = _resolve_mode(ctx)
    if mode is None:
        data = api_call("GET", "/api/node/status")
    else:
        data = api_call("GET", "/api/node/status", params={"mode": mode})

    if output_format() == "json":
        output(data)
        return

    nodes = data.get("nodes", {})
    risk = data.get("risk_metrics", {})
    workers = data.get("backtest_workers", [])

    if mode is not None:
        # Single node view
        _render_node_card(mode, nodes.get(mode, {}), risk)
    else:
        # Unified view -- table of all nodes
        _render_nodes_table(nodes, risk, workers)


def _render_node_card(mode: str, info: dict, risk: dict) -> None:
    """Render a detailed status card for a single node."""
    st = info.get("status", "stopped")
    pid = info.get("pid")
    restarts = info.get("restart_count", 0)
    strategies = info.get("strategies", [])
    heartbeat = info.get("heartbeat")

    header(f"{_node_badge(st)} Node Status  {_mode_label(mode)}")
    divider()
    kv("Mode", _mode_label(mode), 14)
    kv("State", _color_node_status(st), 14)
    if pid:
        kv("PID", str(pid), 14)
    if restarts > 0:
        kv("Restarts", typer.style(str(restarts), fg=C.WARN), 14)

    # Heartbeat info
    if heartbeat and isinstance(heartbeat, dict):
        uptime = heartbeat.get("uptime")
        if uptime:
            kv("Uptime", str(uptime), 14)

    # Strategies sub-list
    if strategies:
        typer.echo()
        typer.echo(f"    {bold('Strategies:')}")
        for s in strategies:
            typer.echo(f"      {typer.style('-', fg=C.ACCENT)} {s}")

    # Risk metrics (only if node is running)
    if st == "running" and risk:
        typer.echo()
        typer.echo(f"    {bold('Risk Metrics:')}")
        exposure = risk.get("total_exposure", 0)
        margin = risk.get("margin_used_pct", 0)
        leverage = risk.get("leverage", 0)
        daily_var = risk.get("daily_var", 0)

        kv("Exposure", f"{exposure:,.2f} USDT", 14)
        margin_color = C.POSITIVE if margin < 50 else (C.WARN if margin < 80 else C.NEGATIVE)
        kv("Margin Used", typer.style(f"{margin:.2f}%", fg=margin_color), 14)
        kv("Leverage", f"{leverage:.4f}x", 14)
        kv("Daily VaR", f"{daily_var:,.2f} USDT", 14)

    typer.echo()


def _render_nodes_table(nodes: dict, risk: dict, workers: list) -> None:
    """Render a table overview of all nodes."""
    header("Node Status (all)")

    t = Table([
        ("Mode", 10, "left"),
        ("State", 10, "left"),
        ("PID", 8, "right"),
        ("Restarts", 8, "right"),
        ("Strategies", 24, "left"),
    ])
    t.header()

    for node_type in sorted(nodes.keys()):
        info = nodes[node_type]
        st = info.get("status", "stopped")
        pid = info.get("pid")
        restarts = info.get("restart_count", 0)
        strategies = info.get("strategies", [])

        strats_str = ", ".join(strategies) if strategies else muted("none")
        restart_str = typer.style(str(restarts), fg=C.WARN) if restarts > 0 else str(restarts)

        t.row([
            _mode_label(node_type),
            _color_node_status(st),
            str(pid) if pid else muted("-"),
            restart_str,
            strats_str[:24],
        ])

    t.footer()

    # Backtest workers summary
    if workers:
        alive_count = sum(1 for w in workers if w.get("alive"))
        total_count = len(workers)
        typer.echo(f"    Backtest workers: {bold(str(alive_count))}/{total_count} alive")

    # Risk metrics summary
    if risk:
        exposure = risk.get("total_exposure", 0)
        margin = risk.get("margin_used_pct", 0)
        if exposure > 0:
            margin_color = C.POSITIVE if margin < 50 else (C.WARN if margin < 80 else C.NEGATIVE)
            typer.echo(
                f"    Risk: exposure={bold(f'{exposure:,.2f}')} USDT  "
                f"margin={typer.style(f'{margin:.2f}%', fg=margin_color)}  "
                f"leverage={risk.get('leverage', 0):.4f}x"
            )

    typer.echo()
