"""Tests for the strategy-pod announce protocol.

When a strategy pod boots, it must broadcast its identity to
``tinohelm:announce`` so the notifier can route subsequent events to the
correct Discord channel (sandbox vs live). The notifier reads this
stream both at startup (via ``XRANGE`` to recover state) and on the fly
(via ``XREAD $`` to pick up new pods).

Two invariants pinned here:
  1. The announce envelope carries the four fields the notifier actually
     consumes — anything else is decoration.
  2. The stream is auto-trimmed at ~1000 entries so a long-lived
     deployment doesn't drift into multi-GB memory pressure on Redis.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import fakeredis
import pytest

from tinohelm.config import TinoStrategyFile
from tinohelm.notifier.runner import (
    apply_fallback_scan,
    load_announce_history,
    read_new_announces,
)
from tinohelm.strategy_runner import publish_announce

ANNOUNCE_STREAM = "tinohelm:announce"


@pytest.fixture()
def redis_client():
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=False)


def _write_strategy_dir(tmp_path: Path, strategy_id: str, mode: str) -> Path:
    strategy_dir = tmp_path / "strategies" / strategy_id.lower()
    strategy_dir.mkdir(parents=True)
    body = textwrap.dedent(
        f"""
        [strategy]
        id = "{strategy_id}"
        trader_id = "TINO-001"
        mode = "{mode}"
        class = "strategies.foo.strategy:FooStrategy"
        config_class = "strategies.foo.strategy:FooStrategyConfig"

        [strategy.params]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    (strategy_dir / "tinohelm.toml").write_text(body)
    return strategy_dir / "tinohelm.toml"


def test_publish_announce_writes_envelope(tmp_path: Path, redis_client) -> None:
    """A booted pod adds exactly one entry with the routable fields.

    The four fields the notifier needs: ``strategy_id`` (which pod),
    ``mode`` (which Discord channel), ``trader_id`` (debug context), ``ts``
    (recency tiebreak when the same id is re-announced).
    """

    file = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))

    publish_announce(redis_client, file)

    entries = redis_client.xrange(ANNOUNCE_STREAM)
    assert len(entries) == 1
    _id, fields = entries[0]
    decoded = {k.decode(): v.decode() for k, v in fields.items()}
    assert decoded["strategy_id"] == "FOO-001"
    assert decoded["mode"] == "sandbox"
    assert decoded["trader_id"] == "TINO-001"
    assert decoded["ts"].isdigit()  # millis epoch


def test_publish_announce_carries_live_mode(tmp_path: Path, redis_client) -> None:
    """``mode = "live"`` survives the round-trip — channel routing depends on it."""

    file = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "BAR-001", "live"))

    publish_announce(redis_client, file)

    entries = redis_client.xrange(ANNOUNCE_STREAM)
    assert entries[0][1][b"mode"] == b"live"


def test_publish_announce_carries_version_handshake(tmp_path: Path, redis_client) -> None:
    """The announce envelope must carry both NT's version and our own protocol
    version so the notifier can detect cross-version pods.

    Without this, a strategy pod upgraded to a newer NT release that changed
    an event field would silently feed broken payloads into a notifier still
    pinned to the old NT — schema-tolerant decoding helps, but the operator
    deserves a heads-up. ``tino_protocol_version`` lets us evolve the
    announce / control wire without forcing a lock-step upgrade.
    """

    import nautilus_trader

    from tinohelm.strategy_runner import TINO_PROTOCOL_VERSION

    file = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))
    publish_announce(redis_client, file)

    entries = redis_client.xrange(ANNOUNCE_STREAM)
    decoded = {k.decode(): v.decode() for k, v in entries[0][1].items()}
    assert decoded["nt_version"] == nautilus_trader.__version__
    assert decoded["tino_protocol_version"] == TINO_PROTOCOL_VERSION


def test_read_new_announces_carries_versions(tmp_path: Path, redis_client) -> None:
    """``read_new_announces`` must surface the version fields to the notifier
    loop alongside ``(strategy_id, mode)`` — the notifier needs them to detect
    drift. Old pods without versions get sentinel defaults so this stays
    backward-compatible: a pod still on the previous announce schema doesn't
    crash the notifier loop.
    """

    file = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))
    publish_announce(redis_client, file)

    new_entries, _cursor = read_new_announces(redis_client, "0", block_ms=10)
    assert len(new_entries) == 1
    sid, mode, nt_version, proto = new_entries[0]
    assert sid == "FOO-001"
    assert mode == "sandbox"
    assert nt_version  # non-empty
    assert proto  # non-empty


def test_announce_stream_caps_at_thousand_entries(tmp_path: Path, redis_client) -> None:
    """Long-lived deployments must not unbounded-grow this stream.

    1000 announces x ~100B ~= 100KB — well within Redis memory budget,
    but enough history that even an aggressive restart loop on every
    strategy keeps a recent, complete picture.
    """

    file = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))

    for _ in range(1100):
        publish_announce(redis_client, file)

    length = redis_client.xlen(ANNOUNCE_STREAM)
    # Approximate trimming (~) gives the broker some leeway; bound it loosely.
    assert length <= 1100
    assert length >= 900
    assert length < 1100  # trim must have kicked in


def test_load_announce_history_recovers_registry(tmp_path: Path, redis_client) -> None:
    """A notifier restart must recover the live/sandbox registry from Redis.

    Without this, every notifier restart would lose channel-routing context
    until each pod re-announces — which only happens on pod restart, not on
    notifier restart. Tens of minutes of mis-routed events otherwise.
    """

    foo = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))
    bar = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "BAR-001", "live"))
    publish_announce(redis_client, foo)
    publish_announce(redis_client, bar)

    registry, cursor = load_announce_history(redis_client)

    assert registry == {"FOO-001": "sandbox", "BAR-001": "live"}
    assert cursor != "0-0"  # cursor advanced past replayed history


def test_load_announce_history_takes_latest_mode(tmp_path: Path, redis_client) -> None:
    """A pod that flips sandbox → live (or back) must be tracked on its newest mode.

    Operators sometimes test a strategy in sandbox, then redeploy with
    ``mode = "live"``. Channel routing must immediately follow the switch.
    """

    file_dir = tmp_path / "strategies" / "foo"
    file_dir.mkdir(parents=True)
    toml_path = file_dir / "tinohelm.toml"

    toml_path.write_text(
        textwrap.dedent(
            """
            [strategy]
            id = "FOO-001"
            trader_id = "TINO-001"
            mode = "sandbox"
            class = "x:Y"
            config_class = "x:Y"
            [strategy.params]
            [factories.data]
            [factories.exec]
            """,
        ).strip(),
    )
    publish_announce(redis_client, TinoStrategyFile.load(toml_path))

    toml_path.write_text(
        textwrap.dedent(
            """
            [strategy]
            id = "FOO-001"
            trader_id = "TINO-001"
            mode = "live"
            class = "x:Y"
            config_class = "x:Y"
            [strategy.params]
            [factories.data]
            [factories.exec]
            """,
        ).strip(),
    )
    publish_announce(redis_client, TinoStrategyFile.load(toml_path))

    registry, _cursor = load_announce_history(redis_client)
    assert registry["FOO-001"] == "live"


def test_read_new_announces_returns_only_entries_after_cursor(
    tmp_path: Path,
    redis_client,
) -> None:
    """The notifier loop must pick up new pods without re-processing old ones.

    A naive ``XRANGE`` re-reads every entry on every poll — at 60s polling
    x 1000 entries that's 1000 redis ops/min for nothing. Cursor-based
    ``XREAD $`` reads only new ones since last poll.
    """

    foo = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "FOO-001", "sandbox"))
    publish_announce(redis_client, foo)

    # Notifier startup: replay history, get the last id as the cursor.
    _registry, cursor = load_announce_history(redis_client)
    new_entries, cursor = read_new_announces(redis_client, cursor, block_ms=10)
    assert new_entries == []  # cursor advanced past FOO

    bar = TinoStrategyFile.load(_write_strategy_dir(tmp_path, "BAR-001", "live"))
    publish_announce(redis_client, bar)

    new_entries, cursor = read_new_announces(redis_client, cursor, block_ms=10)
    assert len(new_entries) == 1
    assert new_entries[0][0] == "BAR-001"
    assert new_entries[0][1] == "live"

    # Second call after no new writes yields empty.
    new_entries, cursor = read_new_announces(redis_client, cursor, block_ms=10)
    assert new_entries == []


def test_fallback_scan_picks_up_strategies_missing_from_registry(redis_client) -> None:
    """If a pod's announce was missed (Redis flush, race, etc.) but its
    control stream exists, the notifier must still know about it.

    Default mode for a fallback-discovered pod is ``"sandbox"`` — the
    safe assumption when we can't tell. An operator can correct this by
    restarting the pod (which re-announces with the real mode).
    """

    redis_client.xadd("tinohelm:control:FOO-001", {"k": "v"})
    redis_client.xadd("tinohelm:control:GHOST-001", {"k": "v"})

    registry: dict[str, str] = {"FOO-001": "live"}  # known via announce
    apply_fallback_scan(redis_client, registry)

    # FOO already known: NOT clobbered
    assert registry["FOO-001"] == "live"
    # GHOST never announced: defaulted to sandbox
    assert registry["GHOST-001"] == "sandbox"
