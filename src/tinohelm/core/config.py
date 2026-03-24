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
    strategies: Path = Path("tino/strategies")
    catalog: Path = Path("tino/data/catalog")
    artifacts: Path = Path("tino/data/artifacts")
    logs: Path = Path("tino/logs")


class BacktestSettings(BaseModel):
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
