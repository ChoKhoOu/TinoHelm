"""Tests for standalone node entry points (live_main, sandbox_main)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestLiveMainCredentialValidation:
    """Verify live_main.py rejects empty credentials."""

    @patch("tinohelm.node.live_main.run_node")
    @patch("tinohelm.node.live_main.get_settings")
    @patch("tinohelm.node.live_main.redis.Redis")
    def test_live_main_exits_on_missing_api_key(self, mock_redis_cls, mock_settings, mock_run):
        """Live entry point must sys.exit(1) when BINANCE_API_KEY is empty."""
        mock_r = MagicMock()
        mock_r.get.return_value = None  # No Redis config
        mock_redis_cls.from_url.return_value = mock_r

        # Mock settings
        settings = MagicMock()
        settings.redis.url = "redis://localhost:6379"
        settings.binance.api_key = ""
        settings.binance.api_secret = ""
        settings.binance.account_type = "usdt_future"
        settings.binance.testnet = False
        settings.database.url = "postgresql://localhost/test"
        settings.paths.catalog = "/tmp/catalog"
        settings.paths.logs = "/tmp/logs"
        mock_settings.return_value = settings

        with patch.dict("os.environ", {"BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                from tinohelm.node.live_main import main
                main()
            assert exc_info.value.code == 1

        mock_run.assert_not_called()

    @patch("tinohelm.node.live_main.run_node")
    @patch("tinohelm.node.live_main.get_settings")
    @patch("tinohelm.node.live_main.redis.Redis")
    def test_live_main_exits_on_missing_api_secret(self, mock_redis_cls, mock_settings, mock_run):
        """Live entry point must sys.exit(1) when BINANCE_API_SECRET is empty."""
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_cls.from_url.return_value = mock_r

        settings = MagicMock()
        settings.redis.url = "redis://localhost:6379"
        settings.binance.api_key = ""
        settings.binance.api_secret = ""
        settings.binance.account_type = "usdt_future"
        settings.binance.testnet = False
        settings.database.url = "postgresql://localhost/test"
        settings.paths.catalog = "/tmp/catalog"
        settings.paths.logs = "/tmp/logs"
        mock_settings.return_value = settings

        with patch.dict("os.environ", {"BINANCE_API_KEY": "key", "BINANCE_API_SECRET": ""}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                from tinohelm.node.live_main import main
                main()
            assert exc_info.value.code == 1

        mock_run.assert_not_called()


class TestSandboxMainFallback:
    """Verify sandbox_main.py reads config correctly."""

    @patch("tinohelm.node.sandbox_main.run_node")
    @patch("tinohelm.node.sandbox_main.build_trading_node_config")
    @patch("tinohelm.node.sandbox_main.get_settings")
    @patch("tinohelm.node.sandbox_main.redis.Redis")
    def test_sandbox_main_falls_back_to_settings(
        self, mock_redis_cls, mock_settings, mock_factory, mock_run
    ):
        """When no Redis config exists, sandbox builds config from settings."""
        mock_r = MagicMock()
        mock_r.get.return_value = None  # No Redis config
        mock_redis_cls.from_url.return_value = mock_r

        settings = MagicMock()
        settings.redis.url = "redis://localhost:6379"
        settings.binance.account_type = "usdt_future"
        mock_settings.return_value = settings

        mock_factory.return_value = {
            "node_type": "sandbox",
            "binance": {"api_key": "", "api_secret": "", "account_type": "usdt_future"},
        }

        with patch.dict("os.environ", {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"}, clear=False):
            from tinohelm.node.sandbox_main import main
            main()

        mock_factory.assert_called_once()
        mock_run.assert_called_once()
        # Verify credentials came from env, not factory
        call_config = mock_run.call_args[0][0]
        assert call_config["binance"]["api_key"] == "k"
        assert call_config["binance"]["api_secret"] == "s"

    @patch("tinohelm.node.sandbox_main.run_node")
    @patch("tinohelm.node.sandbox_main.get_settings")
    @patch("tinohelm.node.sandbox_main.redis.Redis")
    def test_sandbox_main_reads_from_redis(self, mock_redis_cls, mock_settings, mock_run):
        """When Redis config exists, sandbox uses it."""
        redis_config = {
            "node_type": "sandbox",
            "config_version": "12345",
            "binance": {"from_env": True, "account_type": "usdt_future"},
        }
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(redis_config)
        mock_redis_cls.from_url.return_value = mock_r

        settings = MagicMock()
        settings.redis.url = "redis://localhost:6379"
        settings.binance.account_type = "usdt_future"
        mock_settings.return_value = settings

        with patch.dict("os.environ", {"BINANCE_API_KEY": "mykey", "BINANCE_API_SECRET": "mysec"}, clear=False):
            from tinohelm.node.sandbox_main import main
            main()

        mock_run.assert_called_once()
        call_config = mock_run.call_args[0][0]
        # Credentials injected from env, not from Redis sentinel
        assert call_config["binance"]["api_key"] == "mykey"
        assert call_config["binance"]["api_secret"] == "mysec"
        assert "from_env" not in call_config["binance"]
