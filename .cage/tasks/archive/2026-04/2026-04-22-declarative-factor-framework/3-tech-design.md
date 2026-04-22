# 声明式因子研究框架 -- 技术设计

## 1. 系统分层架构

```
┌─────────────────────────────────────────────────────┐
│  Frontend (Next.js)                                 │
│  /factor          → FactorExplorer (列表+配置+结果) │
│  /factor/report/[id] → FactorReport (4-tab 诊断)    │
└────────────────────────┬────────────────────────────┘
                         │ HTTP + WebSocket
┌────────────────────────┼────────────────────────────┐
│  API Layer             │                            │
│  /api/factor/*         │  routes/factor.py           │
│  EventBridge ← Redis PubSub tino:factor:*           │
└────────────────────────┼────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────┐
│  Factor Engine         │  src/tinohelm/factor/       │
│  ┌─────────────────────┴──────────────────────────┐ │
│  │           Orchestrator (run / batch_run)        │ │
│  │  ┌──────────┐ ┌──────────┐ ┌────────────────┐  │ │
│  │  │ Registry │ │ Planner  │ │   Scheduler    │  │ │
│  │  │ (发现+   │ │ (DAG +   │ │ (拓扑调度 +   │  │ │
│  │  │  注册+   │ │  合并)   │ │  并行执行)    │  │ │
│  │  │  AST)    │ │          │ │               │  │ │
│  │  └────┬─────┘ └────┬─────┘ └───────┬───────┘  │ │
│  │       │            │               │           │ │
│  │  ┌────┴────────────┴───────────────┴───────┐   │ │
│  │  │              DataLayer                   │   │ │
│  │  │  (多币种并行加载 + 时间对齐 + PIT)       │   │ │
│  │  └────────────────┬────────────────────────┘   │ │
│  │                   │                            │ │
│  │  ┌───────────┐ ┌──┴──────┐ ┌──────────────┐   │ │
│  │  │ Backend   │ │Evaluator│ │   Cache      │   │ │
│  │  │ (Pandas/  │ │(IC/IR/  │ │ (L2 disk +  │   │ │
│  │  │  Polars)  │ │ decay)  │ │  manifest)  │   │ │
│  │  └───────────┘ └─────────┘ └──────────────┘   │ │
│  │                                                │ │
│  │  ┌──────────┐ ┌──────────┐                     │ │
│  │  │ Observer │ │ Worker   │                     │ │
│  │  │(span+log)│ │(async q) │                     │ │
│  │  └──────────┘ └──────────┘                     │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────┐
│  Data Layer (现有)      │                            │
│  data/catalog.py       data/pipeline.py              │
│  data/converters/*     data/funding_cache.py         │
│  Parquet files (~/.tino/data/catalog/)               │
└─────────────────────────────────────────────────────┘
```

---

## 2. 架构对齐分析

### 现有架构模式识别

#### 2.1 变体处理模式

现有代码中处理同类概念的不同变体采用**函数分派 + 类型映射表**模式:

- `research/loader.py`: `_BAR_TYPES / _TICK_TYPES / _FUNDING_TYPES` frozenset + `load_data()` dispatcher
- `data/pipeline_helpers.py`: `WRITE_CATEGORY` MappingProxyType 做 data_type → category 映射
- `research/factors.py`: `BUILTIN_FACTORS` dict + `_COMPUTE_MAP` dict 做 factor_name → compute_fn 分派

新框架的对齐决策: **顺着走** — 使用映射表模式:
- 字段别名表 (`alias.py`) 是 `FIELD_ALIAS: dict[str, tuple[str, str]]`
- ComputeBackend 使用 ABC 抽象类 + 具体实现类（PandasBackend, PolarsBackend），因为 backend 有状态/行为（shift/rolling 等算子），适合用类而非函数

#### 2.2 分层策略

现有代码按**技术层**分包:
- `api/routes/` — HTTP 层
- `core/` — 共享基建
- `db/` — 数据库层
- `data/` — 数据管道层
- `research/` — 业务逻辑层（扁平结构，11 个文件）

新框架的对齐决策: **偏离** — `factor/` 内部按**功能子模块**组织（见下方目录结构），因为因子引擎的复杂度远高于旧 research 模块的扁平结构。但对外接口（API route、DB model、worker）仍顺着现有的技术层分层。

偏离理由: 旧 research/ 是 11 个并列文件（含 `__init__.py` 和 `_template.py`），新 factor/ 预计 15+ 文件，扁平放置会导致职责不清。按功能分子模块（core, engine, evaluation, cache, worker）更利于维护和测试。

#### 2.3 横切关注点

- `core/async_queue_worker.py` — 异步队列原语，已被 research/worker.py 和 data/worker.py 复用
- `core/bridge.py` — EventBridge，通过 `_CHANNEL_TYPE_MAP` 扩展新 channel

新框架的对齐决策: **顺着走** — factor worker 复用 `async_queue_worker.py` 的 `consumer_loop`、`WorkerHandle`、`PercentStepThrottle`。EventBridge 只需加一行 `"tino:factor:": "factor."`。

---

## 3. 模块划分

### 新目录结构 (`src/tinohelm/factor/`)

```
src/tinohelm/factor/
├── __init__.py                    # 公共 API: factor decorator, run, batch_run
├── types.py                       # 核心类型: FactorSpec, InputSpec, OutputSpec, Panel, EvalResult
├── alias.py                       # 字段别名表 FIELD_ALIAS + 解析器
├── decorator.py                   # @factor 装饰器实现
├── ast_check.py                   # AST 静态检查 (shift 检测)
├── registry.py                    # Registry: 扫描 + 注册 + FactorSpec 生成
├── universe.py                    # Universe: CSV 加载 + PIT 查询
├── data_layer.py                  # DataLayer: 多币种并行加载 + 时间对齐 + PIT 过滤
├── backend/
│   ├── __init__.py
│   ├── base.py                    # AbstractBackend 接口
│   └── pandas_backend.py          # PandasBackend 实现
├── engine/
│   ├── __init__.py
│   ├── planner.py                 # DAG 构建 + 数据需求合并
│   ├── scheduler.py               # 拓扑调度 + 并行执行
│   └── orchestrator.py            # 编排: Registry → Planner → DataLayer → Backend → Evaluator
├── evaluation/
│   ├── __init__.py
│   ├── evaluator.py               # 统一评估管道
│   ├── ic.py                      # IC/RankIC/IR/t-stat/decay 计算
│   ├── quantile.py                # Quantile PnL
│   ├── distribution.py            # 分布统计
│   ├── turnover.py                # Turnover 计算
│   ├── robustness.py              # Shuffle test + subsample + cross-symbol
│   ├── cost.py                    # Edge waterfall
│   └── rating.py                  # 评级逻辑
├── cache.py                       # L2 磁盘缓存 + manifest
├── observer.py                    # Observer: 结构化日志 + span
├── worker.py                      # 异步队列 worker (基于 core/async_queue_worker.py)
└── builtins/
    ├── __init__.py
    ├── momentum.py                # ret_N, rsi_signal
    ├── volatility.py              # parkinson_vol, vol_ratio
    ├── volume.py                  # obv_slope, vwap_dev
    ├── microstructure.py          # trade_imbalance, amihud_illiq
    ├── crypto_funding.py          # funding_rate_level, funding_rate_mom
    └── crypto_data.py             # oi_change, orderbook_imbalance_L1
```

### 影响的现有文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tinohelm/research/` (全部 11 个 .py 文件，含 `__init__.py` 和 `_template.py`) | **删除** | 被 `factor/` 替换 |
| `src/tinohelm/api/routes/research.py` | **删除** | 被 `routes/factor.py` 替换 |
| `src/tinohelm/api/app.py` (行 17, 25-28, 103-104, 113, 161) | **修改** | import 和 router 注册从 research → factor |
| `src/tinohelm/core/bridge.py` (行 27) | **修改** | `"tino:research:"` → `"tino:factor:"` |
| `src/tinohelm/core/config.py` (行 42) | **修改** | `research: Path` → 保留但加 `factor_cache: Path` |
| `src/tinohelm/db/models.py` (行 275-300) | **修改** | ResearchJob → FactorRun (或新增 FactorRun 并删除 ResearchJob) |
| `src/tinohelm/data/converters/funding_rate.py` | **修改** | 新增 Parquet 写入路径 |
| `src/tinohelm/data/funding_cache.py` | **保留** | 标记 deprecated，作为降级回退 |
| `src/web/src/app/research/` (全部文件) | **删除** | 被 `app/factor/` 替换 |
| `src/web/src/components/TopBar.tsx` (行 17) | **修改** | `"/research"` 路由标题映射改为 `"/factor"` |
| `src/web/src/lib/notification-router.ts` (行 24, 34-35, 96-100) | **修改** | `research.*` → `factor.*` |

### 文件存在性验证

以下文件已通过 Read/Grep 验证存在:
- `src/tinohelm/research/factors.py` (14 因子 + _COMPUTE_MAP)
- `src/tinohelm/research/loader.py` (load_data/load_bars/load_trade_ticks/load_funding_rates)
- `src/tinohelm/research/registry.py` (get_all_factors, get_compute_fn)
- `src/tinohelm/research/analysis.py` (IC/decay/quantile/distribution/turnover)
- `src/tinohelm/research/robustness.py` (shuffle_test, subsample_ic, cross_symbol_ic)
- `src/tinohelm/research/report.py` (generate_report, 4-tab verdict)
- `src/tinohelm/research/worker.py` (async consumer, 基于 core/async_queue_worker)
- `src/tinohelm/research/cost.py` (edge_waterfall)
- `src/tinohelm/research/param_scan.py` (sweep_1d, sweep_2d, build_ic_matrix)
- `src/tinohelm/research/_template.py` (因子开发模板，含 FACTOR_META + compute 示例)
- `src/tinohelm/api/routes/research.py` (7 endpoints)
- `src/tinohelm/api/app.py` (router 注册 + worker 启停)
- `src/tinohelm/core/bridge.py` (`_CHANNEL_TYPE_MAP` 含 `tino:research:`)
- `src/tinohelm/core/async_queue_worker.py` (consumer_loop, WorkerHandle, PercentStepThrottle)
- `src/tinohelm/core/config.py` (PathSettings.research)
- `src/tinohelm/db/models.py` (ResearchJob, 行 275)
- `src/tinohelm/db/migrations/versions/008_add_research_jobs.py`
- `src/tinohelm/data/pipeline_helpers.py` (WRITE_CATEGORY 含 fundingRate→funding_rate)
- `src/tinohelm/data/converters/funding_rate.py`
- `src/tinohelm/data/converters/metrics.py` (BinanceMetrics)
- `src/tinohelm/data/converters/book_ticker.py` (BookTickerConverter)
- `src/tinohelm/data/funding_cache.py`
- `src/web/src/app/research/page.tsx`
- `src/web/src/app/research/components/` (10 files)
- `src/web/src/app/research/report/[id]/` (page.tsx, ReportClient.tsx, 7 components)
- `src/web/src/lib/notification-router.ts` (research.progress/completed/failed)
- `src/web/src/components/TopBar.tsx` (行 17: `"/research": "Factor Research"` 路由标题映射)

---

## 4. 核心数据模型

### 4.1 Panel

```python
# factor/types.py
import pandas as pd

Panel = pd.DataFrame
# index = DatetimeIndex (T 时间步)
# columns = symbol names (N 币种)
# values = float64
```

### 4.2 FactorSpec

```python
@dataclass(frozen=True)
class InputSpec:
    alias: str           # 参数名 (e.g. "close")
    table: str           # 数据表 (e.g. "bar")
    field: str           # 字段名 (e.g. "close")
    lookback: int        # 额外 lookback (shift 检测累加)
    pit: bool = True     # 是否需要 PIT 过滤

@dataclass(frozen=True)
class OutputSpec:
    kind: str = "cross_section"   # 输出类型
    dtype: str = "float64"
    shape: str = "(T, N)"

@dataclass(frozen=True)
class FactorSpec:
    name: str
    category: str
    inputs: tuple[InputSpec, ...]
    output: OutputSpec
    lookback: int            # 总 lookback (装饰器声明 + AST 检测)
    code_hash: str           # 源码 SHA-256
    version: str = "1.0"
    source: str = "builtin"  # "builtin" | "custom"
    params: dict = field(default_factory=dict)  # 可配置参数
```

### 4.3 EvalConfig / EvalResult

```python
@dataclass
class EvalConfig:
    forward_periods: list[int] = field(default_factory=lambda: [5, 15, 30])
    n_quantiles: int = 5
    ic_freq: str = "D"
    fee_rate: float = 0.0004
    slippage_bps: float = 1.0
    shuffle_iterations: int = 1000
    cross_symbols: list[str] | None = None
    param_scan_config: dict | None = None

@dataclass
class EvalResult:
    factor_name: str
    ic_summary: dict        # ic_mean, ic_std, ir, ic_tstat, ic_positive_pct
    ic_series: list[dict]   # [{date, ic}]
    ic_decay: list[dict]    # [{lag, ic}]
    half_life: int | None
    quantile_returns: dict  # {avg_returns, cum_returns, is_monotonic}
    distribution: dict      # {histogram, stats}
    turnover: dict          # {daily, annualized, fee_drag_monthly}
    rating: int             # 0-3
```

### 4.4 DataRequest

```python
@dataclass
class DataRequest:
    symbols: list[str]
    interval: str
    start: str
    end: str
    lookback_bars: int        # 最大 lookback (数据预加载)
    table_groups: dict[str, list[str]]  # table → [field, ...]
    # e.g. {"bar": ["close", "volume"], "funding_rate": ["funding_rate"]}
```

---

## 5. API 设计

### 新端点 (`/api/factor/`)

| 方法 | 路径 | 描述 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/api/factor/list` | 列出所有因子 | - | `FactorGroup[]` |
| GET | `/api/factor/universes` | 列出可用 universe | - | `Universe[]` |
| GET | `/api/factor/symbols` | 列出有数据的 symbol | - | `SymbolOption[]` |
| POST | `/api/factor/explore` | 同步快速探索 | `ExploreRequest` | `ExploreResult` |
| POST | `/api/factor/run` | 提交异步深度诊断 | `RunRequest` | `{run_id, status}` |
| GET | `/api/factor/runs` | 列出运行历史 | `?status=` | `RunSummary[]` |
| GET | `/api/factor/report/{run_id}` | 获取完整报告 | - | `FullReport` |
| POST | `/api/factor/create` | 创建自定义因子 | `{name}` | `{name, path}` |

### ExploreRequest 结构

```python
class ExploreRequest(BaseModel):
    universe: str = "binance_perp_top20"
    interval: str = "5m"
    start_date: date
    end_date: date
    factors: list[str]
    factor_params: dict[str, dict] | None = None
    forward_period: int = 5
    quantiles: int = 5
```

### RunRequest 结构

```python
class RunRequest(BaseModel):
    universe: str = "binance_perp_top20"
    interval: str = "5m"
    start_date: date
    end_date: date
    factor_name: str
    factor_params: dict | None = None
    forward_periods: list[int] = [5, 15, 30]
    quantiles: int = 5
    shuffle_iterations: int = 1000
    cross_symbols: list[str] | None = None
    param_scan: dict | None = None
    fee_rate: float = 0.0004
    slippage_bps: float = 1.0
```

---

## 6. DB Schema

### factor_runs 表 (migration 011)

```sql
CREATE TABLE factor_runs (
    id           SERIAL PRIMARY KEY,
    run_id       VARCHAR(36) UNIQUE NOT NULL,
    factor_name  VARCHAR(100) NOT NULL,
    universe     VARCHAR(100) NOT NULL DEFAULT 'binance_perp_top20',
    interval     VARCHAR(10) NOT NULL DEFAULT '5m',
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,
    config_json  JSON,              -- EvalConfig 序列化
    status       VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress     INTEGER DEFAULT 0,
    message      TEXT,
    error        TEXT,
    result_path  VARCHAR(500),      -- ~/.tino/research/reports/{run_id}.json
    rating       INTEGER,           -- 0-3
    verdict_json JSON,              -- {signal_profile, predictive_power, robustness, cost_params}
    result_summary_json JSON,       -- 评估摘要 (ic_mean, ir 等)
    cache_hit    BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT now(),
    completed_at TIMESTAMP
);
CREATE INDEX ix_factor_runs_status ON factor_runs (status);
```

Migration revision chain: `010 → 011`

---

## 7. Redis Key 设计

| Key | 类型 | 用途 |
|-----|------|------|
| `tino:factor:queue` | List (LPUSH/BRPOP) | 异步任务队列 |
| `tino:factor:progress:{run_id}` | PubSub | 实时进度推送 |
| `tino:factor:events` | PubSub | 完成/失败事件 |

EventBridge `_CHANNEL_TYPE_MAP` 新增:
```python
"tino:factor:": "factor.",
```

---

## 8. 前端页面结构

### 路由

| 路由 | 页面 | 对应旧页面 |
|------|------|-----------|
| `/factor` | FactorExplorer | `/research` |
| `/factor/report/[id]` | FactorReport | `/research/report/[id]` |

### `/factor` 页面组件树

```
FactorExplorerPage
├── PageHeader (QDS)
├── <flex>
│   ├── <aside> 左侧配置面板 (w-80)
│   │   ├── DatasetPanel (universe, interval, dates)
│   │   ├── FactorList (分类手风琴, 多选)
│   │   ├── ParamsPanel (forward_period, quantiles, factor params)
│   │   └── ActionButtons (运行探索 / 提交诊断)
│   └── <main> 右侧结果面板
│       ├── JobQueue (历史任务列表)
│       └── ExploreResult
│           ├── FactorSummaryTable
│           ├── IC TimeseriesChart (Recharts Line)
│           ├── IC DecayChart (Recharts Bar)
│           ├── QuantileReturnChart (Recharts Line)
│           ├── DistributionChart (Recharts Bar)
│           └── TurnoverKPI (StatCard)
```

### `/factor/report/[id]` 页面组件树

```
FactorReportPage
├── PageHeader (QDS)
├── StatusBadge (QDS)
├── Tabs (4 个)
│   ├── SignalProfileTab
│   │   ├── StatsGrid (StatCard)
│   │   ├── DistributionChart
│   │   └── ACFChart
│   ├── PredictivePowerTab
│   │   ├── HorizonTable
│   │   ├── ICTimeseriesChart
│   │   ├── ICDecayChart
│   │   └── QuantileReturnChart
│   ├── RobustnessTab
│   │   ├── ShuffleDistChart
│   │   ├── SubsampleBarChart
│   │   └── CrossSymbolTable
│   └── CostParamsTab
│       ├── WaterfallChart
│       ├── SweepChart
│       └── HeatmapChart
```

### 设计规范

- 遵循 QDS 设计规范 (TinoHelmDS Skill)
- 使用 Tailwind 语义类 + QDS 组件（StatCard, PageHeader, SectionLabel, InlineError, StatusBadge, ShimmerBar, HelpTip）
- 图表使用 `chartTheme.ts` 统一主题（CHART_TOOLTIP_PROPS, CHART_GRID_STYLE, CHART_AXIS_STYLE 等）
- 禁止遗留 class (sc/cd/sl/fl 等)
- useAction hook 处理 API 调用状态
- InlineError 处理 API 错误

---

## 9. 关键流程序列图

### 9.1 单因子同步探索 (`POST /api/factor/explore`)

```
Client → API /factor/explore
  │
  ├─ Registry.get_specs(factor_names)
  │   → [FactorSpec, ...]
  │
  ├─ Planner.plan(specs, universe, time_range)
  │   → DataRequest + ExecutionPlan
  │
  ├─ Cache.lookup(specs, data_snapshot)
  │   → HIT: return cached EvalResult
  │   → MISS: continue
  │
  ├─ DataLayer.load(DataRequest)
  │   → dict[alias, Panel]
  │
  ├─ Backend.execute(kernel, panels)
  │   → factor_values: Panel
  │
  ├─ Evaluator.evaluate(factor_values, forward_returns)
  │   → EvalResult
  │
  ├─ Cache.store(key, factor_values, EvalResult)
  │
  └─ return ExploreResult
```

### 9.2 异步深度诊断 (`POST /api/factor/run`)

```
Client → API /factor/run
  │
  ├─ Create FactorRun DB record (status=queued)
  ├─ LPUSH run_id → tino:factor:queue
  └─ return {run_id, status: "queued"}

Worker (BRPOP tino:factor:queue):
  │
  ├─ Load FactorRun from DB
  ├─ Set status = running
  │
  ├─ [Phase 1] Data load + Kernel exec
  │   → PUBLISH tino:factor:progress:{run_id} {pct: 10}
  │
  ├─ [Phase 2] Full evaluation (IC/decay/quantile/distribution/turnover)
  │   → PUBLISH tino:factor:progress:{run_id} {pct: 25}
  │
  ├─ [Phase 3] Robustness (shuffle + subsample + cross-symbol)
  │   → PUBLISH tino:factor:progress:{run_id} {pct: 65}
  │
  ├─ [Phase 4] Cost & param scan
  │   → PUBLISH tino:factor:progress:{run_id} {pct: 85}
  │
  ├─ Save report JSON to disk
  ├─ Set status = completed, result_path, rating, verdict
  │
  └─ PUBLISH tino:factor:events {type: "factor.completed", run_id, rating}
       │
       └─ EventBridge → WS → NotificationListener → toast
```

### 9.3 多因子批量运行

```
batch_run(factor_names, universe, time_range):
  │
  ├─ Registry.get_specs(factor_names)
  │   → [FactorSpec_A, FactorSpec_B, FactorSpec_C, ...]
  │
  ├─ Planner.plan_batch(specs)
  │   ├─ merge_data_requests(specs)  → 合并去重
  │   ├─ compute_lookback_closure()  → max(lookback)
  │   └─ topological_sort()          → execution_order + parallel_groups
  │
  ├─ DataLayer.load(merged_request)
  │   → dict[alias, Panel]  (所有因子共享)
  │
  ├─ for group in parallel_groups:
  │     ProcessPoolExecutor.map(
  │       lambda spec: Backend.execute(spec.kernel, panels),
  │       group
  │     )
  │   → dict[factor_name, Panel]
  │
  ├─ for factor_name, values in results:
  │     Evaluator.evaluate(values, forward_returns)
  │   → dict[factor_name, EvalResult]
  │
  └─ return BatchResult
```

---

## 10. 测试策略

### 单元测试

| 模块 | 测试文件 | 测试点 |
|------|---------|--------|
| `decorator.py` + `ast_check.py` | `tests/factor/test_decorator.py` | @factor 解析, AST shift 检测, code_hash |
| `alias.py` | `tests/factor/test_alias.py` | 默认别名解析, 冲突检测, 自定义 override |
| `registry.py` | `tests/factor/test_registry.py` | 文件扫描, FactorSpec 生成, code_hash 变更检测 |
| `universe.py` | `tests/factor/test_universe.py` | CSV 加载, PIT 查询, 新币隔离 |
| `data_layer.py` | `tests/factor/test_data_layer.py` | 多源加载, 时间对齐, PIT 过滤, 并行加载 |
| `backend/pandas_backend.py` | `tests/factor/test_pandas_backend.py` | shift, rolling, rank, clip |
| `engine/planner.py` | `tests/factor/test_planner.py` | 数据需求合并, lookback closure, 拓扑排序 |
| `engine/scheduler.py` | `tests/factor/test_scheduler.py` | 并行分组, 执行顺序 |
| `evaluation/*.py` | `tests/factor/test_evaluation.py` | IC/decay/quantile/distribution/turnover/rating |
| `cache.py` | `tests/factor/test_cache.py` | 完整命中, 部分命中, 失效, manifest |
| `observer.py` | `tests/factor/test_observer.py` | span 记录, 日志结构 |
| `builtins/*.py` | `tests/factor/test_builtins.py` | 12 个内置因子输出正确性 |

### 集成测试

| 测试场景 | 测试文件 | 覆盖范围 |
|---------|---------|---------|
| 单因子端到端 | `tests/factor/test_e2e_single.py` | Registry → Planner → DataLayer → Backend → Evaluator → Cache |
| 多因子批量 | `tests/factor/test_e2e_batch.py` | DAG 合并 → 并行执行 → 多结果聚合 |
| 异步 worker | `tests/factor/test_worker.py` | Redis queue → DB 状态机 → 报告生成 |
| API 端点 | `tests/factor/test_api.py` | /explore, /run, /runs, /report |

### 回归测试

| 测试场景 | 说明 |
|---------|------|
| 因子数值回归 | 对于共用 bar 数据源的旧因子，新旧实现输出差 < 1e-10 |

### E2E 测试 (Playwright)

| 测试场景 | 说明 |
|---------|------|
| 探索页面 | 加载 → 因子列表 → 选因子 → 运行 → 结果展示 |
| 报告页面 | 加载 → 4-tab 切换 → 图表渲染 |
