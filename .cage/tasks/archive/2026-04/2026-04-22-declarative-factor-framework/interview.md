# 声明式因子研究框架 — 访谈记录

## 项目概述

**定位**: TinoHelm 量化平台的因子研究子系统重写。用声明式框架替换现有 `src/tinohelm/research/` 模块。

**一句话目标**: 因子开发者只写因子计算逻辑（`@factor` 装饰器），引擎自动完成数据注入、依赖求解、向量化计算、评估报告生成。

## 目标

1. **声明式因子定义**: `@factor` 装饰器 + 参数名即数据依赖，引擎自动推断 FactorSpec
2. **自动数据注入**: 从参数名查字段别名表 → 自动拉取数据 → 构造 Panel (T, N) → 注入 kernel
3. **统一评估管道**: IC / RankIC / IC IR / IC t-stat / IC Decay / Quantile PnL / Turnover 自动生成
4. **工业级引擎**: PIT 正确性、可复现、可缓存、可观测、Backend 可替换
5. **多因子批量运行**: DAG 依赖求解 + 数据合并 + 并行调度
6. **前后端并重**: 全新 QDS 设计规范前端 + 完整后端引擎

## 约束

### v1 边界（每根柱子的缩减版）

| 柱子 | v1 做到 | v2+ 再做 |
|------|---------|---------|
| **多 Backend** | PandasBackend 跑通全闭环 → 再加 PolarsBackend | Numba/Rust/Cupy |
| **PIT** | 手工 CSV 月度快照 + bar 封口保证 + AST 查 shift(-n) + 新币隔离 7 天 + 资金费 as-of 60s 延迟 | 自动 universe 历史 API + 数据版本快照 + 复杂 as-of 配置 |
| **实盘复用** | Panel 抽象 + lookback 语义 v1 定死接口，只实现 ParquetProvider | LiveFeedProvider + IncrementalScheduler |
| **缓存** | L2 disk parquet + 完整 key (含 code_hash/data_snapshot) + manifest + 部分命中 + DAG 失效传播 | L1 内存 + L3 对象存储 |
| **Observer** | 结构化 JSON 日志 + 基础 span (data_load / kernel_exec / evaluate) + 每因子 compute 时长/内存/NaN/输出分布 + run_id 汇总 | Prometheus/Grafana + 告警 + 滚动 IC + 在线离线对比 |

### 其他约束

- **命名**: 全部换为 factor 体系（`/api/factor/*`、`tino:factor:*`、`factor_runs` DB 表）
- **替换范围**: 整体重写替换 `src/tinohelm/research/`，不保留旧代码
- **DataLayer**: 重构 loader.py 为全新 DataLayer，原生支持多币种并行加载 + 时间对齐
- **funding_rate 存储升级**: 从 JSON 缓存升级为 Parquet 数据资产
- **前端**: 按 QDS 设计规范（TinoHelmDS Skill）重写，前后端并重

## 非目标

- 策略回测（PnL、仓位、执行）— 策略层的事
- 因子自动生成 / 遗传算法 — 研究方法学
- 多租户 / 权限系统 — 假设可信环境
- 分布式调度 — 单机多核，因子级并行

## 验收标准

### 端到端验收场景（8 步）

1. 写一个 `mom_reversal.py`，用 `@factor` 装饰器，参数写 `close` 和 `funding_rate`，放到 `~/.tino/research/factors/`
2. 引擎自动发现它，做静态检查（参数名解析、AST 查 shift(-n)），注册到 Registry
3. Web UI 上看到新因子、选 universe（预置 PIT CSV）、时间范围、frequency，点 Run
4. 后端构建 DAG、DataLayer 拼装 Panel (T, N)、PandasBackend 执行 kernel、Observer 记录日志
5. 评估管道自动计算 IC / RankIC / IC IR / decay / quantile PnL / turnover
6. 结果缓存到 L2 disk，再次相同参数秒出
7. 前端展示完整报告（QDS 设计规范）
8. 再写 5 个因子一起跑，DAG 自动合并数据依赖、并行调度

### 12 个起手因子

| 维度 | 因子 | 数据依赖 |
|------|------|---------|
| price/trend | ret_N | bar (close) |
| price/trend | rsi_signal | bar (close) |
| volatility | parkinson_vol | bar (high, low) |
| volume/flow | vol_ratio | bar (volume) |
| volume/flow | obv_slope | bar (close, volume) |
| microstructure-bar | vwap_dev | bar (close, vwap) |
| microstructure-bar | trade_imbalance | trade_tick |
| microstructure-liquidity | amihud_illiq | bar (close, volume) |
| crypto-funding | funding_rate_level | funding_rate |
| crypto-funding | funding_rate_mom | funding_rate |
| crypto-OI | oi_change | metrics (sum_open_interest) |
| crypto-orderbook | orderbook_imbalance_L1 | bookTicker (bid, ask) |

### 数据管道覆盖确认

| 数据类型 | 管道状态 | 存储格式 |
|---------|---------|---------|
| bar (OHLCV) | ✅ 已有 | Parquet |
| trade_tick | ✅ 已有 | Parquet |
| funding_rate | ✅ 已有但需升级 | JSON → Parquet |
| metrics (OI) | ✅ 已有 | Parquet |
| bookTicker (L1) | ✅ 已有 | Parquet |

### Universe

- v1 走预置 PIT universe（手工 CSV 月度快照）
- 支持可扩展的 universe 维护方式，默认走预置
- 前端下拉框选已有 universe

## 实体模型

| 实体 | 类型 | 关键属性 |
|------|------|---------|
| Factor | 核心领域 | name, kernel, inputs, lookback, category, version |
| FactorSpec | 核心领域 | 契约化表示: code_hash, InputSpec[], OutputSpec |
| Panel | 核心领域 | (T, N) 数据面板, T=lookback 时间步, N=universe symbols |
| Universe | 核心领域 | PIT 历史快照, symbols 列表, 月度 CSV |
| FieldAlias | 核心领域 | 参数名 → (table, field) 映射 |
| InputSpec | 核心领域 | alias, table, field, lookback, pit |
| OutputSpec | 核心领域 | kind, dtype, shape |
| ComputeBackend | 辅助 | Pandas/Polars 实现 shift/rolling/rank 等算子 |
| DataLayer | 辅助 | 多币种并行加载 + 时间对齐 + Panel 构造 |
| Registry | 辅助 | 因子注册表 + 静态检查 + 发现 |
| DAG/Planner | 辅助 | 依赖合并 + lookback closure + 拓扑排序 |
| Scheduler | 辅助 | 拓扑调度 + 因子级并行 |
| Evaluator | 辅助 | IC/IR/decay/quantile PnL/turnover |
| Cache | 辅助 | L2 disk + manifest + 部分命中 + DAG 失效传播 |
| Observer | 辅助 | 结构化日志 + span + 指标采集 |

### 本体论收敛

| 轮次 | 实体数 | 稳定性 |
|------|--------|--------|
| 1 | 8 | N/A |
| 2-4 | 13 | 1.0 |
| 9 | 14 (+Universe) | 0.93 |
| 10-11 | 14 | 1.0 |

第 9 轮后模型完全收敛，连续 6 轮无变更。

## 访谈记录

| 轮次 | 维度 | 问题 | 回答 |
|------|------|------|------|
| 1 | 架构对齐 | 新框架与现有 research/ 的关系？ | 重写替换 |
| 2 | 约束 | v1 的最小可用范围？ | 草案全集——但实际是每柱的缩减版（Claude Desktop 建议的 "v1 做到哪一步"） |
| 3 | 验收标准 | v1 验收演示场景？ | 全链路工业级 |
| 4 | 约束 (反驳者) | 草案全集 vs 每柱缩减版矛盾？ | 每柱缩减版 |
| 5 | 架构对齐 | 数据拼装层怎么做？ | 重构 loader 为全新 DataLayer |
| 6 | 约束 | v1 前端范围？ | 前后端并重，按 QDS 设计规范重写 (TinoHelmDS Skill) |
| 7 | 验收标准 | 8 步端到端场景确认？ | 完全符合 |
| 8 | 架构对齐 | API/Redis/DB 命名？ | 换新名 factor |
| 9 | 约束 | 内置因子迁移策略？ | 12 个起手因子（按信息维度去冗余 + 补齐 crypto） |
| 10 | 架构对齐 | OI/orderbook 数据管道覆盖？ | 已有管道支持，funding_rate 需升级为 Parquet |
| 11 | 验收标准 | Universe 定义方式？ | 预置 PIT universe + 支持扩展 |

## 模糊度收敛

| 轮次 | 模糊度 | 焦点 |
|------|--------|------|
| 初始 | 55.9% | — |
| 1 | 50.5% | 架构对齐 |
| 2 | 38.3% | 约束 |
| 3 | 33.7% | 验收标准 |
| 4 | 28.2% | 约束 (反驳者) |
| 5 | 26.0% | 架构对齐 |
| 6 | 23.3% | 约束 |
| 7 | 17.7% | 验收标准 |
| 8 | 15.3% | 架构对齐 |
| 9 | 12.7% | 约束 |
| 10 | 11.2% | 架构对齐 |
| 11 | 9.5% | 验收标准 |
