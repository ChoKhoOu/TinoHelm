"""Tests for tinohelm.node.factory.build_trading_node_config."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from tinohelm.node.factory import build_trading_node_config


def _make_settings(
    redis_url: str = "redis://localhost:6379",
    api_key: str = "test-key",
    api_secret: str = "test-secret",
    account_type: str = "USDT_FUTURES",
    testnet: bool = True,
    db_url: str = "postgresql+asyncpg://user:pass@localhost/db",
) -> MagicMock:
    """Build a minimal mock Settings object."""
    settings = MagicMock()
    settings.redis.url = redis_url
    settings.binance.api_key = SecretStr(api_key)
    settings.binance.api_secret = SecretStr(api_secret)
    settings.binance.account_type = account_type
    settings.binance.testnet = testnet
    settings.database.url = db_url
    settings.paths.catalog = "/tmp/catalog"
    settings.paths.logs = "/tmp/logs"
    return settings


class TestBuildTradingNodeConfig:

    def test_build_config_default_includes_credentials(self):
        """Normal call (for_redis=False) embeds api_key and api_secret."""
        settings = _make_settings(api_key="my-key", api_secret="my-secret")
        config = build_trading_node_config("sandbox", [], settings)

        assert config["binance"]["api_key"] == "my-key"
        assert config["binance"]["api_secret"] == "my-secret"
        assert "from_env" not in config["binance"]

    def test_build_config_for_redis_excludes_credentials(self):
        """for_redis=True replaces credentials with from_env sentinel."""
        settings = _make_settings()
        config = build_trading_node_config("sandbox", [], settings, for_redis=True)

        assert config["binance"]["from_env"] is True
        assert "api_key" not in config["binance"]
        assert "api_secret" not in config["binance"]
        # account_type is non-sensitive and should still be present
        assert "account_type" in config["binance"]

    def test_build_config_has_config_version(self):
        """config_version is present and is a string of digits (ms timestamp)."""
        settings = _make_settings()
        config = build_trading_node_config("sandbox", [], settings)

        assert "config_version" in config
        version = config["config_version"]
        assert isinstance(version, str)
        assert version.isdigit(), f"config_version should be a digit string, got {version!r}"

    def test_build_config_has_redis_host_port(self):
        """redis_host and redis_port are parsed from the Redis URL."""
        settings = _make_settings(redis_url="redis://myhost:6380")
        config = build_trading_node_config("sandbox", [], settings)

        assert config["redis_host"] == "myhost"
        assert config["redis_port"] == 6380

    def test_build_config_redis_defaults(self):
        """Default Redis URL yields localhost:6379."""
        settings = _make_settings(redis_url="redis://localhost:6379")
        config = build_trading_node_config("sandbox", [], settings)

        assert config["redis_host"] == "localhost"
        assert config["redis_port"] == 6379

    def test_build_config_sandbox_trader_id(self):
        """Sandbox node gets trader_id SANDBOX-001."""
        settings = _make_settings()
        config = build_trading_node_config("sandbox", [], settings)

        assert config["trader_id"] == "SANDBOX-001"

    def test_build_config_live_trader_id(self):
        """Live node gets trader_id LIVE-001."""
        settings = _make_settings()
        config = build_trading_node_config("live", [], settings)

        assert config["trader_id"] == "LIVE-001"

    def test_build_config_unknown_node_type_raises(self):
        """Unknown node_type raises ValueError."""
        settings = _make_settings()
        with pytest.raises(ValueError, match="Unknown node_type"):
            build_trading_node_config("unknown", [], settings)
