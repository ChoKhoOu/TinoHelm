"""Tests for the periodic ReportingActor.

The actor calls ``trader.generate_positions_report()`` on a clock timer and
forwards a snapshot to the notifier. Two pieces are testable without spinning
up an NT TradingNode:

  1. The pure ``build_positions_report_payload`` helper that turns a trader
     into a CSV-encoded ``(topic, body)`` envelope tuple.
  2. The actor's ``on_time_event`` glue, which we exercise with a stand-in
     trader and publish-spy.
"""

from __future__ import annotations

import pandas as pd

from tinohelm.reporting_actor import (
    ReportingActor,
    ReportingActorConfig,
    build_positions_report_payload,
)


class _FakeTrader:
    """Stand-in for nautilus_trader.trading.Trader.

    Only ``generate_positions_report`` is hit by the helper; the real trader
    has many more methods, but the helper's contract is narrow on purpose.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def generate_positions_report(self) -> pd.DataFrame:
        return self._df


def test_build_positions_report_payload_emits_csv_envelope() -> None:
    """The helper must return ``(topic, body)`` where the topic routes to
    logging via NotifierActor's ``tinohelm.*`` rule, and the body is a dict
    that survives msgpack encoding (i.e. plain Python types — no DataFrame).

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

    topic, body = build_positions_report_payload(_FakeTrader(df), strategy_id="FOO-001")

    assert topic == "tinohelm.report.positions"
    assert body["strategy_id"] == "FOO-001"
    assert body["row_count"] == 2
    assert "csv" in body
    assert "strategy_id,side,quantity,realized_pnl" in body["csv"]


def test_build_positions_report_payload_handles_empty_frame() -> None:
    """No open or closed positions yet (fresh pod) — return zero-row CSV
    rather than crashing. The notifier shouldn't have to special-case empty.
    """

    topic, body = build_positions_report_payload(
        _FakeTrader(pd.DataFrame()),
        strategy_id="GHOST-001",
    )

    assert topic == "tinohelm.report.positions"
    assert body["row_count"] == 0
    assert body["csv"] == ""


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
    ``tinohelm.report.positions`` topic. We sub a fake trader + msgbus and
    drive on_time_event directly so the test stays free of NT runtime.
    """

    df = pd.DataFrame({"strategy_id": ["FOO-001"], "side": ["LONG"]})
    fake_trader = _FakeTrader(df)

    published: list[tuple[str, dict]] = []

    class _FakeMsgbus:
        def publish(self, *, topic: str, msg: dict) -> None:
            published.append((topic, msg))

    actor = ReportingActor(ReportingActorConfig(strategy_id="FOO-001"))
    # Drive the pure on-tick handler with our fakes — bypass NT lifecycle.
    actor._tick(trader=fake_trader, msgbus=_FakeMsgbus())

    assert len(published) == 1
    topic, msg = published[0]
    assert topic == "tinohelm.report.positions"
    assert msg["strategy_id"] == "FOO-001"
    assert msg["row_count"] == 1
