from pathlib import Path


def test_lifespan_bootstraps_database_before_redis_setup():
    source = Path("src/tinohelm/api/app.py").read_text()

    bootstrap_call = source.index("await bootstrap_database_schema(cfg.database.url)")
    redis_setup = source.index("redis_client = aioredis.from_url(cfg.redis.url)")

    assert bootstrap_call < redis_setup


def test_api_lifespan_does_not_manage_data_fetch_worker_lifecycle():
    source = Path("src/tinohelm/api/app.py").read_text()

    assert "await recover_interrupted_jobs(redis_client)" not in source
    assert "start_data_worker(redis_url=cfg.redis.url, catalog_path=str(catalog_root))" not in source
    assert "await stop_data_worker_and_wait(timeout=30.0)" not in source
