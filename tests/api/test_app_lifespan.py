from pathlib import Path


def test_lifespan_bootstraps_database_before_redis_setup():
    source = Path("src/tinohelm/api/app.py").read_text()

    bootstrap_call = source.index("await bootstrap_database_schema(cfg.database.url)")
    redis_setup = source.index("redis_client = aioredis.from_url(cfg.redis.url)")

    assert bootstrap_call < redis_setup
