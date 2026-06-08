"""Tests for the periodic ReportingActor.

The actor reads positions from ``self.cache`` (via NT's ``ReportProvider``) on
a clock timer and forwards a snapshot to the notifier. An NT ``Actor`` exposes
``cache`` but NOT ``trader`` (only the ``Controller`` subclass holds a trader),
so the actor never touches a trader. Two pieces are testable without spinning
up an NT TradingNode:

  1. The pure ``build_positions_report_payload`` helper that turns a positions
     ``DataFrame`` into a CSV-encoded ``(topic, body)`` envelope tuple.
  2. The actor's ``on_time_event`` glue, which we exercise with a stand-in
     DataFrame and publish-spy.

``positions_report_df(cache)`` itself is a thin one-line delegation to NT's
``ReportProvider`` (it needs real ``Position`` objects to snapshot), so it's
exercised by integration rather than unit-tested here.
"""

from __future__ import annotations

import msgspec.msgpack
import pandas as pd

from tinohelm.reporting_actor import (
    ReportingActor,
    ReportingActorConfig,
    build_positions_report_payload,
    positions_report_df,
)


def _decode(body: bytes) -> dict:
    return msgspec.msgpack.decode(body)


def test_positions_report_df_uses_open_positions_not_all() -> None:
    """The 持仓快照 must list only genuinely-open positions.

    Regression for the bug where closed (FLAT) positions kept showing in the
    snapshot with stale avg_px_open/close — root cause was feeding NT's
    ``cache.positions()`` (every position ever held) instead of
    ``cache.positions_open()`` (open only). We reuse NT's own open/closed
    bookkeeping rather than hand-filtering FLAT rows, so this test pins which
    cache method we call.
    """

    calls: list[str] = []

    class _SpyCache:
        def positions(self) -> list:
            calls.append("positions")
            # A closed position would land here — must NOT be what we read.
            return ["CLOSED-FLAT-POSITION"]

        def positions_open(self) -> list:
            calls.append("positions_open")
            return []  # nothing currently open

        def position_snapshots(self) -> list:
            return []

    df = positions_report_df(_SpyCache())

    assert "positions_open" in calls
    assert "positions" not in calls  # the all-positions accessor must not be used
    # Empty open set → empty report (renders as "当前无持仓" downstream).
    assert df.empty


def test_build_positions_report_payload_emits_csv_envelope() -> None:
    """The helper must return ``(topic, body)`` where the topic routes to
    logging via NotifierActor's ``tinohelm.*`` rule, and the body is msgpack
    bytes (bytes is in NT's _EXTERNAL_PUBLISHABLE_TYPES; dict is not, so
    dict payloads are silently dropped before reaching the Redis stream).

    We pin the topic name explicitly: ``tinohelm.report.positions``. Anything
    else risks landing in the trade-flow channels.
    """

    df = pd.DataFrame(
        {
            "strategy_id": ["FOO-001", "FOO-001"],
            "side": ["LONG", "LONG"],
            "quantity": [1.0, 2.0],
            "realized_pnl": [10.5, 0.0],
        },
    )

    topic, body = build_positions_report_payload(df, strategy_id="FOO-001")
    decoded = _decode(body)

    assert topic == "tinohelm.report.positions"
    assert decoded["strategy_id"] == "FOO-001"
    assert decoded["row_count"] == 2
    assert "csv" in decoded
    assert "strategy_id,side,quantity,realized_pnl" in decoded["csv"]


class _FakePortfolio:
    """Stand-in for NT's Portfolio — returns per-currency PnL dicts.

    NT's ``realized_pnls`` / ``unrealized_pnls`` / ``net_exposures`` return
    ``dict[Currency, Money]`` keyed by a Currency object whose ``str()`` is the
    code (e.g. 'USDT'). We mimic that with plain strings → values; the helper
    must stringify keys and values so the payload stays msgpack-friendly.
    """

    def __init__(self, realized=None, unrealized=None, net_exposure=None) -> None:
        self._realized = realized or {}
        self._unrealized = unrealized or {}
        self._net_exposure = net_exposure or {}

    def realized_pnls(self, venue=None):
        return self._realized

    def unrealized_pnls(self, venue=None):
        return self._unrealized

    def net_exposures(self, venue=None):
        return self._net_exposure


def test_payload_includes_account_pnl_when_portfolio_supplied() -> None:
    """With a portfolio + venues, the payload carries an ``account_pnl`` block
    so the notifier can render an account-level summary under the positions
    table. Values come straight from NT's Portfolio (we never compute PnL
    ourselves) and are stringified for msgpack transport.
    """

    df = pd.DataFrame({"strategy_id": ["FOO-001"], "side": ["LONG"], "quantity": [1.0]})
    portfolio = _FakePortfolio(
        realized={"USDT": "120.50 USDT"},
        unrealized={"USDT": "-15.00 USDT"},
        net_exposure={"USDT": "5000.00 USDT"},
    )

    topic, body = build_positions_report_payload(
        df,
        strategy_id="FOO-001",
        portfolio=portfolio,
        venues=["BYBIT"],
    )
    decoded = _decode(body)

    assert topic == "tinohelm.report.positions"
    pnl = decoded["account_pnl"]
    # Keyed by venue, then by currency code; values are stringified Money.
    assert pnl["BYBIT"]["realized"]["USDT"] == "120.50 USDT"
    assert pnl["BYBIT"]["unrealized"]["USDT"] == "-15.00 USDT"
    assert pnl["BYBIT"]["net_exposure"]["USDT"] == "5000.00 USDT"


def test_payload_degrades_when_portfolio_raises() -> None:
    """A Portfolio access failure (NT not fully initialized, API drift) must
    NOT sink the whole report — the positions table is the primary payload and
    must survive. account_pnl is additive: drop it, keep the table. Mirrors
    venues_from_cache's degrade-to-empty posture.
    """

    class _BoomPortfolio:
        def realized_pnls(self, venue=None):
            raise RuntimeError("NT portfolio not ready")

        def unrealized_pnls(self, venue=None):
            raise RuntimeError("NT portfolio not ready")

        def net_exposures(self, venue=None):
            raise RuntimeError("NT portfolio not ready")

    df = pd.DataFrame({"strategy_id": ["FOO-001"], "side": ["LONG"], "quantity": [1.0]})
    _topic, body = build_positions_report_payload(
        df,
        strategy_id="FOO-001",
        portfolio=_BoomPortfolio(),
        venues=["BYBIT"],
    )
    decoded = _decode(body)

    # Table survived; PnL block silently omitted.
    assert decoded["row_count"] == 1
    assert "csv" in decoded
    assert "account_pnl" not in decoded


def test_payload_omits_account_pnl_without_portfolio() -> None:
    """Backward-compatible: callers that don't pass a portfolio (or the old
    positional-only form) get no ``account_pnl`` key — the notifier then just
    renders the positions table as before.
    """

    df = pd.DataFrame({"strategy_id": ["FOO-001"], "side": ["LONG"]})
    _topic, body = build_positions_report_payload(df, strategy_id="FOO-001")
    assert "account_pnl" not in _decode(body)


def test_build_positions_report_payload_handles_empty_frame() -> None:
    """No open or closed positions yet (fresh pod) — return zero-row CSV
    rather than crashing. The notifier shouldn't have to special-case empty.
    """

    topic, body = build_positions_report_payload(
        pd.DataFrame(),
        strategy_id="GHOST-001",
    )
    decoded = _decode(body)

    assert topic == "tinohelm.report.positions"
    assert decoded["row_count"] == 0
    assert decoded["csv"] == ""


def test_reporting_actor_config_defaults() -> None:
    """Operators set ``[reporting] interval_minutes = 30``; default to 30
    minutes (matching the NT docs example) so the typical TOML stays empty.
    Setting ``enabled = false`` short-circuits the actor's on_start.
    """

    cfg = ReportingActorConfig(strategy_id="FOO-001")
    assert cfg.interval_minutes == 30
    assert cfg.enabled is True


def test_reporting_actor_publishes_on_timer_event() -> None:
    """When the clock fires, the actor must publish to msgbus on the
    ``tinohelm.report.positions`` topic. We hand ``_tick`` a positions
    DataFrame + a msgbus spy directly so the test stays free of NT runtime
    (the cache → DataFrame step is NT's ``ReportProvider``, not our glue).
    """

    df = pd.DataFrame({"strategy_id": ["FOO-001"], "side": ["LONG"]})

    published: list[tuple[str, bytes]] = []

    class _FakeMsgbus:
        def publish(self, *, topic: str, msg: bytes) -> None:
            published.append((topic, msg))

    actor = ReportingActor(ReportingActorConfig(strategy_id="FOO-001"))
    # Drive the pure on-tick handler with our fakes — bypass NT lifecycle.
    actor._tick(df=df, msgbus=_FakeMsgbus())

    assert len(published) == 1
    topic, msg = published[0]
    decoded = _decode(msg)
    assert topic == "tinohelm.report.positions"
    assert decoded["strategy_id"] == "FOO-001"
    assert decoded["row_count"] == 1
