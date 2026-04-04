"""trades CSV → TradeTick converter (chunk mode)."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

# Binance Vision trades CSV columns (no header row)
# id, price, qty, quoteQty, time, isBuyerMaker
_COLUMN_NAMES = ["id", "price", "qty", "quote_qty", "time", "is_buyer_maker"]


@register("trades")
class TradesConverter:
    """Converts Binance Vision trades CSV (no header) to NT TradeTick objects."""

    supports_chunked = True

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 6:
            raise SchemaError(
                f"trades CSV requires at least 6 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        from nautilus_trader.model.data import TradeTick
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import TradeId

        # Assign column names if integer columns (no-header read)
        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        inst_id = instrument.id
        ticks: list[TradeTick] = []

        for row in df.itertuples(index=False):
            ts_ns = int(row.time) * 1_000_000  # ms → ns
            is_buyer_maker = bool(row.is_buyer_maker)
            ticks.append(TradeTick(
                instrument_id=inst_id,
                price=instrument.make_price(float(row.price)),
                size=instrument.make_qty(float(row.qty)),
                aggressor_side=AggressorSide.SELLER if is_buyer_maker else AggressorSide.BUYER,
                trade_id=TradeId(str(int(row.id))),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d trades rows to %d TradeTick objects", len(df), len(ticks)
        )
        return ticks

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
