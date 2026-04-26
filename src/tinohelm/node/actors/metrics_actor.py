"""MetricsActor — equity snapshots to Redis + PostgreSQL."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event

from tinohelm.node.actors._utils import redis_publish, ts_ns_to_iso
from tinohelm.node.actors.serialize import build_equity_snapshot
from tinohelm.node.topics import SIGNAL_COST_DEVIATION


class MetricsActorConfig(ActorConfig):
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"
    equity_interval_secs: int = 60
    db_url: str = ""
    venue_name: str = "BINANCE"
    currency: str = "USDT"
    # Signal cost deviation monitoring.
    # Set cost_model_fee_bps_per_side to 0.0 to disable monitoring.
    cost_model_fee_bps_per_side: float = 0.0   # expected per-side fee (bps); 0 = disabled
    deviation_threshold_bps: float = 5.0        # alert when |actual - expected| > threshold


class MetricsActor(Actor):
    """Samples equity periodically, publishes to Redis and persists to PostgreSQL."""

    def __init__(self, config: MetricsActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._equity_interval = config.equity_interval_secs
        self._db_url = config.db_url or os.environ.get("TINO_DATABASE__URL", "")
        self._venue_name_str = config.venue_name
        self._currency_str = config.currency
        self._cost_model_fee_bps = config.cost_model_fee_bps_per_side
        self._deviation_threshold_bps = config.deviation_threshold_bps
        self._redis: redis.Redis | None = None
        self._db_engine: Any = None
        self._venue = None
        self._currency_obj = None

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)

        from nautilus_trader.model.identifiers import Venue
        from tinohelm.data.instruments import _resolve_currency
        self._venue = Venue(self._venue_name_str)
        self._currency_obj = _resolve_currency(self._currency_str)

        if self._db_url:
            try:
                from tinohelm.db.sync_engine import get_sync_engine
                self._db_engine = get_sync_engine(self._db_url)
            except Exception as e:
                self.log.error(f"MetricsActor: DB engine init failed: {e}")

        self.clock.set_timer(
            name="equity_snapshot",
            interval=timedelta(seconds=self._equity_interval),
        )

        self.log.info(f"MetricsActor started for {self._node_type}")

    def on_event(self, event: Event) -> None:
        if isinstance(event, TimeEvent) and event.name == "equity_snapshot":
            self._take_equity_snapshot()
            return
        # Signal cost deviation monitoring — fires on every fill when enabled.
        if self._cost_model_fee_bps > 0.0:
            try:
                from nautilus_trader.model.events import OrderFilled

                if isinstance(event, OrderFilled):
                    self._check_cost_deviation(event)
            except ImportError:
                pass

    def on_stop(self) -> None:
        if self._redis:
            self._redis.close()

    def _take_equity_snapshot(self) -> None:
        if not self._redis or self._venue is None:
            return
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return
            balance_money = account.balance_total(self._currency_obj)
            if balance_money is None:
                return
            balance = float(balance_money.as_double())

            unrealized = 0.0
            try:
                pnls = self.portfolio.unrealized_pnls(self._venue)
                if pnls:
                    for _currency, pnl_money in pnls.items():
                        unrealized += float(pnl_money.as_double())
            except Exception:
                pass

            equity = balance + unrealized
            ts = ts_ns_to_iso(self.clock.timestamp_ns())

            payload = build_equity_snapshot(
                self._node_type, equity, balance, unrealized, ts,
            )

            redis_publish(self._redis, self._node_type, "equity", payload)

            key = f"tino:{self._node_type}:equity_history"
            self._redis.rpush(key, json.dumps(payload, default=str))
            self._redis.ltrim(key, -1440, -1)

            # Persist to DB via executor (non-blocking)
            if self._db_engine:
                self.queue_for_executor(
                    self._persist_equity_snapshot,
                    (equity, balance, unrealized, ts),
                )
        except Exception as e:
            self.log.error(f"Equity snapshot error: {e}")

    def _check_cost_deviation(self, fill_event: object) -> None:
        """Check whether fill cost deviates from cost_model.fee_bps_per_side.

        Publishes to ``tino:{node_type}:signal.cost.deviation`` when the
        absolute deviation exceeds ``deviation_threshold_bps``.

        Parameters
        ----------
        fill_event:
            An ``OrderFilled`` NT event.  Accessed via ``getattr`` so the
            method is testable without the NT Cython extension.
        """
        try:
            commission = float(getattr(fill_event, "commission").as_double())
            quantity = float(getattr(fill_event, "last_qty").as_double())
            last_px = float(getattr(fill_event, "last_px").as_double())
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            return

        if quantity == 0.0 or last_px == 0.0:
            return

        actual_cost_bps = (commission / (quantity * last_px)) * 10_000.0
        deviation_bps = abs(actual_cost_bps - self._cost_model_fee_bps)

        if deviation_bps <= self._deviation_threshold_bps:
            return

        payload: dict[str, Any] = {
            "fill_id": str(getattr(fill_event, "trade_id", "")),
            "instrument_id": str(getattr(fill_event, "instrument_id", "")),
            "expected_bps": self._cost_model_fee_bps,
            "actual_bps": round(actual_cost_bps, 6),
            "deviation_bps": round(deviation_bps, 6),
            "ts_ns": getattr(fill_event, "ts_init", 0),
        }

        redis_publish(
            self._redis,
            self._node_type,
            SIGNAL_COST_DEVIATION,
            payload,
        )

        self.log.warning(
            f"signal.cost.deviation: expected={self._cost_model_fee_bps}bps "
            f"actual={actual_cost_bps:.2f}bps deviation={deviation_bps:.2f}bps "
            f"fill={payload['fill_id']} instrument={payload['instrument_id']}"
        )

    def _persist_equity_snapshot(
        self, equity: float, balance: float, unrealized: float, ts: str,
    ) -> None:
        """Runs in executor thread."""
        try:
            from sqlalchemy import text
            from sqlalchemy.orm import Session
            stmt = text(
                "INSERT INTO equity_snapshots (node_type, equity, balance, unrealized_pnl, ts) "
                "VALUES (:node_type, :equity, :balance, :unrealized_pnl, :ts)"
            )
            with Session(self._db_engine) as session:
                session.execute(stmt, {
                    "node_type": self._node_type,
                    "equity": equity,
                    "balance": balance,
                    "unrealized_pnl": unrealized,
                    "ts": ts,
                })
                session.commit()
        except Exception as e:
            self.log.error(f"DB persist equity error: {e}")
