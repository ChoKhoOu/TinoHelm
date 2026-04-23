# 声明式因子研究框架 -- 任务清单

## 子任务列表

---

### s1: 核心类型与字段别名表

**描述**: 创建 `src/tinohelm/factor/` 包骨架，定义核心类型（FactorSpec, InputSpec, OutputSpec, Panel, EvalConfig, EvalResult, DataRequest）和字段别名表（FIELD_ALIAS）。

**depends_on**: []

**产出**:
- `src/tinohelm/factor/__init__.py`
- `src/tinohelm/factor/types.py` — 全部数据类型定义
- `src/tinohelm/factor/alias.py` — FIELD_ALIAS dict + 解析函数 resolve_alias()

**验证方式**:
- `python -c "from tinohelm.factor.types import FactorSpec, Panel; print('OK')"` 无 ImportError
- 单元测试 `tests/factor/test_alias.py`: 别名解析、冲突检测、自定义 override

---

### s2: @factor 装饰器与 AST 静态检查

**描述**: 实现 `@factor` 装饰器（decorator.py）和 AST shift 检测器（ast_check.py）。装饰器从函数签名 + 字段别名表自动生成 FactorSpec。

**depends_on**: [s1]

**产出**:
- `src/tinohelm/factor/decorator.py` — @factor 装饰器实现
- `src/tinohelm/factor/ast_check.py` — ShiftDetector 类

**验证方式**:
- 单元测试 `tests/factor/test_decorator.py`:
  - 给定 `@factor(category="动量", lookback=20) def f(close: Panel) -> Panel`，断言 FactorSpec 正确
  - 给定含 `shift(-3)` 的函数，断言 lookback 自动 +3
  - 给定含 `code_hash` 计算，断言源码变更后 hash 变化

---

### s3: Registry 因子注册表

**描述**: 实现 Registry，扫描 `~/.tino/research/factors/` 目录发现用户因子 + 内置因子注册。支持增量扫描、code_hash 变更检测。

**depends_on**: [s2]

**产出**:
- `src/tinohelm/factor/registry.py` — Registry 类 (scan, get_spec, get_all_specs, get_kernel)

**验证方式**:
- 单元测试 `tests/factor/test_registry.py`:
  - 写入一个 `@factor` 装饰的 .py 文件到临时目录，Registry.scan() 后能发现
  - 修改文件内容后 rescan，断言 code_hash 变化

---

### s4: Universe PIT 管理

**描述**: 实现 Universe CSV 加载与 PIT 查询。支持按时间点查询 symbol 列表、新币隔离、预置 universe 文件。

**depends_on**: [s1]

**产出**:
- `src/tinohelm/factor/universe.py` — Universe 类 (load_csv, get_symbols_at, list_universes)
- `~/.tino/research/universes/binance_perp_top20.csv` — 预置 universe（或生成脚本）

**验证方式**:
- 单元测试 `tests/factor/test_universe.py`:
  - CSV 加载 + PIT 查询正确性
  - 新币隔离 7 天
  - list_universes 返回目录中所有 CSV

---

### s5: PandasBackend 计算后端

**描述**: 实现 AbstractBackend 接口和 PandasBackend（shift, rolling, rank, clip 算子）。

**depends_on**: [s1]

**产出**:
- `src/tinohelm/factor/backend/__init__.py`
- `src/tinohelm/factor/backend/base.py` — AbstractBackend ABC
- `src/tinohelm/factor/backend/pandas_backend.py` — PandasBackend 实现

**验证方式**:
- 单元测试 `tests/factor/test_pandas_backend.py`:
  - shift(panel, 3) 与 pd.DataFrame.shift(3) 一致
  - rolling(panel, 20, "mean") 正确
  - rank(panel, axis=0) 正确

---

### s6: DataLayer 数据加载层

**描述**: 实现 DataLayer，支持多币种并行加载、不同频率数据时间对齐（forward-fill）、PIT 过滤（新币隔离 + funding_rate as-of 延迟）。复用现有 `data/catalog.py` 的 Parquet 读取。

**depends_on**: [s1, s4]

**产出**:
- `src/tinohelm/factor/data_layer.py` — DataLayer 类 (load, _load_table, _align_time, _apply_pit)

**验证方式**:
- 单元测试 `tests/factor/test_data_layer.py`:
  - 2 symbols + bar(close) + funding_rate → Panel 时间对齐且 shape 正确
  - 新币隔离期内不在 Panel N 维度
  - funding_rate as-of 延迟

---

### s7: DAG Planner 与 Scheduler

**描述**: 实现 Planner（数据需求合并、lookback closure、拓扑排序）和 Scheduler（按拓扑序并行执行因子）。

**depends_on**: [s1, s5]

**产出**:
- `src/tinohelm/factor/engine/__init__.py`
- `src/tinohelm/factor/engine/planner.py` — Planner 类 (plan, plan_batch, merge_data_requests)
- `src/tinohelm/factor/engine/scheduler.py` — Scheduler 类 (execute, _parallel_group)

**验证方式**:
- 单元测试 `tests/factor/test_planner.py`:
  - 3 因子（2 bar + 1 funding_rate）→ DataRequest 去重为 2 组
  - lookback closure 取最大值
- 单元测试 `tests/factor/test_scheduler.py`:
  - 独立因子全部并行

---

### s8: 评估管道

**描述**: 将现有 `research/analysis.py` + `robustness.py` + `cost.py` + `param_scan.py` 的逻辑重组为结构化的评估管道（evaluator.py + 6 个子模块）。输出 EvalResult dataclass。

**depends_on**: [s1]

**产出**:
- `src/tinohelm/factor/evaluation/__init__.py`
- `src/tinohelm/factor/evaluation/evaluator.py` — Evaluator 类 (evaluate, evaluate_full)
- `src/tinohelm/factor/evaluation/ic.py` — IC/RankIC/IR/t-stat/decay
- `src/tinohelm/factor/evaluation/quantile.py` — Quantile PnL
- `src/tinohelm/factor/evaluation/distribution.py` — 分布统计
- `src/tinohelm/factor/evaluation/turnover.py` — Turnover
- `src/tinohelm/factor/evaluation/robustness.py` — shuffle/subsample/cross-symbol
- `src/tinohelm/factor/evaluation/cost.py` — edge waterfall
- `src/tinohelm/factor/evaluation/rating.py` — 评级

**验证方式**:
- 单元测试 `tests/factor/test_evaluation.py`:
  - 合成数据 → EvalResult 所有字段非空
  - NaN 值序列化后无 NaN
- 回归测试：与旧 analysis.py 输出数值对比

---

### s9: L2 磁盘缓存

**描述**: 实现 L2 磁盘缓存（cache_key 计算、Parquet + JSON 存储、部分命中、manifest）。

**depends_on**: [s1, s8]

**产出**:
- `src/tinohelm/factor/cache.py` — FactorCache 类 (lookup, store, invalidate, build_key)

**验证方式**:
- 单元测试 `tests/factor/test_cache.py`:
  - 首次 miss → store → 第二次 hit
  - 修改 code_hash → miss
  - 部分命中：factor_values hit + eval miss → 只重新评估

---

### s10: Observer 可观测性

**描述**: 实现 Observer（结构化 JSON 日志、基础 span、每因子输出统计）。

**depends_on**: [s1]

**产出**:
- `src/tinohelm/factor/observer.py` — Observer 类 (start_span, end_span, record_output_stats, summary)

**验证方式**:
- 单元测试 `tests/factor/test_observer.py`:
  - 运行后 spans 含 data_load / kernel_exec / evaluate
  - 每个 span 有 duration_ms > 0

---

### s11: Orchestrator 编排器

**描述**: 实现 Orchestrator（编排 Registry → Planner → DataLayer → Backend → Evaluator → Cache → Observer 的完整流程）。提供 `run()` 和 `batch_run()` 两个入口。

**depends_on**: [s3, s5, s6, s7, s8, s9, s10]

**产出**:
- `src/tinohelm/factor/engine/orchestrator.py` — Orchestrator 类 (run, batch_run)

**验证方式**:
- 集成测试 `tests/factor/test_e2e_single.py`: 单因子端到端通过
- 集成测试 `tests/factor/test_e2e_batch.py`: 多因子批量通过

---

### s12: 12 个内置因子重写

**描述**: 将 12 个起手因子用 `@factor` 装饰器重写为声明式风格，分布在 `builtins/` 子包的 6 个模块中。

**depends_on**: [s2, s5]

**产出**:
- `src/tinohelm/factor/builtins/__init__.py`
- `src/tinohelm/factor/builtins/momentum.py` — ret_N, rsi_signal
- `src/tinohelm/factor/builtins/volatility.py` — parkinson_vol, vol_ratio
- `src/tinohelm/factor/builtins/volume.py` — obv_slope, vwap_dev
- `src/tinohelm/factor/builtins/microstructure.py` — trade_imbalance, amihud_illiq
- `src/tinohelm/factor/builtins/crypto_funding.py` — funding_rate_level, funding_rate_mom
- `src/tinohelm/factor/builtins/crypto_data.py` — oi_change, orderbook_imbalance_L1

**验证方式**:
- 单元测试 `tests/factor/test_builtins.py`: 12 个因子全部输出正确
- 回归测试：bar 数据源因子与旧实现输出差 < 1e-10

---

### s13: funding_rate 存储升级

**描述**: 修改 `data/converters/funding_rate.py` 支持 Parquet 写入路径。编写 JSON → Parquet 迁移脚本。DataLayer 优先读 Parquet。

**depends_on**: [s6]

**产出**:
- 修改 `src/tinohelm/data/converters/funding_rate.py` — 新增 Parquet 写入
- `scripts/migrate_funding_json_to_parquet.py` — 迁移脚本
- DataLayer 中 funding_rate 加载逻辑走 Parquet

**验证方式**:
- 单元测试：写入 Parquet → DataLayer 正确读取
- 单元测试：迁移脚本将 JSON 转为 Parquet，读取结果一致

---

### s14: DB migration + FactorRun model

**描述**: 创建 `factor_runs` 表的 Alembic migration (011)，在 `db/models.py` 中添加 FactorRun model。

**depends_on**: []

**产出**:
- `src/tinohelm/db/migrations/versions/011_add_factor_runs.py`
- `src/tinohelm/db/models.py` 中新增 FactorRun class

**验证方式**:
- `alembic upgrade head` 无报错
- `python -c "from tinohelm.db.models import FactorRun; print('OK')"` 无 ImportError

---

### s15: 异步 Worker

**描述**: 基于 `core/async_queue_worker.py` 实现因子 worker，消费 `tino:factor:queue`。处理深度诊断任务（含 shuffle test、跨品种 IC、参数扫描）。

**depends_on**: [s11, s14]

**产出**:
- `src/tinohelm/factor/worker.py` — start_factor_worker, stop_factor_worker, _process_job

**验证方式**:
- 单元测试 `tests/factor/test_worker.py`: mock Redis + DB → 状态机 queued → running → completed
- 集成测试：端到端提交任务 → worker 处理 → 报告生成

---

### s16: API 路由 + app.py 集成

**描述**: 创建 `src/tinohelm/api/routes/factor.py`（8 个端点），修改 `app.py` 注册新路由和 worker。其中 `POST /api/factor/create` 端点需要实现因子文件模板生成逻辑：基于新的 `@factor` 装饰器风格生成模板文件到 `~/.tino/research/factors/`（替代旧的 `_template.py` 的 `FACTOR_META` + `compute` 模式）。

**depends_on**: [s11, s15]

**产出**:
- `src/tinohelm/api/routes/factor.py` — 8 个端点，含:
  - `GET /api/factor/list` — 列出所有因子
  - `GET /api/factor/universes` — 列出可用 universe
  - `GET /api/factor/symbols` — 列出有数据的 symbol
  - `POST /api/factor/explore` — 同步快速探索
  - `POST /api/factor/run` — 提交异步深度诊断
  - `GET /api/factor/runs` — 列出运行历史
  - `GET /api/factor/report/{run_id}` — 获取完整报告
  - `POST /api/factor/create` — 创建自定义因子文件（生成 `@factor` 装饰器风格模板到 `~/.tino/research/factors/{name}.py`）
- 修改 `src/tinohelm/api/app.py` — import factor, 注册 router, 启停 worker

**验证方式**:
- 集成测试 `tests/factor/test_api.py`:
  - `GET /api/factor/list` → 200，返回非空因子列表
  - `GET /api/factor/universes` → 200
  - `GET /api/factor/symbols` → 200
  - `POST /api/factor/explore` → 200 (with mock data)
  - `POST /api/factor/run` → 200，返回 `{run_id, status: "queued"}`
  - `GET /api/factor/runs` → 200
  - `GET /api/factor/report/{run_id}` → 200 (with mock run_id)
  - `POST /api/factor/create {"name": "test_factor"}` → 200，返回 `{name, path}`；断言文件写入 `~/.tino/research/factors/test_factor.py` 且包含 `@factor` 装饰器

---

### s17: EventBridge + notification-router 迁移

**描述**: 更新 EventBridge channel 映射（research → factor）和前端 notification-router（research.* → factor.*）。

**depends_on**: [s16]

**产出**:
- 修改 `src/tinohelm/core/bridge.py` — channel 映射
- 修改 `src/web/src/lib/notification-router.ts` — 事件路由

**验证方式**:
- 集成测试：mock Redis PUBLISH `tino:factor:events` → 断言 EventBridge 转发为 WS 消息 `type: "factor.completed"`
- 单元测试：断言 notification-router 将 `factor.completed` 路由到 toast channel（`dedupeKey` 为 `run_id`）

---

### s18: 前端因子探索页面 (`/factor`)

**描述**: 用 QDS 设计规范重写因子探索页面。左侧配置面板（universe、因子列表、参数）+ 右侧结果面板（摘要表、IC 图表、分位收益、分布、Turnover）。

**depends_on**: [s16]

**产出**:
- `src/web/src/app/factor/page.tsx`
- `src/web/src/app/factor/components/` — FactorList, DatasetPanel, ParamsPanel, ExploreResult 等

**验证方式**:
- `npm run build` 无编译错误
- E2E (Playwright): 页面加载 → 因子列表可见 → 选因子 → 运行 → 结果展示

---

### s19: 前端诊断报告页面 (`/factor/report/[id]`)

**描述**: 用 QDS 设计规范重写 4-tab 诊断报告页面。

**depends_on**: [s18]

**产出**:
- `src/web/src/app/factor/report/[id]/page.tsx`
- `src/web/src/app/factor/report/[id]/ReportClient.tsx`
- `src/web/src/app/factor/report/[id]/components/` — SignalProfileTab, PredictivePowerTab, RobustnessTab, CostParamsTab 等

**验证方式**:
- `npm run build` 无编译错误
- E2E (Playwright): 报告页面加载 → 4 tab 可切换 → 图表渲染

---

### s20: 旧模块清理

**描述**: 删除 `src/tinohelm/research/` 全部文件（11 个 .py，含 `__init__.py` 和 `_template.py`）、`src/tinohelm/api/routes/research.py`、`src/web/src/app/research/` 全部文件。清理 app.py 中的旧 import。清理 Sidebar 和 TopBar 中的旧路由映射。

**depends_on**: [s16, s17, s18, s19]

**产出**:
- 删除 `src/tinohelm/research/` (11 个 .py 文件，含 `__init__.py` 和 `_template.py`)
- 删除 `src/tinohelm/api/routes/research.py`
- 删除 `src/web/src/app/research/` (全部文件)
- 修改 `src/tinohelm/api/app.py` — 移除旧 import
- 修改 `src/web/src/components/Sidebar.tsx` — 更新导航链接 (`/research` → `/factor`)
- 修改 `src/web/src/components/TopBar.tsx` — 将 `"/research": "Factor Research"` 改为 `"/factor": "Factor Research"`

**验证方式**:
- `pip install -e .` 无 import 错误
- `npm run build` 无编译错误
- `python -m pytest tests/ -x -q` 无因旧模块缺失的失败

---

## 并行分组 (parallel_groups)

```
Wave 1: [s1, s14]
  - s1: 核心类型 + 别名表（无依赖）
  - s14: DB migration + FactorRun model（无依赖）

Wave 2: [s2, s4, s5, s8, s10]
  - s2: 装饰器 + AST（依赖 s1）
  - s4: Universe PIT（依赖 s1）
  - s5: PandasBackend（依赖 s1）
  - s8: 评估管道（依赖 s1）
  - s10: Observer（依赖 s1）

Wave 3: [s3, s6, s7, s9, s12]
  - s3: Registry（依赖 s2）
  - s6: DataLayer（依赖 s1, s4）
  - s7: DAG Planner + Scheduler（依赖 s1, s5）
  - s9: L2 缓存（依赖 s1, s8）
  - s12: 内置因子重写（依赖 s2, s5）

Wave 4: [s11, s13]
  - s11: Orchestrator（依赖 s3, s5, s6, s7, s8, s9, s10）
  - s13: funding_rate 升级（依赖 s6）

Wave 5: [s15]
  - s15: 异步 Worker（依赖 s11, s14）

Wave 6: [s16]
  - s16: API 路由 + app.py 集成（依赖 s11, s15）

Wave 7: [s17, s18]
  - s17: EventBridge + notification-router 迁移（依赖 s16）
  - s18: 前端探索页面（依赖 s16）

Wave 8: [s19]
  - s19: 前端报告页面（依赖 s18）

Wave 9: [s20]
  - s20: 旧模块清理（依赖 s16, s17, s18, s19）
```

---

## DAG 依赖图

```
s1 ──┬── s2 ── s3 ──┐
     │               │
     ├── s4 ── s6 ──┤
     │               │
     ├── s5 ──┬── s7 ┤
     │        │      │
     │        └── s12│
     │               │
     ├── s8 ── s9 ──┤
     │               │
     ├── s10 ────────┤
     │               │
     │          s11 ──┤
     │                │
     │   s13 ─────────┘ (s6)
     │
s14 ─────────── s15 ── s16 ──┬── s17 ──┐
                              │         │
                              ├── s18 ──┤── s20
                              │         │
                              └── s19 ──┘
```
