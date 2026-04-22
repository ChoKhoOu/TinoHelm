# Verifier Report — Round 1

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **FAIL**
**置信度**: High

## 核心结论

Python 声明式因子框架（s1~s17 的后端部分）达到极高完成度 — 450+ 单元/集成测试全过、模块导入正常、migration 与 API 端点完整、EventBridge/notification-router 迁移到位。**但前端部分（s18/s19）和依赖的 shadcn UI 组件（超范围变更）处于 broken 状态**：`npm run build` Turbopack **完全失败**（12 个 module-not-found 错误，清空 .next 缓存后实测确认），`npx tsc --noEmit` 对 factor/ 报出 9+ 个 case-mismatch + `CardContent/CardHeader` 未导出错误，`npx vitest run` 有 10 个 font-tokens 失败。

根本原因：**本次执行越界重写了 shadcn ui 底层组件**（`card.tsx`/`button.tsx`/`input.tsx`/`badge.tsx`/`toggle.tsx`）**与 `globals.css` 的整个 QDS token 系统**（878 行 diff，ui/ 目录 5 个文件共 360 行 diff），这些既不在 4-tasks.md 的 20 个 subtask 范围内，也不在 3-tech-design.md 的"影响的现有文件"清单里。新 factor 组件按标准 shadcn 风格使用 `CardContent/CardHeader`，但底层 `Card.tsx` 只 export `Card`/`MetricCard`（本项目被重写成极简版），产生自洽断裂。

## 证据

### A. Next.js build（FAIL）
清空 `.next` 缓存后实测：
```
Turbopack build failed with 12 errors:
./src/app/factor/FactorExploreClient.tsx:5:1 — Can't resolve '@/components/ui/button'
./src/app/factor/report/[id]/ReportClient.tsx:6:1 — Can't resolve '@/components/ui/button'
./src/app/factor/components/DatasetPanel.tsx:5:1 — Can't resolve '@/components/ui/input'
./src/app/factor/components/ParamsPanel.tsx:3:1 — Can't resolve '@/components/ui/input'
./src/components/ConfirmModal.tsx:13/14 — Can't resolve '@/components/ui/button' '@/components/ui/input'
./src/components/EmptyState.tsx:4:1 — Can't resolve '@/components/ui/button'
./src/components/ui/dialog.tsx:7:1 — Can't resolve '@/components/ui/button'
./src/app/optimization/page.tsx:14:1 — Can't resolve '@/components/ui/input'
./src/app/factor/components/ExploreResult.tsx:3 + ChartPanel.tsx:4 + ParamsEcho.tsx:3 — Can't resolve '@/components/ui/card'
```
早期主 agent build 成功是 `.next` 缓存欺骗了判断。

### B. TypeScript 严格检查（FAIL）
```
src/app/factor/components/ExploreResult.tsx(3,16): TS2305: '@/components/ui/card' has no exported member 'CardContent'.
src/app/factor/components/ExploreResult.tsx(3,29): TS2305: 'CardHeader'.
src/app/factor/components/ExploreResult.tsx(3,47): TS1149: card.tsx vs Card.tsx casing.
src/app/factor/components/DatasetPanel.tsx(5,23): TS1149: input.tsx vs Input.tsx casing.
src/app/factor/components/ParamsPanel.tsx(3,23): TS1149 同上.
src/app/factor/report/[id]/components/ChartPanel.tsx(4,16/29/47): 同 ExploreResult.
src/app/factor/report/[id]/components/ParamsEcho.tsx(3,16/35): 同上.
```
11 处 `CardContent`/`CardHeader` 调用会在浏览器抛 `Element type is invalid: got: undefined`。

### C. Python 单元+集成测试（PASS）
`tests/factor/` 18 个 test 文件 450+ 测试全过。Python 后端测试通过。

### D. vitest（PARTIAL）
- `tests/lib/notification-router.test.ts` (19 tests) PASS
- `src/lib/__tests__/notification-router.test.ts` (16 tests) PASS
- `tests/fonts/tokens.test.ts` 15 tests 中 10 failed — 根因是 `globals.css` 878 行 diff 删除了 QDS token

### E. 旧模块清理（PASS）
```
grep -rn "from tinohelm.research\|import research" src/ tests/ → 无匹配
ls src/tinohelm/research/ → 不存在
ls src/tinohelm/api/routes/research.py → 不存在
ls src/web/src/app/research/ → 不存在
```

### F. 未预期的 git 变更（15+ 越界文件）
不在 20 个 subtask 范围内但被修改：
- `src/web/.gitignore` `package-lock.json` `package.json`
- `src/web/src/app/analytics/page.tsx` `backtest/page.tsx` `data-catalog/page.tsx` `orders/page.tsx` `settings/page.tsx` `strategies/page.tsx` `strategies/[name]/EditorClient.tsx` `watchlist/page.tsx` `layout.tsx` `page.tsx`
- `src/web/src/components/Providers.tsx` `hooks/useWebSocket.ts`
- `src/web/src/components/ui/badge.tsx` `button.tsx` `card.tsx` `input.tsx` `toggle.tsx`（shadcn primitive 重写 → 全站崩）
- `src/web/src/app/globals.css`（878 行 diff → QDS token 系统被删）

## Subtask 验证矩阵

| Subtask | 标题 | 判定 | 证据 |
|---------|------|------|------|
| s1  | 核心类型 + 别名表 | VERIFIED | types.py 导入 + test_alias.py 全过 |
| s2  | @factor 装饰器 + AST | VERIFIED | test_decorator.py 全过 |
| s3  | Registry | VERIFIED | test_registry.py 全过 |
| s4  | Universe PIT | VERIFIED | test_universe.py 全过 + `~/.tino/research/universes/binance_perp_top20.csv` 存在 |
| s5  | PandasBackend | VERIFIED | test_pandas_backend.py 全过 |
| s6  | DataLayer | VERIFIED | test_data_layer.py 全过 |
| s7  | Planner + Scheduler | VERIFIED | test_planner/scheduler.py 全过 |
| s8  | 评估管道 | PARTIAL | test_evaluation.py 过；AC-13.2 新旧数值回归无对比证据 |
| s9  | L2 缓存 | VERIFIED | test_cache.py 全过 |
| s10 | Observer | VERIFIED | test_observer.py 全过 |
| s11 | Orchestrator | VERIFIED | test_e2e_single/batch 全过 |
| s12 | 12 因子重写 | PARTIAL | test_builtins.py 过；旧实现已删无对比基准（AC-13.2 差异 < 1e-10 无法验证），且 trade_imbalance 用 bar 数据替代 trade tick |
| s13 | funding_rate 升级 | PARTIAL | 功能达成，但未改 `data/converters/funding_rate.py`（产出清单偏离） |
| s14 | DB migration | PARTIAL | 表创建 + model OK，但 schema 比 tech-design 精简（无 run_id UNIQUE/universe/interval/start_date/end_date/message/result_path/rating/verdict_json/cache_hit 列） |
| s15 | Worker | PARTIAL | test_worker.py 过；缺 recover_interrupted_jobs；3 处 datetime.utcnow() DeprecationWarning |
| s16 | API 路由 | VERIFIED | 8 端点齐全 + test_api.py 全过 |
| s17 | EventBridge + notif-router | VERIFIED | bridge.py:30 + notif-router.ts 正确 + 19 vitest 过 |
| s18 | /factor 页面 | **FAIL** | **Turbopack build fail + TS case mismatch + CardContent undefined** |
| s19 | /factor/report/[id] 页面 | **FAIL** | 同 s18 + ChartPanel 运行时会崩 |
| s20 | 旧模块清理 | PARTIAL | 删除彻底；但 Sidebar.tsx **没加 /factor 导航项**（AC-15.7 未满足） |

**统计**：VERIFIED 12/20，PARTIAL 6/20，FAIL 2/20

## 回归风险

| 区域 | 影响 | 评级 |
|------|------|------|
| **shadcn ui 重写** | 全站 10+ 页面 import 失败 | HIGH — 整个前端生产环境 broken |
| **globals.css 重写** | Tailwind 依赖 QDS token 的类全失效 | HIGH — 视觉崩塌 |
| **factor 页面** | /factor + /factor/report/[id] | HIGH — build 失败 |
| **Sidebar 导航** | UI 可访问性 | MEDIUM — 无入口 |
| **tests/fonts/tokens.test.ts** | CI 自检 | MEDIUM — 10 守护测试 fail |
| **旧 research API 客户端** | CLI/TUI 影响 | LOW — CLI/TUI 未用 research API |

## Verdict: **FAIL**

Kickback 合并到 `kickback-r1.md`。
