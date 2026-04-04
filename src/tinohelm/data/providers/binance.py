"""Binance REST API — 补尾专用.

Main data pipeline uses BinanceVisionPipeline (data/pipeline.py).
This module is retained ONLY for filling the T+1~T+3 gap where
Vision archives are not yet available.

Retained: fetch_klines, fetch_mark_price_klines, fetch_index_price_klines,
          fetch_agg_trades, _fetch_klines_generic
Removed:  fetch_funding_rates (replaced by Vision fundingRate monthly packs)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from tinohelm.data.instruments import strip_to_binance_api_symbol as _strip_to_binance_api_symbol

logger = logging.getLogger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


async def fetch_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1500,
) -> list[dict[str, Any]]:
    """Fetch klines from Binance Futures API with pagination.

    Returns list of dicts with keys: open_time, open, high, low, close, volume, close_time.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    url = f"{base_url}/fapi/v1/klines"

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    all_klines: list[dict[str, Any]] = []
    current_start = start_ms

    max_retries = 5
    retry_count = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                "symbol": _strip_to_binance_api_symbol(symbol),
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }

            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                raw = resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 418):  # Rate limited
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    wait = min(2 ** retry_count, 60)
                    logger.warning("Rate limited (HTTP %d), retry %d/%d in %ds", status, retry_count, max_retries, wait)
                    await asyncio.sleep(wait)
                    continue
                elif status >= 500:  # Server error
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(2)
                    continue
                else:  # 4xx client error — don't retry
                    raise
            except (httpx.RequestError, Exception) as e:
                retry_count += 1
                if retry_count > max_retries:
                    raise
                logger.warning("Request error: %s, retry %d/%d", e, retry_count, max_retries)
                await asyncio.sleep(2)
                continue

            # Reset retry count on success
            retry_count = 0

            if not raw:
                break

            for k in raw:
                all_klines.append({
                    "open_time": k[0],
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "volume": k[5],
                    "close_time": k[6],
                    "quote_volume": k[7],
                    "trades": k[8],
                })

            # Move start to after last candle
            last_close_time = raw[-1][6]
            current_start = last_close_time + 1

            if len(all_klines) % 15000 == 0 or len(raw) < limit:
                logger.info("Progress: %d klines fetched for %s %s", len(all_klines), symbol, interval)

            if len(raw) < limit:
                break

            # Dynamic throttling based on Binance weight header (limit=1500 costs ~20 weight per call)
            used_weight = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", "0"))
            if used_weight > 1800:  # 75% of 2400 limit
                await asyncio.sleep(5)
            elif used_weight > 1200:  # 50% of limit
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.5)  # Minimum 0.5s between calls

    logger.info(f"Fetched {len(all_klines)} klines for {symbol} {interval}")
    return all_klines


async def fetch_mark_price_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1500,
) -> list[dict[str, Any]]:
    """Fetch mark price klines from Binance ``/fapi/v1/markPriceKlines``.

    Returns list of dicts with: open_time, open, high, low, close, close_time.
    Volume fields are always 0 for mark price klines.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    return await _fetch_klines_generic(
        f"{base_url}/fapi/v1/markPriceKlines",
        _strip_to_binance_api_symbol(symbol),
        interval, start, end, limit,
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

    Uses ``pair`` param (not ``symbol``).
    Returns list of dicts with: open_time, open, high, low, close, close_time.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    return await _fetch_klines_generic(
        f"{base_url}/fapi/v1/indexPriceKlines",
        _strip_to_binance_api_symbol(symbol),
        interval, start, end, limit,
        param_name="pair",
        label=f"index price klines for {symbol}",
    )


async def fetch_agg_trades(
    symbol: str,
    start: datetime,
    end: datetime,
    testnet: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Fetch aggregate trades from Binance ``/fapi/v1/aggTrades``.

    Returns list of dicts with: agg_id, price, quantity, timestamp_ms, is_buyer_maker.
    """
    base_url = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    url = f"{base_url}/fapi/v1/aggTrades"

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    all_trades: list[dict[str, Any]] = []
    current_start = start_ms

    max_retries = 5
    retry_count = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                "symbol": _strip_to_binance_api_symbol(symbol),
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }

            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                raw = resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 418):
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(min(2 ** retry_count, 60))
                    continue
                elif status >= 500:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(2)
                    continue
                else:
                    raise
            except (httpx.RequestError, Exception) as e:
                retry_count += 1
                if retry_count > max_retries:
                    raise
                logger.warning("Request error: %s, retry %d/%d", e, retry_count, max_retries)
                await asyncio.sleep(2)
                continue

            retry_count = 0
            if not raw:
                break

            for t in raw:
                all_trades.append({
                    "agg_id": t["a"],
                    "price": t["p"],
                    "quantity": t["q"],
                    "timestamp_ms": t["T"],
                    "is_buyer_maker": t["m"],
                })

            last_ts = raw[-1]["T"]
            current_start = last_ts + 1

            if len(all_trades) % 50000 == 0:
                logger.info("Progress: %d trades fetched for %s", len(all_trades), symbol)

            if len(raw) < limit:
                break

            used_weight = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", "0"))
            if used_weight > 1800:
                await asyncio.sleep(5)
            elif used_weight > 1200:
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.3)

    logger.info("Fetched %d aggregate trades for %s", len(all_trades), symbol)
    return all_trades


async def _fetch_klines_generic(
    url: str,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    limit: int,
    *,
    param_name: str = "symbol",
    label: str = "klines",
) -> list[dict[str, Any]]:
    """Generic klines-format fetcher shared by mark/index price endpoints."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    all_klines: list[dict[str, Any]] = []
    current_start = start_ms

    max_retries = 5
    retry_count = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                param_name: symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }

            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                raw = resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 418):
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(min(2 ** retry_count, 60))
                    continue
                elif status >= 500:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(2)
                    continue
                else:
                    raise
            except (httpx.RequestError, Exception) as e:
                retry_count += 1
                if retry_count > max_retries:
                    raise
                logger.warning("Request error: %s, retry %d/%d", e, retry_count, max_retries)
                await asyncio.sleep(2)
                continue

            retry_count = 0
            if not raw:
                break

            for k in raw:
                all_klines.append({
                    "open_time": k[0],
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "close_time": k[6],
                })

            last_close_time = raw[-1][6]
            current_start = last_close_time + 1

            if len(raw) < limit:
                break

            await asyncio.sleep(0.5)

    logger.info("Fetched %d %s", len(all_klines), label)
    return all_klines
