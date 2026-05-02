"""TinoHelm configuration management.

Priority: ENV > config/user.yaml > config/default.yaml
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://tinohelm:tinohelm@localhost:5432/tinohelm"


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379"


class BinanceSettings(BaseModel):
    api_key: SecretStr = SecretStr("")
    api_secret: SecretStr = SecretStr("")
    account_type: str = "USDT_FUTURES"
    testnet: bool = True


class PathSettings(BaseModel):
    """Canonical filesystem roots for all TinoHelm components.

    Defaults are expressed as **relative** paths (e.g. ``tino/strategies``)
    so they resolve against the current working directory.  The API / node
    containers set ``WORKDIR=/app`` and bind-mount the host's ``~/.tino/*``
    trees at ``/app/tino/*`` (note: no leading ``.`` — ``HOME=/app`` in the
    container has no matching ``.tino`` directory).

    Local developers either run from the project root and have
    ``~/.tino/*`` bind-mounted via docker-compose, or they override these
    paths via ``config/user.yaml`` / ``TINO_PATHS__*`` env vars.  Each
    module-level ``_FALLBACK_*`` constant pins the home-dir default so
    zero-config local runs still work when settings cannot be loaded or
    the relative root isn't present under CWD.
    """

    strategies: Path = Path("tino/strategies")
    actors: Path = Path("tino/actors")
    catalog: Path = Path("tino/data/catalog")
    artifacts: Path = Path("tino/data/artifacts")
    research: Path = Path("tino/research")
    logs: Path = Path("tino/logs")
    funding_rates: Path = Path("tino/data/funding_rates")
    # Shared ``data/`` root used by caches that live alongside ``catalog/``
    # (e.g. ``instruments_cache.json`` / ``funding_info_cache.json``).  The
    # ``catalog`` and ``funding_rates`` subtrees are nested under this root
    # but stay independent — callers must read them via their dedicated
    # fields and never assume ``data_cache / "catalog"`` is authoritative.
    data_cache: Path = Path("tino/data")
    factor_cache: Path = Path("tino/factor_cache")


class DataSettings(BaseModel):
    download_concurrency: int = Field(default=2, ge=1)
    convert_workers: int = Field(default=1, ge=1)
    chunk_rows: int = Field(default=1_000_000, ge=1)
    agg_trades_chunk_rows: int = Field(default=500_000, ge=1)
    csv_queue_maxsize: int = Field(default=1, ge=1)
    agg_trades_max_days_per_job: int = Field(default=1, ge=1)


class BacktestSettings(BaseModel):
    max_concurrent: int = 4
    # DEPRECATED: use max_concurrent instead. Retained so existing yaml entries don't break pydantic validation.
    max_workers: int = 2


class RiskConfig(BaseModel):
    base_capital: float = 100_000
    var_multiplier: float = 0.02


class Settings(BaseSettings):
    """Root settings for TinoHelm."""

    model_config = SettingsConfigDict(
        env_prefix="TINO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    risk: RiskConfig = Field(default_factory=RiskConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings() -> Settings:
    """Load settings with priority: ENV > user.yaml > default.yaml."""
    import os

    merged: dict = {}

    # Load default.yaml
    default_path = _PROJECT_ROOT / "config" / "default.yaml"
    if default_path.exists():
        with open(default_path) as f:
            data = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, data)

    # Load user.yaml (optional override)
    user_path = _PROJECT_ROOT / "config" / "user.yaml"
    if user_path.exists():
        with open(user_path) as f:
            data = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, data)

    # Strip specific YAML keys that have env var overrides so that
    # pydantic-settings can pick up env vars (TINO_REDIS__URL etc.)
    # instead of being overridden by YAML constructor kwargs.
    # Only delete the specific nested key, not the entire section.
    for section in list(merged.keys()):
        env_prefix = f"TINO_{section.upper()}__"
        matching_env_keys = [k for k in os.environ if k.startswith(env_prefix)]
        if matching_env_keys and isinstance(merged.get(section), dict):
            for env_key in matching_env_keys:
                # e.g. TINO_DATABASE__URL -> nested key "url"
                nested_key = env_key[len(env_prefix):].lower().split("__")[0]
                merged[section].pop(nested_key, None)
        elif matching_env_keys:
            del merged[section]

    return Settings(**merged)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return load_settings()
