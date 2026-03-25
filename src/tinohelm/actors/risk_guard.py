"""RiskGuardActor — cross-strategy portfolio risk overlay.

Monitors daily PnL, max drawdown, total exposure, and position count.
Communicates with strategies exclusively via NT msgbus.

Risk checks are triggered by a fixed-interval timer (default 10s) rather
than bar events, ensuring checks fire even during market-quiet periods or
with low-frequency bar data (e.g. 4h/1d bars).

This Actor is fully optional — strategies work without it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

import pandas as pd
from nautilus_trader.common.actor import Actor, ActorConfig

from tinohelm.data.instruments import _resolve_currency
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

    # Risk check interval in seconds (decoupled from bar frequency)
    check_interval_secs: int = 10

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
        self._check_interval_secs = config.check_interval_secs

        # NT objects resolved in on_start() (not available in __init__)
        self._venue = None
        self._currency_obj = None
        # RiskEngine reference — set externally by BacktestRunner for direct
        # TradingState enforcement without LifecycleController.
        self._risk_engine = None

        # State tracking
        self._peak_equity: float = config.starting_balance
        self._day_start_equity: float = config.starting_balance
        self._current_day: int | None = None  # day-of-year for boundary detection
        self._breached: bool = False
        self._breach_reason: str = ""

    _TIMER_NAME: str = "risk_guard_check"

    def on_start(self) -> None:
        """Initialize equity tracking and start periodic risk-check timer."""
        from nautilus_trader.model.identifiers import Venue

        # Resolve NT objects once
        self._venue = Venue(self._venue_name)
        self._currency_obj = _resolve_currency(self._currency_str)

        # Fixed-interval timer — fires every N seconds regardless of bar arrival.
        # This ensures risk checks happen even between 4h/1d bars or during
        # market-quiet periods.
        self.clock.set_timer(
            name=self._TIMER_NAME,
            interval=pd.Timedelta(seconds=self._check_interval_secs),
        )

        equity = self._get_equity()
        if equity > 0:
            self._peak_equity = equity
            self._day_start_equity = equity
        logger.info(
            "RiskGuardActor started: equity=%.2f, breach_action=%s, check_interval=%ds",
            self._peak_equity, self._breach_action.value,
            self._check_interval_secs,
        )

    def on_event(self, event) -> None:
        """Handle timer events for periodic risk checks."""
        from nautilus_trader.common.events import TimeEvent

        if not isinstance(event, TimeEvent):
            return

        # Detect day boundary via event timestamp (UTC)
        event_ns = event.ts_event
        event_date = datetime.fromtimestamp(event_ns / 1e9, tz=timezone.utc)
        day_of_year = event_date.timetuple().tm_yday + event_date.year * 1000

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

    def _get_equity(self) -> float:
        """Get current portfolio equity: balance + unrealized PnL.

        ``balance_total`` may not include unrealized PnL under certain account
        models.  We explicitly add the sum of unrealized PnLs from the
        Portfolio API to ensure accurate equity calculation.
        """
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return self._starting_balance

            balance = account.balance_total(self._currency_obj)
            if balance is None:
                return self._starting_balance

            equity = float(balance.as_double())

            # Add unrealized PnL from all open positions
            try:
                unrealized_pnls = self.portfolio.unrealized_pnls(self._venue)
                if unrealized_pnls:
                    for _currency, pnl_money in unrealized_pnls.items():
                        equity += float(pnl_money.as_double())
            except Exception:
                pass  # Fallback to balance-only if unrealized_pnls fails

            return equity
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
        # Breach when exceeding max, not at exactly max
        if self._max_positions is not None:
            position_count = len(self.portfolio.positions_open())
            if position_count > self._max_positions:
                logger.warning(
                    "RISK BREACH: positions %d > limit %d",
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
        """Publish breach action to msgbus and enforce via RiskEngine if available."""
        self._breached = True
        self._breach_reason = reason
        action = self._breach_action.value

        logger.warning(
            "RiskGuardActor: publishing breach action '%s' (reason: %s)",
            action, reason,
        )

        # Publish breach state to msgbus (for LifecycleController / strategies)
        self.msgbus.publish(RISK_GUARD_STATE, action)

        # Direct RiskEngine enforcement — works even without LifecycleController
        # (e.g. pure backtest mode). This is the authoritative system-level block.
        if self._risk_engine is not None:
            try:
                from nautilus_trader.model.enums import TradingState

                if self._breach_action == BreachAction.HALT_NEW:
                    self._risk_engine.set_trading_state(TradingState.HALTED)
                    logger.warning("RiskEngine: TradingState set to HALTED")
                elif self._breach_action == BreachAction.REDUCE_ONLY:
                    self._risk_engine.set_trading_state(TradingState.REDUCING)
                    logger.warning("RiskEngine: TradingState set to REDUCING")
                elif self._breach_action == BreachAction.FLATTEN_ALL:
                    self._risk_engine.set_trading_state(TradingState.HALTED)
                    logger.warning("RiskEngine: TradingState set to HALTED (flatten_all)")
            except Exception as e:
                logger.warning("Failed to set TradingState on RiskEngine: %s", e)

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
