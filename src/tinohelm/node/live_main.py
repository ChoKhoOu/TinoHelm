"""Standalone live node entry point for Docker container."""
import logging
import sys

from tinohelm.core.config import get_settings
from tinohelm.node._common import inject_credentials, load_node_config
from tinohelm.node.live import run_node


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    settings = get_settings()
    config = load_node_config("live", settings, logger)
    inject_credentials(config, settings)

    # Live trading requires valid credentials
    if not config["binance"]["api_key"] or not config["binance"]["api_secret"]:
        logger.critical("BINANCE_API_KEY and BINANCE_API_SECRET must be set for live trading")
        sys.exit(1)

    run_node(config)


if __name__ == "__main__":
    main()
