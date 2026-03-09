"""TinoHelm CLI — tino command."""
from __future__ import annotations

import typer

from tinohelm.cli.strategy import app as strategy_app
from tinohelm.cli.data import app as data_app
from tinohelm.cli.backtest import app as backtest_app
from tinohelm.cli.node import app as node_app

app = typer.Typer(
    name="tino",
    help="TinoHelm — Quantitative trading platform powered by NautilusTrader.",
    no_args_is_help=True,
)

# Global options
state = {"api_url": "http://localhost:8000", "format": "text"}


@app.callback()
def main(
    api_url: str = typer.Option("http://localhost:8000", "--api-url", help="API server URL"),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
):
    """TinoHelm CLI."""
    state["api_url"] = api_url.rstrip("/")
    state["format"] = fmt

    typer.echo(
        typer.style(
            "Hint: a faster native CLI is available — "
            "build from cli/ with `cargo build --release` or download from Releases.",
            fg=typer.colors.YELLOW,
            dim=True,
        ),
        err=True,
    )


app.add_typer(strategy_app, name="strategy", help="Strategy management")
app.add_typer(data_app, name="data", help="Data management")
app.add_typer(backtest_app, name="backtest", help="Backtest management")
app.add_typer(node_app, name="sandbox", help="Sandbox node control")
app.add_typer(node_app, name="live", help="Live node control")
app.add_typer(node_app, name="node", help="Unified node management")


@app.command()
def version():
    """Show TinoHelm version."""
    from tinohelm import __version__
    from tinohelm.cli._style import bold, accent, muted
    typer.echo(f"  {bold(accent('TinoHelm'))} v{__version__}")
    typer.echo(f"  {muted('Quantitative trading platform powered by NautilusTrader')}")


if __name__ == "__main__":
    app()
