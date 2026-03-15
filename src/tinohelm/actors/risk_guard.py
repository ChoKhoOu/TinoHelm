"""RiskGuardActor — cross-strategy portfolio risk overlay.

Monitors daily PnL, max drawdown, total exposure, and position count.
Communicates with strategies exclusively via NT msgbus.

This Actor is fully optional — strategies work without it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

from nautilus_trader.common.actor import Actor, ActorConfig
from nautilus_trader.model.data import Bar

from tinohelm.node.topics import RISK_GUARD_FLATTEN, RISK_GUARD_STATE

logger = logging.getLogger(__name__)


class BreachAction(str, Enum):
    """Configurable action when a risk limit is breached."""

    REDUCE_ONLY = "reduce_only"
    HALT_NEW = "halt_new"
    FLATTEN_ALL = "flatten_all"


class RiskGuardConfig(ActorConfig, frozen=True):
    """Configuration for RiskGuardActor.

    All thresholds are optional — only enabled limits are checked.
    """

    # Daily PnL stop loss (negative fraction, e.g. -0.02 = -2%)
    daily_stop_loss_pct: float | None = None

    # Max drawdown from HWM (negative fraction, e.g. -0.10 = -10%)
    max_drawdown_pct: float | None = None

    # Max total exposure in account currency (e.g. 100000.0)
    max_total_exposure: float | None = None

    # Max open positions across all instruments
    max_positions: int | None = None

    # Action to take on breach
    breach_action: str = BreachAction.REDUCE_ONLY.value

    # Account settings for equity calculation
    venue_name: str = "BINANCE"
    currency: str = "USDT"
    starting_balance: float = 10000.0


class RiskGuardActor(Actor):
    """Portfolio-level risk guard that publishes state via msgbus.

    Monitors:
    - Daily PnL with UTC 00:00 day boundary
    - Max drawdown from high-water mark (HWM)
    - Total exposure across all instruments (via NT Portfolio API)
    - Open position count

    Publishes to:
    - ``risk.guard.state``: breach action string when a limit is breached
    - ``risk.guard.flatten``: instrument IDs when flatten_all is triggered
    """

    def __init__(self, config: RiskGuardConfig) -> None:
        super().__init__(config)

        self._venue_name = config.venue_name
        self._currency_str = config.currency
        self._starting_balance = config.starting_balance

        # Thresholds
        self._daily_stop_loss_pct = config.daily_stop_loss_pct
        self._max_drawdown_pct = config.max_drawdown_pct
        self._max_total_exposure = config.max_total_exposure
        self._max_positions = config.max_positions
        self._breach_action = BreachAction(config.breach_action)

        # NT objects resolved in on_start() (not available in __init__)
        self._venue = None
        self._currency_obj = None

        # State tracking
        self._peak_equity: float = config.starting_balance
        self._day_start_equity: float = config.starting_balance
        self._current_day: int | None = None  # day-of-year for boundary detection
        self._breached: bool = False
        self._breach_reason: str = ""

    def on_start(self) -> None:
        """Initialize equity tracking and subscribe to all bar types."""
        from nautilus_trader.model.identifiers import Venue

        # Resolve NT objects once
        self._venue = Venue(self._venue_name)
        self._currency_obj = self._resolve_currency(self._currency_str)

        # Subscribe to every bar type the engine has loaded so on_bar() fires
        for bar_type in self.cache.bar_types():
            self.subscribe_bars(bar_type)

        equity = self._get_equity()
        if equity > 0:
            self._peak_equity = equity
            self._day_start_equity = equity
        logger.info(
            "RiskGuardActor started: equity=%.2f, breach_action=%s, bar_subscriptions=%d",
            self._peak_equity, self._breach_action.value,
            len(self.cache.bar_types()),
        )

    def on_bar(self, bar: Bar) -> None:
        """Check risk limits on every bar event."""
        # Detect day boundary via bar.ts_event (UTC)
        bar_dt = bar.ts_event  # nanosecond timestamp
        bar_date = datetime.fromtimestamp(bar_dt / 1e9, tz=timezone.utc)
        day_of_year = bar_date.timetuple().tm_yday + bar_date.year * 1000

        if self._current_day is None:
            self._current_day = day_of_year
        elif day_of_year != self._current_day:
            # Day boundary crossed — reset daily tracking
            self._current_day = day_of_year
            equity = self._get_equity()
            if equity > 0:
                self._day_start_equity = equity
            # Reset daily PnL breach (drawdown/exposure breaches remain permanent)
            if self._breached and self._breach_reason == "daily_pnl":
                self._breached = False
                self._breach_reason = ""
                logger.info("Day boundary: clearing daily PnL breach flag")
            logger.debug("Day boundary: reset day_start_equity=%.2f", self._day_start_equity)

        # Run all risk checks
        self._check_risks()

    @staticmethod
    def _resolve_currency(currency_str: str):
        """Resolve a currency string to an NT Currency object."""
        from nautilus_trader.model.currencies import USDT, USD, BTC, ETH

        _CURRENCY_MAP = {
            "USDT": USDT, "USD": USD, "BTC": BTC, "ETH": ETH,
        }
        currency = _CURRENCY_MAP.get(currency_str)
        if currency is None:
            logger.warning("Unknown currency: %s, falling back to USDT", currency_str)
            return USDT
        return currency

    def _get_equity(self) -> float:
        """Get current portfolio equity including unrealized PnL via NT Portfolio API."""
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return self._starting_balance

            balance = account.balance_total(self._currency_obj)
            if balance is None:
                return self._starting_balance

            return float(balance.as_double())
        except Exception as e:
            logger.debug("Could not get equity: %s", e)
            return self._starting_balance

    def _check_risks(self) -> None:
        """Run all configured risk checks and publish breach if triggered."""
        if self._breached:
            return  # Already breached, don't re-publish

        equity = self._get_equity()
        if equity <= 0:
            return

        # Update HWM
        if equity > self._peak_equity:
            self._peak_equity = equity

        # 1. Daily PnL check
        if self._daily_stop_loss_pct is not None and self._day_start_equity > 0:
            daily_return = (equity - self._day_start_equity) / self._day_start_equity
            if daily_return <= self._daily_stop_loss_pct:
                logger.warning(
                    "RISK BREACH: daily return %.4f <= limit %.4f",
                    daily_return, self._daily_stop_loss_pct,
                )
                self._trigger_breach("daily_pnl")
                return

        # 2. Max drawdown check
        if self._max_drawdown_pct is not None and self._peak_equity > 0:
            drawdown = (equity - self._peak_equity) / self._peak_equity
            if drawdown <= self._max_drawdown_pct:
                logger.warning(
                    "RISK BREACH: drawdown %.4f <= limit %.4f",
                    drawdown, self._max_drawdown_pct,
                )
                self._trigger_breach("max_drawdown")
                return

        # 3. Total exposure check
        if self._max_total_exposure is not None:
            total_exposure = self._calc_total_exposure()
            if total_exposure > self._max_total_exposure:
                logger.warning(
                    "RISK BREACH: exposure %.2f > limit %.2f",
                    total_exposure, self._max_total_exposure,
                )
                self._trigger_breach("total_exposure")
                return

        # 4. Position count check
        if self._max_positions is not None:
            position_count = len(self.portfolio.positions_open())
            if position_count >= self._max_positions:
                logger.warning(
                    "RISK BREACH: positions %d >= limit %d",
                    position_count, self._max_positions,
                )
                self._trigger_breach("position_count")
                return

    def _calc_total_exposure(self) -> float:
        """Get total net exposure via NT Portfolio API.

        Uses ``portfolio.net_exposures(venue)`` which returns a
        ``dict[Currency, Money]`` with proper currency handling.
        """
        exposures = self.portfolio.net_exposures(self._venue)
        if not exposures:
            return 0.0
        total = 0.0
        for _currency, money in exposures.items():
            total += abs(float(money.as_double()))
        return total

    def _trigger_breach(self, reason: str) -> None:
        """Publish breach action to msgbus."""
        self._breached = True
        self._breach_reason = reason
        action = self._breach_action.value

        logger.warning(
            "RiskGuardActor: publishing breach action '%s' (reason: %s)",
            action, reason,
        )

        # Publish breach state to msgbus
        self.msgbus.publish(RISK_GUARD_STATE, action)

        # If flatten_all, also publish each instrument for flattening
        if self._breach_action == BreachAction.FLATTEN_ALL:
            self._publish_flatten_all()

    def _publish_flatten_all(self) -> None:
        """Publish instrument IDs of all open positions for flattening."""
        for position in self.portfolio.positions_open():
            instrument_id = str(position.instrument_id)
            self.msgbus.publish(RISK_GUARD_FLATTEN, instrument_id)
            logger.warning("RiskGuardActor: flatten instrument %s", instrument_id)

    def on_stop(self) -> None:
        """Log final state on stop."""
        equity = self._get_equity()
        logger.info(
            "RiskGuardActor stopped: equity=%.2f, peak=%.2f, breached=%s",
            equity, self._peak_equity, self._breached,
        )
