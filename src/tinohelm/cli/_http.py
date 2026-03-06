"""Shared HTTP client for CLI commands."""
from __future__ import annotations

import json
import sys

import httpx
import typer

from tinohelm.cli._style import C, bold, muted, header, divider, kv


def api_url() -> str:
    from tinohelm.cli.main import state
    return state["api_url"]


def output_format() -> str:
    from tinohelm.cli.main import state
    return state["format"]


def api_call(method: str, path: str, **kwargs) -> dict:
    """Make an API call and return JSON response."""
    url = f"{api_url()}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        prefix = typer.style("Connection Error", fg=C.NEGATIVE, bold=True)
        typer.echo(f"  {prefix}  Cannot connect to API at {api_url()}", err=True)
        typer.echo(f"  {muted('Hint: is the server running? Try: docker compose up -d')}", err=True)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text
        code = typer.style(str(e.response.status_code), fg=C.NEGATIVE, bold=True)
        typer.echo(f"  {code}  {detail}", err=True)
        raise typer.Exit(1)


def output(data, title: str | None = None):
    """Output data in the configured format."""
    if output_format() == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        if title:
            header(title)
            divider()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    for k_name, v in item.items():
                        kv(k_name, str(v))
                    typer.echo()
                else:
                    typer.echo(f"    {item}")
        elif isinstance(data, dict):
            for k_name, v in data.items():
                kv(k_name, str(v))
        else:
            typer.echo(str(data))
