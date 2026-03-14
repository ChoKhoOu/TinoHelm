"""Standalone sandbox node entry point for Docker container."""
import logging

from tinohelm.core.config import get_settings
from tinohelm.node._common import inject_credentials, load_node_config
from tinohelm.node.sandbox import run_node


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    settings = get_settings()
    config = load_node_config("sandbox", settings, logger)
    inject_credentials(config, settings)

    run_node(config)


if __name__ == "__main__":
    main()
