"""Tests for tinohelm.core.config — configuration loading, merging, and env overrides."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from tinohelm.core.config import (
    Settings,
    ServerSettings,
    DatabaseSettings,
    RedisSettings,
    PathSettings,
    DataSettings,
    BacktestSettings,
    BinanceSettings,
    RiskConfig,
    _deep_merge,
    load_settings,
)


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------

class TestDeepMerge:

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_empty_override(self):
        assert _deep_merge({"a": 1}, {}) == {"a": 1}

    def test_both_empty(self):
        assert _deep_merge({}, {}) == {}

    def test_simple_override(self):
        result = _deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_add_new_key(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_nested_merge(self):
        base = {"server": {"host": "0.0.0.0", "port": 8000}}
        override = {"server": {"port": 9000}}
        result = _deep_merge(base, override)
        assert result == {"server": {"host": "0.0.0.0", "port": 9000}}

    def test_deeply_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 99, "d": 2}}}

    def test_override_dict_with_scalar(self):
        """When override has a scalar where base has a dict, scalar wins."""
        base = {"a": {"nested": 1}}
        override = {"a": "flat"}
        result = _deep_merge(base, override)
        assert result == {"a": "flat"}

    def test_override_scalar_with_dict(self):
        """When override has a dict where base has a scalar, dict wins."""
        base = {"a": "flat"}
        override = {"a": {"nested": 1}}
        result = _deep_merge(base, override)
        assert result == {"a": {"nested": 1}}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"b": 1}}


# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------

class TestSettingsDefaults:

    def test_server_defaults(self):
        s = ServerSettings()
        assert s.host == "0.0.0.0"
        assert s.port == 8000
        assert s.cors_origins == ["*"]

    def test_database_default_url(self):
        s = DatabaseSettings()
        assert "postgresql+asyncpg://" in s.url
        assert "tinohelm" in s.url

    def test_redis_default_url(self):
        s = RedisSettings()
        assert s.url == "redis://localhost:6379"

    def test_binance_defaults(self):
        s = BinanceSettings()
        assert s.testnet is True
        assert s.account_type == "USDT_FUTURES"
        assert s.api_key.get_secret_value() == ""
        assert s.api_secret.get_secret_value() == ""

    def test_path_defaults(self):
        s = PathSettings()
        assert s.strategies == Path("tino/strategies")
        assert s.catalog == Path("tino/data/catalog")
        assert s.artifacts == Path("tino/data/artifacts")

    def test_data_defaults(self):
        s = DataSettings()
        assert s.download_concurrency == 4
        assert s.job_concurrency == 4
        assert s.convert_workers == 1
        assert s.chunk_rows == 1_000_000
        assert s.tick_chunk_rows == 2_000_000
        assert s.csv_queue_maxsize == 1

    @pytest.mark.parametrize(
        "field",
        [
            "download_concurrency",
            "job_concurrency",
            "convert_workers",
            "chunk_rows",
            "tick_chunk_rows",
            "csv_queue_maxsize",
        ],
    )
    def test_data_settings_reject_zero_or_negative_overrides(self, field):
        with pytest.raises(ValidationError):
            DataSettings(**{field: 0})
        with pytest.raises(ValidationError):
            DataSettings(**{field: -1})

    def test_backtest_defaults(self):
        s = BacktestSettings()
        assert s.max_concurrent == 4
        assert s.max_workers == 2

    def test_risk_defaults(self):
        s = RiskConfig()
        assert s.base_capital == 100_000
        assert s.var_multiplier == 0.02

    def test_settings_assembles_all_sub_models(self):
        s = Settings()
        assert isinstance(s.server, ServerSettings)
        assert isinstance(s.database, DatabaseSettings)
        assert isinstance(s.redis, RedisSettings)
        assert isinstance(s.binance, BinanceSettings)
        assert isinstance(s.paths, PathSettings)
        assert isinstance(s.data, DataSettings)
        assert isinstance(s.backtest, BacktestSettings)
        assert isinstance(s.risk, RiskConfig)

    def test_default_max_concurrent_is_4(self, clean_env):
        """Backtest.max_concurrent defaults to 4."""
        from tinohelm.core.config import get_settings
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.backtest.max_concurrent == 4
        finally:
            get_settings.cache_clear()

    def test_env_override_max_concurrent(self, monkeypatch, clean_env):
        """TINO_BACKTEST__MAX_CONCURRENT env var overrides backtest.max_concurrent."""
        from tinohelm.core.config import get_settings
        monkeypatch.setenv("TINO_BACKTEST__MAX_CONCURRENT", "8")
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.backtest.max_concurrent == 8
        finally:
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# load_settings with YAML files
# ---------------------------------------------------------------------------

class TestLoadSettings:

    def test_loads_default_yaml(self, tmp_path, monkeypatch, clean_env):
        """load_settings reads config/default.yaml when it exists."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            "server:\n  port: 9999\n"
        )
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.server.port == 9999

    def test_repo_default_yaml_uses_hardened_data_defaults(self, clean_env):
        """Repo default.yaml must not override hardened DataSettings defaults."""
        s = load_settings()

        assert s.data.download_concurrency == 4
        assert s.data.job_concurrency == 4
        assert s.data.convert_workers == 1
        assert s.data.chunk_rows == 1_000_000
        assert s.data.tick_chunk_rows == 2_000_000
        assert s.data.csv_queue_maxsize == 1

    def test_user_yaml_overrides_default(self, tmp_path, monkeypatch, clean_env):
        """user.yaml overrides values from default.yaml."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            "server:\n  port: 8000\n  host: '0.0.0.0'\n"
        )
        (config_dir / "user.yaml").write_text(
            "server:\n  port: 3000\n"
        )
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.server.port == 3000
        # host should survive from default
        assert s.server.host == "0.0.0.0"

    def test_no_yaml_files_uses_defaults(self, tmp_path, monkeypatch, clean_env):
        """When no YAML files exist, pydantic defaults are used."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.server.port == 8000

    def test_no_config_dir_uses_defaults(self, tmp_path, monkeypatch, clean_env):
        """When config/ directory doesn't exist, pydantic defaults are used."""
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.server.port == 8000


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

class TestEnvOverrides:

    def test_env_overrides_yaml(self, tmp_path, monkeypatch, clean_env):
        """TINO_ env vars take priority over YAML values."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            "redis:\n  url: 'redis://from-yaml:6379'\n"
        )
        monkeypatch.setenv("TINO_REDIS__URL", "redis://from-env:6379")
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.redis.url == "redis://from-env:6379"

    def test_env_overrides_database_url(self, tmp_path, monkeypatch, clean_env):
        """TINO_DATABASE__URL env var overrides YAML database.url."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            "database:\n  url: 'postgresql+asyncpg://yaml-host/db'\n"
        )
        monkeypatch.setenv("TINO_DATABASE__URL", "postgresql+asyncpg://env-host/db")
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert "env-host" in s.database.url

    def test_env_without_yaml(self, tmp_path, monkeypatch, clean_env):
        """Env vars work even when no YAML files exist."""
        monkeypatch.setenv("TINO_REDIS__URL", "redis://standalone:6379")
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.redis.url == "redis://standalone:6379"


# ---------------------------------------------------------------------------
# get_settings caching
# ---------------------------------------------------------------------------

class TestGetSettingsCaching:

    def test_get_settings_returns_same_instance(self, clean_env):
        """get_settings() is cached — repeated calls return the same object."""
        from tinohelm.core.config import get_settings
        # Clear the LRU cache to avoid pollution from other tests
        get_settings.cache_clear()
        try:
            s1 = get_settings()
            s2 = get_settings()
            assert s1 is s2
        finally:
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestConfigEdgeCases:

    def test_empty_yaml_file(self, tmp_path, monkeypatch, clean_env):
        """An empty YAML file should not cause errors."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text("")
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        # Should fall back to pydantic defaults
        assert s.server.port == 8000

    def test_yaml_with_extra_keys_ignored(self, tmp_path, monkeypatch, clean_env):
        """Extra keys in YAML that don't match Settings fields are ignored."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            "server:\n  port: 8000\nunknown_section:\n  key: value\n"
        )
        with patch("tinohelm.core.config._PROJECT_ROOT", tmp_path):
            s = load_settings()
        assert s.server.port == 8000
        assert not hasattr(s, "unknown_section")
