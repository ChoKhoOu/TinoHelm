"""Tests for tinohelm.core.process_manager.ProcessManager."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings() -> MagicMock:
    """Minimal mock Settings matching what get_settings() returns."""
    settings = MagicMock()
    settings.redis.url = "redis://localhost:6379"
    settings.binance.api_key = "test-key"
    settings.binance.api_secret = "test-secret"
    settings.binance.account_type = "USDT_FUTURES"
    settings.binance.testnet = True
    settings.database.url = "postgresql+asyncpg://user:pass@localhost/db"
    settings.paths.catalog = "/tmp/catalog"
    settings.paths.logs = "/tmp/logs"
    return settings


def _make_factory_config(node_type: str = "sandbox") -> dict:
    """A minimal return value from build_trading_node_config(for_redis=True)."""
    return {
        "node_type": node_type,
        "config_version": "1700000000000",
        "trader_id": "SANDBOX-001" if node_type == "sandbox" else "LIVE-001",
        "redis_host": "localhost",
        "redis_port": 6379,
        "binance": {
            "from_env": True,
            "account_type": "usdt_future",
        },
        "strategies": [],
    }


def _make_pm(mock_redis: MagicMock | None = None):
    """Create a ProcessManager with a mocked Redis client.

    Returns (pm, mock_redis) so tests can inspect Redis calls.
    """
    if mock_redis is None:
        mock_redis = MagicMock()

    with patch("redis.Redis.from_url", return_value=mock_redis):
        from tinohelm.core.process_manager import ProcessManager
        pm = ProcessManager(redis_url="redis://localhost:6379")

    # Overwrite the stored client in case the constructor stored a different ref
    pm._redis = mock_redis
    return pm, mock_redis


# ---------------------------------------------------------------------------
# start_node tests
# ---------------------------------------------------------------------------

class TestStartNode:

    def _run_start_node(self, node_type: str = "sandbox", strategies=None,
                        portfolio_config=None):
        """Helper that patches both lazy imports and calls start_node."""
        if strategies is None:
            strategies = []
        mock_redis = MagicMock()
        pm, mock_redis = _make_pm(mock_redis)

        factory_config = _make_factory_config(node_type)

        with patch("tinohelm.core.process_manager.ProcessManager.start_node",
                   wraps=pm.start_node):
            with patch("tinohelm.core.config.get_settings", return_value=_make_settings()), \
                 patch("tinohelm.node.factory.build_trading_node_config",
                       return_value=factory_config) as mock_factory:
                result = pm.start_node(node_type, strategies,
                                       portfolio_config=portfolio_config)

        return result, mock_redis, mock_factory

    def test_start_node_writes_config_to_redis(self):
        """Redis SET is called with key tino:node:config:{node_type} and TTL=3600."""
        _, mock_redis, _ = self._run_start_node("sandbox")

        # Find the SET call for the config key
        config_set_calls = [
            c for c in mock_redis.set.call_args_list
            if c.args and "tino:node:config:sandbox" in str(c.args[0])
        ]
        assert len(config_set_calls) == 1, \
            f"Expected exactly one SET for config key, got: {mock_redis.set.call_args_list}"

        call_args = config_set_calls[0]
        assert call_args.args[0] == "tino:node:config:sandbox"
        assert call_args.kwargs.get("ex") == 3600

        # Value should be valid JSON
        json.loads(call_args.args[1])

    def test_start_node_writes_state_to_redis(self):
        """Redis SET is called with key tino:node:state:{node_type} containing status='config_ready'."""
        _, mock_redis, _ = self._run_start_node("sandbox")

        state_set_calls = [
            c for c in mock_redis.set.call_args_list
            if c.args and "tino:node:state:sandbox" in str(c.args[0])
        ]
        assert len(state_set_calls) == 1

        state = json.loads(state_set_calls[0].args[1])
        assert state["status"] == "config_ready"
        assert "config_version" in state

    def test_start_node_returns_config_version(self):
        """Return dict has 'status' and 'config_version'."""
        result, _, _ = self._run_start_node("sandbox")

        assert "status" in result
        assert "config_version" in result
        assert result["status"] == "config_ready"
        assert result["config_version"] == "1700000000000"

    def test_start_node_excludes_credentials(self):
        """Config written to Redis has from_env=True instead of real credentials."""
        _, mock_redis, _ = self._run_start_node("sandbox")

        config_set_calls = [
            c for c in mock_redis.set.call_args_list
            if c.args and "tino:node:config:sandbox" in str(c.args[0])
        ]
        config = json.loads(config_set_calls[0].args[1])

        assert config["binance"].get("from_env") is True
        assert "api_key" not in config["binance"]
        assert "api_secret" not in config["binance"]

    def test_start_node_accepts_portfolio_config(self):
        """portfolio_config kwarg is forwarded to the factory."""
        mock_redis = MagicMock()
        pm, mock_redis = _make_pm(mock_redis)
        factory_config = _make_factory_config("sandbox")

        with patch("tinohelm.core.config.get_settings", return_value=_make_settings()), \
             patch("tinohelm.node.factory.build_trading_node_config",
                   return_value=factory_config) as mock_factory:
            pm.start_node("sandbox", [], portfolio_config="my-portfolio")

        mock_factory.assert_called_once()
        _, kwargs = mock_factory.call_args
        assert kwargs.get("portfolio_config") == "my-portfolio"

    def test_start_node_invalid_type_raises(self):
        """ValueError is raised for an unrecognised node_type."""
        pm, _ = _make_pm()
        with pytest.raises(ValueError, match="Invalid node_type"):
            pm.start_node("unknown", [])


# ---------------------------------------------------------------------------
# stop_node tests
# ---------------------------------------------------------------------------

class TestStopNode:

    def test_stop_node_publishes_shutdown(self):
        """stop_node publishes a shutdown command on the correct channel."""
        mock_redis = MagicMock()
        # Simulate heartbeat already gone on first check
        mock_redis.exists.return_value = False

        pm, mock_redis = _make_pm(mock_redis)
        pm.stop_node("sandbox")

        mock_redis.publish.assert_called_once_with(
            "tino:sandbox:commands",
            json.dumps({"cmd": "shutdown"}),
        )

    def test_stop_node_polls_heartbeat(self):
        """stop_node checks for heartbeat existence after publishing shutdown."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False

        pm, mock_redis = _make_pm(mock_redis)
        pm.stop_node("live")

        mock_redis.exists.assert_called_with("tino:heartbeat:live")


# ---------------------------------------------------------------------------
# get_status tests
# ---------------------------------------------------------------------------

class TestGetStatus:

    def test_get_status_running_when_heartbeat_exists(self):
        """Status is 'running' when a heartbeat key is present in Redis."""
        mock_redis = MagicMock()

        def redis_get(key):
            if "heartbeat:sandbox" in key:
                return json.dumps({"ts": "2026-01-01T00:00:00"})
            if "state:sandbox" in key:
                return json.dumps({"status": "config_ready", "config_version": "123"})
            return None

        mock_redis.get.side_effect = redis_get
        pm, _ = _make_pm(mock_redis)

        status = pm.get_status()
        assert status["nodes"]["sandbox"]["status"] == "running"

    def test_get_status_stopped_when_no_heartbeat_no_state(self):
        """Status is 'stopped' when neither heartbeat nor state key exists."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # All keys missing

        pm, _ = _make_pm(mock_redis)
        status = pm.get_status()

        assert status["nodes"]["sandbox"]["status"] == "stopped"
        assert status["nodes"]["live"]["status"] == "stopped"

    def test_get_status_config_ready_when_state_but_no_heartbeat(self):
        """Status is 'config_ready' when state key exists but heartbeat is absent."""
        mock_redis = MagicMock()

        def redis_get(key):
            if "heartbeat" in key:
                return None
            if "state:sandbox" in key:
                return json.dumps({"status": "config_ready", "config_version": "abc"})
            return None

        mock_redis.get.side_effect = redis_get
        pm, _ = _make_pm(mock_redis)

        status = pm.get_status()
        assert status["nodes"]["sandbox"]["status"] == "config_ready"


# ---------------------------------------------------------------------------
# kill_switch tests
# ---------------------------------------------------------------------------

class TestKillSwitch:

    def test_kill_switch_level1_publishes_pause(self):
        """Level-1 kill switch publishes a pause command with strategy_id."""
        mock_redis = MagicMock()
        pm, mock_redis = _make_pm(mock_redis)

        pm.kill_switch(level=1, node_type="live", strategy_id="strat-abc")

        mock_redis.publish.assert_called_once_with(
            "tino:live:commands",
            json.dumps({"cmd": "pause", "strategy_id": "strat-abc"}),
        )

    def test_kill_switch_level2_publishes_flatten(self):
        """Level-2 kill switch publishes a flatten command."""
        mock_redis = MagicMock()
        pm, mock_redis = _make_pm(mock_redis)

        pm.kill_switch(level=2, node_type="live")

        mock_redis.publish.assert_called_once_with(
            "tino:live:commands",
            json.dumps({"cmd": "flatten"}),
        )

    def test_kill_switch_level3_publishes_shutdown(self):
        """Level-3 kill switch publishes a shutdown command."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False  # Heartbeat immediately gone
        pm, mock_redis = _make_pm(mock_redis)

        pm.kill_switch(level=3, node_type="live")

        mock_redis.publish.assert_called_once_with(
            "tino:live:commands",
            json.dumps({"cmd": "shutdown"}),
        )

    def test_kill_switch_level1_requires_strategy_id(self):
        """Level-1 kill switch raises ValueError when strategy_id is not provided."""
        pm, _ = _make_pm()
        with pytest.raises(ValueError, match="strategy_id is required"):
            pm.kill_switch(level=1, node_type="live", strategy_id=None)
