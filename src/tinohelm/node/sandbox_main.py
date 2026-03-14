"""Standalone sandbox node entry point for Docker container."""
import json
import logging
import os

import redis

from tinohelm.core.config import get_settings
from tinohelm.node.factory import build_trading_node_config
from tinohelm.node.sandbox import run_node


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    settings = get_settings()
    r = redis.Redis.from_url(settings.redis.url, decode_responses=True)

    # Try to read non-sensitive config from Redis (written by API)
    config_raw = r.get("tino:node:config:sandbox")
    if config_raw:
        config = json.loads(config_raw)
        logger.info("Loaded sandbox config from Redis (version=%s)",
                    config.get("config_version", "unknown"))
    else:
        # Build from settings (container started directly)
        config = build_trading_node_config("sandbox", [], settings)
        logger.info("Built sandbox config from settings (no Redis config found)")

    # SECURITY: Always read credentials from environment, never from Redis
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    config.setdefault("binance", {})
    config["binance"]["api_key"] = api_key
    config["binance"]["api_secret"] = api_secret
    config["binance"].setdefault("account_type", settings.binance.account_type)
    config["binance"].pop("from_env", None)

    r.close()
    run_node(config)


if __name__ == "__main__":
    main()
