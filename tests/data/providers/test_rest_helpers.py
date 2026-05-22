"""Pure-helper unit tests for ``tinohelm.data.providers._rest``.

Covers the classification, backoff, throttle, header parsing, row-transform,
and cursor-advancement helpers. Plus the async ``request_with_retry`` wrapper
driven by ``httpx.MockTransport`` so no network is touched.

All retry/backoff timing is driven against a patched ``asyncio.sleep`` so the
test suite runs in a few milliseconds regardless of the underlying policy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinohelm.data.providers import _rest
from tinohelm.data.providers._rest import (
    DEFAULT_MAX_RETRIES,
    MAX_BACKOFF_SECONDS,
    REQUEST_ERROR_SLEEP_SECONDS,
    SERVER_ERROR_SLEEP_SECONDS,
    WEIGHT_HIGH_SLEEP,
    WEIGHT_HIGH_THRESHOLD,
    WEIGHT_MEDIUM_SLEEP,
    WEIGHT_MEDIUM_THRESHOLD,
    advance_cursor_after_kline,
    backoff_seconds,
    classify_http_status,
    kline_row_to_dict,
    ms_range,
    parse_used_weight_header,
    request_with_retry,
    throttle_seconds,
)


# ---------------------------------------------------------------------------
# classify_http_status
# ---------------------------------------------------------------------------


class TestClassifyHttpStatus:
    @pytest.mark.parametrize("status", [200, 201, 204, 206, 250, 299])
    def test_2xx_is_success(self, status):
        assert classify_http_status(status) == "success"

    def test_404_is_not_found_special_case(self):
        assert classify_http_status(404) == "not_found"

    @pytest.mark.parametrize("status", [418, 429])
    def test_rate_limit_codes(self, status):
        assert classify_http_status(status) == "rate_limit"

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 599, 600, 999])
    def test_5xx_and_above_is_server_error(self, status):
        assert classify_http_status(status) == "server_error"

    @pytest.mark.parametrize("status", [400, 401, 403, 405, 409, 422])
    def test_other_4xx_is_abort(self, status):
        assert classify_http_status(status) == "abort"

    @pytest.mark.parametrize("status", [300, 301, 302, 304, 307, 308, 399])
    def test_3xx_is_abort(self, status):
        """3xx without follow_redirects should propagate — no retry."""
        assert classify_http_status(status) == "abort"

    def test_404_distinct_from_other_4xx(self):
        assert classify_http_status(404) != classify_http_status(400)
        assert classify_http_status(404) != classify_http_status(403)


# ---------------------------------------------------------------------------
# backoff_seconds
# ---------------------------------------------------------------------------


class TestBackoffSeconds:
    @pytest.mark.parametrize(
        "attempt,expected",
        [
            (1, 2),
            (2, 4),
            (3, 8),
            (4, 16),
            (5, 32),
        ],
    )
    def test_exponential_growth_below_cap(self, attempt, expected):
        assert backoff_seconds(attempt) == expected

    def test_cap_at_max_seconds_default(self):
        # 2**6 = 64 → capped to 60
        assert backoff_seconds(6) == MAX_BACKOFF_SECONDS
        assert backoff_seconds(10) == MAX_BACKOFF_SECONDS
        assert backoff_seconds(100) == MAX_BACKOFF_SECONDS

    def test_cap_uses_custom_max(self):
        assert backoff_seconds(10, max_seconds=5) == 5
        assert backoff_seconds(1, max_seconds=5) == 2

    def test_zero_and_negative_return_zero(self):
        assert backoff_seconds(0) == 0
        assert backoff_seconds(-1) == 0
        assert backoff_seconds(-100) == 0

    def test_returns_int_type(self):
        """Downstream asyncio.sleep accepts int — verify we don't return float."""
        assert isinstance(backoff_seconds(3), int)


# ---------------------------------------------------------------------------
# parse_used_weight_header
# ---------------------------------------------------------------------------


class TestParseUsedWeightHeader:
    def test_missing_header_returns_zero(self):
        assert parse_used_weight_header({}) == 0

    def test_valid_string_int(self):
        assert parse_used_weight_header({"X-MBX-USED-WEIGHT-1M": "1500"}) == 1500

    def test_empty_string_returns_zero(self):
        assert parse_used_weight_header({"X-MBX-USED-WEIGHT-1M": ""}) == 0

    def test_non_numeric_returns_zero(self):
        assert parse_used_weight_header({"X-MBX-USED-WEIGHT-1M": "abc"}) == 0

    def test_none_returns_zero(self):
        assert parse_used_weight_header({"X-MBX-USED-WEIGHT-1M": None}) == 0

    def test_case_matters(self):
        """Header is tried only under the exact canonical key name."""
        # Lowercase not recognised → default 0 (consistent with httpx.Headers is case-insensitive but we pass raw dict).
        assert parse_used_weight_header({"x-mbx-used-weight-1m": "777"}) == 0

    def test_accepts_httpx_headers_case_insensitive(self):
        """httpx.Headers is case-insensitive — helper must work with it too."""
        headers = httpx.Headers({"x-mbx-used-weight-1m": "1234"})
        assert parse_used_weight_header(headers) == 1234

    def test_negative_returned_as_is(self):
        """Negative weights are malformed but we don't explode — pass through to throttle_seconds."""
        assert parse_used_weight_header({"X-MBX-USED-WEIGHT-1M": "-5"}) == -5


# ---------------------------------------------------------------------------
# throttle_seconds
# ---------------------------------------------------------------------------


class TestThrottleSeconds:
    def test_low_load_uses_endpoint_baseline(self):
        # 0, 100, exactly 1200 → low
        assert throttle_seconds(0, low_sleep=0.3) == 0.3
        assert throttle_seconds(100, low_sleep=0.3) == 0.3
        assert throttle_seconds(1200, low_sleep=0.3) == 0.3

    def test_medium_load(self):
        # >1200, ≤1800 → medium
        assert throttle_seconds(1201, low_sleep=0.3) == WEIGHT_MEDIUM_SLEEP
        assert throttle_seconds(1500, low_sleep=0.3) == WEIGHT_MEDIUM_SLEEP
        assert throttle_seconds(1800, low_sleep=0.3) == WEIGHT_MEDIUM_SLEEP

    def test_high_load(self):
        # >1800 → high
        assert throttle_seconds(1801, low_sleep=0.3) == WEIGHT_HIGH_SLEEP
        assert throttle_seconds(2400, low_sleep=0.3) == WEIGHT_HIGH_SLEEP
        assert throttle_seconds(9999, low_sleep=0.3) == WEIGHT_HIGH_SLEEP

    def test_boundary_strictly_greater_than(self):
        """Exactly at threshold stays in lower tier — strict > comparison."""
        assert throttle_seconds(WEIGHT_MEDIUM_THRESHOLD, low_sleep=0.5) == 0.5
        assert throttle_seconds(WEIGHT_HIGH_THRESHOLD, low_sleep=0.5) == WEIGHT_MEDIUM_SLEEP

    def test_custom_low_sleep_per_endpoint(self):
        """Different endpoints pass different low_sleep values."""
        assert throttle_seconds(0, low_sleep=0.5) == 0.5
        assert throttle_seconds(500, low_sleep=0.3) == 0.3

    def test_custom_thresholds_override(self):
        assert (
            throttle_seconds(
                100, low_sleep=0.1, medium_threshold=50, high_threshold=200,
            )
            == WEIGHT_MEDIUM_SLEEP
        )

    def test_negative_weight_treated_as_low(self):
        """parse_used_weight_header can return -ve; throttle must not crash."""
        assert throttle_seconds(-100, low_sleep=0.3) == 0.3


# ---------------------------------------------------------------------------
# ms_range
# ---------------------------------------------------------------------------


class TestMsRange:
    def test_basic_utc(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 2, tzinfo=timezone.utc)
        start_ms, end_ms = ms_range(start, end)
        assert start_ms == 1735689600_000
        assert end_ms == 1735776000_000
        assert end_ms - start_ms == 86_400_000  # one day

    def test_returns_int(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        start_ms, end_ms = ms_range(start, end)
        assert isinstance(start_ms, int)
        assert isinstance(end_ms, int)

    def test_same_instant_yields_equal_ms(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        start_ms, end_ms = ms_range(dt, dt)
        assert start_ms == end_ms

    def test_pre_epoch(self):
        start = datetime(1969, 1, 1, tzinfo=timezone.utc)
        end = datetime(1970, 1, 1, tzinfo=timezone.utc)
        start_ms, end_ms = ms_range(start, end)
        assert start_ms < 0
        assert end_ms == 0


# ---------------------------------------------------------------------------
# kline_row_to_dict
# ---------------------------------------------------------------------------


class TestKlineRowToDict:
    # Binance klines row layout (arrays are index-positional)
    _FULL_ROW: list[Any] = [
        1735689600000,  # open_time
        "100.0",        # open
        "110.0",        # high
        "95.0",         # low
        "105.0",        # close
        "1234.567",     # volume
        1735689659999,  # close_time
        "129791.2",     # quote_volume
        5432,           # trades
        "700.0",        # taker buy base
        "73000",        # taker buy quote
        "0",            # ignore
    ]

    def test_full_klines_include_volume(self):
        out = kline_row_to_dict(self._FULL_ROW, include_volume=True)
        assert out["open_time"] == 1735689600000
        assert out["open"] == "100.0"
        assert out["high"] == "110.0"
        assert out["low"] == "95.0"
        assert out["close"] == "105.0"
        assert out["close_time"] == 1735689659999
        assert out["volume"] == "1234.567"
        assert out["quote_volume"] == "129791.2"
        assert out["trades"] == 5432

    def test_full_klines_schema_is_9_keys(self):
        out = kline_row_to_dict(self._FULL_ROW, include_volume=True)
        assert set(out.keys()) == {
            "open_time", "open", "high", "low", "close",
            "close_time", "volume", "quote_volume", "trades",
        }

    def test_mark_index_klines_no_volume(self):
        out = kline_row_to_dict(self._FULL_ROW, include_volume=False)
        assert "volume" not in out
        assert "quote_volume" not in out
        assert "trades" not in out

    def test_mark_index_klines_schema_is_6_keys(self):
        out = kline_row_to_dict(self._FULL_ROW, include_volume=False)
        assert set(out.keys()) == {
            "open_time", "open", "high", "low", "close", "close_time",
        }

    def test_price_fields_preserved_as_strings(self):
        """Binance returns price/volume as strings — we don't cast."""
        out = kline_row_to_dict(self._FULL_ROW, include_volume=False)
        assert all(isinstance(out[k], str) for k in ("open", "high", "low", "close"))


# ---------------------------------------------------------------------------
# advance_cursor_after_kline
# ---------------------------------------------------------------------------


class TestAdvanceCursor:
    def test_kline_cursor_is_strict_plus_one(self):
        """Next-page startTime must be strictly after the last close time."""
        assert advance_cursor_after_kline(1735689659999) == 1735689660000
        assert advance_cursor_after_kline(0) == 1


# ---------------------------------------------------------------------------
# request_with_retry — async integration via httpx.MockTransport
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_asyncio_sleep():
    """All retry tests should run in sub-second time — patch asyncio.sleep."""
    with patch("tinohelm.data.providers._rest.asyncio.sleep", new=AsyncMock()) as m:
        yield m


def _make_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://fake.test")


class TestRequestWithRetrySuccess:
    async def test_single_get_returns_response(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        async with _make_client(handler) as client:
            resp = await request_with_retry(client, "/path")
        assert resp is not None
        assert resp.json() == {"ok": True}

    async def test_params_are_forwarded(self):
        captured: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/path", params={"symbol": "BTCUSDT", "limit": 500})

        assert captured == {"symbol": "BTCUSDT", "limit": "500"}

    async def test_success_no_sleep_called(self, _patch_asyncio_sleep):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/path")

        _patch_asyncio_sleep.assert_not_called()


class TestRequestWithRetryRateLimit:
    async def test_retries_on_429_then_succeeds(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429)
            return httpx.Response(200, json={"ok": True})

        async with _make_client(handler) as client:
            resp = await request_with_retry(client, "/rl")
        assert calls["n"] == 3
        assert resp is not None and resp.json() == {"ok": True}

    async def test_retries_on_418_then_succeeds(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return (
                httpx.Response(418) if calls["n"] == 1 else httpx.Response(200, json=[])
            )

        async with _make_client(handler) as client:
            resp = await request_with_retry(client, "/rl")
        assert resp is not None
        assert calls["n"] == 2

    async def test_uses_exponential_backoff_sleep_values(self, _patch_asyncio_sleep):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 3:
                return httpx.Response(429)
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/rl")

        # First 3 attempts all rate-limited → 3 sleeps of 2s, 4s, 8s
        sleep_values = [call.args[0] for call in _patch_asyncio_sleep.call_args_list]
        assert sleep_values == [2, 4, 8]

    async def test_exhausts_retries_then_raises(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await request_with_retry(client, "/rl", max_retries=2)

        assert exc.value.response.status_code == 429

    async def test_backoff_respects_custom_max_cap(self, _patch_asyncio_sleep):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 5:
                return httpx.Response(429)
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/rl", rate_limit_max_backoff=5)

        # Sleeps: 2, 4, 5(cap), 5, 5
        sleep_values = [call.args[0] for call in _patch_asyncio_sleep.call_args_list]
        assert sleep_values == [2, 4, 5, 5, 5]


class TestRequestWithRetryServerError:
    async def test_retries_on_500_then_succeeds(self, _patch_asyncio_sleep):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(500)
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/se")

        assert calls["n"] == 2
        _patch_asyncio_sleep.assert_awaited_once_with(SERVER_ERROR_SLEEP_SECONDS)

    async def test_502_and_503_also_retry(self):
        for status in (502, 503, 504):
            calls = {"n": 0}

            async def handler(request: httpx.Request, _status=status) -> httpx.Response:
                calls["n"] += 1
                if calls["n"] < 2:
                    return httpx.Response(_status)
                return httpx.Response(200, json=[])

            async with _make_client(handler) as client:
                await request_with_retry(client, "/se")

            assert calls["n"] == 2

    async def test_exhausts_retries_then_raises(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await request_with_retry(client, "/se", max_retries=1)

        assert exc.value.response.status_code == 500


class TestRequestWithRetryAbort4xx:
    @pytest.mark.parametrize("status", [400, 401, 403, 405, 409, 410, 422])
    async def test_4xx_not_retried(self, status):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(status)

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await request_with_retry(client, "/abort")

        assert calls["n"] == 1  # no retry
        assert exc.value.response.status_code == status


class TestRequestWithRetryNotFound:
    async def test_404_raises_by_default(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await request_with_retry(client, "/missing")

        assert calls["n"] == 1
        assert exc.value.response.status_code == 404

    async def test_404_returns_none_when_raise_on_404_false(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        async with _make_client(handler) as client:
            resp = await request_with_retry(client, "/missing", raise_on_404=False)

        assert resp is None
        assert calls["n"] == 1


class TestRequestWithRetryTransportError:
    async def test_retries_on_connect_error_then_succeeds(self, _patch_asyncio_sleep):
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json=[])

        async with _make_client(handler) as client:
            await request_with_retry(client, "/t")

        assert calls["n"] == 2
        _patch_asyncio_sleep.assert_awaited_once_with(REQUEST_ERROR_SLEEP_SECONDS)

    async def test_exhausts_retries_then_raises(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        async with _make_client(handler) as client:
            with pytest.raises(httpx.ReadTimeout):
                await request_with_retry(client, "/t", max_retries=2)


class TestRequestWithRetryJsonDecodeNoRetry:
    """Deliberate behaviour narrowing from the legacy bare-Exception catch:
    malformed JSON now bubbles up to the caller on the first offence rather
    than being silently retried five times."""

    async def test_malformed_json_not_retried(self):
        """JSONDecodeError is raised on the first parse attempt by the caller."""

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json-at-all")

        async with _make_client(handler) as client:
            resp = await request_with_retry(client, "/j")

        assert resp is not None
        # request_with_retry returns the raw response — malformed JSON is
        # caller's problem (as it should be: retrying a persistently-malformed
        # body is a waste of 5 rate-limit hits and a second per failure).
        with pytest.raises(Exception):
            resp.json()


class TestRequestWithRetryFollowRedirects:
    async def test_follow_redirects_propagated(self):
        """downloader passes follow_redirects=True; binance.py does not."""
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/one":
                return httpx.Response(302, headers={"location": "/two"})
            return httpx.Response(200, json={"ok": True})

        async with _make_client(handler) as client:
            resp = await request_with_retry(
                client, "/one", follow_redirects=True,
            )
        assert resp is not None and resp.json() == {"ok": True}

    async def test_no_follow_redirects_raises_on_3xx(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "/x"})

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await request_with_retry(client, "/r")


class TestRequestWithRetryDefaults:
    def test_default_max_retries_matches_exported_constant(self):
        """Downstream downloader used a private _MAX_RETRIES=5; pin it."""
        assert DEFAULT_MAX_RETRIES == 5

    def test_default_max_backoff_matches_exported_constant(self):
        assert MAX_BACKOFF_SECONDS == 60

    def test_server_error_sleep_matches_exported_constant(self):
        assert SERVER_ERROR_SLEEP_SECONDS == 2.0

    def test_weight_thresholds(self):
        assert WEIGHT_MEDIUM_THRESHOLD == 1200
        assert WEIGHT_HIGH_THRESHOLD == 1800

    def test_weight_sleeps(self):
        assert WEIGHT_MEDIUM_SLEEP == 1.0
        assert WEIGHT_HIGH_SLEEP == 5.0


class TestRestModuleSurface:
    """Pin the public symbol surface so future imports are stable."""

    def test_public_helpers_importable(self):
        for name in (
            "classify_http_status",
            "backoff_seconds",
            "parse_used_weight_header",
            "throttle_seconds",
            "ms_range",
            "kline_row_to_dict",
            "advance_cursor_after_kline",
            "request_with_retry",
        ):
            assert hasattr(_rest, name), f"_rest.{name} missing"

    def test_public_constants_importable(self):
        for name in (
            "DEFAULT_MAX_RETRIES",
            "MAX_BACKOFF_SECONDS",
            "SERVER_ERROR_SLEEP_SECONDS",
            "REQUEST_ERROR_SLEEP_SECONDS",
            "WEIGHT_HIGH_THRESHOLD",
            "WEIGHT_MEDIUM_THRESHOLD",
            "WEIGHT_HIGH_SLEEP",
            "WEIGHT_MEDIUM_SLEEP",
        ):
            assert hasattr(_rest, name), f"_rest.{name} missing"
