# Code-Reviewer Report — Round 3

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **APPROVE**
**问题总数**: 3（均为 r1/r2 遗留 LOW，本轮未要求修复）

## Quality Gates
- pytest 1797 passed / 1 skipped / 0 failed
- npm build 16 pages
- tsc --noEmit 0 errors
- vitest 62/62 passed

## r2 kickback 修复核对（3/3 FIXED）

### HIGH: FactorRegistry → Registry
`grep -rn "FactorRegistry" src/ tests/` 全代码库无匹配。`robustness.py:160, 189` 正确使用 `Registry`。

### MEDIUM-1: TestFromSymbols 测试
读取 `test_universe.py:274-326`，8 个测试质量评估：

| 测试 | 评估 |
|------|------|
| test_basic_construction | 合理 |
| test_empty_list | 边界覆盖 |
| test_pit_no_isolation | 注释清晰 |
| test_name_defaults_to_inline | 合理 |
| test_custom_name | 合理 |
| test_custom_listing_date_applies_isolation | **数学精确**（6d/7d 双边界断言与 `ts < eligible_from` 对应） |
| test_returns_sorted_list | 合理 |
| test_len_matches_symbol_count | 合理 |

无 mock 过度、无空断言、无实现耦合。质量良好。

### MEDIUM-2: recover Redis/DB 顺序
读取 `worker.py:56-101`：
```
1. UPDATE FactorRun WHERE status='running' → status='queued'
2. await db.commit()                       ← DB 先 commit
3. SELECT queued_ids
4. await rds.delete(QUEUE_KEY)
5. for run_id: await rds.lpush
```

**竞态分析**：
- commit 成功 + rds.delete 前崩溃 → 下次启动重建，无丢失
- lpush 中途崩溃 → DB 保有完整 queued，下次启动重建
- rds.delete + lpush 非原子（毫秒级空窗）但 BRPOP 仅短暂阻塞，不丢 job
- 幂等性满足

**结论**：顺序正确，竞态可接受。

## 新代码质量（Stage 2/3/4）

- `TestFromSymbols` — 无 debug/print，无 TODO，断言具体
- `worker.py:56-101` — 逻辑清晰，注释高质量
- 无新增 SOLID 违反、N+1 查询、硬编码凭据

**已知不覆盖**：`recover_interrupted_jobs` 函数本身无专用单元测试（r2 MEDIUM 已接受遗留）。

## r1/r2 遗留 LOW 问题

- **LOW-1** `cache.py:191, 338` — `json.dump(default=str)` 静默类型丢失（未修）
- **LOW-2** Migration 011 schema 补列 — 实际核查：011 已包含 id/factor_name/status/config/result/progress/error/timestamps/code_hash，覆盖合理，降低优先级
- **LOW-3** Sidebar icon 是 `Hexagon` 非 `FlaskConical`（未修，语义小改进）

## 正面观察

1. **Fix 1 彻底性**：全代码库 `FactorRegistry` 清零
2. **Fix 2 测试边界精度**：双边界断言无 off-by-one
3. **Fix 3 注释质量**：DB-first 设计理由在代码注释中清晰记录
4. **设计理念正确**：DB 权威 + Redis 重建派生缓存，符合项目架构

## Verdict: **APPROVE**

Quality Gates 全绿，r2 kickback 3 问题全 FIXED，无新 CRITICAL/HIGH。3 个遗留 LOW 可合并到后续任务处理。
