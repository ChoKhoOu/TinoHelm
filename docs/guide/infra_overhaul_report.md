# TinoHelm 量化基建重构报告

> 日期: 2026-04-03
> 流水线: Deep Interview (4.3% ambiguity) → Ralplan (v4, Architect+Critic) → Autopilot → Ralph (7 stories) → Architect Review → UltraWork 审查

---

## 一、重构概览

基于 `docs/guide/` 中的 4 份 NautilusTrader 技术文档，对 TinoHelm 的量化基建进行了全面重构。核心目标：对齐 NT 架构哲学（六边形架构、Actor SRP、原生能力利用），消除所有技术债务。

### 改动统计

| 类别 | 数量 |
|------|------|
| 新建文件 | 12 |
| 删除文件 | 3 (2 shim + 1 单体) |
| 重构文件 | 15+ |
| 涉及语言 | Python + TypeScript |
| 测试结果 | 200 passed |
| 前端构建 | npm run build 成功 |

---

## 二、7 个 Story 详细改动

### US-001: 统一模块加载器

**问题:** 6 处不同的 importlib 模式，`registry.py` 有 sys.path 永久泄漏。

**改动:**
- **新建** `src/tinohelm/strategy/module_loader.py`
  - `load_module_from_file(file_path, module_name, boundary_dir)` — 统一入口
  - `discover_strategy_classes(module)` — 找 Strategy/StrategyConfig 子类
  - `discover_actor_classes(module)` — 找 Actor/ActorConfig 子类
  - `load_strategy_module(file_path)` — 高级接口，返回 `ModuleLoadResult`
  - sys.path 在 `try/finally` 中管理，模块名用 MD5 避免冲突，支持 boundary_dir 防穿越
- **替换 6 个调用点:**
  - `strategy/registry.py` — `importlib.import_module()` → `load_module_from_file()`，删除永久 sys.path.insert
  - `strategy/validator.py` — 内联 `spec_from_file_location` → `load_module_from_file()`
  - `strategy/loader.py` — 自写的 `_load_module_from_file()` (29行) → 委托到统一加载器
  - `portfolio/config.py` — 内联 importlib → `load_strategy_module()` 一行搞定
  - `api/routes/strategy.py` — 函数内 importlib → `load_module_from_file()`
  - `api/routes/optimize.py` — 函数内 importlib → `load_strategy_module()`

---

### US-002: result.py 拆分 + Tearsheet 提取 + ProgressReporter

**问题:** `result.py` 1781 行单文件；tearsheet 276 行 HTML 嵌在 runner 里；ProgressReporter 用类级可变属性。

**改动:**
- **result.py → `backtest/result/` 包:**
  - `statistics.py` (253行) — 工具函数: `_safe_float`, `_compute_psr`, `_compute_monte_carlo` 等
  - `extract.py` (1553行) — `extract_backtest_results()` 主函数
  - `__init__.py` — re-export，现有 import 路径不变
  - 删除旧 `result.py` 单文件
- **Tearsheet 提取:**
  - 新建 `backtest/tearsheet.py`，`enhance_tearsheet(artifacts_dir, results)` 独立函数
  - runner.py 调用改为 `from tinohelm.backtest.tearsheet import enhance_tearsheet`
- **ProgressReporter 修复:**
  - 类级属性 (`_ProgressReporter._redis` 等) → 实例属性
  - BacktestRunner 在 `add_actor()` 后直接设置: `reporter._redis = self._redis_client`

---

### US-003: BridgeActor 分解为 5 个 Actor

**问题:** `bridge_actor.py` 895 行，混合 6+ 职责，违反 Actor SRP。

**改动 — 5 个新 Actor:**

#### SnapshotActor (`node/actors/snapshot_actor.py`, ~230行)
- **职责:** NT 事件 → Redis PubSub
- 订阅: position/order/bar 事件 + StrategySnapshot + risk metrics
- 包含 `_RedisLogHandler`（速率限制日志转发）
- Redis publish 是 sub-ms，可在事件循环上同步调用

#### CommandActor (`node/actors/command_actor.py`, ~210行)
- **职责:** 外部 Redis 命令 → LifecycleController
- Daemon thread + Redis SUBSCRIBE + `collections.deque`（CPython GIL 原子操作）
- NT timer 200ms drain deque → 事件循环线程执行
- 持有 LifecycleController + StrategyRegistry 引用

#### DbWriterActor (`node/actors/db_writer_actor.py`, ~190行)
- **职责:** 交易事件 → PostgreSQL
- 订阅全部 position 事件 (Opened/Changed/Closed) + OrderFilled
- `queue_for_executor` 批量写入（1s flush），不阻塞事件循环
- `on_stop()` 同步 flush 剩余 buffer（shutdown 可接受）
- `node_type` 从 config 读取（不硬编码）

#### HealthActor (`node/actors/health_actor.py`, ~190行)
- **职责:** 心跳 + 文件监控 + 自动恢复
- 心跳: 5s timer → Redis `tino:heartbeat:{type}` + `lifecycle_state` + `strategy_registry`
- 文件监控: daemon thread 10s poll → 通过共享 deque 发 rescan 命令
- 自动恢复: on_start 检查 was_running，15s 后自动恢复

#### MetricsActor (`node/actors/metrics_actor.py`, ~140行)
- **职责:** Equity 快照
- 60s timer → `portfolio.account()` + `portfolio.unrealized_pnls()`
- Redis publish + ring buffer + `queue_for_executor` 写入 PG

#### 共享工具 (`node/actors/_utils.py`)
- `ts_ns_to_iso()`, `redis_publish()`

#### _common.py 改动
- `load_components()` 创建 5 个 Actor 实例（替代单个 BridgeActor）
- 返回 `{"command": ..., "health": ...}` 用于 lifecycle 注入
- `inject_lifecycle_deps()` 签名更新
- live.py / sandbox.py 适配

---

### US-004: CacheConfig 集中化

**问题:** `live.py` 和 `sandbox.py` 各有一份几乎相同的 CacheConfig，缺少统一管理。

**改动:**
- 新建 `_common.py:build_cache_config(redis_host, redis_port, redis_password, is_sandbox)`
- `encoding="msgpack"`, `buffer_interval_ms=100`, `persist_account_events=True`
- `flush_on_start=is_sandbox` (sandbox=True 清空, live=False 恢复)
- live.py / sandbox.py 改为调用 `build_cache_config()`

**注意:** purge 设置（`purge_closed_orders_interval_mins` 等）属于 `LiveExecEngineConfig`（不是 CacheConfig），live.py/sandbox.py 中原有配置保持不动。

---

### US-005: Python 命名清理 (portfolio → strategy)

**问题:** portfolio→strategy 重命名只做了一半，到处是 shim 和 alias。

**改动:**
- **删除 alias:** `PortfolioConfig`, `PortfolioEntry`, `PortfolioRegistry`, `load_portfolio_config`, `start_portfolio/pause_portfolio/resume_portfolio/flatten_stop_portfolio`
- **删除 shim 文件:** `portfolio/loader.py` (15行), `node/portfolio_registry.py` (6行)
- **变量重命名:** `portfolio_config` → `strategy_bundle` (runner.py ~30处, optimizer.py, factory.py, _common.py), `portfolio_name` → `strategy_name` (loader.py, risk_guard.py)
- **API:** `PortfolioLifecycleRequest` → `StrategyLifecycleRequest`, Redis key `portfolio_registry` → `strategy_registry`
- **保留:** `/portfolio/*` API 端点作为 deprecated redirect（Rust CLI 兼容），`process_manager.portfolio_name` 参数标记 DEPRECATED

**保留不动的 (NT Portfolio API):** `engine.portfolio.*`, `self.portfolio.*`, `PortfolioStatistic`, `PortfolioAnalyzer`, `portfolio_analytics`

---

### US-006: 前端命名清理 (TypeScript)

**改动:**
- `StrategiesTab.tsx`: `PortfolioInfo` → `RuntimeEntry`, `PortfolioButton` → `RuntimeEntryButton`, `portfolios` → `runtimeEntries`, API `/portfolios` → `/strategies`
- `BacktestCompareTab.tsx`: `Portfolio` → `StrategyInfo`, API 更新
- `StrategyPanel.tsx`: `PortfolioInfo` → `StrategyInfo`
- `backtest/page.tsx`: `isPortfolio` → `isBundle`
- `useWebSocket.ts`: `portfolios` → `strategies_registry`

**保留:** `translations.ts` 中的投资组合 UI 文案, `types.ts` 的 `PortfolioAnalytics`

---

### US-007: 测试更新 + 验证

- 重写 `test_node_portfolio.py` → 验证 5 个新 Actor（不再验证 BridgeActor）
- 更新 `test_config.py`, `test_loader.py`, `test_runner_compat.py` 中的 alias 引用
- **200 tests passed**, `npm run build` 成功

---

## 三、Architect Review 修复

| 问题 | 严重度 | 修复 |
|------|--------|------|
| DbWriterActor 硬编码 `node_type="live"` | Critical | `self._node_type` from `DbWriterActorConfig` |
| DbWriterActor 只持久化 PositionClosed | Critical (回归) | 恢复全事件 UPSERT (Opened/Changed/Closed) |
| on_stop 同步 flush 未文档化 | Moderate | 添加注释说明 shutdown 可接受阻塞 |
| 1s durability 窗口未文档化 | Minor | Config 字段注释说明 trade-off |
| CommandActor portfolio shims 无注释 | Minor | 添加 `# COMPAT` 注释 |

---

## 四、UltraWork NT 文档对照审查

对照 `docs/guide/` 中 4 份 NT 文档进行的最终审查:

### ✅ 符合 NT 最佳实践

| 检查项 | 结果 |
|--------|------|
| Actor 生命周期 (on_start/on_stop) | ✅ |
| 数据订阅在 on_start 中 | ✅ |
| 事件循环不阻塞 (queue_for_executor) | ✅ |
| CacheConfig 参数正确 | ✅ (修复后) |
| Thread safety (deque + GIL) | ✅ |
| Actor SRP (单一职责) | ✅ |

### 已修复的 NT 合规问题

| 问题 | 说明 |
|------|------|
| CacheConfig 错放 purge 参数 | purge 属于 `LiveExecEngineConfig`，不是 `CacheConfig`。已从 `build_cache_config()` 移除（live.py/sandbox.py 原有正确配置） |
| CacheConfig 缺少 `persist_account_events` | 已补上 `persist_account_events=True` |

### 已知可接受的设计权衡

| 项目 | 说明 |
|------|------|
| Redis sync 操作在事件循环上 | publish/setex 是 sub-ms，和旧 BridgeActor 一样 |
| DbWriter 1s flush 窗口 | 非阻塞 vs 即时持久化。crash 最多丢 1s 数据 |
| CommandActor daemon thread | 保留 SUBSCRIBE+deque 模式（零延迟推送），不用 timer 轮询 |

---

## 五、文件清单

### 新建 (12 个)

```
src/tinohelm/strategy/module_loader.py
src/tinohelm/node/actors/__init__.py
src/tinohelm/node/actors/_utils.py
src/tinohelm/node/actors/snapshot_actor.py
src/tinohelm/node/actors/command_actor.py
src/tinohelm/node/actors/db_writer_actor.py
src/tinohelm/node/actors/health_actor.py
src/tinohelm/node/actors/metrics_actor.py
src/tinohelm/backtest/result/__init__.py
src/tinohelm/backtest/result/statistics.py
src/tinohelm/backtest/result/extract.py
src/tinohelm/backtest/tearsheet.py
```

### 删除 (3 个)

```
src/tinohelm/portfolio/loader.py          (15行 shim)
src/tinohelm/node/portfolio_registry.py   (6行 shim)
src/tinohelm/backtest/result.py           (1781行 → 拆分为包)
```

### 主要修改 (15+)

```
src/tinohelm/node/_common.py              — 5 Actor 加载 + CacheConfig helper
src/tinohelm/node/live.py                 — build_cache_config + Actor wiring
src/tinohelm/node/sandbox.py              — build_cache_config + Actor wiring
src/tinohelm/node/lifecycle_controller.py — 删除 4 个 portfolio alias
src/tinohelm/node/strategy_registry.py    — 删除 2 个 portfolio alias
src/tinohelm/node/factory.py              — portfolio_config → strategy_bundle
src/tinohelm/strategy/registry.py         — 统一加载器 + sys.path 泄漏修复
src/tinohelm/strategy/validator.py        — 统一加载器
src/tinohelm/strategy/loader.py           — 统一加载器 + portfolio_name → strategy_name
src/tinohelm/portfolio/config.py          — 删除 PortfolioConfig alias + load_portfolio_config
src/tinohelm/backtest/runner.py           — portfolio_config → strategy_bundle + ProgressReporter + tearsheet
src/tinohelm/backtest/optimizer.py        — shared_portfolio_config rename
src/tinohelm/api/routes/node.py           — StrategyLifecycleRequest + strategy_registry key
src/tinohelm/api/routes/strategy.py       — 统一加载器
src/tinohelm/api/routes/optimize.py       — 统一加载器
src/tinohelm/actors/risk_guard.py         — portfolio_name → strategy_name
src/tinohelm/core/process_manager.py      — portfolio_name deprecated

src/web/src/app/trading/components/tabs/StrategiesTab.tsx   — 全面重命名
src/web/src/app/trading/components/tabs/BacktestCompareTab.tsx
src/web/src/app/trading/components/StrategyPanel.tsx
src/web/src/app/backtest/page.tsx
src/web/src/hooks/useWebSocket.ts

tests/node/test_node_portfolio.py         — 重写验证 5 Actor
tests/portfolio/test_config.py            — 删除 alias 测试
tests/portfolio/test_loader.py            — 更新 import
tests/backtest/test_runner_compat.py      — portfolio → strategy
```

---

## 六、后续待办

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P1 | Rust CLI/TUI portfolio→strategy | 156 处引用，用户决定延后。API `/portfolio/*` deprecated redirect 暂时保留 |
| P2 | `portfolio/` 目录迁移 | `portfolio/config.py` → `strategy/bundle.py`，import 面广，风险较大 |
| P2 | `bridge_actor.py` 删除 | 旧文件保留为参考，`_common.py` 不再 import。确认无问题后可删 |
| P3 | StreamingConfig (Feather) | 三层存储的冷层，计划中标记为 optional |
| P3 | MessageBusConfig 配置 | 当前用默认值，可配置 heartbeat_interval、autotrim_mins 等 |
| P3 | Alembic migration | 如果 PG schema 有实际变更需创建迁移文件 |
