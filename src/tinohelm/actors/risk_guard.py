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

    # Portfolio isolation: only monitor strategies with this tag prefix.
    # If None, monitors ALL positions (backward compatible).
    strategy_name: str | None = None
    strategy_tag_prefix: str | None = None

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

        self._strategy_name = config.strategy_name
        self._strategy_tag_prefix = config.strategy_tag_prefix

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

    def _is_my_position(self, position) -> bool:
        """Check if a position belongs to this RiskGuard's portfolio."""
        if self._strategy_tag_prefix is None:
            return True  # No filtering — monitor everything (backward compat)
        strategy_id = str(position.strategy_id)
        return self._strategy_tag_prefix in strategy_id

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
            "RiskGuardActor started: portfolio=%s, equity=%.2f, breach_action=%s, check_interval=%ds",
            self._strategy_name or "ALL", self._peak_equity,
            self._breach_action.value, self._check_interval_secs,
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
        equity = self._get_equity()
        if equity <= 0:
            return

        # Update HWM
        if equity > self._peak_equity:
            self._peak_equity = equity

        # ALWAYS publish metrics (before any breach early-return)
        self._publish_metrics(equity)

        if self._breached:
            return  # Already breached, don't re-check

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
            position_count = sum(
                1 for p in self.cache.positions_open() if self._is_my_position(p)
            )
            if position_count > self._max_positions:
                logger.warning(
                    "RISK BREACH: positions %d > limit %d",
                    position_count, self._max_positions,
                )
                self._trigger_breach("position_count")
                return

    def _publish_metrics(self, equity: float) -> None:
        """Publish current risk metrics to msgbus for SnapshotActor relay."""
        drawdown_pct = (equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        daily_pnl_pct = (equity - self._day_start_equity) / self._day_start_equity if self._day_start_equity > 0 else 0.0
        total_exposure = self._calc_total_exposure()
        position_count = sum(1 for p in self.cache.positions_open() if self._is_my_position(p))

        per_instrument: dict[str, float] = {}
        for p in self.cache.positions_open():
            if not self._is_my_position(p):
                continue
            iid = str(p.instrument_id)
            price = p.avg_px_open
            tick = self.cache.trade_tick(p.instrument_id)
            if tick is not None:
                price = float(tick.price)
            per_instrument[iid] = round(abs(float(p.quantity) * price), 2)

        self.msgbus.publish("risk.metrics.snapshot", {
            "equity": round(equity, 2),
            "peak_equity": round(self._peak_equity, 2),
            "drawdown_pct": round(drawdown_pct, 6),
            "daily_pnl_pct": round(daily_pnl_pct, 6),
            "total_exposure": round(total_exposure, 2),
            "position_count": position_count,
            "breached": self._breached,
            "breach_reason": self._breach_reason,
            "per_instrument_exposure": per_instrument,
        })

    def _calc_total_exposure(self) -> float:
        """Get total exposure for this portfolio's positions.

        When portfolio isolation is active (``strategy_tag_prefix`` set),
        manually sums exposure from filtered positions instead of using
        the global ``portfolio.net_exposures()``.
        """
        if self._strategy_tag_prefix is None:
            # No filtering — use global NT Portfolio API (backward compat)
            exposures = self.portfolio.net_exposures(self._venue)
            if not exposures:
                return 0.0
            total = 0.0
            for _currency, money in exposures.items():
                total += abs(float(money.as_double()))
            return total

        # Filtered: manually sum exposure from this portfolio's positions
        total = 0.0
        for position in self.cache.positions_open():
            if not self._is_my_position(position):
                continue
            instrument = self.cache.instrument(position.instrument_id)
            if instrument is None:
                continue
            # Try to get last price from trade tick, then quote tick
            last_price = None
            tick = self.cache.trade_tick(position.instrument_id)
            if tick is not None:
                last_price = float(tick.price)
            else:
                qtick = self.cache.quote_tick(position.instrument_id)
                if qtick is not None:
                    last_price = float(qtick.ask_price)
            if last_price is not None:
                total += abs(float(position.quantity) * last_price)
            else:
                # Fallback: use avg_px_open
                total += abs(float(position.quantity) * position.avg_px_open)
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
        """Publish instrument IDs of this portfolio's open positions for flattening."""
        for position in self.cache.positions_open():
            if not self._is_my_position(position):
                continue
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
