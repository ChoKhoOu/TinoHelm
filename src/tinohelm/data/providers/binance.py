"""Binance REST API — 补尾专用.

Main data pipeline uses BinanceVisionPipeline (data/pipeline.py).
This module is retained ONLY for filling the T+1~T+3 gap where
Vision archives are not yet available.

Retained: fetch_klines, fetch_mark_price_klines, fetch_index_price_klines,
          fetch_agg_trades
Removed:  fetch_funding_rates (replaced by Vision fundingRate monthly packs)

Shared pagination / retry / throttle policy lives in :mod:`tinohelm.data.providers._rest`
— see that module for the retry classification matrix and the Binance
"X-MBX-USED-WEIGHT-1M" throttle tiers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from tinohelm.data.instruments import strip_to_binance_api_symbol as _strip_to_binance_api_symbol
from tinohelm.data.providers._rest import (
    advance_cursor_after_agg_trade,
    advance_cursor_after_kline,
    agg_trade_row_to_dict,
    kline_row_to_dict,
    ms_range,
    parse_used_weight_header,
    request_with_retry,
    throttle_seconds,
)

logger = logging.getLogger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}

# Minimum per-request sleep for each endpoint family. Matches legacy behaviour:
# the two klines-family loops used 0.5s, the aggTrades loop used 0.3s, and the
# simpler mark/index "generic" path also used 0.5s.
_KLINES_LOW_SLEEP: float = 0.5
_AGG_TRADES_LOW_SLEEP: float = 0.3

# Progress log intervals (rows-between-emits) — retained from legacy code.
_KLINES_PROGRESS_EVERY: int = 15_000
_AGG_TRADES_PROGRESS_EVERY: int = 50_000


async def fetch_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1500,
) -> list[dict[str, Any]]:
    """Fetch full klines from Binance Futures with pagination.

    Returns list of dicts with keys: ``open_time, open, high, low, close,
    volume, close_time, quote_volume, trades``.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    return await _paginate_klines(
        url=f"{base_url}/fapi/v1/klines",
        api_symbol=_strip_to_binance_api_symbol(symbol),
        interval=interval,
        start=start,
        end=end,
        limit=limit,
        include_volume=True,
        symbol_param="symbol",
        label=f"klines for {symbol} {interval}",
    )


async def fetch_mark_price_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1500,
) -> list[dict[str, Any]]:
    """Fetch mark price klines from Binance ``/fapi/v1/markPriceKlines``.

    Returns list of dicts with: ``open_time, open, high, low, close,
    close_time``. Volume fields are always 0 for mark price klines (the
    API returns them as ``0`` strings and we omit them).
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    return await _paginate_klines(
        url=f"{base_url}/fapi/v1/markPriceKlines",
        api_symbol=_strip_to_binance_api_symbol(symbol),
        interval=interval,
        start=start,
        end=end,
        limit=limit,
        include_volume=False,
        symbol_param="symbol",
        label=f"mark price klines for {symbol}",
    )


async def fetch_index_price_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1500,
) -> list[dict[str, Any]]:
    """Fetch index price klines from Binance ``/fapi/v1/indexPriceKlines``.

    Uses the ``pair`` query parameter instead of ``symbol`` (this is a
    Binance-specific quirk for index-price endpoints). Returns list of
    dicts with: ``open_time, open, high, low, close, close_time``.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    return await _paginate_klines(
        url=f"{base_url}/fapi/v1/indexPriceKlines",
        api_symbol=_strip_to_binance_api_symbol(symbol),
        interval=interval,
        start=start,
        end=end,
        limit=limit,
        include_volume=False,
        symbol_param="pair",
        label=f"index price klines for {symbol}",
    )


async def fetch_agg_trades(
    symbol: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Fetch aggregate trades from Binance ``/fapi/v1/aggTrades`` with pagination.

    Returns list of dicts with: ``agg_id, price, quantity, timestamp_ms,
    is_buyer_maker``.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    url = f"{base_url}/fapi/v1/aggTrades"
    api_symbol = _strip_to_binance_api_symbol(symbol)

    start_ms, end_ms = ms_range(start, end)
    all_trades: list[dict[str, Any]] = []
    current_start = start_ms

    async with httpx.AsyncClient(timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                "symbol": api_symbol,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }
            resp = await request_with_retry(client, url, params=params)
            assert resp is not None  # raise_on_404=True default — never None
            raw = resp.json()
            if not raw:
                break

            all_trades.extend(agg_trade_row_to_dict(t) for t in raw)

            if len(all_trades) % _AGG_TRADES_PROGRESS_EVERY == 0:
                logger.info("Progress: %d trades fetched for %s", len(all_trades), symbol)

            current_start = advance_cursor_after_agg_trade(raw[-1]["T"])

            if len(raw) < limit:
                break

            sleep_s = throttle_seconds(
                parse_used_weight_header(resp.headers),
                low_sleep=_AGG_TRADES_LOW_SLEEP,
            )
            await asyncio.sleep(sleep_s)

    logger.info("Fetched %d aggregate trades for %s", len(all_trades), symbol)
    return all_trades


async def _paginate_klines(
    *,
    url: str,
    api_symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    limit: int,
    include_volume: bool,
    symbol_param: str,
    label: str,
) -> list[dict[str, Any]]:
    """Shared klines-family pagination loop used by all three klines endpoints.

    The three endpoints differ only in URL path, whether rows carry volume
    fields, and the name of the symbol query parameter (``symbol`` vs
    ``pair``). Pagination, retry and throttle are identical.
    """
    start_ms, end_ms = ms_range(start, end)
    all_klines: list[dict[str, Any]] = []
    current_start = start_ms

    async with httpx.AsyncClient(timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                symbol_param: api_symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }
            resp = await request_with_retry(client, url, params=params)
            assert resp is not None
            raw = resp.json()
            if not raw:
                break

            all_klines.extend(
                kline_row_to_dict(k, include_volume=include_volume) for k in raw
            )

            if (
                len(all_klines) % _KLINES_PROGRESS_EVERY == 0
                or len(raw) < limit
            ):
                logger.info("Progress: %d %s", len(all_klines), label)

            current_start = advance_cursor_after_kline(raw[-1][6])

            if len(raw) < limit:
                break

            sleep_s = throttle_seconds(
                parse_used_weight_header(resp.headers),
                low_sleep=_KLINES_LOW_SLEEP,
            )
            await asyncio.sleep(sleep_s)

    logger.info("Fetched %d %s", len(all_klines), label)
    return all_klines
