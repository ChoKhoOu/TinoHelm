"""Data management commands."""
from __future__ import annotations

import json
from typing import List

import typer

from tinohelm.cli._http import api_call, output_format
from tinohelm.cli._style import (
    C, Table, accent, bold, dim, muted,
    header, divider, kv, status_badge,
)

app = typer.Typer(no_args_is_help=True)


def _fmt_size(size_bytes: int) -> str:
    """Format byte count into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@app.command()
def fetch(
    symbol: str = typer.Argument(..., help="Instrument symbol (e.g. AAPL)"),
    interval: str = typer.Argument(..., help="Bar interval (e.g. 1m, 5m, 1h, 1d)"),
    start: str = typer.Argument(..., help="Start date (YYYY-MM-DD)"),
    end: str = typer.Argument(..., help="End date (YYYY-MM-DD)"),
):
    """Fetch historical market data."""
    data = api_call(
        "POST",
        "/api/data/fetch",
        json={"symbol": symbol, "interval": interval, "start": start, "end": end},
    )
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    st = data.get("status", "unknown")
    header("Data Fetch Submitted")
    divider()
    kv("Symbol", accent(data.get("symbol", symbol)), 12)
    kv("Interval", data.get("interval", interval), 12)
    kv("Period", f"{data.get('start', start)} ~ {data.get('end', end)}", 12)
    kv("Status", status_badge(st) + "  " + bold(st), 12)
    typer.echo()
    if data.get("message"):
        typer.echo(f"    {dim(data['message'])}")
        typer.echo()


@app.command("fetch-batch")
def fetch_batch(
    symbols: List[str] = typer.Argument(..., help="Instrument symbols (e.g. BTCUSDT-PERP ETHUSDT-PERP)"),
    interval: str = typer.Option(..., "-i", "--interval", help="Bar interval (e.g. 1m, 5m, 1h, 1d)"),
    start: str = typer.Option(..., "-s", "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "-e", "--end", help="End date (YYYY-MM-DD)"),
):
    """Fetch historical market data for multiple symbols in parallel."""
    data = api_call(
        "POST",
        "/api/data/fetch-batch",
        json={"symbols": symbols, "interval": interval, "start": start, "end": end},
    )
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    st = data.get("status", "unknown")
    header("Batch Data Fetch Submitted")
    divider()
    kv("Symbols", accent(", ".join(data.get("symbols", symbols))), 12)
    kv("Interval", data.get("interval", interval), 12)
    kv("Period", f"{data.get('start', start)} ~ {data.get('end', end)}", 12)
    kv("Queued", bold(str(data.get("count", len(symbols)))), 12)
    kv("Status", status_badge(st) + "  " + bold(st), 12)
    typer.echo()
    if data.get("message"):
        typer.echo(f"    {dim(data['message'])}")
        typer.echo()


@app.command()
def catalog():
    """Show available data catalog."""
    data = api_call("GET", "/api/data/catalog")
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    items = data if isinstance(data, list) else data.get("items", [])

    if not items:
        typer.echo()
        typer.echo(f"  {muted('No data in catalog.')}")
        typer.echo()
        return

    t = Table([
        ("Symbol", 18, "left"),
        ("Interval", 10, "left"),
        ("Start", 12, "left"),
        ("End", 12, "left"),
        ("Size", 10, "right"),
    ])
    t.header()

    for item in items:
        t.row([
            accent(str(item.get("symbol", "?"))),
            str(item.get("interval", "?")),
            str(item.get("start_date", "-"))[:10],
            str(item.get("end_date", "-"))[:10],
            _fmt_size(item.get("size_bytes", 0)),
        ])

    t.footer()
    typer.echo(f"    {muted(f'{len(items)} dataset(s)')}")
    typer.echo()


@app.command()
def compact(
    symbol: str = typer.Argument(..., help="Instrument symbol (e.g. AAPL)"),
    interval: str = typer.Argument(..., help="Bar interval (e.g. 1m, 5m, 1h, 1d)"),
):
    """Compact stored data for a symbol/interval."""
    data = api_call(
        "POST",
        "/api/data/compact",
        json={"symbol": symbol, "interval": interval},
    )
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    st = data.get("status", "unknown")
    badge = status_badge("completed") if st == "accepted" else status_badge(st)

    header(f"Compact: {symbol} {interval}")
    divider()
    kv("Symbol", accent(symbol), 12)
    kv("Interval", interval, 12)
    kv("Status", badge + "  " + bold(st), 12)
    if data.get("message"):
        typer.echo(f"    {dim(data['message'])}")
    typer.echo()


@app.command()
def validate(
    symbol: str = typer.Argument(..., help="Instrument symbol (e.g. AAPL)"),
    interval: str = typer.Argument(..., help="Bar interval (e.g. 1m, 5m, 1h, 1d)"),
):
    """Validate data integrity for a symbol/interval."""
    data = api_call("GET", f"/api/data/validate/{symbol}/{interval}")
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    header(f"Validate: {symbol} {interval}")
    divider()

    if isinstance(data, dict):
        issues = data.get("issues", [])
        st = data.get("status", "unknown")

        if st == "ok" and not issues:
            typer.echo(f"    {typer.style('[+]', fg=C.POSITIVE, bold=True)}  {typer.style('Data is valid', fg=C.POSITIVE)}")
        else:
            kv("Status", status_badge(st if st != "ok" else "completed") + "  " + bold(st), 12)

        # Show extra fields (bars_count, gaps, etc.)
        for key in ("bars_count", "start_date", "end_date", "gaps", "duplicates"):
            if key in data:
                kv(key.replace("_", " ").title(), str(data[key]), 12)

        if issues:
            typer.echo()
            typer.echo(f"    {typer.style('Issues:', fg=C.NEGATIVE, bold=True)}")
            for issue in issues:
                typer.echo(f"      {typer.style('-', fg=C.NEGATIVE)} {issue}")
    typer.echo()
