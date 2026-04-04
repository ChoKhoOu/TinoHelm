"""aggTrades CSV → TradeTick converter (chunk mode)."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

# Binance Vision aggTrades CSV columns (no header row)
_COLUMN_NAMES = [
    "agg_trade_id", "price", "quantity",
    "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]


@register("aggTrades")
class AggTradesConverter:
    """Converts Binance Vision aggTrades CSV (no header) to NT TradeTick objects."""

    supports_chunked = True

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 7:
            raise SchemaError(
                f"aggTrades CSV requires at least 7 columns, got {len(df.columns)}"
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
            ts_ns = int(row.transact_time) * 1_000_000  # ms → ns
            is_buyer_maker = bool(row.is_buyer_maker)
            ticks.append(TradeTick(
                instrument_id=inst_id,
                price=instrument.make_price(float(row.price)),
                size=instrument.make_qty(float(row.quantity)),
                aggressor_side=(
                    AggressorSide.SELLER if is_buyer_maker
                    else AggressorSide.BUYER
                ),
                trade_id=TradeId(str(int(row.agg_trade_id))),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d aggTrades rows to %d TradeTick objects", len(df), len(ticks)
        )
        return ticks

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
