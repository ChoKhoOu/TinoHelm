"""Fetch real instrument definitions from Binance Futures exchangeInfo API.

Builds proper NautilusTrader ``CryptoPerpetual`` objects with real exchange
parameters (tick size, lot size, margin requirements, notional limits) instead
of hardcoded defaults.

Cache: ``~/.tino/data/instruments_cache.json`` with 24-hour TTL.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"

_CACHE_DIR = Path.home() / ".tino" / "data"
_CACHE_FILE = _CACHE_DIR / "instruments_cache.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Known NT currency constants (import once, map for fast lookup)
_KNOWN_CURRENCIES: dict[str, Any] | None = None


def _get_known_currencies() -> dict[str, Any]:
    """Lazily load known NT currency objects."""
    global _KNOWN_CURRENCIES  # noqa: PLW0603
    if _KNOWN_CURRENCIES is not None:
        return _KNOWN_CURRENCIES

    from nautilus_trader.model.currencies import (
        BNB,
        BTC,
        ETH,
        SOL,
        USD,
        USDT,
        XRP,
    )

    _KNOWN_CURRENCIES = {
        "BTC": BTC,
        "ETH": ETH,
        "SOL": SOL,
        "BNB": BNB,
        "XRP": XRP,
        "USDT": USDT,
        "USD": USD,
    }
    return _KNOWN_CURRENCIES


def _resolve_currency(name: str):
    """Resolve a currency string to an NT Currency object.

    Uses pre-defined constants for well-known currencies and falls back
    to ``Currency.from_str()`` for exotic ones.
    """
    known = _get_known_currencies()
    currency = known.get(name)
    if currency is not None:
        return currency

    from nautilus_trader.model.currencies import Currency

    logger.debug("Resolving unknown currency via Currency.from_str: %s", name)
    return Currency.from_str(name)


# ---------------------------------------------------------------------------
# Public utilities
# ---------------------------------------------------------------------------

def strip_to_binance_api_symbol(symbol: str) -> str:
    """Strip NT/TinoHelm decorations to get the plain Binance API symbol.

    Handles:
    - ``BTCUSDT-PERP.BINANCE`` -> ``BTCUSDT``
    - ``BTCUSDT-PERP`` -> ``BTCUSDT``
    - ``ETHUSDT-SWAP`` -> ``ETHUSDT``
    - ``SOLUSDT-LINEAR`` -> ``SOLUSDT``
    - ``BTCUSDT-SPOT`` -> ``BTCUSDT``
    - ``BTCUSDT`` -> ``BTCUSDT`` (no-op)
    """
    s = symbol
    # Remove venue suffix
    if s.endswith(".BINANCE"):
        s = s[: -len(".BINANCE")]
    # Remove instrument type suffixes
    for suffix in ("-PERP", "-SWAP", "-SPOT", "-LINEAR"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def _precision_from_string(value_str: str) -> int:
    """Derive decimal precision from a numeric string.

    Examples::

        "0.10"      -> 1
        "0.001"     -> 3
        "1"         -> 0
        "0.00010000"-> 4
        "10"        -> 0
    """
    # Normalize: strip trailing zeros after decimal point
    if "." not in value_str:
        return 0
    # Remove trailing zeros
    stripped = value_str.rstrip("0")
    if stripped.endswith("."):
        return 0
    return len(stripped.split(".")[1])


# ---------------------------------------------------------------------------
# Exchange info fetching & caching
# ---------------------------------------------------------------------------

def fetch_exchange_info(testnet: bool = False) -> dict:
    """Fetch ``/fapi/v1/exchangeInfo`` from Binance Futures.

    Results are cached to ``~/.tino/data/instruments_cache.json`` with a
    24-hour TTL.  On cache hit within TTL the file is read instead of
    calling the API.

    Returns the raw JSON response dict.
    """
    # --- Check cache ---
    if _CACHE_FILE.exists():
        try:
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            fetched_at = raw.get("fetched_at", 0)
            if time.time() - fetched_at < _CACHE_TTL_SECONDS:
                logger.debug(
                    "Using cached exchangeInfo (age %.0f s)",
                    time.time() - fetched_at,
                )
                return raw
            logger.debug("exchangeInfo cache expired, refetching")
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Corrupt instruments cache, refetching")

    # --- Fetch from API ---
    import httpx

    base = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
    url = f"{base}/fapi/v1/exchangeInfo"

    logger.info("Fetching exchangeInfo from %s", url)
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data: dict = resp.json()
    except Exception:
        logger.exception("Failed to fetch exchangeInfo from %s", url)
        raise

    # --- Write cache (atomic: write to temp file, then rename) ---
    data["fetched_at"] = time.time()
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(_CACHE_FILE.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, str(_CACHE_FILE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.debug("Wrote exchangeInfo cache to %s", _CACHE_FILE)
    except OSError:
        logger.warning("Could not write instruments cache to %s", _CACHE_FILE)

    return data


def _find_symbol_info(
    exchange_info: dict,
    binance_symbol: str,
) -> dict | None:
    """Find a symbol entry in the exchangeInfo response.

    Only returns ``contractType == "PERPETUAL"`` symbols.
    """
    for sym in exchange_info.get("symbols", []):
        if (
            sym.get("symbol") == binance_symbol
            and sym.get("contractType") == "PERPETUAL"
        ):
            return sym
    return None


def _extract_filter(
    filters: list[dict],
    filter_type: str,
) -> dict | None:
    """Extract a single filter dict by ``filterType``."""
    for f in filters:
        if f.get("filterType") == filter_type:
            return f
    return None


def _normalize_value_str(value: str, precision: int) -> str:
    """Normalize a numeric string to exactly *precision* decimal places.

    Binance may return ``"0.10000000"`` but NT expects a string whose
    decimal places match the declared precision.
    """
    if "." not in value:
        if precision == 0:
            return value
        return value + "." + "0" * precision

    integer_part, frac = value.split(".", 1)
    # Strip trailing zeros, then pad to at least *precision*
    frac_stripped = frac.rstrip("0") or "0"
    if precision == 0:
        return integer_part
    # Use the greater of the stripped length and declared precision
    target = max(len(frac_stripped), precision)
    return f"{integer_part}.{frac[:target]}"


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def make_instrument(
    symbol: str,
    *,
    use_cache: bool = True,
    maker_fee: Decimal = Decimal("0.000200"),
    taker_fee: Decimal = Decimal("0.000400"),
    testnet: bool = False,
):
    """Create a NautilusTrader ``CryptoPerpetual`` with real Binance parameters.

    Parameters
    ----------
    symbol
        Any of ``"BTCUSDT-PERP"``, ``"BTCUSDT-PERP.BINANCE"``,
        ``"ETHUSDT-PERP"``, etc.
    use_cache
        If ``True`` (default), use the 24 h file cache for exchangeInfo.
    maker_fee / taker_fee
        Account-level fee rates (not in exchangeInfo).  Defaults match
        Binance VIP-0 futures fees.
    testnet
        Fetch from testnet API instead of production.

    Returns
    -------
    CryptoPerpetual
    """
    from nautilus_trader.model.identifiers import (
        InstrumentId,
        Symbol as NTSymbol,
        Venue,
    )
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity

    # --- Resolve symbol strings ---
    nt_symbol_str = symbol.replace(".BINANCE", "")  # e.g. "BTCUSDT-PERP"
    binance_api_sym = strip_to_binance_api_symbol(symbol)  # e.g. "BTCUSDT"

    instrument_id = InstrumentId(
        symbol=NTSymbol(nt_symbol_str),
        venue=Venue("BINANCE"),
    )

    # --- Attempt to fetch real parameters ---
    sym_info: dict | None = None
    try:
        if use_cache:
            exchange_info = fetch_exchange_info(testnet=testnet)
        else:
            # Bypass the file cache but still call the API
            import httpx

            base = BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_BASE
            url = f"{base}/fapi/v1/exchangeInfo"
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                exchange_info = resp.json()

        sym_info = _find_symbol_info(exchange_info, binance_api_sym)
        if sym_info is None:
            logger.warning(
                "Symbol %s not found in exchangeInfo (or not PERPETUAL). "
                "Falling back to defaults.",
                binance_api_sym,
            )
    except Exception:
        logger.warning(
            "Failed to fetch exchangeInfo for %s. Falling back to defaults.",
            binance_api_sym,
            exc_info=True,
        )

    if sym_info is not None:
        return _build_from_exchange_info(
            sym_info=sym_info,
            instrument_id=instrument_id,
            nt_symbol_str=nt_symbol_str,
            binance_api_sym=binance_api_sym,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
        )

    # --- Fallback: hardcoded defaults (mirrors catalog._make_instrument) ---
    return _build_fallback(
        instrument_id=instrument_id,
        nt_symbol_str=nt_symbol_str,
        binance_api_sym=binance_api_sym,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def _build_from_exchange_info(
    sym_info: dict,
    instrument_id,
    nt_symbol_str: str,
    binance_api_sym: str,
    maker_fee: Decimal,
    taker_fee: Decimal,
):
    """Build a CryptoPerpetual from real exchangeInfo data."""
    from nautilus_trader.model.identifiers import Symbol as NTSymbol
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity

    filters = sym_info.get("filters", [])
    price_filter = _extract_filter(filters, "PRICE_FILTER") or {}
    lot_filter = _extract_filter(filters, "LOT_SIZE") or {}
    notional_filter = _extract_filter(filters, "MIN_NOTIONAL") or {}

    # --- Precision ---
    price_precision: int = sym_info.get("pricePrecision", 2)
    size_precision: int = sym_info.get("quantityPrecision", 3)

    # --- Price parameters ---
    tick_size_raw = price_filter.get("tickSize", "0.01")
    min_price_raw = price_filter.get("minPrice", "0.01")
    max_price_raw = price_filter.get("maxPrice", "1000000.00")

    tick_size_str = _normalize_value_str(tick_size_raw, price_precision)
    min_price_str = _normalize_value_str(min_price_raw, price_precision)
    max_price_str = _normalize_value_str(max_price_raw, price_precision)

    # --- Size parameters ---
    step_size_raw = lot_filter.get("stepSize", "0.001")
    min_qty_raw = lot_filter.get("minQty", "0.001")
    max_qty_raw = lot_filter.get("maxQty", "1000")

    step_size_str = _normalize_value_str(step_size_raw, size_precision)
    min_qty_str = _normalize_value_str(min_qty_raw, size_precision)
    max_qty_str = _normalize_value_str(max_qty_raw, size_precision)

    # --- Currencies ---
    base_asset = sym_info.get("baseAsset", binance_api_sym.replace("USDT", ""))
    quote_asset = sym_info.get("quoteAsset", "USDT")
    margin_asset = sym_info.get("marginAsset", quote_asset)

    base_currency = _resolve_currency(base_asset)
    quote_currency = _resolve_currency(quote_asset)
    settlement_currency = _resolve_currency(margin_asset)

    # --- Margin ---
    required_margin_pct = sym_info.get("requiredMarginPercent", "5.0000")
    maint_margin_pct = sym_info.get("maintMarginPercent", "2.5000")
    margin_init = Decimal(required_margin_pct) / Decimal("100")
    margin_maint = Decimal(maint_margin_pct) / Decimal("100")

    # --- Notional ---
    min_notional_val = notional_filter.get("notional", "5")
    min_notional = Money(float(min_notional_val), settlement_currency)

    logger.info(
        "Built instrument %s from exchangeInfo: price_prec=%d, size_prec=%d, "
        "tick=%s, step=%s, margin_init=%s, margin_maint=%s",
        instrument_id,
        price_precision,
        size_precision,
        tick_size_str,
        step_size_str,
        margin_init,
        margin_maint,
    )

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=NTSymbol(binance_api_sym),
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
        is_inverse=False,
        price_precision=price_precision,
        price_increment=Price.from_str(tick_size_str),
        size_precision=size_precision,
        size_increment=Quantity.from_str(step_size_str),
        max_quantity=Quantity.from_str(max_qty_str),
        min_quantity=Quantity.from_str(min_qty_str),
        max_notional=None,
        min_notional=min_notional,
        max_price=Price.from_str(max_price_str),
        min_price=Price.from_str(min_price_str),
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )


def _build_fallback(
    instrument_id,
    nt_symbol_str: str,
    binance_api_sym: str,
    maker_fee: Decimal,
    taker_fee: Decimal,
):
    """Build a CryptoPerpetual with hardcoded fallback defaults.

    Mirrors the precision table from ``catalog._make_instrument`` so that
    offline / error scenarios still produce usable instruments.
    """
    from nautilus_trader.model.identifiers import Symbol as NTSymbol
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity

    known = _get_known_currencies()
    usdt = known["USDT"]

    # Extract base from raw symbol (e.g. BTCUSDT -> BTC)
    base_str = binance_api_sym.replace("USDT", "")
    base_currency = _resolve_currency(base_str)

    # Hardcoded precision table (price_prec, size_prec, tick, step)
    _PRECISION: dict[str, tuple[int, int, str, str]] = {
        "BTC": (1, 3, "0.1", "0.001"),
        "ETH": (2, 3, "0.01", "0.001"),
        "SOL": (2, 0, "0.01", "1"),
        "BNB": (2, 2, "0.01", "0.01"),
        "XRP": (4, 0, "0.0001", "1"),
    }
    pp, sp, pi, si = _PRECISION.get(base_str, (2, 3, "0.01", "0.001"))

    logger.warning(
        "Using fallback defaults for %s (price_prec=%d, size_prec=%d)",
        instrument_id,
        pp,
        sp,
    )

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=NTSymbol(binance_api_sym),
        base_currency=base_currency,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=pp,
        price_increment=Price.from_str(pi),
        size_precision=sp,
        size_increment=Quantity.from_str(si),
        max_quantity=Quantity.from_str(
            "1000.000" if sp >= 3 else "10000" if sp == 0 else "1000.00",
        ),
        min_quantity=Quantity.from_str(si),
        max_notional=None,
        min_notional=Money(10.00, usdt),
        max_price=Price.from_str(
            "10000000.0" if pp == 1 else "1000000.00" if pp == 2 else "100000.0000",
        ),
        min_price=Price.from_str(pi),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )
