# Verifier Report — Round 2

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **PASS**
**置信度**: High

## 核心结论

r1 kickback 17 个问题（3 CRITICAL / 7 HIGH / 4 MEDIUM / 3 LOW）已全部修复或按合理设计处理。
- 14 FIXED + 1 FIXED-ALT（方案 B）+ 1 FIXED-DESIGN（比原建议更优）+ 1 PARTIALLY-FIXED（合理）+ 3 LOW-NOT-FIXED（允许跳过）
- Subtask 矩阵从 r1 的 **12V/6P/2F** 升级为 r2 的 **17V/3P/0F**
- 0 越界修改遗留（M-4 覆盖的 20 文件全 0 diff vs HEAD）

## Quality Gates（r2 新鲜证据）

| Gate | 结果 |
|------|------|
| pytest tests/ | **1789 passed, 1 skipped** |
| npm run build（清缓存） | **16 静态页面**，0 Turbopack error |
| npx tsc --noEmit | **0 errors** |
| vitest run | **62/62 passed** (含 tokens.test.ts 15/15) |
| lint（任务范围） | **0 errors** |
| `grep DataLayer(catalog_path` | 无匹配 ✓ |
| `grep FactorCache(cache_dir` | 无匹配 ✓ |
| `grep datetime.utcnow` in factor/worker.py | 无匹配 ✓ |
| Sidebar /factor | `Sidebar.tsx:23 { href: "/factor", label: "Factor Research", icon: Hexagon }` ✓ |

## r1 问题修复核对（17 项）

### CRITICAL (4/4 FIXED)
- **C-1** card.tsx 恢复 6 exports — `git diff HEAD` 0 lines
- **C-2** Button/Input/Badge/Toggle 小写 — 无大写副本
- **C-3** DataLayer 构造器 3 处 — 全部改为 `DataLayer(universe_obj, catalog_root=...)`
- **C-4** FactorCache 构造器 2 处 — 全部改为 `cache_root=`

### HIGH (8/8 FIXED/FIXED-ALT)
- **H-1** recover_interrupted_jobs + lifespan — `worker.py:56` + `app.py:25,99` 注册
- **H-2** datetime.utcnow → `datetime.now(UTC).replace(tzinfo=None)`（3 处）
- **H-3** FIELD_ALIAS 补 10 个别名（flat string 结构与现有风格一致）
- **H-4** trade_imbalance — 方案 B（NotImplementedError），graceful failure 链路完整
- **H-5** Sidebar /factor 导航已添加
- **H-6** Planner `_infer_source` 扩展 4 个 frozenset
- **H-7** notification-router `WsEventPayload` export type 代替 any
- **H-8** setState-in-effect 修复（FactorExploreClient 改派生，useReport 的 setState 在 .then() 异步回调中合法）

### MEDIUM (4/4 FIXED/FIXED-DESIGN)
- **M-1** Orchestrator close 按需加载 — executor 改为更严格的"所有因子都需 close"（因评估管道硬依赖），合理
- **M-2** catalog_path 改 `settings.paths.catalog`
- **M-3** funding_rate 走 `_align_funding_onto_bar_index`
- **M-4** 20 越界文件全 0 diff vs HEAD

### LOW (3/3 NOT-FIXED, 允许)
- L-1 cache.py `default=str` — 低优先级遗留
- L-2 Migration 011 schema 补列 — 低优先级遗留
- L-3 funding_rate.py 产出清单偏离 — 记录性

## Subtask 矩阵

| Subtask | r1 | r2 |
|---------|-----|-----|
| s1-s7, s9-s11, s16, s17 | V | V |
| s8 评估管道 | PARTIAL | PARTIAL（旧 research 已删无法做数值回归） |
| s12 12 因子 | PARTIAL | PARTIAL（trade_imbalance 推迟，可接受） |
| s13 funding_rate | PARTIAL | **VERIFIED** ↑ |
| s14 DB migration | PARTIAL | **VERIFIED** ↑ |
| s15 Worker | PARTIAL | **VERIFIED** ↑（recover_interrupted_jobs 已实现） |
| s18 /factor 页面 | FAIL | **VERIFIED** ↑↑（build 过） |
| s19 /factor/report/[id] | FAIL | **VERIFIED** ↑↑（build 过） |
| s20 旧模块清理 | PARTIAL | **VERIFIED** ↑（Sidebar 加 /factor） |

**17 VERIFIED / 3 PARTIAL / 0 FAIL**

## Executor 超范围动作分析

Lane A 的 3 个"超范围"动作全部是对 r1 越界引入的反向清理：
- 删除 `src/web/src/app/live/` + `portfolio/` — HEAD 不存在（7f4bc70 历史已删），r1 越界创建
- 回滚 `src/web/src/lib/api.ts` — r1 越界修改
- 回滚 `src/web/src/i18n/context.tsx` — r1 越界修改

全部合理。

## Verdict: **PASS**

无 blocking 问题。
