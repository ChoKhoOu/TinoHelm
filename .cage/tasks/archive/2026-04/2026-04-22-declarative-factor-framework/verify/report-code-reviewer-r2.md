# Code-Reviewer Report — Round 2

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **REQUEST CHANGES**
**问题总数**: 7（0 CRITICAL / 1 HIGH / 2 MEDIUM / 3 LOW + 1 风格）

## r1 问题修复核对（17/17 已处理）

| 编号 | 状态 |
|------|------|
| C-1 card.tsx 6 exports | FIXED |
| C-2 Button/Input/Badge/Toggle 小写 | FIXED |
| C-3 DataLayer 构造器 | FIXED |
| C-4 FactorCache 构造器 | FIXED |
| H-1 recover_interrupted_jobs | FIXED |
| H-2 datetime.utcnow | FIXED |
| H-3 FIELD_ALIAS 补全 | FIXED（flat string 结构，功能正确） |
| H-4 trade_imbalance | FIXED（方案 B） |
| H-5 Sidebar /factor | FIXED |
| H-6 Planner _infer_source | FIXED |
| H-7 notification-router any | FIXED（WsEventPayload export type 超出要求） |
| H-8 setState-in-effect | FIXED |
| M-1 Orchestrator close | PARTIALLY FIXED（评估管道固有依赖 close，实际无 RuntimeError 风险） |
| M-2 catalog_path | FIXED |
| M-3 funding_rate as-of | FIXED |
| M-4 回滚越界 | FIXED |
| L-1 cache default=str | NOT FIXED（LOW，允许） |

## 新发现问题

### [HIGH] `FactorRegistry` 不存在 — `cross_symbol_ic` 运行时 ImportError

**File**: `src/tinohelm/factor/evaluation/robustness.py:160, 189`

```python
# 第 160 行：
from tinohelm.factor.registry import FactorRegistry   # ImportError！
# 第 189 行：
registry = FactorRegistry()
```

`registry.py:57` 实际类名是 **`Registry`**（不是 `FactorRegistry`）。证据：
```
$ grep -n "^class" src/tinohelm/factor/registry.py
57:class Registry:
```

**触发路径**：`POST /api/factor/run?full=true` → `Orchestrator.run(full=True)` → `Evaluator.evaluate_full` → `cross_symbol_ic` → `ProcessPoolExecutor._cross_symbol_worker` → `from tinohelm.factor.registry import FactorRegistry` → **ImportError**。

`test_evaluation.py` 不覆盖 `cross_symbol_ic` 路径，所以 pytest 通过。

**Fix**:
```python
# robustness.py:160:
from tinohelm.factor.registry import Registry

# robustness.py:189:
registry = Registry()
```

### [MEDIUM] `Universe.from_symbols` 无测试覆盖

**File**: `tests/factor/test_universe.py`

r2 新增的 classmethod 是 worker/routes/robustness 的关键依赖，但 0 测试。建议加 `TestFromSymbols` 覆盖正常构造、空列表、PIT 不隔离行为。

### [MEDIUM] `recover_interrupted_jobs` Redis/DB 非原子

**File**: `src/tinohelm/factor/worker.py:82-87`

Redis `rds.delete(QUEUE_KEY)` + `rds.lpush` 在 `await db.commit()` 之前。若 commit 失败，队列已更新但 DB 未更新，下次重启会双重入队（running job 被再次 recover）。

**Fix**: 将 Redis 操作移到 `db.commit()` 之后；或使用 commit callback 模式。

### [LOW] `_apply_pit` O(n×m) 嵌套循环

**File**: `src/tinohelm/factor/data_layer.py:524-528`

1 年 1min × 20 symbol ≈ 1050 万次 `mask.loc` 单元素赋值，PIT 过滤成主要耗时。可矢量化为 boolean DataFrame `.where`。

### [LOW] Sidebar icon 是 `Hexagon` 非 kickback 指定的 `FlaskConical`

功能无影响，语义 FlaskConical 更贴合因子研究。

### [LOW] `recover_interrupted_jobs` 扫描所有 queued 而非仅 recovered

文档注释不清晰，非 bug。

## Verdict: **REQUEST CHANGES**

HIGH 级别的 `FactorRegistry` ImportError 是可重现的运行时 bug，必须修复。
