"""Field alias table for the declarative factor framework.

``FIELD_ALIAS`` maps user-facing short names (including Chinese aliases and
common abbreviations) to canonical field names used by the data layer.

Canonical names follow the actual column names in the Parquet catalog:
- Bar fields: ``open``, ``high``, ``low``, ``close``, ``volume``, ``amount``
- Derived bar fields: ``vwap``
- Funding rate: ``funding_rate``
- Open interest: ``open_interest``
- Order book: ``orderbook_imbalance``

``resolve_alias(name, custom=None) -> str`` is the public entry point.
Unknown names are returned as-is (pass-through semantics), so callers that
already use canonical names are unaffected.

Case handling
-------------
Resolution is **case-insensitive**: the input is lowercased before lookup.
The canonical name returned is always lowercase.

Custom overrides
----------------
Pass a ``dict[str, str]`` as ``custom`` to add or override mappings for a
single call.  Custom entries are also case-insensitive on the key side.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Master alias table
# ---------------------------------------------------------------------------

#: Maps alias → canonical field name.
#: All keys are lowercase.  Values are canonical lowercase field names used
#: by the DataLayer and NT Parquet catalog.
FIELD_ALIAS: dict[str, str] = {
    # ── close price ───────────────────────────────────────────────────────
    "close": "close",
    "收盘": "close",
    "收盘价": "close",
    "c": "close",
    "last": "close",
    "last_price": "close",

    # ── open price ────────────────────────────────────────────────────────
    "open": "open",
    "开盘": "open",
    "开盘价": "open",
    "o": "open",

    # ── high price ────────────────────────────────────────────────────────
    "high": "high",
    "最高": "high",
    "最高价": "high",
    "h": "high",

    # ── low price ─────────────────────────────────────────────────────────
    "low": "low",
    "最低": "low",
    "最低价": "low",
    "l": "low",

    # ── volume (contract/coin units) ──────────────────────────────────────
    "volume": "volume",
    "vol": "volume",
    "成交量": "volume",
    "v": "volume",

    # ── amount (quote currency turnover, i.e. dollar volume) ──────────────
    "amount": "amount",
    "turnover": "amount",
    "成交额": "amount",
    "quote_volume": "amount",
    "quoteqty": "amount",
    "quote_qty": "amount",

    # ── vwap ──────────────────────────────────────────────────────────────
    "vwap": "vwap",
    "成交均价": "vwap",
    "volume_weighted_price": "vwap",
    "vwap_price": "vwap",

    # ── funding rate ──────────────────────────────────────────────────────
    "funding_rate": "funding_rate",
    "funding": "funding_rate",
    "资金费率": "funding_rate",
    "fr": "funding_rate",

    # ── open interest ─────────────────────────────────────────────────────
    "open_interest": "open_interest",
    "oi": "open_interest",
    "持仓量": "open_interest",
    "openinterest": "open_interest",

    # ── orderbook imbalance (L1 bid/ask) ──────────────────────────────────
    "orderbook_imbalance": "orderbook_imbalance",
    "ob_imbalance": "orderbook_imbalance",
    "book_imbalance": "orderbook_imbalance",
    "obi": "orderbook_imbalance",
    "委托不平衡": "orderbook_imbalance",

    # ── quote tick fields (source="quote_tick" / bookTicker) ─────────────
    "bid_price": "bid_price",
    "bid_qty": "bid_qty",
    "ask_price": "ask_price",
    "ask_qty": "ask_qty",
    "bid": "bid_price",
    "ask": "ask_price",

    # ── trade tick fields (source="trade_tick" / aggTrades) ───────────────
    "trade_price": "trade_price",
    "trade_qty": "trade_qty",
    "trade_side": "trade_side",
    "taker_buy": "trade_side",

    # ── open interest variants (source="metrics" / OI endpoint) ──────────
    "sum_open_interest": "sum_open_interest",
    "open_interest_value": "open_interest_value",
    "oi_value": "open_interest_value",
    "sum_oi": "sum_open_interest",

    # ── mark price (source="funding_rate" settlement data) ────────────────
    "mark_price": "mark_price",
    "mark": "mark_price",

    # ── data source shortcuts (used in DataRequest.source) ────────────────
    "bar": "bar",
    "k线": "bar",
    "kline": "bar",
    "klines": "bar",
    "trade_tick": "trade_tick",
    "trade": "trade_tick",
    "aggtrade": "trade_tick",
    "aggtrades": "trade_tick",
    "quote_tick": "quote_tick",
    "book_ticker": "quote_tick",
    "bookticker": "quote_tick",
}


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

def resolve_alias(name: str, custom: dict[str, str] | None = None) -> str:
    """Resolve a field alias to its canonical name.

    Lookup is case-insensitive on the input side.  The returned canonical
    name is always lowercase.

    Parameters
    ----------
    name:
        User-supplied field name or alias (e.g. ``"Close"``, ``"收盘价"``,
        ``"vol"``).
    custom:
        Optional dict of additional/override aliases applied **before** the
        built-in table.  Keys are matched case-insensitively.

    Returns
    -------
    str
        Canonical field name.  If ``name`` (after lowercasing) is not found
        in either ``custom`` or ``FIELD_ALIAS``, the lowercased input is
        returned as-is.  This pass-through behaviour allows callers that
        already use canonical names to work without modification.

    Examples
    --------
    >>> resolve_alias("Close")
    'close'
    >>> resolve_alias("收盘价")
    'close'
    >>> resolve_alias("vol")
    'volume'
    >>> resolve_alias("my_custom_field")
    'my_custom_field'
    >>> resolve_alias("VOL", custom={"vol": "custom_volume"})
    'custom_volume'
    """
    lower = name.lower()

    # Custom overrides take priority
    if custom:
        custom_lower = {k.lower(): v for k, v in custom.items()}
        if lower in custom_lower:
            return custom_lower[lower]

    return FIELD_ALIAS.get(lower, lower)
