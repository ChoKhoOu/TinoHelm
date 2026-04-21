"""Integration tests for ``tinohelm.data.providers.binance``.

Every test drives the fetch functions through ``httpx.MockTransport`` — no
network calls, no sleeps (``asyncio.sleep`` is patched autouse-style). The
tests cover:

- Happy-path pagination (multi-page with proper cursor advancement)
- Empty response handling (early break)
- Symbol stripping: ``BTCUSDT-PERP`` → ``BTCUSDT`` in outgoing requests
- Query-parameter contract: ``symbol=`` for klines/mark/agg, ``pair=`` for index
- Volume fields for full klines, omission for mark/index klines
- Rate-limit retry (429) propagated to end-to-end fetch
- Server-error retry (500) propagated
- Throttle call emitted with correct low_sleep per endpoint family
- Testnet base URL routing

Previously this module had **zero** direct tests and was the only data-path
REST client without coverage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinohelm.data.providers import binance as mod
from tinohelm.data.providers.binance import (
    BINANCE_FUTURES_BASE,
    BINANCE_FUTURES_TESTNET,
    fetch_agg_trades,
    fetch_index_price_klines,
    fetch_klines,
    fetch_mark_price_klines,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_sleeps():
    """Patch every place in the module tree that calls asyncio.sleep."""
    with (
        patch("tinohelm.data.providers._rest.asyncio.sleep", new=AsyncMock()),
        patch("tinohelm.data.providers.binance.asyncio.sleep", new=AsyncMock()) as outer,
    ):
        yield outer


@pytest.fixture
def start_end() -> tuple[datetime, datetime]:
    return (
        datetime(2025, 2, 1, tzinfo=timezone.utc),
        datetime(2025, 2, 2, tzinfo=timezone.utc),
    )


def _kline_row(open_time: int, close_time: int, close: str = "100.0") -> list[Any]:
    """Construct a Binance-style kline row in the canonical array layout."""
    return [
        open_time, "100.0", "110.0", "90.0", close, "12.345",
        close_time, "1234.5", 42, "5.0", "500.0", "0",
    ]


def _mock_transport(handler):
    """Patch ``httpx.AsyncClient`` at the providers.binance callsite so fetch
    functions pick up the MockTransport client."""
    real_client_cls = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    return patch("tinohelm.data.providers.binance.httpx.AsyncClient", side_effect=factory)


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


class TestFetchKlinesHappyPath:
    async def test_single_page_returns_full_schema(self, start_end):
        start, end = start_end
        row = _kline_row(1_700_000_000_000, 1_700_000_059_999)

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/fapi/v1/klines"
            return httpx.Response(200, json=[row])

        with _mock_transport(handler):
            out = await fetch_klines(
                "BTCUSDT-PERP", "1m", start, end, limit=1500,
            )

        assert len(out) == 1
        row_dict = out[0]
        assert row_dict["open_time"] == 1_700_000_000_000
        assert row_dict["close_time"] == 1_700_000_059_999
        # Full klines include volume fields
        assert "volume" in row_dict
        assert "quote_volume" in row_dict
        assert "trades" in row_dict

    async def test_empty_response_breaks_immediately(self, start_end):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            out = await fetch_klines(
                "BTCUSDT-PERP", "1m", *start_end,
            )

        assert out == []
        assert calls["n"] == 1

    async def test_pagination_advances_start_time_across_pages(self, start_end):
        start, end = start_end
        # Page 1: rows at 10:00:00 and 10:00:59; page 2: starts after last close
        page_1 = [
            _kline_row(1_700_000_000_000, 1_700_000_059_999),
            _kline_row(1_700_000_060_000, 1_700_000_119_999),
        ]
        page_2 = [
            _kline_row(1_700_000_120_000, 1_700_000_179_999),
        ]
        captured_start_times: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            st = request.url.params["startTime"]
            captured_start_times.append(st)
            # Return limit-sized first page to force pagination
            if len(captured_start_times) == 1:
                return httpx.Response(200, json=page_1)
            return httpx.Response(200, json=page_2)

        with _mock_transport(handler):
            out = await fetch_klines(
                "BTCUSDT-PERP", "1m", start, end, limit=2,  # small limit forces pagination
            )

        assert len(out) == 3
        # First request uses start_ms of start date
        first_call_start = int(captured_start_times[0])
        assert first_call_start == int(start.timestamp() * 1000)
        # Second request starts at last_close_time + 1
        second_call_start = int(captured_start_times[1])
        assert second_call_start == 1_700_000_119_999 + 1

    async def test_terminates_when_page_below_limit(self, start_end):
        """len(raw) < limit → stop paginating even if end_ms not reached."""
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            # Always return fewer than limit → single call
            return httpx.Response(200, json=[_kline_row(1_700_000_000_000, 1_700_000_059_999)])

        with _mock_transport(handler):
            await fetch_klines("BTCUSDT-PERP", "1m", *start_end, limit=500)

        assert calls["n"] == 1

    async def test_strips_symbol_suffix_in_outgoing_request(self, start_end):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_klines("BTCUSDT-PERP", "5m", *start_end)

        assert captured["symbol"] == "BTCUSDT"
        assert "-PERP" not in captured["symbol"]

    async def test_uses_mainnet_url_by_default(self, start_end):
        urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_klines("BTCUSDT-PERP", "1m", *start_end)

        assert BINANCE_FUTURES_BASE in urls[0]
        assert BINANCE_FUTURES_TESTNET not in urls[0]

    async def test_uses_testnet_url_when_flag_set(self, start_end):
        urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_klines("BTCUSDT-PERP", "1m", *start_end, testnet=True)

        assert BINANCE_FUTURES_TESTNET in urls[0]
        assert BINANCE_FUTURES_BASE not in urls[0] or urls[0].startswith(BINANCE_FUTURES_TESTNET)


# ---------------------------------------------------------------------------
# fetch_klines — retry matrix
# ---------------------------------------------------------------------------


class TestFetchKlinesRetry:
    async def test_rate_limit_429_then_success(self, start_end):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429)
            return httpx.Response(200, json=[_kline_row(1, 60_000)])

        with _mock_transport(handler):
            out = await fetch_klines("BTCUSDT-PERP", "1m", *start_end)

        assert len(out) == 1
        assert calls["n"] == 3

    async def test_server_500_then_success(self, start_end):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(500)
            return httpx.Response(200, json=[_kline_row(1, 60_000)])

        with _mock_transport(handler):
            out = await fetch_klines("BTCUSDT-PERP", "1m", *start_end)

        assert len(out) == 1
        assert calls["n"] == 2

    async def test_404_propagates(self, start_end):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with _mock_transport(handler):
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await fetch_klines("BTCUSDT-PERP", "1m", *start_end)
        assert exc.value.response.status_code == 404


# ---------------------------------------------------------------------------
# fetch_klines — throttle
# ---------------------------------------------------------------------------


class TestFetchKlinesThrottle:
    async def test_low_sleep_is_klines_baseline(self, start_end):
        """Low load → 0.5s throttle (klines family baseline)."""
        # Must paginate twice so throttle_seconds is actually invoked between pages.
        start, end = start_end
        pages = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            pages["n"] += 1
            if pages["n"] == 1:
                return httpx.Response(
                    200,
                    json=[
                        _kline_row(1_700_000_000_000, 1_700_000_059_999),
                        _kline_row(1_700_000_060_000, 1_700_000_119_999),
                    ],
                    headers={"X-MBX-USED-WEIGHT-1M": "100"},  # low
                )
            return httpx.Response(200, json=[])

        with patch("tinohelm.data.providers.binance.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            with _mock_transport(handler):
                await fetch_klines(
                    "BTCUSDT-PERP", "1m", start, end, limit=2,
                )

        # Between page 1 and page 2: one sleep with low_sleep value (0.5s for klines)
        sleep_values = [call.args[0] for call in sleep_mock.call_args_list]
        assert 0.5 in sleep_values

    async def test_high_weight_triggers_high_sleep(self, start_end):
        """X-MBX-USED-WEIGHT-1M > 1800 → 5.0s sleep."""
        start, end = start_end
        pages = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            pages["n"] += 1
            if pages["n"] == 1:
                return httpx.Response(
                    200,
                    json=[
                        _kline_row(1_700_000_000_000, 1_700_000_059_999),
                        _kline_row(1_700_000_060_000, 1_700_000_119_999),
                    ],
                    headers={"X-MBX-USED-WEIGHT-1M": "2200"},  # high
                )
            return httpx.Response(200, json=[])

        with patch("tinohelm.data.providers.binance.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            with _mock_transport(handler):
                await fetch_klines(
                    "BTCUSDT-PERP", "1m", start, end, limit=2,
                )

        sleep_values = [call.args[0] for call in sleep_mock.call_args_list]
        assert 5.0 in sleep_values


# ---------------------------------------------------------------------------
# fetch_mark_price_klines
# ---------------------------------------------------------------------------


class TestFetchMarkPriceKlines:
    async def test_uses_mark_price_endpoint(self, start_end):
        urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            urls.append(request.url.path)
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_mark_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert urls[0] == "/fapi/v1/markPriceKlines"

    async def test_rows_exclude_volume(self, start_end):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_kline_row(1, 60_000)])

        with _mock_transport(handler):
            out = await fetch_mark_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert len(out) == 1
        assert "volume" not in out[0]
        assert "quote_volume" not in out[0]
        assert "trades" not in out[0]

    async def test_symbol_param_not_pair(self, start_end):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_mark_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert captured["symbol"] == "BTCUSDT"
        assert "pair" not in captured


# ---------------------------------------------------------------------------
# fetch_index_price_klines
# ---------------------------------------------------------------------------


class TestFetchIndexPriceKlines:
    async def test_uses_index_price_endpoint(self, start_end):
        urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            urls.append(request.url.path)
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_index_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert urls[0] == "/fapi/v1/indexPriceKlines"

    async def test_uses_pair_param_not_symbol(self, start_end):
        """Binance index endpoints use ``pair`` not ``symbol`` — core quirk."""
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_index_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert captured["pair"] == "BTCUSDT"
        assert "symbol" not in captured

    async def test_rows_exclude_volume(self, start_end):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_kline_row(1, 60_000)])

        with _mock_transport(handler):
            out = await fetch_index_price_klines("BTCUSDT-PERP", "5m", *start_end)

        assert "volume" not in out[0]


# ---------------------------------------------------------------------------
# fetch_agg_trades
# ---------------------------------------------------------------------------


class TestFetchAggTrades:
    async def test_single_page_returns_five_keys(self, start_end):
        raw = {"a": 12345, "p": "50000.00", "q": "0.001", "T": 1_700_000_000_000, "m": True}

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/fapi/v1/aggTrades"
            return httpx.Response(200, json=[raw])

        with _mock_transport(handler):
            out = await fetch_agg_trades("BTCUSDT-PERP", *start_end)

        assert len(out) == 1
        assert out[0] == {
            "agg_id": 12345,
            "price": "50000.00",
            "quantity": "0.001",
            "timestamp_ms": 1_700_000_000_000,
            "is_buyer_maker": True,
        }

    async def test_empty_response_breaks(self, start_end):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            out = await fetch_agg_trades("BTCUSDT-PERP", *start_end)

        assert out == []

    async def test_pagination_advances_by_last_trade_ts_plus_1(self, start_end):
        """Agg trades cursor = last["T"] + 1, not last["T"] + some_interval."""
        captured_starts: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured_starts.append(int(request.url.params["startTime"]))
            if len(captured_starts) == 1:
                return httpx.Response(
                    200,
                    json=[
                        {"a": 1, "p": "1", "q": "1", "T": 1_700_000_000_100, "m": True},
                        {"a": 2, "p": "1", "q": "1", "T": 1_700_000_000_200, "m": False},
                    ],
                )
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_agg_trades("BTCUSDT-PERP", *start_end, limit=2)

        assert len(captured_starts) == 2
        assert captured_starts[1] == 1_700_000_000_200 + 1

    async def test_strips_symbol_suffix(self, start_end):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        with _mock_transport(handler):
            await fetch_agg_trades("BTCUSDT-PERP", *start_end)

        assert captured["symbol"] == "BTCUSDT"

    async def test_low_sleep_is_agg_trades_baseline(self, start_end):
        """Agg trades endpoint uses 0.3s low_sleep (vs klines 0.5s)."""
        pages = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            pages["n"] += 1
            if pages["n"] == 1:
                return httpx.Response(
                    200,
                    json=[
                        {"a": 1, "p": "1", "q": "1", "T": 100, "m": True},
                        {"a": 2, "p": "1", "q": "1", "T": 200, "m": False},
                    ],
                    headers={"X-MBX-USED-WEIGHT-1M": "50"},
                )
            return httpx.Response(200, json=[])

        with patch("tinohelm.data.providers.binance.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            with _mock_transport(handler):
                await fetch_agg_trades("BTCUSDT-PERP", *start_end, limit=2)

        sleep_values = [call.args[0] for call in sleep_mock.call_args_list]
        assert 0.3 in sleep_values
        assert 0.5 not in sleep_values  # must NOT use klines baseline

    async def test_retry_on_429_then_success(self, start_end):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(429)
            return httpx.Response(
                200,
                json=[{"a": 1, "p": "1", "q": "1", "T": 100, "m": False}],
            )

        with _mock_transport(handler):
            out = await fetch_agg_trades("BTCUSDT-PERP", *start_end)

        assert len(out) == 1
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Shared invariants
# ---------------------------------------------------------------------------


class TestKlinesFamilyConstants:
    def test_low_sleep_constants_distinct(self):
        """Different endpoints keep their own throttle baselines."""
        assert mod._KLINES_LOW_SLEEP == 0.5
        assert mod._AGG_TRADES_LOW_SLEEP == 0.3

    def test_progress_intervals(self):
        assert mod._KLINES_PROGRESS_EVERY == 15_000
        assert mod._AGG_TRADES_PROGRESS_EVERY == 50_000

    def test_interval_ms_table_sanity(self):
        """Exported table is referenced by upstream consumers — pin its shape."""
        assert mod.INTERVAL_MS["1m"] == 60_000
        assert mod.INTERVAL_MS["1h"] == 3_600_000
        assert mod.INTERVAL_MS["1d"] == 86_400_000


class TestLegacyEntryPointsPreserved:
    """Backwards compatibility: the four public coroutines stay importable
    with their historical names and positional-arg signatures."""

    def test_all_four_fetch_functions_exported(self):
        assert callable(fetch_klines)
        assert callable(fetch_mark_price_klines)
        assert callable(fetch_index_price_klines)
        assert callable(fetch_agg_trades)

    def test_legacy_generic_helper_removed(self):
        """_fetch_klines_generic was the legacy pre-refactor shared helper."""
        assert not hasattr(mod, "_fetch_klines_generic")
