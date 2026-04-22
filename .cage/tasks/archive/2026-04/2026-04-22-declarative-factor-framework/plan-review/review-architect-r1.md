# Architect Review -- Round 1

**VERDICT: REVISE**

## 摘要

技术设计整体架构合理，模块划分清晰，DAG 无环且并行分组最优。所有核心代码文件引用均已验证存在，行号引用准确。发现 2 个 MAJOR 问题需修复：字段别名表中 `vwap` 映射到不存在的 bar 字段、s20 旧模块清理遗漏 `TopBar.tsx` 中的 `/research` 路由映射。另有数个 MINOR 改进建议。

## 发现（逐条 <= 5 行）

[MAJOR] R-01: 字段别名表中 `vwap` 映射 `(bar, vwap)` 但 bar Parquet 中不存在 `vwap` 列
证据: `data/converters/klines.py:56` 确认 bar 仅有 `open, high, low, close, volume` 五列；grep 整个 `data/` 模块无 `vwap` 字段写入。2-research.md 第 201 行声明 `vwap → (bar, vwap)` 并备注"通过 tp * volume rolling sum 计算"，但 DataLayer 按 `(table, field)` 直接读 Parquet 列，计算列不在存储中。
建议: 从别名表中移除 `vwap` 条目；`vwap_dev` 因子改为显式依赖 `close, high, low, volume` 四个别名（与 AC-13.1 和现有 `factors.py:177-182` 的实现一致）。若仍需 `vwap` 作为语义别名，则在 DataLayer 中添加"计算字段"机制并在 tech-design 中明确其实现方式。
依据: rubric (a) 代码引用不存在的字段 -- `vwap` 在 bar 数据表中不存在

[MAJOR] R-02: s20 旧模块清理遗漏 `TopBar.tsx` 中的 `/research` 路由标题映射
证据: `src/web/src/components/TopBar.tsx:17` 存在 `"/research": "Factor Research"` 条目。s20 产出列表仅提及 `Sidebar.tsx` 更新，未提及 `TopBar.tsx`。执行完成后 `/factor` 页面的 TopBar 标题将无法正确显示，而 `/research` 的 TopBar 映射成为幽灵配置。
建议: 在 s20 产出中增加"修改 `src/web/src/components/TopBar.tsx` -- 将 `"/research"` 改为 `"/factor"`"。
依据: rubric (g) 任务拆分遗漏关键子步骤 -- 路由迁移未覆盖所有引用点

[MINOR] R-03: research/ 目录文件数量记为"10 文件"但实际为 11 文件（含 `__init__.py`）
证据: `ls src/tinohelm/research/*.py` 列出 11 个文件；3-tech-design.md 和 4-tasks.md 均写"全部 10 文件"。
建议: 改为"全部 11 文件"或"10 个源文件 + `__init__.py`"以准确反映删除范围。
依据: 不影响执行（删除整个目录即可），属于文档精确性改进。

[MINOR] R-04: 2-research.md 第 74 行旧 Registry 描述引用 `FACTOR_META` dict 但实际变量名为 `BUILTIN_FACTORS`
证据: `research/factors.py:14` 定义 `BUILTIN_FACTORS: dict[str, FactorMeta]`，无 `FACTOR_META` 符号。
建议: 将"查 `FACTOR_META` dict"改为"查 `BUILTIN_FACTORS` dict"。
依据: 旧代码将被删除，此引用仅作对比说明，不影响执行。

[MINOR] R-05: `vwap_dev` 因子在 interview.md 中数据依赖标为 `bar (close, vwap)` 与 1-requirements.md AC-13.1 的 `close, high, low, volume` 不一致
证据: interview.md 第 68 行 vs 1-requirements.md 第 251 行。AC-13.1 是正确的（与现有 `factors.py:177-182` 实现一致）。
建议: interview.md 是只读参考，不修改；但 planner 在修复 R-01 时应确保 3-tech-design.md 和 2-research.md 中的 `vwap_dev` 依赖描述与 AC-13.1 保持一致。
依据: interview.md 是原始访谈记录，不影响执行。需求文档中已修正。

[MINOR] R-06: s19 前端报告页面依赖 s18 但两者之间无数据依赖，仅共享 API 层
证据: s19 (`/factor/report/[id]`) 调用 `GET /api/factor/report/{run_id}`，s18 (`/factor`) 调用 `/api/factor/list` 和 `/api/factor/explore`。两者使用不同的 API 端点和不同的组件，没有代码共享。s19 真正依赖的是 s16（API 路由可用）。
建议: 可将 s19 改为 `depends_on: [s16]`，与 s18 并行到 Wave 7，缩短关键路径 1 个波次。
依据: DAG 并行度优化属于 MINOR（见降级声明），不触发 REVISE。

[MINOR] R-07: 前端报告页面可能共享探索页面的部分图表组件，但 s18/s19 的组件设计未明确复用关系
证据: 3-tech-design.md 中 `ExploreResult` 和 `PredictivePowerTab` 都包含 ICTimeseriesChart、QuantileReturnChart 等同名组件。技术设计未说明是共享组件还是各自独立实现。
建议: 在 tech-design 中明确图表组件复用策略（建议提取到 `app/factor/components/charts/` 共享目录）。
依据: 执行者可在实现时自行决定复用策略，不阻塞执行。

## 架构决策权衡分析

### 决策 1: `factor/` 内部按功能子模块组织（偏离现有扁平分层）

**正面**: 15+ 文件按功能分组（core, engine, evaluation, cache, worker, builtins）职责清晰，利于维护和测试隔离。
**代价**: 与现有 `research/` 扁平风格不一致；增加导入路径深度（`from tinohelm.factor.engine.orchestrator import Orchestrator`）；新开发者需理解子包结构。
**判定**: 合理偏离，复杂度增长证明分层。

### 决策 2: 使用 `graphlib.TopologicalSorter` 而非自实现 DAG

**正面**: 零外部依赖，标准库维护，`get_ready()/done()` API 天然支持分组并行，自动 `CycleError` 检测。
**代价**: Python 3.9+ 才有（项目已要求 3.11+，不成问题）；`graphlib` 功能有限（无权重、无优先级）。
**判定**: v1 场景合适（因子间无直接依赖，只有数据共享）。

### 决策 3: v1 Panel = `pd.DataFrame`，不做 xarray/polars

**正面**: 与现有 `loader.py` 返回类型一致，迁移成本最低；IC 计算依赖 `scipy.stats.spearmanr` 天然兼容；shift/rolling/rank 原生支持。
**代价**: universe 规模 >500 symbols 时 pivot 开销大；单 DataFrame 非列式内存布局不利大规模计算；`Panel = pd.DataFrame` 类型别名缺少运行时约束（无法强制 index/columns 结构）。
**判定**: v1 universe <50 symbols 足够，代价在 v2 通过 PolarsBackend 解决。

### 决策 4: ProcessPoolExecutor 做因子级并行

**正面**: CPU 密集型 kernel 计算天然适合多进程；Python GIL 不阻塞并行。
**代价**: 进程间数据传输需序列化 Panel（pickle 大 DataFrame 有开销）；进程池启动有固定成本；debug 难度增加（子进程异常 traceback 需特殊处理）。
**判定**: v1 因子计算量大于序列化开销时合理；小因子应考虑 fallback 到 ThreadPoolExecutor。

### 决策 5: funding_rate 从 JSON 升级为 Parquet

**正面**: DataLayer 统一走 Parquet 读取路径，消除 JSON 特殊处理；Parquet 压缩率好、列式读取快。
**代价**: 需要编写迁移脚本 + 保留 JSON 降级回退；pipeline 修改涉及 `converters/funding_rate.py`，需同时更新 `_WRITE_CATEGORY` 或新增 Parquet writer。
**判定**: 必要的技术债清理，代价可控。

## 代码引用验证清单

| 引用 | 验证方式 | 结果 |
|------|---------|------|
| `research/factors.py` (14 因子 + `_COMPUTE_MAP`) | grep | OK (`BUILTIN_FACTORS`:14, `_COMPUTE_MAP`:230) |
| `research/loader.py` (load_data/load_bars/load_trade_ticks/load_funding_rates) | grep | OK (59/103/164/221) |
| `research/registry.py` (get_all_factors, get_compute_fn) | grep | OK (39/60) |
| `research/analysis.py` | ls | OK (308 行) |
| `research/robustness.py` (shuffle_test, subsample_ic, cross_symbol_ic) | grep | OK (74/109/160) |
| `research/report.py` (generate_report) | grep | OK (88) |
| `research/worker.py` | grep | OK (含 ResearchJob import, QUEUE_KEY) |
| `research/cost.py` (edge_waterfall) | grep | OK (5) |
| `research/param_scan.py` (sweep_1d, sweep_2d, build_ic_matrix) | grep | OK (41/124/97) |
| `api/routes/research.py` | ls | OK |
| `api/app.py` (行 17, 25-28, 103-104, 113, 161) | grep -n | OK (全部匹配) |
| `core/bridge.py` (行 27, `_CHANNEL_TYPE_MAP`) | grep -n | OK (27: `"tino:research:"`) |
| `core/config.py` (行 42, `PathSettings.research`) | grep -n | OK (42) |
| `core/async_queue_worker.py` (consumer_loop, WorkerHandle, PercentStepThrottle) | grep | OK (124/159/214) |
| `db/models.py` (行 275, ResearchJob) | grep -n | OK (275) |
| `db/migrations/versions/008_add_research_jobs.py` | ls | OK |
| `data/pipeline_helpers.py` (WRITE_CATEGORY) | grep | OK (32) |
| `data/converters/funding_rate.py` | ls | OK |
| `data/converters/metrics.py` (BinanceMetrics) | grep | OK (MetricsConverter:38) |
| `data/converters/book_ticker.py` (BookTickerConverter) | grep | OK (25) |
| `data/funding_cache.py` | ls | OK |
| `data/catalog.py` | ls | OK |
| `web/app/research/page.tsx` | ls | OK |
| `web/app/research/components/` (10 files) | ls | OK (10 files) |
| `web/app/research/report/[id]/` (page.tsx, ReportClient.tsx, 7 components) | ls | OK |
| `web/lib/notification-router.ts` (行 24, 34-35, 96-100) | grep -n | OK (全部匹配) |
| `web/components/Sidebar.tsx` (行 23, `/research`) | grep -n | OK |
| `web/components/TopBar.tsx` (行 17, `/research`) | grep -n | OK -- **但 s20 遗漏此文件** |
| Migration chain `010 → 011` | grep revision | OK (010.down_revision=009) |
| bar Parquet 列 (open/high/low/close/volume) | klines.py:56 | OK -- **无 vwap 列** |

## DAG 验证

- 无环依赖（`graphlib.TopologicalSorter` 验证通过）
- 全部 20 个子任务均在 parallel_groups 中
- 无子任务可移至更早波次（分组已最优）
- 依赖关系逐条检查: 每个子任务的 `depends_on` 均在其所在波次的前序波次中

ReviewPass: architect
VERDICT: REVISE
