"""Periodic positions-report actor for strategy pods.

NT v1.226 ships :meth:`Trader.generate_positions_report` (and its order /
account siblings), which return ``pandas.DataFrame`` snapshots of the live
``Cache``. This actor calls them on a clock timer and publishes the result
on ``tinohelm.report.positions`` — picked up by the notifier's
``tinohelm.*`` route, so the report lands in the read-only logging channel
rather than mixing with trade-flow signals.

The actual report-generation logic (DataFrame → ``(topic, body)`` envelope)
lives in :func:`build_positions_report_payload` so it is testable without
spinning up an NT runtime.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig

REPORT_TOPIC_POSITIONS = "tinohelm.report.positions"


class ReportingActorConfig(ActorConfig, frozen=True):
    """Config for :class:`ReportingActor`.

    Parameters
    ----------
    strategy_id : str
        The strategy id this report relates to. Recorded in the envelope so
        the notifier can render it without re-deriving from the topic.
    interval_minutes : int, default 30
        Minutes between report generations. NT's docs example uses 30; we
        match that so the typical TOML stays empty.
    enabled : bool, default True
        Set False in TOML to skip the timer entirely (useful while developing
        a strategy when reports are noise).
    """

    strategy_id: str
    interval_minutes: int = 30
    enabled: bool = True


def build_positions_report_payload(trader: Any, *, strategy_id: str) -> tuple[str, dict[str, Any]]:
    """Snapshot the trader's open+closed positions and encode for transport.

    Returns ``(topic, body)`` where ``body`` is plain Python types
    (msgpack-friendly): ``strategy_id``, ``row_count``, and a CSV string.
    CSV (rather than JSON) keeps the wire small for high-row days and means
    the operator can ``redis-cli xrange`` the stream and pipe straight to
    ``column -t -s,``.
    """

    df = trader.generate_positions_report()
    if df is None or df.empty:
        return REPORT_TOPIC_POSITIONS, {
            "strategy_id": strategy_id,
            "row_count": 0,
            "csv": "",
        }
    csv = df.to_csv(index=False)
    return REPORT_TOPIC_POSITIONS, {
        "strategy_id": strategy_id,
        "row_count": len(df),
        "csv": csv,
    }


class ReportingActor(Actor):
    """NT actor that publishes a positions-report snapshot every N minutes.

    Lifecycle:
      * ``on_start`` schedules ``self.clock.set_timer`` — unless ``enabled``
        is False in config.
      * Each tick calls :meth:`_tick`, which we expose as a top-level method
        so tests can drive it without an NT runtime.
    """

    TIMER_NAME = "tinohelm-positions-report"

    def __init__(self, config: ReportingActorConfig) -> None:
        super().__init__(config=config)
        self._strategy_id = config.strategy_id
        self._interval_minutes = config.interval_minutes
        self._enabled = config.enabled

    def on_start(self) -> None:
        if not self._enabled:
            self.log.info("ReportingActor disabled in config; skipping timer")
            return
        self.clock.set_timer(
            name=self.TIMER_NAME,
            interval=timedelta(minutes=self._interval_minutes),
            callback=self._on_time_event,
        )
        self.log.info(
            f"ReportingActor scheduled every {self._interval_minutes}m "
            f"-> {REPORT_TOPIC_POSITIONS}",
        )

    def on_stop(self) -> None:
        # NT's clock auto-cancels timers on actor stop; keep this explicit
        # for symmetry. ``cancel_timer`` is a no-op if the timer doesn't exist.
        with contextlib.suppress(Exception):  # pragma: no cover — defensive on shutdown races
            self.clock.cancel_timer(self.TIMER_NAME)

    def _on_time_event(self, _event: Any) -> None:
        """NT clock callback — defers to :meth:`_tick` so tests stay clean."""

        self._tick(trader=self.trader, msgbus=self.msgbus)

    def _tick(self, *, trader: Any, msgbus: Any) -> None:
        """Pure-ish tick handler. Drives the report build + msgbus publish.

        Kept separate from :meth:`_on_time_event` so unit tests can supply
        fake collaborators without instantiating a TradingNode.
        """

        topic, body = build_positions_report_payload(trader, strategy_id=self._strategy_id)
        msgbus.publish(topic=topic, msg=body)
