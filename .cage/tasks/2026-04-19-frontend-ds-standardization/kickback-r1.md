# Kickback R1 — frontend-ds-standardization

**Verdict**: FAIL (Verifier: FAIL | Code-Reviewer: REQUEST CHANGES)
**Round**: 1 / 10
**Generated**: 2026-04-20

Phase 2 agents in agreement: build/typecheck 断链、StatusBadge 双实现未统一、核心 4 文件未执行拆分。Phase 3（反熵）跳过。

## Quality Gates 状态

| Gate | 结果 | 备注 |
|------|------|------|
| DS 合规扫描 R1-R14 | ✅ 0 violations | verify-ds-compliance.sh 全过 |
| `npm run build` | ⚠️ **已确认失败** | macOS 大小写不敏感掩盖，Linux CI 必败（见 F1） |
| `npx tsc --noEmit` | ⚠️ **已确认失败** | TS1261（见 F1） |
| `npm run test:fonts` | ✅ 15/15 通过 | — |
| `npx eslint src/` | ⚠️ 46E/41W（与 baseline 48E/43W 相当） | 新变更未新增错误，但变更文件内 LOW 级遗留（见 F6） |

## 阻塞级修复指令（BLOCKER — 必须全部完成才能 PASS）

### F1 · HIGH · 编译/构建断链（CRITICAL）

**现象**：`src/web/src/app/research/components/ResearchExploreResult.tsx:16` 引用 `@/components/ui/Card`（大写 C），实际文件为 `@/components/ui/card.tsx`（小写）。TS 报 `TS1261`；Turbopack 报 "Module not found"。macOS 大小写不敏感 FS 掩盖了错误，Linux CI 必失败。

**修复**：
```diff
- import { Card, CardContent, CardHeader } from "@/components/ui/Card";
+ import { Card, CardContent, CardHeader } from "@/components/ui/card";
```

**验证命令**：
```bash
cd src/web && npx tsc --noEmit && npm run build
```
两者都必须干净退出。

---

### F2 · HIGH · StatusBadge 规格未达成（FR-5 未实现）

**现象**：
1. `src/web/src/components/qds/status-badge.tsx` 的 `StatusKind` union 与规格不符：
   - 当前：`running/done/failed/queued/paused/flattening/starting`
   - 规格要求（user-acceptance.md / s11 AC）：7 键含 `completed/cancelling/cancelled`
   - 键名漂移：`done` vs `completed`
2. `src/web/src/components/StatusBadge.tsx`（顶层旧版）**未**改为 barrel re-export — 仍是独立的 Badge 实现，有自己的 LABEL/COLOR_MAP，造成两份实现并行服役
3. `page.tsx`、`optimization/page.tsx` 仍从旧路径 `@/components/StatusBadge` 导入
4. `JobQueue.tsx:189` `<StatusBadge status="queued">已取消</StatusBadge>` —— 由于 qds 版没有 `cancelled` 键，临时用 `queued` 加 children 覆盖文案。语义错误（"排队中"→"已取消"会导致颜色/文案不一致）

**修复**：
1. 将 `src/web/src/components/qds/status-badge.tsx` 的 `StatusKind` 扩展为规格 7 键（含 `completed/cancelling/cancelled`，并决定是否保留 `done` 作为 `completed` 的别名），同步更新 LABEL_MAP_ZH/LABEL_MAP_EN/COLOR_MAP
2. 将顶层 `src/web/src/components/StatusBadge.tsx` 改为 barrel re-export：
   ```ts
   export { StatusBadge, type StatusKind } from "@/components/qds/status-badge";
   ```
   （或直接删除并修改 `page.tsx`/`optimization/page.tsx` 的 import 路径）
3. `JobQueue.tsx:189` 改为 `<StatusBadge status="cancelled" />`，移除 children 覆盖

**验证命令**：
```bash
# 确认只有一份 StatusBadge 实现
grep -rn "export function StatusBadge\|export const StatusBadge" src/web/src/components/
# 确认 JobQueue 不再用 hack
grep -n 'status="queued">已取消' src/web/src/app/data-catalog/JobQueue.tsx  # 应为空
```

---

### F3 · HIGH · 核心文件未执行拆分（FR-4.1 / NFR-2 未达成）

**规格目标**：所有路由文件 `wc -l < 700`。
**实测**：
| 文件 | 行数 | 超出 |
|------|------|------|
| `backtest/page.tsx` | 1805 | +1105（较 1754 起点还增长 51） |
| `backtest/components/PerformanceTab.tsx` | 2061 | +1361 |
| `backtest/components/TradesTab.tsx` | 851 | +151 |
| `backtest/components/OverviewTab.tsx` | 836 | +136 |

s4 AC 明确要求拆分这些文件，当前完全未执行。

**修复方案**（参考 research 子系统拆分模式）：
- 按 tab 内的"图表子区块"/"统计卡组"/"表格段落"/"Memoized 派生计算"分离到 `backtest/components/<SubBlock>.tsx` 或 `backtest/components/hooks/<useX>.ts`
- 每个拆分子模块必须有单一职责（一个图表 / 一个表 / 一组 memoize 计算），不得为凑数而拆
- 主 page.tsx 仅保留路由装配与数据请求
- 如果计算密集型 memoize 超过 100 行，优先抽到 `hooks/useBacktest*.ts`

**验证命令**：
```bash
cd src/web && find src/app/backtest -name "*.tsx" -o -name "*.ts" | xargs wc -l | awk '$1>=700 && $2!="total" {print}'
# 期望输出为空
```

---

### F4 · HIGH · R8 扫描脚本多行 Legend 漏报

**现象**：`analytics/page.tsx:334-338` 与 `TradesTab.tsx:340-342` 存在真实的 `<Legend ... wrapperStyle={{ ... }} />` 跨行内联违规，但 `scan_r8()` 的正则未跨行匹配，R1-R14 扫描显示 0 violations 与实际不符。

**修复**（两件事都要做）：
1. **修脚本**：`src/web/scripts/verify-ds-compliance.sh` 的 R8 正则改为支持 PCRE2 多行匹配（与 R9 ReferenceLine label 的多行模式对齐，参考 s2 已有实现）
2. **修代码**：这两处 `wrapperStyle` 改为 spread `CHART_LEGEND_STYLE`（s1 已补全该常量）

**验证命令**：
```bash
cd src/web && bash scripts/verify-ds-compliance.sh --selftest  # 新 R8 多行 fixture 必须能被检出
cd src/web && bash scripts/verify-ds-compliance.sh             # 修复代码后必须仍为 0 violations
```

---

## MEDIUM（不阻塞，但建议本轮一并修复）

### F5 · MEDIUM · globals.css 删除量超出 NFR-2 目标 ~420 行

**现象**：globals.css 从 1987 → 785 行（删 1202 行），NFR-2 目标约 780 行（±50 容差）。超删区间需要 diff 审核是否误删了非 legacy 内容（如 shadcn 主题 token 或 QDS 业务组件 class）。

**修复**：
```bash
git diff HEAD src/web/src/app/globals.css | grep "^-" | grep -vE "^(\-\-\-|\-bt-|\-dc-|\-sc|\-sc-l|\-fl|\-fi|\-fsel|\-ctbl|\-hm-|factor)" | head -50
```
确认删除行都属于：`.bt-*` / `.dc-*` / factor-research 原语 / hm-* / L1856 单行定义；若发现误删（如 `.qds-*`、`@theme inline` 内的 token），回填。

---

### F6 · LOW · 变更文件内的 lint 遗留（未新增，但可修）

- `backtest/page.tsx:669` — `'pEnd' is never reassigned. Use 'const' instead`
- `backtest/page.tsx:784,799,815` — 多处 `setState synchronously within an effect`（同 useCountUp 情况，可能 baseline 有类似）
- `PerformanceTab.tsx:213:76` — `Comments inside children section of tag should be placed inside braces`
- 多处 `no-unused-vars` 警告（TrendingUp/Grid3x3/Activity/BarChart3/CorrelationEntry/X/Badge/STATUS_OPTIONS/STATUS_ZH）

建议本轮修复 prefer-const 与 jsx-no-comment-textnodes 的 error 级别项，unused-vars 警告不阻塞。

---

### F7 · LOW · 16 个新增文件 + 扫描脚本仍 untracked

NFR-3 要求新增文件纳入版本控制。当前：
- `src/web/scripts/verify-ds-compliance.sh`
- `src/web/src/app/research/components/` 下 6+ 新文件
- `src/web/src/app/research/report/[id]/components/` 下拆分产物
- `.cage/tasks/2026-04-19-frontend-ds-standardization/` 整目录

**修复**：执行拆分 / commit 前由 `/cage:commit` 阶段统一 git add（无需在本轮 exec 处理，仅提示 post-verify 注意）。

---

## 修复优先级

1. **必须**（BLOCKER）：F1 → F2 → F3 → F4
2. **强烈建议**：F5（避免引入不可知 regression）
3. **可选**：F6、F7

## 下一步

请运行 `/cage:exec` 对上述 F1-F4 执行修复。修复完成后重新运行 `/cage:verify`，kickback_round 进入 R2。

## 报告归档

- Verifier: `.cage/tasks/2026-04-19-frontend-ds-standardization/verify/report-verifier-r1.md`
- Code-Reviewer: `.cage/tasks/2026-04-19-frontend-ds-standardization/verify/report-code-reviewer-r1.md`
