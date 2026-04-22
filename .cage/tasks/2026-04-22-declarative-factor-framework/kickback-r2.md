# Kickback to Execute — Round 2

**任务**: 2026-04-22-declarative-factor-framework
**来源**: `/cage:verify` r2
**状态**: Verifier PASS；Code-Reviewer REQUEST CHANGES（1 HIGH）
**整体进度**: Quality Gates 全过（pytest 1789 passed、build 16 pages、tsc 0 errors、vitest 62/62）；r1 的 17 个问题全部修复；但 code-reviewer 发现 1 个 r2 遗漏的真实 bug

## 一句话概述

r2 修复整体成功，Subtask 矩阵从 r1 的 12V/6P/2F 升级为 17V/3P/0F。唯一阻断：`robustness.py` 导入了**不存在的** `FactorRegistry` 类（实际类名是 `Registry`），导致 `cross_symbol_ic` 深度评估路径（`full=true`）运行时 `ImportError`。一行修复即可。

## 必须修复

### ★★ HIGH：`FactorRegistry` → `Registry`（1 个 import + 1 个实例化）

**证据**：
```bash
$ grep -n "FactorRegistry\|from tinohelm.factor.registry" src/tinohelm/factor/evaluation/robustness.py
160:        from tinohelm.factor.registry import FactorRegistry
189:        registry = FactorRegistry()

$ grep -n "^class" src/tinohelm/factor/registry.py
57:class Registry:
```

**修复**（2 行修改）：

`src/tinohelm/factor/evaluation/robustness.py:160`：
```python
# 错：
from tinohelm.factor.registry import FactorRegistry
# 正：
from tinohelm.factor.registry import Registry
```

`src/tinohelm/factor/evaluation/robustness.py:189`：
```python
# 错：
registry = FactorRegistry()
# 正：
registry = Registry()
```

**触发路径**：`POST /api/factor/run?full=true` → `Orchestrator.run(full=True)` → `Evaluator.evaluate_full` → `cross_symbol_ic` → `ProcessPoolExecutor._cross_symbol_worker`（进程池）→ 在子进程中 `import FactorRegistry` → **ImportError**，任务 FAIL。

**为什么 pytest 没抓到**：`tests/factor/test_evaluation.py` 没有覆盖 `cross_symbol_ic` 深度评估路径，所以 import error 在单元测试阶段不会 fire。

## 建议修复（非阻断）

### [MEDIUM] 为 `Universe.from_symbols` 补测试

**File**: `tests/factor/test_universe.py`

添加 `TestFromSymbols` 类：
```python
class TestFromSymbols:
    def test_basic_construction(self):
        u = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP"])
        assert "BTCUSDT-PERP" in u.get_symbols_at(datetime(2025, 1, 1))

    def test_empty_list(self):
        u = Universe.from_symbols([])
        assert u.get_symbols_at(datetime.utcnow()) == []

    def test_pit_no_isolation(self):
        # listing_date=1970-01-01 → 2025-01-01 应直接可用（绕过 7d 隔离）
        u = Universe.from_symbols(["BTCUSDT-PERP"])
        assert "BTCUSDT-PERP" in u.get_symbols_at(datetime(2025, 1, 1))
```

### [MEDIUM] `recover_interrupted_jobs` Redis/DB 顺序调整

**File**: `src/tinohelm/factor/worker.py:76-91`

当前顺序（非原子）：
```python
result = await db.execute(select(FactorRun.id).where(FactorRun.status=="running"))
running_ids = [row[0] for row in result]
if running_ids:
    await db.execute(update(FactorRun).where(...).values(status="queued"))
# Redis ops 在 commit 之前：
if queued_ids:
    await rds.delete(QUEUE_KEY)
    for run_id in queued_ids:
        await rds.lpush(QUEUE_KEY, run_id)
await db.commit()   # ← 如果此步失败，DB 未改但 Redis 已改
```

**修复**：把 Redis 操作移到 commit 之后
```python
# DB 先 commit
if running_ids:
    await db.execute(update(FactorRun).where(...).values(status="queued"))
await db.commit()

# Commit 成功后再动 Redis
# 重新读 queued_ids（此时是 commit 后的状态）
result2 = await db.execute(select(FactorRun.id).where(FactorRun.status=="queued"))
queued_ids = [row[0] for row in result2]
if queued_ids:
    await rds.delete(QUEUE_KEY)
    for run_id in queued_ids:
        await rds.lpush(QUEUE_KEY, run_id)
```

注意保留幂等性（重复 recover 不应产生副作用）。

## 非必需（LOW，允许保留）

- `_apply_pit` 向量化（性能优化，本轮可跳过）
- Sidebar icon `Hexagon` → `FlaskConical`（语义小改进）
- `recover_interrupted_jobs` 日志完善

## 修复后验证

```bash
cd /Users/ouzhuohao/TinoHelm

# 1. 全 Python 测试
.venv/bin/python -m pytest tests/ -q
# 期望：1789 passed (或 +N if 补了 from_symbols 测试)

# 2. 专门验证 registry import
.venv/bin/python -c "from tinohelm.factor.evaluation.robustness import _cross_symbol_worker; print('OK')"
# 期望：OK

# 3. grep 验证
grep -n "FactorRegistry" src/tinohelm/factor/evaluation/robustness.py && echo "not fixed" || echo "OK"
# 期望：OK

# 4. 如果 recover 顺序调整，跑 worker 相关测试
.venv/bin/python -m pytest tests/factor/test_worker.py -v
# 期望：全部 pass

# 5. 前端未改，只需确认 build 仍通过
cd src/web && rm -rf .next && npm run build 2>&1 | tail -5
# 期望：Generating static pages
```

## 超限提醒

kickback_round 已 2/10，本轮修复量极小（HIGH 只需 2 行改动）。完成后重跑 `/cage:verify` 应能进入 Phase 3 反熵检查 + 最终 PASS。

## 相关报告

- `.cage/tasks/2026-04-22-declarative-factor-framework/verify/report-verifier-r2.md`
- `.cage/tasks/2026-04-22-declarative-factor-framework/verify/report-code-reviewer-r2.md`
