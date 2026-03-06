"""Shared CLI styling utilities for consistent, ergonomic terminal output."""
from __future__ import annotations

from typing import Any

import typer


# ── Color Palette ──────────────────────────────────────────────────────────
# Semantic color mapping for consistent theming across all CLI commands.

class C:
    """Semantic color constants."""
    POSITIVE = typer.colors.GREEN
    NEGATIVE = typer.colors.RED
    NEUTRAL = typer.colors.WHITE
    ACCENT = typer.colors.CYAN
    WARN = typer.colors.YELLOW
    MUTED = typer.colors.BRIGHT_BLACK
    HIGHLIGHT = typer.colors.MAGENTA

    # Status-specific
    STATUS = {
        "completed": typer.colors.GREEN,
        "running": typer.colors.YELLOW,
        "queued": typer.colors.CYAN,
        "failed": typer.colors.RED,
        "cancelled": typer.colors.MAGENTA,
        "error": typer.colors.RED,
    }

    STATUS_ICON = {
        "completed": "+",
        "running": "~",
        "queued": ".",
        "failed": "x",
        "cancelled": "!",
        "error": "x",
    }


# ── Text Helpers ───────────────────────────────────────────────────────────

def bold(text: str) -> str:
    return typer.style(str(text), bold=True)


def dim(text: str) -> str:
    return typer.style(str(text), dim=True)


def accent(text: str) -> str:
    return typer.style(str(text), fg=C.ACCENT)


def muted(text: str) -> str:
    return typer.style(str(text), fg=C.MUTED)


def color_value(value: float | None, fmt: str = "+.2f") -> str:
    """Format and color a numeric value: green if positive, red if negative."""
    if value is None:
        return muted("-")
    s = f"{value:{fmt}}"
    if value > 0:
        return typer.style(s, fg=C.POSITIVE)
    elif value < 0:
        return typer.style(s, fg=C.NEGATIVE)
    return s


def color_status(status: str) -> str:
    """Color a status string."""
    fg = C.STATUS.get(status, C.NEUTRAL)
    return typer.style(status, fg=fg, bold=True)


def status_badge(status: str) -> str:
    """Render a colored [icon] badge for a status."""
    fg = C.STATUS.get(status, C.NEUTRAL)
    icon = C.STATUS_ICON.get(status, "?")
    return typer.style(f"[{icon}]", fg=fg)


# ── Layout Helpers ─────────────────────────────────────────────────────────

def header(title: str) -> None:
    """Print a section header with divider."""
    typer.echo()
    typer.echo(f"  {bold(title)}")


def divider(width: int = 70) -> None:
    typer.echo("  " + "-" * width)


def kv(label: str, value: str, label_width: int = 20) -> None:
    """Print a key-value pair with right-aligned label."""
    typer.echo(f"    {label:>{label_width}s}: {value}")


def kv_color(label: str, value: float | None, fmt: str = ".4f",
             label_width: int = 20, colorize: bool = False,
             pct: bool = False, pct_x100: bool = False) -> None:
    """Print a key-value pair with optional coloring and percentage formatting."""
    if value is None:
        kv(label, muted("-"), label_width)
        return

    if pct_x100:
        v_str = f"{value * 100:{fmt}}%"
    elif pct:
        v_str = f"{value:{fmt}}%"
    else:
        v_str = f"{value:{fmt}}"

    if colorize and isinstance(value, (int, float)):
        if value > 0:
            v_str = typer.style(v_str, fg=C.POSITIVE)
        elif value < 0:
            v_str = typer.style(v_str, fg=C.NEGATIVE)

    kv(label, v_str, label_width)


# ── Table Helpers ──────────────────────────────────────────────────────────

class Table:
    """Simple fixed-width table renderer that handles ANSI colors correctly.

    Usage:
        t = Table([("ID", 8), ("Name", 16), ("PnL", 10, "right")])
        t.header()
        t.row(["abc123", "my_strat", color_value(42.5)])
    """

    def __init__(self, columns: list[tuple[str, int] | tuple[str, int, str]]) -> None:
        self.columns: list[tuple[str, int, str]] = []
        for col in columns:
            if len(col) == 2:
                self.columns.append((col[0], col[1], "left"))
            else:
                self.columns.append((col[0], col[1], col[2]))

    def _pad(self, text: str, width: int, align: str) -> str:
        """Pad plain text to width, THEN apply any ANSI codes that are already in text."""
        # Strip ANSI to measure real length
        import re
        plain = re.sub(r'\033\[[0-9;]*m', '', text)
        padding = max(0, width - len(plain))
        if align == "right":
            return " " * padding + text
        return text + " " * padding

    def header(self) -> None:
        """Print bold header row and divider."""
        parts = [self._pad(c[0], c[1], c[2]) for c in self.columns]
        typer.echo()
        typer.echo("  " + bold("  ".join(parts)))
        total_w = sum(c[1] for c in self.columns) + (len(self.columns) - 1) * 2
        divider(total_w)

    def row(self, values: list[str]) -> None:
        """Print a data row. Values may contain ANSI color codes."""
        parts = []
        for i, val in enumerate(values):
            if i < len(self.columns):
                _, w, align = self.columns[i]
                parts.append(self._pad(str(val), w, align))
            else:
                parts.append(str(val))
        typer.echo("  " + "  ".join(parts))

    def footer(self) -> None:
        typer.echo()


# ── Card Helpers ───────────────────────────────────────────────────────────

def progress_bar(pct: int, width: int = 30) -> str:
    """Render a text progress bar."""
    filled = int(width * pct / 100) if pct else 0
    bar = typer.style("=" * filled, fg=C.POSITIVE) + dim("-" * (width - filled))
    return f"[{bar}] {pct}%"


def inline_progress(pct: int, status: str, elapsed: int, width: int = 20) -> str:
    """Render a single-line progress indicator for polling loops."""
    filled = int(width * pct / 100) if pct else 0
    bar = "=" * filled + "-" * (width - filled)
    sc = C.STATUS.get(status, C.NEUTRAL)
    return f"  [{bar}] {pct:>3d}%  {typer.style(status, fg=sc):>10s}  [{elapsed}s]"
