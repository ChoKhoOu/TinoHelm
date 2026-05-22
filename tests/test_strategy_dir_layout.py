"""Tests for the per-strategy directory layout.

User flow we want to enable: a developer drops a new folder under
``strategies/foo/`` containing ``strategy.py`` + ``tinohelm.toml``, then
runs ``make deploy STRATEGY=foo``. No edits to ``configs/`` or
``compose.yaml``. This file pins the *config-loading* end of that flow:
``TinoStrategyFile.load_for_id("foo")`` must find the toml co-located
with the strategy code.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tinohelm.config import TinoStrategyFile


@pytest.fixture(autouse=True)
def _redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("TINO_MODE", raising=False)


def _toml_body(strategy_id: str = "FOO-001") -> str:
    return textwrap.dedent(
        f"""
        [strategy]
        id = "{strategy_id}"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.foo.strategy:FooStrategy"
        config_class = "strategies.foo.strategy:FooStrategyConfig"

        [strategy.params]

        [message_bus]
        encoding = "msgpack"

        [factories.data]
        [factories.exec]
        """,
    ).strip()


def test_load_for_id_finds_colocated_toml(tmp_path: Path) -> None:
    """``load_for_id("foo")`` reads ``strategies/foo/tinohelm.toml``.

    This is the contract that lets a developer add a strategy by *only*
    touching ``strategies/foo/``. If the loader silently fell back to
    ``configs/strategies/foo.toml`` we would still need the legacy
    ``configs/`` directory for every strategy — defeating the simplification.
    """

    strategy_dir = tmp_path / "strategies" / "foo"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "tinohelm.toml").write_text(_toml_body("FOO-001"))

    file = TinoStrategyFile.load_for_id("foo", search_root=tmp_path)

    assert file.strategy_id == "FOO-001"
    assert file.trader_id == "TINO-001"
    assert file.mode == "sandbox"
    assert file.command_topic == "commands.tinohelm.FOO-001"


def test_load_for_id_missing_strategy_raises_with_path(tmp_path: Path) -> None:
    """Missing folder must error loudly — operator should see the path tried.

    Silent failures here would have ``make deploy STRATEGY=typo`` silently
    boot the wrong (or no) strategy.
    """

    with pytest.raises(FileNotFoundError, match=r"strategies/typo/tinohelm\.toml"):
        TinoStrategyFile.load_for_id("typo", search_root=tmp_path)
