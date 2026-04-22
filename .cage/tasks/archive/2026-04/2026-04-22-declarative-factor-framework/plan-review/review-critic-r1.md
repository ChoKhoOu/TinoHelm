# Critic Review — Round 1

**VERDICT: REVISE**

## 摘要

文档整体质量较高：interview.md 覆盖了 11 轮访谈，需求 15 个 US 带自动化测试验证，技术设计有代码引用验证和序列图，任务 DAG 无环且 parallel_groups 合法。发现 2 个 MAJOR 问题（文件计数错误会导致 executor 清理不完整；s16 缺少 /api/factor/create 端点的实现子步骤和模板迁移）和 5 个 MINOR 建议。

## 预判记录

基于文档类型和项目领域，预判最可能的问题区域：
1. **需求与 interview 的覆盖差距** — 新功能是否有遗漏
2. **文件引用准确性** — 行号、文件数量、符号名是否与代码库一致
3. **验收标准可测试性** — 是否存在"手动验证"或模糊表述
4. **DAG 依赖正确性** — parallel_groups 是否违反依赖约束
5. **任务拆分完整性** — 是否有验收场景的步骤在 tasks 中没有对应子任务

## 关键假设分级

| 假设 | 级别 | 说明 |
|------|------|------|
| `research/` 目录含 10 个 .py 文件 | FRAGILE | 实际含 11 个（含 `__init__.py`），且 `_template.py` 未在技术设计影响文件列表中提及 |
| Alembic migration chain `010 → 011` | VERIFIED | `010_add_backtest_job_payload.py` 存在，revision="010", down_revision="009" |
| `core/async_queue_worker.py` 导出 `consumer_loop`, `WorkerHandle`, `PercentStepThrottle` | VERIFIED | grep 确认全部存在 |
| 现有 `research.py` 有 8 个 @router 端点 | VERIFIED | 确认 8 个 |
| `bridge.py` 行 27 含 `tino:research:` | VERIFIED | grep 确认 |
| `notification-router.ts` 含 research.progress/completed/failed | VERIFIED | grep 确认行 24, 34-35, 96, 100 |
| bar 数据有 `vwap` 字段 | FRAGILE | 现有 `vwap_dev` 因子内联计算 vwap（tp*volume rolling），bar 数据无 `vwap` 列。别名表 AC-2.1 定义 `vwap → (bar, vwap)` 标注"通过 tp * volume rolling sum 计算"，但这不是简单字段映射——需要 DataLayer 层面的派生字段逻辑 |
| 所有 converter 输出的 Parquet 字段名与别名表 field 一致 | REASONABLE | bookTicker 的 converter 输出 NT QuoteTick 格式（`bid_price` 等），但需确认 Parquet 列名是否匹配别名表中的 `best_bid_price` 等 |

## 预验尸（5 个失败场景）

1. **Executor 清理不完整**：s20 按"10 个文件"删除，实际 `research/` 有 11 个 .py（含 `__init__.py`）。`_template.py` 遗留导致 import 路径混乱。→ 文档说"10 文件"有误，但 s20 描述是"删除 `src/tinohelm/research/` 全部文件"，措辞上覆盖了全部，仅数字不准确。降级考虑。
2. **`/api/factor/create` 端点无实现**：技术设计列出 8 个端点含 create，但 s16 验证方式只测了 list/universes/explore。create 端点需要读取模板写入 `~/.tino/research/factors/`，但无对应 `_template.py` 迁移或新模板逻辑。→ s16 子步骤缺失。
3. **`vwap` 别名映射为 `(bar, vwap)` 但 bar Parquet 无 vwap 列**：DataLayer 按别名表 `(bar, vwap)` 读取时会 KeyError。需要 DataLayer 支持派生字段或修改别名映射。→ 别名表设计有隐含复杂度未在 DataLayer 任务中体现。
4. **funding_rate Parquet 写入与 Pipeline 冲突**：s13 修改 `converters/funding_rate.py` 新增 Parquet 写入，但 Pipeline 的 `WRITE_CATEGORY` 已将 fundingRate 映射到 `funding_rate` category。如果 converter 同时写 JSON 和 Parquet，可能导致双写一致性问题。→ 文档已说明"新写入一律走 Parquet"，JSON 保留为降级回退，足够清晰。
5. **ProcessPoolExecutor 序列化 pandas DataFrame 开销**：s7 scheduler 用 `ProcessPoolExecutor` 并行因子，kernel 需要 pickle/unpickle 多个 Panel。50 symbol * 100K bars 的 DataFrame pickle 可能成为瓶颈。→ 性能风险但 NFR-1 有指标（6 因子 < 5s），可在执行中验证。

## 发现（逐条 <= 5 行）

[MAJOR] R-C-01: s16 列出 8 个 API 端点但验证方式只覆盖 3 个（list/universes/explore），且 `/api/factor/create` 端点缺乏实现子步骤 — 该端点需要创建因子文件（类似现有 `_template.py`），但无任务覆盖模板迁移或新模板生成逻辑。
证据: 4-tasks.md s16 验证方式只测 `GET /api/factor/list → 200`、`GET /api/factor/universes → 200`、`POST /api/factor/explore → 200`；3-tech-design.md 第 287 行列出 `POST /api/factor/create` 端点需要 `{name}` 参数返回 `{name, path}`；现有 `research/_template.py` 提供因子模板但未出现在 s20 的清理/迁移范围或 s16 的实现描述中
建议: (1) s16 验证方式补充覆盖 /api/factor/run、/api/factor/runs、/api/factor/report/{run_id}、/api/factor/create、/api/factor/symbols 五个端点的基本断言；(2) s16 产出中明确包含因子文件模板逻辑（从 `_template.py` 迁移或重写为 `@factor` 装饰器风格新模板）
依据: rubric (f) 关键验收条件缺失 + rubric (g) 任务拆分遗漏关键子步骤

[MAJOR] R-C-02: 需求文档和技术设计中 `research/` 目录文件计数均写"10 个文件"，实际为 11 个 .py 文件（含 `__init__.py`）；`_template.py` 未出现在技术设计的"影响的现有文件"表格中。
证据: 1-requirements.md AC-15.1 `src/tinohelm/research/ 目录删除（10 个文件）`；3-tech-design.md 第 145 行 `src/tinohelm/research/ (全部 10 文件) → 删除`；实际 `ls src/tinohelm/research/*.py` 返回 11 个文件：`__init__.py, _template.py, analysis.py, cost.py, factors.py, loader.py, param_scan.py, registry.py, report.py, robustness.py, worker.py`；3-tech-design.md 第 159-175 行"文件存在性验证"列表中无 `_template.py`
建议: 更正文件计数为 11（或注明不含 `__init__.py` 则为 10，但 `_template.py` 仍需在影响文件列表中列出）；在 s16 或 s20 中明确处理 `_template.py` 的迁移（create 端点需要新版模板）
依据: rubric (a) 代码引用错误的文件计数 + rubric (g) 任务拆分遗漏 `_template.py` 迁移

[MINOR] R-C-03: 别名表 AC-2.1 定义 `vwap → (bar, vwap)`（标注"通过 tp * volume rolling sum 计算"），但 bar Parquet 数据不含 `vwap` 列 — 这是派生字段，DataLayer 需要特殊处理。此隐含复杂度未在 s6 DataLayer 的验证方式或 3-tech-design.md 的 DataLayer 设计中体现。
证据: 1-requirements.md AC-2.1 `vwap → (bar, vwap) (通过 tp * volume rolling sum 计算)`；现有 `factors.py:180-182` 内联计算 `vwap = (tp * volume).rolling(n).sum() / (volume.rolling(n).sum() + 1e-12)`，bar 数据无 vwap 列
建议: 在 s6 DataLayer 或 s1 alias.py 中补充"派生字段"逻辑说明：DataLayer 加载 `vwap` 别名时需计算 `(high+low+close)/3 * volume` 的 rolling sum，而非简单读取 Parquet 列。或者移除 `vwap` 别名，让因子直接依赖 `close, high, low, volume` 四个参数（与 AC-13.1 中 vwap_dev 的数据依赖一致）
依据: 隐含实现复杂度但不阻塞执行（executor 遇到时可自行判断处理方式），属 MINOR

[MINOR] R-C-04: interview.md 第 67 行 `vwap_dev` 的数据依赖写为 `bar (close, vwap)`，但 1-requirements.md AC-13.1 写为 `close, high, low, volume`。两处不一致，后者与现有实现 `factors.py:177-182` 匹配。
证据: interview.md 第 67 行 `microstructure-bar | vwap_dev | bar (close, vwap)`；1-requirements.md AC-13.1 `vwap_dev | close, high, low, volume`
建议: interview.md 是只读参考，以 requirements 为准即可，但 executor 可能参考 interview 产生困惑。可在 requirements 对应行加注释说明 vwap 为内联计算
依据: 文档间不一致但 requirements 已与代码对齐，非阻塞

[MINOR] R-C-05: NFR-1 性能指标缺乏自动化验证手段 — `单因子 kernel 执行 < 500ms`、`缓存命中 < 200ms`、`6 因子批量 < 5s` 三项指标在所有 subtask 的验证方式中均无对应 benchmark 测试。
证据: 1-requirements.md NFR-1 三项性能指标；4-tasks.md 所有 s1-s20 的验证方式中无 benchmark/perf 测试
建议: 在 s11（Orchestrator）或 s7（Scheduler）的验证方式中补充：`集成测试：单因子 100K bars 执行时间 < 500ms`，或在测试策略中明确性能回归测试
依据: 性能指标可在执行阶段补充 benchmark，非阻塞但建议提前规划

[MINOR] R-C-06: s17 验证方式描述为功能验证（"EventBridge 收到消息后 WS 客户端收到..."），但未明确这是自动化测试还是其他方式。相比其他 subtask 的 "单元测试/集成测试/E2E" 明确标注，s17 的验证描述偏叙述性。
证据: 4-tasks.md s17 验证方式 `EventBridge 收到 tino:factor:events 消息后，WS 客户端收到 type: "factor.completed"`
建议: 改为 `集成测试：mock Redis PUBLISH tino:factor:events → 断言 EventBridge 转发为 WS 消息 type: "factor.completed"`
依据: 描述可改进但意图明确，executor 可推断需要写集成测试，属 MINOR

[MINOR] R-C-07: 3-tech-design.md 第 342 行 `result_path` 的默认值示例为 `~/.tino/research/reports/{run_id}.json`，虽然用户目录路径保持 `~/.tino/research/` 是合理的（interview 确认），但在全面重命名为 `factor` 的语境下可能产生困惑。
证据: 3-tech-design.md 第 342 行 `result_path VARCHAR(500), -- ~/.tino/research/reports/{run_id}.json`；interview.md 第 49 行确认用户目录 `~/.tino/research/factors/`
建议: 考虑在技术设计中补充一行注释说明"用户数据目录保持 `~/.tino/research/` 不变，仅代码模块和 API/Redis 命名改为 factor"
依据: 命名一致性建议，非阻塞

## 多视角审查

### EXECUTOR 视角
- s1-s10 的验证方式都有明确的单元测试描述，可执行。
- s11 的 Orchestrator 依赖 7 个前置模块，集成复杂度高但 `depends_on` 正确。
- **s16 的 create 端点**：executor 会发现需要写模板文件但无 spec，会卡住需要问问题。→ 已记录为 R-C-01。
- s18/s19 前端任务描述足够（有组件树），但未指定具体使用哪些 QDS 设计规范文件作为参考。考虑到 CLAUDE.md 和 web/CLAUDE.md 已有详细 QDS 使用指南，executor 应能自行定位。

### STAKEHOLDER 视角
- 8 步端到端验收场景在 interview.md 中明确确认，requirements 中每步都有对应 US。
- 12 个起手因子的选择有信息维度覆盖理由（去冗余 + 补齐 crypto），合理。
- 前后端并重的范围与用户期望一致。

### SKEPTIC 视角
- `ProcessPoolExecutor` 并行因子计算：pandas DataFrame 的 pickle 序列化在大数据量下可能慢。但现有 `robustness.py` 和 `param_scan.py` 已在用同一模式且运行正常，v1 universe < 50 symbols 规模可控。
- Panel = pd.DataFrame 的决策合理，v1 不需要更复杂的方案。
- graphlib.TopologicalSorter 的选择合理（标准库，零依赖）。

## 缺口分析

- **`/api/factor/create` 端点的模板逻辑**：技术设计列出但无任务覆盖实现细节。→ R-C-01
- **`_template.py` 迁移**：从 FACTOR_META dict 模板迁移到 @factor 装饰器模板。→ R-C-02
- **NFR 性能验证**：无 benchmark 测试。→ R-C-05
- **vwap 派生字段**：别名表暗示 DataLayer 需要计算能力。→ R-C-03
- **旧测试清理**：现有 `tests/` 下是否有引用 `research/` 的测试文件？s20 未提及测试文件清理。→ 检查后为 MINOR（executor 在删除旧模块后运行 pytest 会自动发现）

## 自审

| 发现 ID | 置信度 | 可被反驳？ | 真实缺陷？ |
|---------|--------|-----------|-----------|
| R-C-01 | HIGH | NO — create 端点在技术设计中明确列出但 s16 验证方式仅覆盖 3/8 端点 | FLAW |
| R-C-02 | HIGH | PARTIALLY — s20 措辞"全部文件"理论上覆盖 _template.py，但计数错误 + 影响文件列表遗漏构成代码引用不准确 | FLAW |
| R-C-03 | MEDIUM | YES — 作者可能预期 vwap 别名在 DataLayer 中特殊处理或由因子直接用 close/high/low/volume | FLAW（但降为 MINOR） |
| R-C-04 | HIGH | NO — 事实性不一致 | FLAW（但 requirements 为准，降为 MINOR） |
| R-C-05 | MEDIUM | YES — 性能测试可在执行阶段补充 | PREFERENCE → MINOR |

## 现实检查

| 发现 ID | 现实最坏情况 | 缓解因素 | 多快被发现？ | 降级？ |
|---------|-------------|---------|------------|--------|
| R-C-01 | create 端点缺实现 → 前端 UI "创建因子" 按钮 404；_template.py 迁移遗漏 → 新因子无模板 | executor 实现 s16 时会看到 8 端点列表自行补齐 | s16 执行时立即发现 | 维持 MAJOR — rubric (g) 任务拆分遗漏关键子步骤明确适用 |
| R-C-02 | 文件计数 11 vs 10 的差异导致 executor 困惑或遗漏清理 | s20 描述为"删除全部文件"弥补了计数错误 | s20 执行时 | 维持 MAJOR — rubric (a) 代码引用错误；_template.py 在影响文件列表中完全缺失更关键 |

ReviewPass: critic
VERDICT: REVISE
