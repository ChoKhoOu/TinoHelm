# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader. FastAPI backend + Redis job queue + PostgreSQL + Next.js frontend. Provides backtesting, paper trading (sandbox), and live trading.

## Build & Run

```bash
# Python env
uv sync --extra test --extra optimize --extra ops

# Database migrations
uv run alembic upgrade head

# Rust CLI
make                         # Build + install tino binary
make build                   # Build only: cli/target/release/tino
tino --help

# Frontend
cd src/web && npm ci && npm run dev    # Dev server :3000
cd src/web && npm run build            # Static export to out/

# E2E
./scripts/e2e_backtest.sh
./scripts/verify_docker.sh
```

## 用户规则
- **MUST**: 涉及 NT API 的任何开发，必须先浏览 https://nautilustrader.io/docs/latest/ 对应文档页面，确认 API 签名和行为后再写代码。不要凭记忆或猜测调用 NT API。
- NT API 参考文档: https://nautilustrader.io/docs/latest/api_reference/backtest/
- 项目内 NT 参考指南: `docs/guide/nautilustrader_complete_guide.md` (中文, 1290 行)
- 策略保存在 `~/.tino/strategies`

## Key Conventions

### Symbol Naming
- User input: `BTCUSDT-PERP` → NT internal: `BTCUSDT-PERP.BINANCE` (auto-appended by `_normalize_symbol()`)
- Binance API: `BTCUSDT` (use `strip_to_binance_api_symbol()` from `data/instruments.py`)
- Jesse format: `BTC-USDT` (used in `SYMBOL_PROFILES` keys)
- **Do NOT auto-append `-PERP`** — user must explicitly specify instrument type

### NT Config System
NautilusTrader uses **msgspec Structs**, not Pydantic. Use `strategy/utils.py` helpers (`get_config_fields`, `get_config_field_names`) — never access `__struct_fields__`/`__struct_defaults__` directly (they're tuples, not dicts).

### Config Priority
ENV vars (`TINO_` prefix, `__` nested delimiter) > `~/.tino/config/user.yaml` > `config/default.yaml`

### Data Storage
All user data lives under `~/.tino/`: strategies, actors, data/catalog (Parquet), data/artifacts, config. Only 1-minute bars need storage — larger intervals are aggregated via NT composite (`INTERNAL@1-MINUTE-EXTERNAL`).

## Strategy Development

- Config fields `instrument_id`, `bar_type`, `order_id_tag`, `manage_stop` are injected by portfolio loader — never hardcode.
- **Constructor (`__init__`)**: Do NOT access `self.clock` or `self.log` — not initialized yet.
- `SYMBOL_PROFILES` dict (Jesse format keys) for per-symbol parameter profiles.
- `on_save()`/`on_load()` for live restart state; `on_order_rejected()` for venue rejections.

## Rust CLI

- Output modes: `-f text` (human), `-f json` (raw), `-f llm` (envelope `{ok,data,error,meta}` for agents).
- API coverage rule: use `tino api call METHOD /path` first; typed subcommands are convenience wrappers.
- Auth: `X-API-Key`. Priority: `--api-key` > `TINO_API_KEY` > `~/.tino/credentials/api_key` > user.yaml. Never print secret values.

## Frontend (Next.js / src/web)

**Detailed guide**: See `src/web/CLAUDE.md` for component architecture, Tailwind mappings, chart theme, and notification system.

**MUST**: All frontend development MUST follow design references in `.claude/skills/TinoHelmDS/`. Pixel-perfect replication expected.

## Pitfalls & Lessons Learned

### NautilusTrader API
- Actor does NOT have `self.publish()` — use `self.msgbus.publish(topic, msg)`.
- `on_bar()` won't fire unless `self.subscribe_bars(bar_type)` called in `on_start()`.
- Position attrs: `ts_opened`/`ts_closed`/`duration_ns` — NOT `opened_ts`/`closed_ts`/`duration`.
- `Money.as_double()` for float — string includes currency suffix.
- **Always** `instrument.make_price()` / `instrument.make_qty()` for orders.
- `Quantity` is unsigned — subtracting larger from smaller raises `ValueError`.
- Cython extension classes — cannot `object.__new__()` in tests; use stub pattern.

### Alembic Migrations
- `revision` is arbitrary string ID, not filename. Migration chain: `None → "001" → … → "007"`.
- DB DateTime is `TIMESTAMP WITHOUT TIME ZONE` (naive). Never use `datetime.now(timezone.utc)` — use `datetime.utcnow()` or `func.now()`.

### Data
- NaN/Infinity crashes PostgreSQL JSON columns — always sanitize.
- ParquetDataCatalog path: `{catalog_path}/data/bar/{bar_type_str}/`.
- Bar `ts_init` must be the **closing time** of the bar.
- FundingRate stored as JSON, not Parquet.
- Never hardcode tick/lot sizes — use `data/instruments.py` (cached from Binance API).

### Docker
- Shared image: `api`, `node-sandbox`, `node-live` all use `tinohelm-api:latest`.
- Code changes: `docker compose up -d --build api`. Strategy changes: hot-reload via `tino strategy rescan`.

## Agent Skills

- **Issue tracker**: GitHub Issues on `ChoKhoOu/TinoHelm` via `gh` CLI. See `docs/agents/issue-tracker.md`.
- **Triage labels**: `needs-triage`/`needs-info`/`ready-for-agent`/`ready-for-human`/`wontfix`. See `docs/agents/triage-labels.md`.
- **Domain docs**: `CONTEXT.md` + `docs/adr/` (lazily created by `/grill-with-docs`).
