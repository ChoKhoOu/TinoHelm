#!/usr/bin/env python3
"""
Emergency Flatten Script for TinoHelm
======================================
Standalone emergency tool to cancel all open orders and close all positions
on Binance Futures. Zero TinoHelm dependencies — only uses stdlib + requests.

Usage:
    python scripts/emergency_flatten.py
    python scripts/emergency_flatten.py --testnet
    python scripts/emergency_flatten.py --dry-run
    python scripts/emergency_flatten.py --testnet --dry-run

Environment variables required:
    BINANCE_API_KEY     — Binance API key
    BINANCE_API_SECRET  — Binance API secret
"""

import argparse
import hashlib
import hmac
import os
import sys
import time
from typing import Any
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Binance Futures API endpoints
# ---------------------------------------------------------------------------
PRODUCTION_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

ENDPOINT_OPEN_ORDERS = "/fapi/v1/openOrders"
ENDPOINT_CANCEL_ALL_ORDERS = "/fapi/v1/allOpenOrders"
ENDPOINT_POSITION_RISK = "/fapi/v2/positionRisk"
ENDPOINT_ORDER = "/fapi/v1/order"

# Request timeout in seconds
REQUEST_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

def _sign(secret: str, params: dict) -> str:
    """Return HMAC-SHA256 hex signature for the given parameter dict."""
    query_string = urlencode(params)
    signature = hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


def _timestamp() -> int:
    """Return current UTC time in milliseconds."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class BinanceFuturesClient:
    """Minimal Binance Futures REST client with HMAC-SHA256 signing."""

    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        import requests as _requests  # deferred so --help works without requests installed
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._requests = _requests
        self.session = _requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def _signed_get(self, endpoint: str, params: dict | None = None) -> Any:
        """Perform a signed GET request."""
        p = dict(params or {})
        p["timestamp"] = _timestamp()
        p["signature"] = _sign(self.api_secret, p)
        url = self.base_url + endpoint
        resp = self.session.get(url, params=p, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _signed_delete(self, endpoint: str, params: dict | None = None) -> Any:
        """Perform a signed DELETE request."""
        p = dict(params or {})
        p["timestamp"] = _timestamp()
        p["signature"] = _sign(self.api_secret, p)
        url = self.base_url + endpoint
        resp = self.session.delete(url, params=p, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _signed_post(self, endpoint: str, params: dict | None = None) -> Any:
        """Perform a signed POST request."""
        p = dict(params or {})
        p["timestamp"] = _timestamp()
        p["signature"] = _sign(self.api_secret, p)
        url = self.base_url + endpoint
        resp = self.session.post(url, params=p, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # --- High-level operations ---

    def get_open_orders(self) -> list[dict]:
        """Return all open orders across all symbols."""
        return self._signed_get(ENDPOINT_OPEN_ORDERS)

    def cancel_all_orders_for_symbol(self, symbol: str) -> dict:
        """Cancel all open orders for a single symbol."""
        return self._signed_delete(ENDPOINT_CANCEL_ALL_ORDERS, {"symbol": symbol})

    def get_positions(self) -> list[dict]:
        """Return all positions with non-zero quantity."""
        all_positions = self._signed_get(ENDPOINT_POSITION_RISK)
        return [p for p in all_positions if float(p["positionAmt"]) != 0.0]

    def close_position(self, symbol: str, position_amt: float) -> dict:
        """
        Close a position by placing a market order in the opposite direction.

        position_amt is positive for LONG, negative for SHORT.
        """
        if position_amt > 0:
            side = "SELL"
            quantity = position_amt
        else:
            side = "BUY"
            quantity = abs(position_amt)

        return self._signed_post(ENDPOINT_ORDER, {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "reduceOnly": "true",
        })


# ---------------------------------------------------------------------------
# Flatten logic
# ---------------------------------------------------------------------------

def cancel_all_orders(client: BinanceFuturesClient, dry_run: bool) -> int:
    """
    Cancel all open orders.

    Returns the number of symbols processed (cancellations attempted).
    """
    print("\n[1/2] Fetching open orders ...")
    try:
        open_orders = client.get_open_orders()
    except client._requests.RequestException as exc:
        print(f"  [ERROR] Failed to fetch open orders: {exc}")
        return 0

    if not open_orders:
        print("  No open orders found.")
        return 0

    # Group by symbol so we make one DELETE call per symbol
    symbols_with_orders: dict[str, list[dict]] = {}
    for order in open_orders:
        sym = order["symbol"]
        symbols_with_orders.setdefault(sym, []).append(order)

    print(f"  Found {len(open_orders)} open order(s) across "
          f"{len(symbols_with_orders)} symbol(s):")
    for sym, orders in symbols_with_orders.items():
        ids = [str(o["orderId"]) for o in orders]
        print(f"    {sym}: {len(orders)} order(s) — IDs {', '.join(ids)}")

    if dry_run:
        print("  [DRY-RUN] Would cancel all of the above orders.")
        return len(symbols_with_orders)

    cancelled_count = 0
    for sym in symbols_with_orders:
        try:
            result = client.cancel_all_orders_for_symbol(sym)
            count = result.get("code", None)
            # Binance returns {"code": 200, "msg": "The operation of cancel all open order is done."} on success
            if count == 200 or "msg" in result:
                print(f"  [OK] Cancelled all orders for {sym}: {result.get('msg', result)}")
                cancelled_count += 1
            else:
                print(f"  [WARN] Unexpected response for {sym}: {result}")
        except client._requests.RequestException as exc:
            print(f"  [ERROR] Failed to cancel orders for {sym}: {exc}")

    return cancelled_count


def close_all_positions(client: BinanceFuturesClient, dry_run: bool) -> int:
    """
    Close all open positions with market orders.

    Returns the number of positions where a close was attempted.
    """
    print("\n[2/2] Fetching open positions ...")
    try:
        positions = client.get_positions()
    except client._requests.RequestException as exc:
        print(f"  [ERROR] Failed to fetch positions: {exc}")
        return 0

    if not positions:
        print("  No open positions found.")
        return 0

    print(f"  Found {len(positions)} open position(s):")
    for pos in positions:
        sym = pos["symbol"]
        amt = float(pos["positionAmt"])
        entry = float(pos["entryPrice"])
        side = "LONG" if amt > 0 else "SHORT"
        print(f"    {sym}: {side} {abs(amt)} @ entry {entry}")

    if dry_run:
        print("  [DRY-RUN] Would close all of the above positions with MARKET orders.")
        return len(positions)

    closed_count = 0
    for pos in positions:
        sym = pos["symbol"]
        amt = float(pos["positionAmt"])
        side_label = "LONG" if amt > 0 else "SHORT"
        try:
            result = client.close_position(sym, amt)
            order_id = result.get("orderId", "?")
            status = result.get("status", "?")
            print(f"  [OK] Closed {side_label} position for {sym} — "
                  f"order {order_id}, status {status}")
            closed_count += 1
        except client._requests.RequestException as exc:
            # Log but continue — don't let one failure block remaining closes
            print(f"  [ERROR] Failed to close position for {sym}: {exc}")
            try:
                # Print the raw response body for debugging if available
                body = exc.response.text if hasattr(exc, "response") and exc.response else ""
                if body:
                    print(f"          Response: {body[:200]}")
            except Exception:
                pass

    return closed_count


def flatten(
    api_key: str,
    api_secret: str,
    base_url: str,
    dry_run: bool,
    skip_confirm: bool = False,
) -> None:
    """Main flatten routine: cancel orders then close positions."""
    env_label = "TESTNET" if "testnet" in base_url else "PRODUCTION"
    mode_label = " [DRY-RUN]" if dry_run else ""

    print("=" * 60)
    print(f"  EMERGENCY FLATTEN — {env_label}{mode_label}")
    print(f"  Endpoint: {base_url}")
    print("=" * 60)

    if dry_run:
        print("\n  DRY-RUN mode: no orders will be placed or cancelled.\n")

    if not dry_run and not skip_confirm:
        answer = input(f"\n  CONFIRM flatten on {env_label}? Type 'yes' to proceed: ")
        if answer.strip().lower() != "yes":
            print("  Aborted.")
            return

    client = BinanceFuturesClient(api_key, api_secret, base_url)

    # Step 1: Cancel all open orders first so they don't interfere with closes
    cancel_all_orders(client, dry_run)

    # Step 2: Close all open positions
    close_all_positions(client, dry_run)

    print("\n[DONE] Emergency flatten complete.")
    if dry_run:
        print("       Re-run without --dry-run to execute for real.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emergency flatten: cancel all open orders and close all positions "
            "on Binance Futures. Reads BINANCE_API_KEY and BINANCE_API_SECRET "
            "from environment variables."
        )
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        default=False,
        help="Use Binance Futures testnet instead of production.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be done without executing any API calls that modify state.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt (use in scripts or when you've already confirmed).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Parse args first so --help works even without requests installed.
    args = _parse_args()

    try:
        import requests  # noqa: F401 — verified here; actual use is deferred into BinanceFuturesClient
    except ImportError:
        print("[ERROR] 'requests' is not installed. Run: pip install requests")
        sys.exit(1)

    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    if not api_key or not api_secret:
        print("[ERROR] BINANCE_API_KEY and BINANCE_API_SECRET must be set in the environment.")
        sys.exit(1)

    base_url = TESTNET_BASE if args.testnet else PRODUCTION_BASE

    try:
        flatten(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            dry_run=args.dry_run,
            skip_confirm=args.yes,
        )
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Flatten aborted by user. Some positions may still be open!")
        sys.exit(1)
