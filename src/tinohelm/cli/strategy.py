"""Strategy management commands."""
from __future__ import annotations

import json

import typer

from tinohelm.cli._http import api_call, api_url, output, output_format
from tinohelm.cli._style import (
    C,
    Table,
    accent,
    bold,
    dim,
    divider,
    header,
    kv,
    muted,
    status_badge,
)

app = typer.Typer(no_args_is_help=True)


# ── Commands ──────────────────────────────────────────────────────────────


@app.command()
def create(
    name: str = typer.Argument(..., help="Strategy name"),
    type: str = typer.Option("bar", "--type", "-t", help="Strategy type: bar or tick"),
):
    """Create a new strategy scaffold."""
    data = api_call("POST", "/api/strategies/create", json={"name": name, "type": type})
    if output_format() == "json":
        output(data)
        return

    header("Strategy Created")
    divider()
    kv("Name", accent(data.get("name", name)), 12)
    kv("Type", type, 12)
    kv("File", dim(data.get("file_path", "-")), 12)
    typer.echo()
    typer.echo(f"    {muted(data.get('message', ''))}")
    typer.echo()


@app.command("list")
def list_strategies():
    """List all strategies."""
    data = api_call("GET", "/api/strategies")
    if output_format() == "json":
        output(data)
        return

    strategies = data if isinstance(data, list) else []
    if not strategies:
        typer.echo("  No strategies found.")
        return

    t = Table([
        ("Name", 20, "left"),
        ("Type", 10, "left"),
        ("Class", 20, "left"),
        ("Symbols", 8, "right"),
        ("Updated", 19, "left"),
    ])
    t.header()

    for s in strategies:
        name = s.get("name", "?")
        stype = s.get("type", "single")
        strat_cls = s.get("strategy_class", "-")
        updated = s.get("updated_at", "")
        if updated:
            updated = updated[:19].replace("T", " ")

        # Symbol count for portfolios
        symbols = s.get("symbols", [])
        sym_count = str(len(symbols)) if symbols else muted("1")

        type_display = accent(stype) if stype == "portfolio" else muted(stype)

        t.row([
            accent(name),
            type_display,
            strat_cls[:20],
            sym_count,
            muted(updated) if updated else muted("-"),
        ])

    t.footer()
    typer.echo(f"    {muted(f'{len(strategies)} strategies')}")
    typer.echo()


@app.command()
def validate(
    name: str = typer.Argument(..., help="Strategy name"),
):
    """Validate a strategy."""
    import httpx

    # Validate endpoint returns 422 for invalid strategies with the result
    # in the detail field. We make a raw call to capture both cases.
    url = f"{api_url()}/api/strategies/{name}/validate"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url)
    except httpx.ConnectError:
        typer.echo(f"Error: Cannot connect to API at {api_url()}", err=True)
        typer.echo("Is the server running? Try: docker compose up -d", err=True)
        raise typer.Exit(1)

    try:
        result = resp.json()
    except Exception:
        typer.echo(f"Error {resp.status_code}: {resp.text}", err=True)
        raise typer.Exit(1)

    # 422 returns {"detail": {...}} with the validation result inside
    if resp.status_code == 422 and isinstance(result.get("detail"), dict):
        result = result["detail"]
    elif resp.status_code not in (200, 422):
        detail = result.get("detail", resp.text) if isinstance(result, dict) else resp.text
        typer.echo(f"Error {resp.status_code}: {detail}", err=True)
        raise typer.Exit(1)

    if output_format() == "json":
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    valid = result.get("valid", False)
    issues = result.get("issues", [])

    header(f"Validation: {name}")
    divider()

    if valid:
        badge = status_badge("completed")
        typer.echo(f"    {badge} {typer.style('VALID', fg=C.POSITIVE, bold=True)}")
    else:
        badge = status_badge("failed")
        typer.echo(f"    {badge} {typer.style('INVALID', fg=C.NEGATIVE, bold=True)}")

    if issues:
        typer.echo()
        for issue in issues:
            bullet = typer.style("*", fg=C.NEGATIVE)
            typer.echo(f"    {bullet} {issue}")

    # Show extra info if present
    for key in ("strategy_class", "config_class"):
        val = result.get(key)
        if val:
            kv(key.replace("_", " ").title(), val, 16)

    typer.echo()


@app.command()
def info(
    name: str = typer.Argument(..., help="Strategy name"),
):
    """Show strategy details."""
    data = api_call("GET", f"/api/strategies/{name}")
    if output_format() == "json":
        output(data)
        return

    stype = data.get("type", "single")
    header(f"Strategy: {accent(data.get('name', name))}")
    divider()
    kv("ID", str(data.get("id", "-")), 16)
    kv("Name", bold(data.get("name", "-")), 16)
    kv("Type", accent(stype) if stype == "portfolio" else muted(stype), 16)
    kv("Strategy Class", data.get("strategy_class", "-"), 16)
    kv("Config Class", muted(data.get("config_class", "-")), 16)
    kv("File", dim(data.get("file_path", "-")), 16)

    created = data.get("created_at", "")
    updated = data.get("updated_at", "")
    if created:
        kv("Created", muted(created[:19].replace("T", " ")), 16)
    if updated:
        kv("Updated", muted(updated[:19].replace("T", " ")), 16)

    # Portfolio details
    symbols = data.get("symbols", [])
    actors = data.get("actors", [])
    interval = data.get("interval", "")
    if stype == "portfolio":
        typer.echo()
        typer.echo(f"    {bold('Portfolio Details')}")
        divider(40)
        kv("Interval", interval or muted("-"), 16)
        kv("Symbols", str(len(symbols)), 16)
        for sym in symbols:
            typer.echo(f"      {accent(sym)}")
        if actors:
            kv("Actors", str(len(actors)), 16)
            for actor_name in actors:
                typer.echo(f"      {accent(actor_name)}")

    # Version history
    versions = data.get("versions", [])
    if versions:
        typer.echo()
        typer.echo(f"    {bold('Version History')}")
        divider(40)
        for v in versions:
            ver = v.get("version", "?")
            code_hash = v.get("code_hash", "-")[:12]
            v_created = v.get("created_at", "")
            ts = muted(v_created[:19].replace("T", " ")) if v_created else muted("-")
            typer.echo(f"      v{ver}  {muted(code_hash)}  {ts}")

    typer.echo()


@app.command()
def rescan():
    """Re-scan strategies directory and register new/updated strategies."""
    data = api_call("POST", "/api/strategies/rescan")
    if output_format() == "json":
        output(data)
        return

    discovered = data.get("discovered", 0)
    names = data.get("strategies", [])

    header("Strategy Rescan")
    divider()
    kv("Discovered", accent(str(discovered)), 14)

    if names:
        typer.echo()
        for name in names:
            bullet = typer.style("+", fg=C.POSITIVE)
            typer.echo(f"    {bullet} {name}")

    typer.echo()
