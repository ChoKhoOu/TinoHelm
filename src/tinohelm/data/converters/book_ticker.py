"""bookTicker CSV → QuoteTick converter.

CSV schema (HAS header row, confirmed from sample):
  update_id, best_bid_price, best_bid_qty, best_ask_price,
  best_ask_qty, transaction_time, event_time
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {
    "best_bid_price", "best_bid_qty",
    "best_ask_price", "best_ask_qty", "transaction_time",
}


@register("bookTicker")
class BookTickerConverter:
    """Converts Binance Vision bookTicker CSV to NT QuoteTick."""

    supports_chunked = True

    def validate_schema(self, df: pd.DataFrame) -> None:
        if hasattr(df.columns[0], 'lower'):
            missing = _REQUIRED_COLUMNS - set(df.columns)
            if missing:
                raise SchemaError(
                    f"bookTicker CSV missing columns: {missing}. "
                    f"Got: {list(df.columns)}"
                )
        elif len(df.columns) < 7:
            raise SchemaError(
                f"bookTicker CSV requires at least 7 columns, "
                f"got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        from nautilus_trader.model.data import QuoteTick

        inst_id = instrument.id
        ticks: list[QuoteTick] = []

        for row in df.itertuples(index=False):
            ts_ns = int(row.transaction_time) * 1_000_000
            ticks.append(QuoteTick(
                instrument_id=inst_id,
                bid_price=instrument.make_price(
                    float(row.best_bid_price),
                ),
                ask_price=instrument.make_price(
                    float(row.best_ask_price),
                ),
                bid_size=instrument.make_qty(
                    float(row.best_bid_qty),
                ),
                ask_size=instrument.make_qty(
                    float(row.best_ask_qty),
                ),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d bookTicker rows to %d QuoteTick",
            len(df), len(ticks),
        )
        return ticks

    def convert_chunk(
        self, chunk: pd.DataFrame, instrument: Any, **kwargs,
    ) -> list:
        return self.convert(chunk, instrument, **kwargs)
