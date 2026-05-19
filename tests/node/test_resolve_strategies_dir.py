"""Tests for resolve_strategies_dir() in node/_common.py.

Covers the TINO_STRATEGIES_DIR env-override priority guarantee introduced
as part of the PathRegistry migration (s9).
"""
from __future__ import annotations

from pathlib import Path


def test_build_cache_config_uses_current_nt_redis_timeout_fields():
    """build_cache_config must construct a Redis CacheConfig on NT 1.226.0."""
    from tinohelm.node._common import build_cache_config

    cache_config = build_cache_config(
        redis_host="redis",
        redis_port=6379,
        redis_password="secret",
        is_sandbox=True,
    )

    assert cache_config.database.type == "redis"
    assert cache_config.database.host == "redis"
    assert cache_config.database.port == 6379
    assert cache_config.database.password == "secret"
    assert cache_config.database.connection_timeout == 2
    assert cache_config.database.response_timeout == 2
    assert cache_config.flush_on_start is True


class TestResolveStrategiesDir:
    """resolve_strategies_dir resolution order."""

    def test_legacy_env_takes_priority(self, monkeypatch, tmp_path):
        """TINO_STRATEGIES_DIR env var must take priority over paths.get("strategies")."""
        # Arrange: set the legacy env to a known tmp dir
        monkeypatch.setenv("TINO_STRATEGIES_DIR", str(tmp_path))

        # Override paths.get so we can assert it is NOT consulted
        import tinohelm.node._common as common_mod

        paths_get_called = []

        class _FakePaths:
            def get(self, field: str) -> Path:  # pragma: no cover
                paths_get_called.append(field)
                return Path("/should/not/be/returned")

        monkeypatch.setattr(common_mod, "paths", _FakePaths())

        # Act
        result = common_mod.resolve_strategies_dir()

        # Assert: env value returned, paths.get never called
        assert result == tmp_path.resolve()
        assert paths_get_called == [], (
            "paths.get() was called despite TINO_STRATEGIES_DIR being set"
        )

    def test_paths_registry_used_when_env_absent(self, monkeypatch, tmp_path):
        """When TINO_STRATEGIES_DIR is unset, paths.get('strategies') is returned."""
        monkeypatch.delenv("TINO_STRATEGIES_DIR", raising=False)

        import tinohelm.node._common as common_mod

        class _FakePaths:
            def get(self, field: str) -> Path:
                assert field == "strategies"
                return tmp_path

        monkeypatch.setattr(common_mod, "paths", _FakePaths())

        result = common_mod.resolve_strategies_dir()

        assert result == tmp_path
