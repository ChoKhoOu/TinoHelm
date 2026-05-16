"""Tests for the bookTicker converter (formerly a stub, now fully implemented)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tinohelm.data.converters import get_converter, SchemaError


class TestConverterRegistration:
    def test_book_ticker_registered(self):
        c = get_converter("bookTicker")
        assert c is not None

    def test_book_ticker_has_supports_chunked(self):
        c = get_converter("bookTicker")
        assert isinstance(c.supports_chunked, bool)


# --- bookTicker ---

class TestBookTickerConverter:
    def test_schema_too_few_columns(self):
        c = get_converter("bookTicker")
        df = pd.DataFrame([[1, 2, 3]])
        with pytest.raises(SchemaError):
            c.validate_schema(df)

    def test_valid_schema(self):
        c = get_converter("bookTicker")
        # 7 columns: update_id, bid_price, bid_qty, ask_price, ask_qty, txn_time, event_time
        df = pd.DataFrame([
            [2849995378056, "42000.0", "1.5", "42001.0", "2.0",
             1704067200000, 1704067200005],
        ])
        c.validate_schema(df)  # should not raise
