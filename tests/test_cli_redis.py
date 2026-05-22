"""Behavioral tests for the CLI ↔ Redis wire contract.

The CLI's job is to write XADD entries that NT's
``crates/infrastructure/src/redis/msgbus.rs::decode_bus_message`` can read
back unchanged. We use ``fakeredis`` (drop-in compatible with redis-py) so the
wire shape is exercised end-to-end without a real broker.
"""

from __future__ import annotations

import json

import fakeredis
import pytest
from typer.testing import CliRunner

import tinohelm.cli as cli_mod
from tinohelm import control_stream_key

runner = CliRunner()


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    """Patch :func:`tinohelm.cli._client` so the CLI talks to fakeredis."""

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=False)
    monkeypatch.setattr(cli_mod, "_client", lambda: client)
    return client


def _entries(client: fakeredis.FakeRedis, key: str) -> list[dict[bytes, bytes]]:
    return [fields for _id, fields in client.xrange(key)]


def test_cli_pause_writes_to_control_stream(fake_redis: fakeredis.FakeRedis) -> None:
    """`tinohelm pause` must XADD into ``tinohelm:control:{strategy_id}``."""

    result = runner.invoke(cli_mod.app, ["pause", "--strategy-id", "FOO-001"])

    assert result.exit_code == 0, result.output
    entries = _entries(fake_redis, control_stream_key("FOO-001"))
    assert len(entries) == 1


def test_payload_field_is_decodable_json_with_action_and_ts(
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """The ``payload`` field must be JSON bytes BridgeActor can decode.

    BridgeActor.._extract_action calls ``json.loads(payload)`` and reads
    ``action``. We also enshrine ``ts`` (millis since epoch) so any future
    operator can audit the order events fired in.
    """

    runner.invoke(cli_mod.app, ["resume", "--strategy-id", "FOO-001"])

    entry = _entries(fake_redis, control_stream_key("FOO-001"))[0]
    payload = json.loads(entry[b"payload"])
    assert payload["action"] == "resume"
    assert isinstance(payload["ts"], int)
    assert payload["ts"] > 0


def test_xadd_entry_topic_field_matches_nt_contract(fake_redis: fakeredis.FakeRedis) -> None:
    """The ``topic`` field must equal ``commands.tinohelm.{id}.{action}``.

    NT decodes ``stream_msg[1]`` as topic (see Rust `decode_bus_message`); the
    field name on the Python publisher side must be the literal string
    ``"topic"`` so the ordering lines up after Redis serializes it.
    """

    runner.invoke(cli_mod.app, ["pause", "--strategy-id", "FOO-001"])

    entry = _entries(fake_redis, control_stream_key("FOO-001"))[0]
    assert entry[b"topic"] == b"commands.tinohelm.FOO-001.pause"


def test_flatten_carries_reason_when_provided(fake_redis: fakeredis.FakeRedis) -> None:
    """`tinohelm flatten --reason "EOD"` must surface ``reason`` in payload.

    Operators reach for `flatten` during incidents; persisting the reason in
    the wire envelope lets the audit log explain why a pod stopped trading.
    """

    result = runner.invoke(
        cli_mod.app,
        ["flatten", "--strategy-id", "FOO-001", "--reason", "EOD-circuit-breaker"],
    )

    assert result.exit_code == 0, result.output
    entry = _entries(fake_redis, control_stream_key("FOO-001"))[0]
    payload = json.loads(entry[b"payload"])
    assert payload["action"] == "flatten"
    assert payload["reason"] == "EOD-circuit-breaker"
