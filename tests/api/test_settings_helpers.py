"""Tests for pure helpers in tinohelm.api.routes.settings."""
from __future__ import annotations

import pytest

from tinohelm.api.routes.settings import EXCHANGE_PING_URLS, _mask_key


class TestMaskKey:
    def test_short_keys_fully_masked(self):
        assert _mask_key("") == "****"
        assert _mask_key("a") == "****"
        assert _mask_key("abcdefgh") == "****"  # exactly 8 chars still fully masked

    def test_long_key_shows_first_and_last_4(self):
        assert _mask_key("abcdefghij") == "abcd****ghij"
        assert _mask_key("apikey_12345abcdefXYZ") == "apik****fXYZ"

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("abcdefghi", "abcd****fghi"),   # 9 chars — longer than 8
            ("12345678abc", "1234****8abc"),
        ],
    )
    def test_boundary_lengths(self, value: str, expected: str):
        assert _mask_key(value) == expected

    def test_structure_never_leaks_middle(self):
        """Middle portion must be entirely masked."""
        secret = "SK-live-ABCDEFGHIJ1234567890"
        masked = _mask_key(secret)
        assert "EFGHIJ" not in masked
        assert "1234567" not in masked
        assert masked.startswith("SK-l")
        assert masked.endswith("7890")


class TestExchangePingUrls:
    def test_only_binance_and_fapi_endpoint(self):
        # Lock: newly-added endpoints must come with migration notes
        assert EXCHANGE_PING_URLS == [
            ("Binance", "https://fapi.binance.com/fapi/v1/ping"),
        ]
