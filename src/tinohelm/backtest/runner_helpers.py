"""Pure helpers for :class:`tinohelm.backtest.runner.BacktestRunner`.

Everything in this module is NautilusTrader-free and side-effect-free —
arithmetic, string building, dict shaping, and list sorting.  By living
outside ``runner.py`` these helpers can be unit-tested without the NT
wheel installed (which is valuable in constrained CI environments).

Keep this module pure:  no I/O, no logging, no NT imports.
"""
from __future__ import annotations

import re as _re
from datetime import datetime, timedelta, timezone as _tz
from typing import Any, Iterable


# Timeframes ordered from lowest to highest for composite source resolution.
# This is the canonical priority list; ``BacktestRunner`` re-exports it as
# ``_TIMEFRAME_PRIORITY`` for backward compatibility.
TIMEFRAME_PRIORITY: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d",
)


def interval_to_minutes(interval: str) -> int:
    """Convert an interval like ``"5m"``, ``"1h"``, ``"1d"`` to minutes.

    Sub-minute intervals (``"30s"``) are rounded up to 1 minute.
    Unknown strings return 0 (the caller decides whether that is fatal).
    """
    m = _re.match(r"^(\d+)([smhd])$", interval.lower())
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2)
    if unit == "s":
        return max(1, n // 60)
    if unit == "m":
        return n
    if unit == "h":
        return n * 60
    if unit == "d":
        return n * 1440
    return 0


def compute_warmup_adjusted_start(
    start: datetime | None,
    interval: str,
    warmup_bars: int | None,
) -> datetime | None:
    """Extend ``start`` backwards by ``warmup_bars * interval`` minutes.

    Returns the original ``start`` unchanged when any input can't be
    applied (missing start, empty/invalid interval, zero/None warmup).
    """
    if start is None or not interval:
        return start
    if not warmup_bars or warmup_bars <= 0:
        return start
    mins = interval_to_minutes(interval)
    if mins <= 0:
        return start
    return start - timedelta(minutes=mins * warmup_bars)


def resolve_symbols_intervals(
    bundle_symbols: list[str] | None,
    bundle_interval: str | None,
    current_symbols: list[str],
    current_intervals: list[str],
) -> tuple[list[str], list[str]]:
    """Choose the effective ``(symbols, intervals)`` for the run.

    Priority: runner-level lists win when non-empty; otherwise fall back
    to the bundle's configuration.  The bundle stores ``interval`` as a
    single string (not a list), so wrap it when used as fallback.
    """
    final_symbols: list[str] = (
        list(current_symbols) if current_symbols
        else list(bundle_symbols or [])
    )
    if current_intervals:
        final_intervals: list[str] = list(current_intervals)
    elif bundle_interval:
        final_intervals = [bundle_interval]
    else:
        final_intervals = []
    return final_symbols, final_intervals


def candidate_source_intervals(
    target_interval: str,
    priority: tuple[str, ...] = TIMEFRAME_PRIORITY,
) -> list[str]:
    """Return the timeframes strictly below ``target_interval``.

    Used when the target interval is missing from the catalog and the
    runner wants to build a composite aggregator from a lower timeframe.
    Returns them in priority order (lowest first) so the caller can pick
    the first that has data on disk.
    """
    try:
        idx = priority.index(target_interval)
    except ValueError:
        return []
    return list(priority[:idx])


def build_composite_bar_type_str(
    nt_symbol: str,
    source_interval: str,
    target_interval: str,
    interval_map: dict[str, str],
) -> str:
    """Build an NT composite bar-type string.

    Shape:  ``{nt_symbol}-{target}-LAST-INTERNAL@{source}-EXTERNAL``.
    Unknown intervals fall back to ``"1-MINUTE"`` (matches the original
    inline fallback in :meth:`BacktestRunner._resolve_bars`).
    """
    source_part = interval_map.get(source_interval, "1-MINUTE")
    target_part = interval_map.get(target_interval, "1-MINUTE")
    return f"{nt_symbol}-{target_part}-LAST-INTERNAL@{source_part}-EXTERNAL"


def extract_benchmark_daily_closes(
    bars: Iterable[tuple[int, float]],
) -> dict[str, float]:
    """Convert ``(ts_init_ns, close)`` tuples to ``{YYYY-MM-DD: close}``.

    The last bar for each UTC day wins (matching the original inline
    iteration, which unconditionally overwrote ``daily_closes[day_key]``).
    Empty input yields an empty dict.
    """
    out: dict[str, float] = {}
    for ts_ns, close in bars:
        day_key = datetime.fromtimestamp(
            int(ts_ns) / 1e9, tz=_tz.utc,
        ).strftime("%Y-%m-%d")
        out[day_key] = float(close)
    return out


def compute_bar_progress_fields(
    bar_count: int,
    total_bars: int,
    elapsed_secs: float,
) -> dict[str, Any]:
    """Compute the progress fields emitted by ``_ProgressReporter.on_bar``.

    Returns ``{pct, elapsed_secs, eta_secs, bars_per_sec}`` where:

    * ``pct``  — bar phase maps to 10–90 (0–10 is setup, 90–100 is post).
    * ``eta_secs`` — ``None`` when ``pct == 0`` (avoids div-by-zero).
    * ``bars_per_sec`` — ``None`` when ``elapsed_secs <= 0``.
    * ``elapsed_secs`` — always rounded to 1 decimal.

    ``total_bars <= 0`` short-circuits to ``pct=10`` (setup floor) with
    the other fields unfilled.
    """
    elapsed_rounded = round(max(elapsed_secs, 0.0), 1)
    if total_bars <= 0:
        return {
            "pct": 10,
            "elapsed_secs": elapsed_rounded,
            "eta_secs": None,
            "bars_per_sec": None,
        }
    pct = min(int(bar_count / total_bars * 80) + 10, 90)
    eta = round(elapsed_rounded * (100 - pct) / pct, 1) if pct > 0 else None
    bps = round(bar_count / elapsed_rounded, 1) if elapsed_rounded > 0 else None
    return {
        "pct": pct,
        "elapsed_secs": elapsed_rounded,
        "eta_secs": eta,
        "bars_per_sec": bps,
    }


def build_progress_payload(
    run_id: str,
    *,
    pct: int,
    elapsed_secs: float | None,
    eta_secs: float | None = None,
    total_bars: int | None = None,
    processed_bars: int | None = None,
    bars_per_sec: float | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build the canonical ``backtest.progress`` event payload.

    Ensures both the bar-phase reporter and the setup-phase reporter
    emit identically shaped events (every key always present), which the
    frontend ``NotificationListener`` and TUI rely on.
    """
    return {
        "type": "backtest.progress",
        "run_id": run_id,
        "pct": pct,
        "elapsed_secs": elapsed_secs,
        "eta_secs": eta_secs,
        "total_bars": total_bars,
        "processed_bars": processed_bars,
        "bars_per_sec": bars_per_sec,
        "trades": None,
        "message": message,
    }


def assemble_funding_events(
    rates_by_symbol: dict[str, list[dict]],
    nt_symbols_by_symbol: dict[str, str],
    interval_minutes_by_symbol: dict[str, int],
) -> list[dict]:
    """Flatten per-symbol funding-rate lists into a sorted event stream.

    Each raw rate dict must carry ``funding_time_ms``, ``funding_rate``,
    and ``mark_price``.  Events with missing or zero ``mark_price`` are
    dropped (notional can't be computed without it).  Output is sorted
    by ``timestamp_ns`` ascending, ready for the FundingCostTracker
    actor to iterate in time order.
    """
    out: list[dict] = []
    for sym, rates in rates_by_symbol.items():
        nt_sym = nt_symbols_by_symbol.get(sym, sym)
        mins = interval_minutes_by_symbol.get(sym, 8 * 60)
        for r in rates:
            mark_price = r.get("mark_price")
            if not mark_price:
                continue
            ts_ms = r["funding_time_ms"]
            ts_iso = datetime.fromtimestamp(
                ts_ms / 1000, tz=_tz.utc,
            ).isoformat()
            out.append({
                "timestamp_ns": ts_ms * 1_000_000,
                "timestamp_iso": ts_iso,
                "symbol": nt_sym,
                "rate": r["funding_rate"],
                "mark_price": mark_price,
                "funding_interval_minutes": mins,
            })
    out.sort(key=lambda e: e["timestamp_ns"])
    return out
