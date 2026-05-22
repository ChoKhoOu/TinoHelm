SHELL := /usr/bin/env bash
COMPOSE := docker compose
STRATEGY ?=
PROFILE := strategy-$(STRATEGY)

.DEFAULT_GOAL := help

require-strategy:
	@if [ -z "$(STRATEGY)" ]; then \
		echo "STRATEGY=<name> is required (configs/strategies/<name>.toml must exist)"; \
		exit 1; \
	fi

.PHONY: help up down redis notifier run sandbox pause resume flatten stop logs ps status restart bash test lint

help:
	@echo "TinoHelm — control plane"
	@echo "  make up                          # 启动 redis + notifier"
	@echo "  make down                        # 停止全部"
	@echo "  make run     STRATEGY=foo        # 启动一个 live 策略 pod"
	@echo "  make sandbox STRATEGY=foo        # 启动 sandbox 模式 (paper fills)"
	@echo "  make pause   STRATEGY=foo        # 软挂起：trader.stop_strategy"
	@echo "  make resume  STRATEGY=foo        # 恢复：trader.start_strategy"
	@echo "  make flatten STRATEGY=foo        # 平仓后停：market_exit_strategy"
	@echo "  make stop    STRATEGY=foo        # 停止整个 pod 容器"
	@echo "  make logs    STRATEGY=foo        # tail logs"
	@echo "  make ps                          # docker compose ps"
	@echo "  make status                      # notifier 综合状态"
	@echo "  make restart STRATEGY=foo        # 硬重启策略容器"
	@echo "  make bash    STRATEGY=foo        # 进容器 shell"
	@echo "  make test                        # pytest"
	@echo "  make lint                        # ruff + mypy"

up:
	$(COMPOSE) up -d redis notifier

down:
	$(COMPOSE) down

redis:
	$(COMPOSE) up -d redis

notifier:
	$(COMPOSE) up -d notifier

run: require-strategy
	TINO_MODE=live $(COMPOSE) --profile $(PROFILE) up -d $(PROFILE)

sandbox: require-strategy
	TINO_MODE=sandbox $(COMPOSE) --profile $(PROFILE) up -d $(PROFILE)

pause: require-strategy
	$(COMPOSE) exec $(PROFILE) python -m tinohelm.cli pause --strategy-id "$(STRATEGY)"

resume: require-strategy
	$(COMPOSE) exec $(PROFILE) python -m tinohelm.cli resume --strategy-id "$(STRATEGY)"

flatten: require-strategy
	$(COMPOSE) exec $(PROFILE) python -m tinohelm.cli flatten --strategy-id "$(STRATEGY)"

stop: require-strategy
	$(COMPOSE) stop $(PROFILE)

restart: require-strategy
	$(COMPOSE) restart $(PROFILE)

logs: require-strategy
	$(COMPOSE) logs -f $(PROFILE)

ps:
	$(COMPOSE) ps

status:
	$(COMPOSE) exec notifier python -m tinohelm.cli status

bash: require-strategy
	$(COMPOSE) exec $(PROFILE) bash

test:
	uv run pytest

lint:
	uv run ruff check tinohelm tests
	uv run ruff format --check tinohelm tests
	uv run mypy tinohelm
