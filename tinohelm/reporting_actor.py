"""Periodic positions-report actor for strategy pods.

NT's :class:`~nautilus_trader.analysis.reporter.ReportProvider` turns the
live ``Cache``'s positions + snapshots into a ``pandas.DataFrame`` —
exactly what ``Trader.generate_positions_report`` does internally
(``trader.py``: ``ReportProvider.generate_positions_report(cache.positions(),
cache.position_snapshots())``). An NT :class:`~nautilus_trader.common.actor.
Actor` exposes ``self.cache`` but NOT ``self.trader`` (only the ``Controller``
subclass holds a trader ref), so this actor reads the cache directly via
:func:`positions_report_df` rather than reaching for a trader it doesn't have.
It runs on a clock timer and publishes on ``tinohelm.report.positions`` —
picked up by the notifier's ``tinohelm.*`` route, so the report lands in the
read-only logging channel rather than mixing with trade-flow signals.

The DataFrame → ``(topic, body)`` envelope logic lives in
:func:`build_positions_report_payload` so it is testable without spinning up
an NT runtime; the cache → DataFrame step is a thin delegation to NT in
:func:`positions_report_df`.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any

import msgspec.msgpack
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig

REPORT_TOPIC_POSITIONS = "tinohelm.report.positions"


def venues_from_cache(cache: Any) -> list[Any]:
    """Distinct ``Venue`` objects the node has accounts for, for PnL queries.

    NT's ``Portfolio.*_pnls`` take a ``Venue``; we derive the set from
    ``cache.accounts()`` (``account.id.get_issuer()`` is the venue string,
    wrapped back into a ``Venue``). Best-effort: any access failure yields an
    empty list so a report still renders its positions table without the
    account-PnL block.
    """

    from nautilus_trader.model.identifiers import Venue

    try:
        accounts = cache.accounts() or []
    except Exception:  # pragma: no cover — defensive on partial NT runtimes
        return []
    seen: dict[str, Any] = {}
    for account in accounts:
        try:
            issuer = account.id.get_issuer()
        except Exception:  # pragma: no cover — defensive
            continue
        if issuer and issuer not in seen:
            seen[issuer] = Venue(issuer)
    return list(seen.values())


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


def positions_report_df(cache: Any) -> Any:
    """Snapshot the cache's positions + snapshots into a ``pandas.DataFrame``.

    Thin delegation to NT's :class:`~nautilus_trader.analysis.reporter.
    ReportProvider` — the exact call ``Trader.generate_positions_report`` makes
    internally. Lives here (rather than inline in the actor) so the cache →
    DataFrame step stays in one place for both the periodic timer and the
    on-demand ``report`` command. We never build the report ourselves; NT owns
    the DataFrame schema.
    """

    from nautilus_trader.analysis.reporter import ReportProvider

    return ReportProvider.generate_positions_report(
        cache.positions(),
        cache.position_snapshots(),
    )


def build_positions_report_payload(
    df: Any,
    *,
    strategy_id: str,
    portfolio: Any | None = None,
    venues: list[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Encode a positions-report ``DataFrame`` for transport.

    ``strategy_id`` MUST be the TinoHelm CONTROL HANDLE (the strategy directory
    name = ``TinoStrategyFile.strategy_id``, e.g. ``"oi_momentum_lowvol"``) —
    NEVER ``str(<NT StrategyId>)`` (e.g. ``"OIMomentum-oi_momentum_lowvol"``).
    This is a cross-process contract: the notifier keys its announce registry,
    its ``/positions`` listener (``_position_listeners``) and its channel
    routing on the control handle, so a snapshot tagged with the NT StrategyId
    can't be correlated to a waiting ``/positions`` future — it falls through to
    #logging and the slash command spins until it times out. Both producers of
    this topic (``ReportingActor`` periodic timer and ``BridgeActor`` on-demand
    ``report``) are wired from ``file.strategy_id`` in config.py for exactly this
    reason; this invariant lives here because this is the single point both call.

    ``df`` is the snapshot produced by :func:`positions_report_df` (NT's
    ``ReportProvider`` output). Returns ``(topic, body)`` where ``body`` is
    plain Python types (msgpack-friendly): ``strategy_id``, ``row_count``, and
    a CSV string. CSV (rather than JSON) keeps the wire small for high-row days
    and means the operator can ``redis-cli xrange`` the stream and pipe
    straight to ``column -t -s,``.

    When ``portfolio`` and ``venues`` are supplied, an ``account_pnl`` block is
    added: account-level realized / unrealized PnL and net exposure per venue,
    straight from NT's :class:`~nautilus_trader.portfolio.portfolio.Portfolio`
    (we never compute PnL ourselves). Both are optional so the periodic timer
    and CLI paths that only want the positions table stay backward-compatible.
    """

    body: dict[str, Any]
    if df is None or df.empty:
        body = {"strategy_id": strategy_id, "row_count": 0, "csv": ""}
    else:
        body = {
            "strategy_id": strategy_id,
            "row_count": len(df),
            "csv": df.to_csv(index=False),
        }

    if portfolio is not None and venues:
        # Account PnL is a strictly additive extra — never let a Portfolio
        # access failure (NT not fully initialized, API drift across an
        # upgrade) sink the whole report and leave /positions or /pnl hanging
        # until Discord times out. Degrade exactly like venues_from_cache:
        # drop the block, keep the positions table.
        with contextlib.suppress(Exception):
            body["account_pnl"] = _account_pnl(portfolio, venues)
    # Encode as bytes so NT's _EXTERNAL_PUBLISHABLE_TYPES whitelist (which
    # includes bytes but not dict) allows this message to be written to the
    # Redis stream. The notifier's parse_payload handles bytes → msgpack decode.
    return REPORT_TOPIC_POSITIONS, msgspec.msgpack.encode(body)


def _account_pnl(portfolio: Any, venues: list[Any]) -> dict[str, Any]:
    """Per-venue account PnL from NT's Portfolio, stringified for msgpack.

    ``venues`` are NT ``Venue`` objects (NT's ``Portfolio.*_pnls`` require
    them, not strings) — produced by :func:`venues_from_cache`. NT returns
    ``dict[Currency, Money]``; we stringify both key (currency code) and value
    (Money) so the wire stays plain types. ``net_exposures`` may be ``None``
    for a venue with no open exposure — coerce to an empty dict.
    """

    def _str_map(mapping: Any) -> dict[str, str]:
        return {str(k): str(v) for k, v in (mapping or {}).items()}

    out: dict[str, Any] = {}
    for venue in venues:
        out[str(venue)] = {
            "realized": _str_map(portfolio.realized_pnls(venue)),
            "unrealized": _str_map(portfolio.unrealized_pnls(venue)),
            "net_exposure": _str_map(portfolio.net_exposures(venue)),
        }
    return out


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
            f"ReportingActor scheduled every {self._interval_minutes}m -> {REPORT_TOPIC_POSITIONS}",
        )

    def on_stop(self) -> None:
        # NT's clock auto-cancels timers on actor stop; keep this explicit
        # for symmetry. ``cancel_timer`` is a no-op if the timer doesn't exist.
        with contextlib.suppress(Exception):  # pragma: no cover — defensive on shutdown races
            self.clock.cancel_timer(self.TIMER_NAME)

    def _on_time_event(self, _event: Any) -> None:
        """NT clock callback — defers to :meth:`_tick` so tests stay clean.

        An :class:`Actor` exposes ``self.cache`` (and ``self.portfolio``) but
        not ``self.trader`` — only the ``Controller`` subclass holds a trader
        ref. We read the positions DataFrame straight from the cache.
        """

        self._tick(
            df=positions_report_df(self.cache),
            msgbus=self.msgbus,
            portfolio=self.portfolio,
            venues=venues_from_cache(self.cache),
        )

    def _tick(
        self,
        *,
        df: Any,
        msgbus: Any,
        portfolio: Any | None = None,
        venues: list[Any] | None = None,
    ) -> None:
        """Pure-ish tick handler. Drives the report build + msgbus publish.

        Kept separate from :meth:`_on_time_event` so unit tests can supply
        fake collaborators without instantiating a TradingNode. ``df`` is the
        positions snapshot (NT's ``ReportProvider`` output); ``portfolio`` and
        ``venues`` are optional so tests that only check the positions table
        keep working.
        """

        topic, body = build_positions_report_payload(
            df,
            strategy_id=self._strategy_id,
            portfolio=portfolio,
            venues=venues,
        )
        msgbus.publish(topic=topic, msg=body)
