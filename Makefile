SHELL := /usr/bin/env bash
COMPOSE := docker compose
STRATEGY ?=
MODE ?= sandbox
CONTAINER := tinohelm-strategy-$(STRATEGY)

.DEFAULT_GOAL := help

require-strategy:
	@if [ -z "$(STRATEGY)" ]; then \
		echo "STRATEGY=<name> is required (strategies/<name>/tinohelm.toml must exist)"; \
		exit 1; \
	fi

.PHONY: help up down redis notifier deploy run sandbox pause resume flatten stop logs ps status restart bash test lint

help:
	@echo "TinoHelm — control plane"
	@echo "  make up                              # 启动 redis + notifier"
	@echo "  make down                            # 停止全部"
	@echo "  make deploy  STRATEGY=foo            # 一键部署：sandbox 模式 (默认)"
	@echo "  make deploy  STRATEGY=foo MODE=live  # 一键部署：live 模式"
	@echo "  make run     STRATEGY=foo            # 等价 deploy MODE=live"
	@echo "  make sandbox STRATEGY=foo            # 等价 deploy MODE=sandbox"
	@echo "  make pause   STRATEGY=foo            # 软挂起：trader.stop_strategy"
	@echo "  make resume  STRATEGY=foo            # 恢复：trader.start_strategy"
	@echo "  make flatten STRATEGY=foo            # 平仓后停：market_exit_strategy"
	@echo "  make stop    STRATEGY=foo            # 停止 pod 容器"
	@echo "  make logs    STRATEGY=foo            # tail logs"
	@echo "  make ps                              # docker compose ps"
	@echo "  make status                          # notifier 综合状态"
	@echo "  make restart STRATEGY=foo            # 硬重启策略容器"
	@echo "  make bash    STRATEGY=foo            # 进容器 shell"
	@echo "  make test                            # pytest"
	@echo "  make lint                            # ruff + mypy"

up:
	$(COMPOSE) up -d redis notifier

down:
	$(COMPOSE) down

redis:
	$(COMPOSE) up -d redis

notifier:
	$(COMPOSE) up -d notifier

# ──── one-key deploy ─────────────────────────────────────────────────
# `docker compose run` instantiates the generic ``strategy`` service with
# a unique container name + TINO_STRATEGY_ID env, so we never modify
# compose.yaml when adding a strategy.

deploy: require-strategy
	$(COMPOSE) run -d --name $(CONTAINER) \
		-e TINO_STRATEGY_ID=$(STRATEGY) \
		-e TINO_MODE=$(MODE) \
		strategy

run: require-strategy
	$(MAKE) deploy STRATEGY=$(STRATEGY) MODE=live

sandbox: require-strategy
	$(MAKE) deploy STRATEGY=$(STRATEGY) MODE=sandbox

# ──── control plane ──────────────────────────────────────────────────

pause: require-strategy
	$(COMPOSE) exec notifier python -m tinohelm.cli pause --strategy-id "$(STRATEGY)"

resume: require-strategy
	$(COMPOSE) exec notifier python -m tinohelm.cli resume --strategy-id "$(STRATEGY)"

flatten: require-strategy
	$(COMPOSE) exec notifier python -m tinohelm.cli flatten --strategy-id "$(STRATEGY)"

stop: require-strategy
	docker stop $(CONTAINER) && docker rm $(CONTAINER)

restart: require-strategy
	docker restart $(CONTAINER)

logs: require-strategy
	docker logs -f $(CONTAINER)

ps:
	$(COMPOSE) ps -a

status:
	$(COMPOSE) exec notifier python -m tinohelm.cli status

bash: require-strategy
	docker exec -it $(CONTAINER) bash

test:
	uv run pytest

lint:
	uv run ruff check tinohelm tests
	uv run ruff format --check tinohelm tests
	uv run mypy tinohelm
