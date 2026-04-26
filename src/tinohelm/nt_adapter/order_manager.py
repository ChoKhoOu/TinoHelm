"""Translate target-weight diffs into NT MarketOrder submissions.

Given a current cross-section position vector (sourced from
``portfolio.net_position(instrument_id)``) and a target weight dict from
the signal kernel, :class:`OrderManager` computes ``Δqty`` per symbol and
submits the necessary ``BUY``/``SELL`` market orders.  The two
non-negotiable invariants are:

1. **Quantity rounding via ``instrument.make_qty(...)``** — never hand
   a raw float (or :class:`Quantity` constructed from one) to
   ``order_factory``.  The exchange-supplied lot-size and precision are
   embedded in the ``Instrument`` object and ``make_qty`` is the only
   correct way to honour them (CLAUDE.md NT pitfall: "Always use
   ``instrument.make_qty()`` … direct ``Quantity`` creation risks
   RiskEngine denial.").  AC-4.2.1 explicitly verifies this.
2. **Below-min-qty guard** — orders smaller than the instrument's
   minimum tradable quantity are silently dropped to avoid endless
   rejections.  We use ``instrument.size_increment`` (the lot-size
   step) as a conservative lower bound.

Position arithmetic
-------------------
NT's ``OmsType.HEDGING`` mode allows multiple simultaneous
long/short positions per instrument.  Aggregating signed quantity from
``cache.positions_open()`` is brittle (must inspect ``pos.side`` for
each).  We use ``portfolio.net_position(instrument_id)`` which returns
a single signed :class:`decimal.Decimal` regardless of OMS type — this
is the canonical way per NT docs.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from nautilus_trader.model.identifiers import InstrumentId

logger = logging.getLogger(__name__)


class OrderManager:
    """Compute target-weight diffs and submit NT market orders.

    Parameters
    ----------
    strategy:
        The owning :class:`~nautilus_trader.trading.strategy.Strategy`
        instance (or any object exposing ``order_factory``,
        ``submit_order``, ``portfolio``, ``cache``, and ``log``).  We
        accept ``Any`` to keep this module unit-testable with stubs.
    """

    def __init__(self, strategy: Any) -> None:
        self.strategy = strategy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_diff(
        self,
        target_weights: dict[str, float],
        instruments: dict[str, Any],
        equity: float,
        prices: dict[str, float] | None = None,
    ) -> list[Any]:
        """Submit the orders that move current → target portfolio.

        Parameters
        ----------
        target_weights:
            ``{symbol_short: target_weight}`` — symbol keys must match
            the keys of ``instruments`` (e.g. ``"BTCUSDT-PERP"``).
            ``target_weight`` is a float fraction; positive = long,
            negative = short.
        instruments:
            ``{symbol_short: Instrument}`` providing ``make_qty``,
            ``id``, ``size_increment``.
        equity:
            Account equity in quote currency.  Used to convert
            ``weight × equity / price`` to a target quantity.
        prices:
            Optional ``{symbol_short: latest_close_price}`` map.  When
            absent we fall back to ``cache.bar(...).close``.  Tests
            inject this explicitly to avoid stubbing ``cache.bar``.

        Returns
        -------
        list[Any]
            Submitted order objects (also forwarded to
            ``strategy.submit_order``).  Returned for test assertions.
        """
        # Lazy NT imports — keeps the module importable in unit tests.
        from nautilus_trader.model.enums import OrderSide, TimeInForce

        prices = prices or {}
        submitted: list[Any] = []

        for symbol, target_w in target_weights.items():
            instrument = instruments.get(symbol)
            if instrument is None:
                logger.debug(
                    "OrderManager.execute_diff: skip %s — no instrument", symbol
                )
                continue

            price = prices.get(symbol)
            if price is None:
                price = self._get_current_price(instrument.id)
            if price is None or price <= 0:
                logger.debug(
                    "OrderManager.execute_diff: skip %s — no positive price (got %r)",
                    symbol,
                    price,
                )
                continue

            current_qty = self._get_current_qty(instrument.id)
            target_qty = float(target_w) * float(equity) / float(price)
            diff_qty = target_qty - current_qty
            abs_diff = abs(diff_qty)

            min_step = self._min_qty_step(instrument)
            if abs_diff < min_step:
                logger.debug(
                    "OrderManager.execute_diff: skip %s — diff %.8f < min_step %.8f",
                    symbol,
                    abs_diff,
                    min_step,
                )
                continue

            side = OrderSide.BUY if diff_qty > 0 else OrderSide.SELL
            qty = instrument.make_qty(abs_diff)
            order = self.strategy.order_factory.market(
                instrument_id=instrument.id,
                order_side=side,
                quantity=qty,
                time_in_force=TimeInForce.GTC,
            )
            self.strategy.submit_order(order)
            submitted.append(order)

        return submitted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_current_qty(self, instrument_id: "InstrumentId") -> float:
        """Return the signed net-position quantity for *instrument_id*.

        Uses ``portfolio.net_position`` which works uniformly across NT's
        NETTING and HEDGING OMS modes.  Returns 0.0 when no position is
        open.
        """
        portfolio = getattr(self.strategy, "portfolio", None)
        if portfolio is None:
            return 0.0
        try:
            net = portfolio.net_position(instrument_id)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "OrderManager._get_current_qty: portfolio.net_position failed: %s",
                exc,
            )
            return 0.0
        if net is None:
            return 0.0
        return float(Decimal(net))

    def _get_current_price(self, instrument_id: "InstrumentId") -> float | None:
        """Get the latest close price for ``instrument_id`` from cache.

        Iterates the cache's bar types and picks the first one matching
        the requested instrument_id.  Returns ``None`` if no bar is
        available.
        """
        cache = getattr(self.strategy, "cache", None)
        if cache is None:
            return None
        try:
            for bar_type in cache.bar_types():
                if bar_type.instrument_id == instrument_id:
                    bar = cache.bar(bar_type)
                    if bar is None:
                        continue
                    return float(bar.close)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "OrderManager._get_current_price failed for %s: %s",
                instrument_id,
                exc,
            )
        return None

    @staticmethod
    def _min_qty_step(instrument: Any) -> float:
        """Best-effort minimum order increment for ``instrument``.

        Prefers ``instrument.size_increment`` (a :class:`Quantity` on
        real NT instruments).  Falls back to ``1 / 10**size_precision``
        and finally to a tiny epsilon when neither is available.
        """
        increment = getattr(instrument, "size_increment", None)
        if increment is not None:
            try:
                return float(increment)
            except (TypeError, ValueError):
                pass
        precision = getattr(instrument, "size_precision", None)
        if precision is not None:
            try:
                return 10 ** -int(precision)
            except (TypeError, ValueError):
                pass
        return 1e-9
