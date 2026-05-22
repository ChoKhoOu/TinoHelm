"""Tests for shared helpers in node/_common.py."""
from __future__ import annotations


class TestBuildCacheConfig:
    """build_cache_config behavior against current NT config fields."""

    def test_sandbox_config_uses_current_nt_redis_timeout_fields(self):
        """Sandbox config must use current NT timeout fields and flush on start."""
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

    def test_live_config_keeps_flush_on_start_disabled(self):
        """Live config must keep flush_on_start disabled."""
        from tinohelm.node._common import build_cache_config

        cache_config = build_cache_config(
            redis_host="redis",
            redis_port=6379,
            is_sandbox=False,
        )

        assert cache_config.database.type == "redis"
        assert cache_config.database.host == "redis"
        assert cache_config.database.port == 6379
        assert cache_config.database.connection_timeout == 2
        assert cache_config.database.response_timeout == 2
        assert cache_config.flush_on_start is False
