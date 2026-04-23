# 声明式因子研究框架 -- 需求文档

## 概述

用声明式框架替换现有 `src/tinohelm/research/` 模块。因子开发者只写因子计算逻辑（`@factor` 装饰器），引擎自动完成数据注入、依赖求解、向量化计算、评估报告生成。

**替换范围**: 完整替换 `src/tinohelm/research/` 全部 11 个 .py 文件（含 `__init__.py` 和 `_template.py`） + `src/tinohelm/api/routes/research.py` + `src/web/src/app/research/` 全部前端代码。新模块入口为 `src/tinohelm/factor/`，API 前缀为 `/api/factor/`，Redis key 前缀为 `tino:factor:`。

---

## 用户故事

### US-001: 声明式因子定义与自动注册

**作为**因子开发者，**我希望**用 `@factor` 装饰器 + 函数签名声明因子逻辑，引擎自动推断数据依赖并注册到 Registry，**以便**我只专注计算逻辑，不需要手动配置数据来源和元数据。

**验收标准**:
- AC-1.1: 用 `@factor(category="动量", lookback=20)` 装饰一个函数，函数签名 `def my_factor(close: Panel, volume: Panel) -> Panel`，引擎能通过字段别名表解析 `close` → `(bar, close)` 和 `volume` → `(bar, volume)`
- AC-1.2: AST 静态检查能检测 kernel 中的 `shift(-n)` 调用，自动累加 lookback 需求
- AC-1.3: 每个因子文件放到 `~/.tino/research/factors/` 后，Registry 自动发现并生成 FactorSpec（包含 code_hash, InputSpec[], OutputSpec）
- AC-1.4: 因子 code_hash 变化时，Registry 标记为需要重新计算（旧缓存失效）
- AC-1.5: API `GET /api/factor/list` 返回所有已注册因子，含 name, category, inputs, lookback

**自动化测试验证**:
- 单元测试：给定一个带 `@factor` 装饰的函数，断言 `FactorSpec` 的 InputSpec 列表正确解析
- 单元测试：给定包含 `shift(-3)` 的 kernel AST，断言 lookback 自动累加 3
- 集成测试：向 `~/.tino/research/factors/` 写入一个 `.py` 文件，调用 Registry.scan()，断言因子被发现

---

### US-002: 字段别名表与多数据源支持

**作为**因子开发者，**我希望**通过参数名直接引用数据字段（如 `close`, `funding_rate`, `bid_price`），引擎自动映射到正确的数据源和字段，**以便**因子代码无需关心底层数据表结构。

**验收标准**:
- AC-2.1: 内置别名表覆盖以下映射:
  - `open`, `high`, `low`, `close`, `volume` → `(bar, <field>)`
  - `funding_rate`, `mark_price` → `(funding_rate, <field>)`
  > 注: `vwap` 不作为别名条目（bar Parquet 中无 `vwap` 列）。需要 vwap 的因子应声明依赖 `close, high, low, volume`，在 kernel 内部计算。
  - `bid_price`, `bid_qty`, `ask_price`, `ask_qty` → `(bookTicker, <field>)`
  - `trade_price`, `trade_qty`, `trade_side` → `(trade_tick, <field>)`
  - `sum_open_interest`, `open_interest_value` → `(metrics, <field>)`
- AC-2.2: 别名解析冲突时（同名不同表），编译期抛出 `AmbiguousAliasError`
- AC-2.3: 用户可通过 `@factor(aliases={"my_field": ("custom_table", "custom_col")})` 自定义别名

**自动化测试验证**:
- 单元测试：断言默认别名表的 `close` 解析为 `("bar", "close")`
- 单元测试：构造一个冲突别名场景，断言抛出 `AmbiguousAliasError`
- 单元测试：构造自定义别名 override，断言覆盖生效

---

### US-003: Panel 数据面板与 DataLayer

**作为**引擎，**我需要**将多数据源的数据加载、时间对齐、PIT 过滤，统一拼装为 Panel (T, N) 面板，**以便** kernel 接收到的数据是对齐的、PIT 正确的。

**验收标准**:
- AC-3.1: DataLayer 接受 `(symbols: list[str], fields: list[InputSpec], time_range, interval)` 参数，返回 `dict[alias, Panel]`
- AC-3.2: Panel 的 T 维度 = lookback + 请求时间范围，N 维度 = universe symbols
- AC-3.3: 不同频率数据（bar 5m vs funding_rate 8h）自动对齐到请求 interval（forward-fill）
- AC-3.4: PIT 过滤: 新币上市后隔离 7 天（不纳入 universe），funding_rate 有 60s as-of 延迟
- AC-3.5: 多币种数据并行加载（asyncio / concurrent.futures）
- AC-3.6: DataLayer 直接复用现有 `data/catalog.py` + `data/converters/` 的 Parquet 读取能力

**自动化测试验证**:
- 单元测试：给定 2 个 symbol + bar(close) + funding_rate 两种数据源，断言返回的 Panel 时间对齐且 shape 正确
- 单元测试：模拟新币上市时间戳，断言 7 天隔离期内该币不在 Panel 的 N 维度中
- 单元测试：断言 funding_rate 的值在 as-of 延迟窗口内不可见

---

### US-004: PIT Universe 管理

**作为**因子研究者，**我希望**选择一个预置的 PIT universe（月度 CSV 快照），引擎根据每个时间点的 universe 组成动态调整 Panel 的 N 维度，**以便**避免生存偏差。

**验收标准**:
- AC-4.1: `~/.tino/research/universes/` 目录存放 CSV 文件，格式: `date,symbol` 列，每月一行
- AC-4.2: Universe 加载器能按时间点查询当前有效的 symbol 列表
- AC-4.3: 预置至少一个 `binance_perp_top20.csv` universe（Binance 永续合约 Top 20 市值）
- AC-4.4: API `GET /api/factor/universes` 返回可选的 universe 列表
- AC-4.5: 前端下拉框展示可用 universe

**自动化测试验证**:
- 单元测试：给定一个 CSV 文件，断言 `2025-02-15` 时间点返回的 symbol 列表与 `2025-02-01` 行一致
- 单元测试：给定一个新币在 `2025-03-01` 加入的 CSV，断言 `2025-03-05`（隔离期内）不含该币

---

### US-005: ComputeBackend 与 Kernel 执行

**作为**引擎，**我需要** kernel 在统一的 ComputeBackend 上执行（v1 为 PandasBackend），backend 提供 shift / rolling / rank 等基础算子，**以便**因子代码在不同 backend 间可移植。

**验收标准**:
- AC-5.1: PandasBackend 实现 `shift(panel, n)`, `rolling(panel, window, fn)`, `rank(panel, axis)`, `clip(panel, lower, upper)` 算子
- AC-5.2: kernel 函数签名 `def kernel(*panels) -> Panel`，backend 通过闭包或注入方式提供给 kernel
- AC-5.3: 12 个内置因子全部用 `@factor` 装饰器重写，在 PandasBackend 上通过测试
- AC-5.4: PolarsBackend 接口定义（AbstractBackend），v1 不要求实现但接口必须存在

**自动化测试验证**:
- 单元测试：PandasBackend.shift(panel, 3) 结果与 `pd.DataFrame.shift(3)` 一致
- 单元测试：PandasBackend.rolling(panel, 20, "mean") 结果正确
- 集成测试：12 个内置因子在 PandasBackend 上全部 pass（输出 shape 和数值正确性）

---

### US-006: DAG 依赖求解与调度

**作为**引擎，**我需要**在多因子批量运行时，自动合并数据依赖、构建 DAG、拓扑排序、并行调度，**以便**相同数据只加载一次，独立因子并行计算。

**验收标准**:
- AC-6.1: Planner 接受 `list[FactorSpec]`，合并所有 InputSpec 的数据需求（去重），生成 DataRequest
- AC-6.2: Planner 计算 lookback closure（所有因子的最大 lookback 作为数据加载的 T 前缀）
- AC-6.3: 拓扑排序产出执行计划（因子无环，独立因子可并行）
- AC-6.4: Scheduler 按拓扑序执行，独立因子用 `ProcessPoolExecutor` 并行（CPU 密集）
- AC-6.5: 6 个因子一起跑时，DAG 合并后的数据加载次数 <= 3（bar/funding_rate/trade_tick）

**自动化测试验证**:
- 单元测试：给定 3 个因子（2 个依赖 bar, 1 个依赖 funding_rate），断言 DataRequest 去重为 2 组
- 单元测试：给定独立因子 A, B, C，断言拓扑排序允许全部并行
- 集成测试：批量运行 6 个因子，断言总数据加载次数符合预期

---

### US-007: 统一评估管道

**作为**因子研究者，**我希望**因子计算完成后，引擎自动运行完整的评估指标集（IC / RankIC / IC IR / IC t-stat / IC Decay / Quantile PnL / Turnover），**以便**我不需要手动调用分析函数。

**验收标准**:
- AC-7.1: Evaluator 接受 `(factor_values: Panel, forward_returns: Panel, params: EvalConfig)` 输入
- AC-7.2: 输出完整评估报告（与现有 `analysis.py` 指标一致但结构化为 EvalResult dataclass）:
  - IC Summary: ic_mean, ic_std, ir, ic_tstat, ic_positive_pct
  - IC Decay: 多 horizon 衰减曲线 + half_life
  - Quantile PnL: N 分位累积收益
  - Distribution: 直方图 + 偏度/峰度/自相关
  - Turnover: daily, annualized, fee_drag
  - Rating: 0-3 星评级
- AC-7.3: 评估管道是可配置的（forward_periods, n_quantiles, freq）
- AC-7.4: 评估结果 JSON 可序列化（NaN/Infinity 自动替换为 None）

**自动化测试验证**:
- 单元测试：给定合成 factor + forward_returns 数据，断言 EvalResult 所有字段非空且数值合理
- 单元测试：注入 NaN 值，断言序列化后无 NaN

---

### US-008: L2 磁盘缓存

**作为**因子研究者，**我希望**相同参数的因子运行结果被缓存到磁盘，再次运行时秒出，**以便**加速迭代。

**验收标准**:
- AC-8.1: 缓存 key 包含: factor_name, code_hash, data_snapshot_hash, params_hash, universe_hash, time_range
- AC-8.2: 缓存存储格式为 Parquet（因子值 Panel）+ JSON（评估结果）
- AC-8.3: 缓存命中时跳过 kernel 执行和评估，直接返回缓存结果
- AC-8.4: 部分命中: 若因子值 Panel 缓存存在但评估参数变化，只重新运行评估（跳过 kernel）
- AC-8.5: DAG 失效传播: 因子 A 的 code_hash 变化 → 依赖 A 的因子 B 的缓存也失效
- AC-8.6: 缓存 manifest 文件记录所有缓存条目元信息，支持扫描和清理

**自动化测试验证**:
- 单元测试：首次运行写入缓存，第二次运行命中缓存（通过 mock 验证 kernel 未被调用）
- 单元测试：修改 code_hash 后缓存未命中
- 单元测试：部分命中场景 — 修改 forward_period，断言 kernel 跳过但 evaluator 重新运行

---

### US-009: Observer 可观测性

**作为**引擎运维者，**我希望**每次因子运行都产生结构化日志和性能 span，**以便**调试和监控。

**验收标准**:
- AC-9.1: 每个因子运行产生结构化 JSON 日志，包含: run_id, factor_name, phase (data_load / kernel_exec / evaluate), duration_ms, memory_delta_mb
- AC-9.2: 基础 span 覆盖 3 个阶段: data_load, kernel_exec, evaluate
- AC-9.3: 每因子输出统计: compute_time_ms, output_nan_pct, output_mean, output_std, output_min, output_max
- AC-9.4: run_id 汇总: 总因子数, 总时间, 缓存命中率, 错误数

**自动化测试验证**:
- 单元测试：运行一个因子后，断言 Observer 的 spans 列表包含 data_load / kernel_exec / evaluate 三项
- 单元测试：断言每个 span 有 duration_ms > 0

---

### US-010: 异步任务管道 (深度诊断)

**作为**因子研究者，**我希望**提交深度诊断任务（含 shuffle test、跨品种 IC、参数扫描），引擎异步执行并通过 WebSocket 推送进度，**以便**我不需要等待长时间计算。

**验收标准**:
- AC-10.1: API `POST /api/factor/run` 提交异步任务，返回 `{run_id, status: "queued"}`
- AC-10.2: Worker 基于 `core/async_queue_worker.py` 原语，Redis queue key 为 `tino:factor:queue`
- AC-10.3: 进度通过 Redis PubSub `tino:factor:progress:{run_id}` 推送，EventBridge 中继到 WebSocket
- AC-10.4: DB 表 `factor_runs` 持久化任务状态（替换现有 `research_jobs`）
- AC-10.5: 完成事件 `factor.completed` / 失败事件 `factor.failed` 通过 notification-router 路由到 toast
- AC-10.6: API `GET /api/factor/runs` 列出任务历史；`GET /api/factor/report/{run_id}` 获取完整报告

**自动化测试验证**:
- 单元测试：mock Redis，断言 enqueue_job 将 run_id 推入 `tino:factor:queue`
- 单元测试：mock DB，断言 _process_job 将状态从 running → completed
- E2E 测试：提交一个任务 → 轮询直到 completed → 验证报告 JSON 结构完整

---

### US-011: 前端因子列表与运行配置

**作为**因子研究者，**我希望**在 Web UI 上看到所有已注册因子，选择 universe、时间范围、frequency，提交运行，**以便**完成全链路操作。

**验收标准**:
- AC-11.1: `/factor` 页面左侧面板展示因子列表（分类手风琴），支持多选（最多 8 个）
- AC-11.2: 配置面板: universe 下拉框、时间范围选择器、interval 选择、forward_period / quantiles 参数
- AC-11.3: "运行探索"按钮调用 `POST /api/factor/explore`（同步快速分析）
- AC-11.4: "提交深度诊断"按钮调用 `POST /api/factor/run`（异步深度分析）
- AC-11.5: 遵循 QDS 设计规范 (TinoHelmDS Skill)，使用 Tailwind 语义类 + QDS 组件

**自动化测试验证**:
- E2E (Playwright): 页面加载 → 因子列表可见 → 选择因子 → 点击运行 → 结果面板出现

---

### US-012: 前端报告展示

**作为**因子研究者，**我希望**运行结果以图表形式展示（IC 时间序列、IC 衰减曲线、分位收益、分布直方图），**以便**直观评估因子质量。

**验收标准**:
- AC-12.1: 探索结果面板展示: 因子摘要表、IC 时间序列折线图、IC 衰减柱状图、分位累积收益折线图、分布直方图、Turnover KPI
- AC-12.2: 深度诊断报告页面 (`/factor/report/[id]`) 展示 4-tab 报告:
  - Signal Profile tab: 分布直方图 + ACF + 统计摘要
  - Predictive Power tab: IC 时间序列 + 衰减曲线 + 分位 PnL + 多 horizon 对比
  - Robustness tab: Shuffle test 分布 + 分段 IC 柱状图 + 跨品种 IC
  - Cost & Params tab: Edge waterfall + 参数扫描曲线 / 热力图
- AC-12.3: 所有图表使用 `chartTheme.ts` 统一主题（CHART_TOOLTIP_PROPS, CHART_GRID_STYLE 等）
- AC-12.4: 遵循 QDS 设计规范 (TinoHelmDS Skill)

**自动化测试验证**:
- E2E (Playwright): 深度诊断报告页面加载 → 4 个 tab 可切换 → 每个 tab 有图表渲染

---

### US-013: 12 个内置因子迁移

**作为**平台，**我需要**将 12 个起手因子用 `@factor` 装饰器重写为声明式风格，**以便**验证框架的表达能力和正确性。

**验收标准**:
- AC-13.1: 以下 12 个因子全部用 `@factor` 装饰器重写:
  | 因子 | 数据依赖 |
  |------|---------|
  | ret_N | close |
  | rsi_signal | close |
  | parkinson_vol | high, low |
  | vol_ratio | close |
  | obv_slope | close, volume |
  | vwap_dev | close, high, low, volume |
  | trade_imbalance | trade_price, trade_qty, trade_side |
  | amihud_illiq | close, volume |
  | funding_rate_level | funding_rate |
  | funding_rate_mom | funding_rate |
  | oi_change | sum_open_interest |
  | orderbook_imbalance_L1 | bid_price, bid_qty, ask_price, ask_qty |
- AC-13.2: 每个因子的输出与旧实现数值误差 < 1e-10（对于使用相同数据源的因子）
- AC-13.3: 新增的 crypto 因子（funding_rate_level, funding_rate_mom, oi_change, orderbook_imbalance_L1）在对应数据源存在时正确计算

**自动化测试验证**:
- 单元测试：每个因子在合成数据上产出 Panel，断言 shape 和数值正确
- 回归测试：对于 bar 数据源因子，断言新旧实现的输出值差异 < 1e-10

---

### US-014: funding_rate 存储升级

**作为**数据管道，**我需要**将 funding_rate 从 JSON 缓存升级为 Parquet 数据资产，**以便** DataLayer 可以用统一的 Parquet 读取路径处理所有数据类型。

**验收标准**:
- AC-14.1: funding_rate 数据写入 `~/.tino/data/catalog/data/funding_rate/{instrument_id}/` 目录（Parquet 格式）
- AC-14.2: DataLayer 加载 funding_rate 时走 Parquet 路径，不再依赖 JSON 缓存
- AC-14.3: 已有 JSON 缓存数据可通过迁移脚本转换为 Parquet
- AC-14.4: `data/funding_cache.py` 保留为降级回退（缓存未迁移时仍可用），但新写入一律走 Parquet

**自动化测试验证**:
- 单元测试：写入一批 funding_rate 记录到 Parquet，断言 DataLayer 能正确读取
- 单元测试：迁移脚本将 JSON 转为 Parquet 后，读取结果一致

---

### US-015: 旧模块清理与路由迁移

**作为**平台维护者，**我需要**清理 `src/tinohelm/research/` 旧代码、迁移 API 路由、更新 EventBridge 和 notification-router 的 channel 映射，**以便**新旧模块不共存。

**验收标准**:
- AC-15.1: `src/tinohelm/research/` 目录删除（11 个 .py 文件，含 `__init__.py` 和 `_template.py`）
- AC-15.2: `src/tinohelm/api/routes/research.py` 删除，新路由在 `src/tinohelm/api/routes/factor.py`
- AC-15.3: `app.py` 中 import 和 router 注册从 `research` 改为 `factor`
- AC-15.4: EventBridge `_CHANNEL_TYPE_MAP` 中 `"tino:research:"` 改为 `"tino:factor:"`
- AC-15.5: notification-router.ts 中 `research.*` 事件改为 `factor.*`
- AC-15.6: DB migration 011: 创建 `factor_runs` 表（schema 参考现有 `research_jobs` 但字段更丰富）
- AC-15.7: 前端路由从 `/research` 迁移到 `/factor`（含 Sidebar 导航链接和 TopBar 路由标题映射）

**自动化测试验证**:
- 构建验证：`pip install -e .` 无 import 错误
- 构建验证：`npm run build` 无编译错误
- 集成测试：`GET /api/factor/list` 返回 200

---

## 非功能需求

### NFR-1: 性能
- 单因子 + 单币种（100K bars）kernel 执行 < 500ms
- 6 因子批量运行（DAG 优化后）< 5s（不含 I/O）
- 缓存命中时端到端响应 < 200ms

### NFR-2: 可扩展性
- ComputeBackend 抽象允许未来添加 Polars/Numba/Rust 实现
- Universe provider 抽象允许未来添加 API 自动获取
- 缓存层抽象允许未来添加 L1 内存 / L3 对象存储

### NFR-3: 可观测性
- 每次运行产生结构化 JSON 日志（不依赖外部系统）
- 基础性能 span 覆盖 3 个阶段

### NFR-4: 前端设计规范
- 所有前端页面严格遵循 QDS 设计规范 (TinoHelmDS Skill)
- 使用 Tailwind 语义类 + QDS 组件，禁止遗留 class
- 图表使用 `chartTheme.ts` 统一主题
