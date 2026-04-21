"""Shared pure helpers for Binance REST API pagination, retry, and throttle.

Consumed by both :mod:`tinohelm.data.providers.binance` (the main REST fallback
used when Vision archives are still in the T+1~T+3 gap) and
:mod:`tinohelm.data.downloader` (the Vision archive downloader). All
synchronous helpers are pure functions with no httpx or network dependency
and are fully unit-testable from standalone fixtures; the one async helper
(:func:`request_with_retry`) is a thin wrapper over ``httpx.AsyncClient.get``
that encapsulates the shared retry policy.

Historical note
---------------
Before this module existed, the same "classify status → exponential backoff /
fixed retry / abort / propagate" policy was implemented inline in four places
(three pagination loops in ``providers/binance.py`` + one download loop in
``data/downloader.py``). Any change to the policy had to be kept in sync
across four sites, and the binance loops additionally caught a bare
``except (httpx.RequestError, Exception)`` which swallowed ``JSONDecodeError``
and retried five times on malformed JSON. This module centralises the policy,
narrows the transport-error catch to ``httpx.RequestError`` only, and makes
the retry math testable in isolation.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal, Mapping

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / throttle constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 5
"""Max retry attempts before re-raising the underlying error."""

MAX_BACKOFF_SECONDS: int = 60
"""Cap on exponential backoff sleep for 429/418 responses."""

SERVER_ERROR_SLEEP_SECONDS: float = 2.0
"""Fixed sleep between 5xx retries."""

REQUEST_ERROR_SLEEP_SECONDS: float = 2.0
"""Fixed sleep between transport-error (``httpx.RequestError``) retries."""

WEIGHT_HIGH_THRESHOLD: int = 1800
"""Binance weight header value above which we throttle hard (``> 75%`` of 2400 limit)."""

WEIGHT_MEDIUM_THRESHOLD: int = 1200
"""Binance weight header value above which we throttle lightly (``> 50%`` of 2400 limit)."""

WEIGHT_HIGH_SLEEP: float = 5.0
"""Sleep seconds when used weight exceeds ``WEIGHT_HIGH_THRESHOLD``."""

WEIGHT_MEDIUM_SLEEP: float = 1.0
"""Sleep seconds when used weight is between ``MEDIUM`` and ``HIGH`` thresholds."""

StatusKind = Literal["success", "rate_limit", "server_error", "not_found", "abort"]

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def classify_http_status(status: int) -> StatusKind:
    """Classify an HTTP status code into a retry-policy kind.

    Returns
    -------
    ``"success"``
        ``200-299``. Caller proceeds with the response body.
    ``"not_found"``
        ``404`` exclusively. Caller decides whether to raise or skip.
    ``"rate_limit"``
        ``429`` (Too Many Requests) or ``418`` (IP banned by Binance).
        Callers should use :func:`backoff_seconds` for sleep duration.
    ``"server_error"``
        ``500+``. Callers should apply a short fixed sleep and retry.
    ``"abort"``
        Everything else (3xx redirects, other 4xx). Callers should propagate.
    """
    if 200 <= status < 300:
        return "success"
    if status == 404:
        return "not_found"
    if status in (429, 418):
        return "rate_limit"
    if status >= 500:
        return "server_error"
    return "abort"


def backoff_seconds(attempt: int, *, max_seconds: int = MAX_BACKOFF_SECONDS) -> int:
    """Exponential backoff sleep duration, capped at ``max_seconds``.

    attempt=1 → 2s, attempt=2 → 4s, attempt=3 → 8s, attempt=4 → 16s,
    attempt=5 → 32s, attempt=6+ → ``max_seconds`` (cap). ``attempt < 1``
    returns ``0`` (no sleep).
    """
    if attempt < 1:
        return 0
    return min(2 ** attempt, max_seconds)


def parse_used_weight_header(headers: Mapping[str, Any]) -> int:
    """Parse Binance's ``X-MBX-USED-WEIGHT-1M`` header safely.

    Returns ``0`` on missing, empty, or non-numeric values. Negative values
    (malformed responses) are returned as-is — callers treat them as "zero
    load" via the ``throttle_seconds`` contract.
    """
    raw = headers.get("X-MBX-USED-WEIGHT-1M", "0")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def throttle_seconds(
    used_weight: int,
    *,
    low_sleep: float,
    medium_sleep: float = WEIGHT_MEDIUM_SLEEP,
    high_sleep: float = WEIGHT_HIGH_SLEEP,
    medium_threshold: int = WEIGHT_MEDIUM_THRESHOLD,
    high_threshold: int = WEIGHT_HIGH_THRESHOLD,
) -> float:
    """Return adaptive per-request throttle sleep based on Binance load.

    Three tiers (strictly greater-than comparisons match legacy behaviour):

    - ``used_weight > high_threshold`` → ``high_sleep``
    - ``used_weight > medium_threshold`` → ``medium_sleep``
    - otherwise → ``low_sleep`` (endpoint-specific baseline)
    """
    if used_weight > high_threshold:
        return high_sleep
    if used_weight > medium_threshold:
        return medium_sleep
    return low_sleep


def ms_range(start: datetime, end: datetime) -> tuple[int, int]:
    """Convert two ``datetime`` bounds into ``(start_ms, end_ms)`` epoch pairs."""
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def kline_row_to_dict(row: list[Any], *, include_volume: bool) -> dict[str, Any]:
    """Transform a Binance klines array row to a normalised dict.

    Full klines (``include_volume=True``, from ``/fapi/v1/klines``) include
    ``open_time / open / high / low / close / volume / close_time /
    quote_volume / trades``. Mark-price and index-price klines
    (``include_volume=False``, from ``/fapi/v1/{markPrice,indexPrice}Klines``)
    have placeholder ``0`` for volume / quote_volume / trades and therefore
    only expose the price fields.
    """
    out: dict[str, Any] = {
        "open_time": row[0],
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "close_time": row[6],
    }
    if include_volume:
        out["volume"] = row[5]
        out["quote_volume"] = row[7]
        out["trades"] = row[8]
    return out


def agg_trade_row_to_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    """Transform a Binance ``/fapi/v1/aggTrades`` row into downstream shape."""
    return {
        "agg_id": row["a"],
        "price": row["p"],
        "quantity": row["q"],
        "timestamp_ms": row["T"],
        "is_buyer_maker": row["m"],
    }


def advance_cursor_after_kline(last_close_time_ms: int) -> int:
    """Next-page ``startTime`` after a kline batch (strictly after last close)."""
    return last_close_time_ms + 1


def advance_cursor_after_agg_trade(last_ts_ms: int) -> int:
    """Next-page ``startTime`` after an aggTrades batch (strictly after last ts)."""
    return last_ts_ms + 1


# ---------------------------------------------------------------------------
# Async retry wrapper
# ---------------------------------------------------------------------------


async def request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    raise_on_404: bool = True,
    rate_limit_max_backoff: int = MAX_BACKOFF_SECONDS,
    server_error_sleep: float = SERVER_ERROR_SLEEP_SECONDS,
    request_error_sleep: float = REQUEST_ERROR_SLEEP_SECONDS,
    follow_redirects: bool = False,
) -> httpx.Response | None:
    """Execute a single GET with TinoHelm's standard retry policy.

    Retry matrix (decided by :func:`classify_http_status`):

    - ``429 / 418`` → exponential backoff capped at ``rate_limit_max_backoff``
    - ``5xx`` → fixed ``server_error_sleep``
    - ``httpx.RequestError`` → fixed ``request_error_sleep``
    - ``404`` → raise or return ``None`` (by ``raise_on_404``)
    - other 4xx / 3xx → propagate immediately (no retry)

    After ``max_retries`` *unsuccessful* attempts the underlying error is
    re-raised. Note: the bare ``except Exception`` previously used in
    ``providers.binance`` is deliberately *not* replicated here — malformed
    JSON (``json.JSONDecodeError``) now bubbles up to the caller on the
    first offence instead of silently retrying five times.
    """
    attempt = 0
    while True:
        try:
            resp = await client.get(
                url, params=params, follow_redirects=follow_redirects,
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            kind = classify_http_status(status)
            if kind == "not_found":
                if raise_on_404:
                    raise
                return None
            if kind == "rate_limit":
                attempt += 1
                if attempt > max_retries:
                    raise
                wait = backoff_seconds(attempt, max_seconds=rate_limit_max_backoff)
                logger.warning(
                    "Rate limited (HTTP %d) for %s, retry %d/%d in %ds",
                    status, url, attempt, max_retries, wait,
                )
                await asyncio.sleep(wait)
                continue
            if kind == "server_error":
                attempt += 1
                if attempt > max_retries:
                    raise
                logger.warning(
                    "Server error (HTTP %d) for %s, retry %d/%d in %.1fs",
                    status, url, attempt, max_retries, server_error_sleep,
                )
                await asyncio.sleep(server_error_sleep)
                continue
            raise
        except httpx.RequestError as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            logger.warning(
                "Request error for %s: %s, retry %d/%d",
                url, exc, attempt, max_retries,
            )
            await asyncio.sleep(request_error_sleep)
            continue
