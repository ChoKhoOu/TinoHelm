# User Acceptance Checklist — 前端 DS 标准化

> **性质**: 本文件是 **User Acceptance（用户验收）** 清单，由主 agent 在 verify phase 向用户展示，由用户在浏览器中完成。
>
> **重要**: 本清单**不是** subtask acceptance_criteria 的一部分，不在 PR 的自动化验证范围内。所有自动化验证（R1-R14 扫描 + build + tsc + lint + selftest）已在 subtask 层完成并通过。
>
> **前提**: 执行本清单前，需要 `cd src/web && npm run dev` 启动本地开发服务器。

---

## AC-2 · 视觉对照（14 页 vs `.claude/skills/TinoHelmDS/preview/*.html`）

对每个页面，在浏览器打开对应路由并与参考 preview 卡片比对。

| # | 路由 | 对照 preview 卡片 | 检查点 | 状态 |
|---|---|---|---|---|
| 1 | `/` | `preview/component-kpi.html` + `Web UI Kit.html` | KPI 栅格间距；数字字体分层（JetBrains Mono + 语义色）；首页 StatCard 对齐 | [ ] |
| 2 | `/backtest`（列表态） | `preview/component-row.html` | 行左侧 3px accent 装饰条存在；hover 态背景切换（`bg-secondary`）；symbol + strategy 名字体分层 | [ ] |
| 3 | `/backtest`（详情 Overview） | `preview/component-kpi.html` + `Web UI Kit.html` | KPI 卡片间距；equity 曲线图 tooltip 样式；monthly heatmap 色阶（Recharts label font） | [ ] |
| 4 | `/backtest`（详情 Performance） | `Charts Spec.html` | CartesianGrid 线条颜色；Tooltip 背景/边框；Legend 字体大小（0.62rem mono）；ReferenceLine label 字号 10 | [ ] |
| 5 | `/backtest`（详情 Trades） | `preview/component-row.html` | SectionLabel 小标题样式（上方 1px 线 + 小 caps + accent 橙）；表格行间距 | [ ] |
| 6 | `/backtest`（详情 Tearsheet / Robustness） | `Charts Spec.html` | ReferenceLine label 位置与字号；图表整体风格一致 | [ ] |
| 7 | `/data-catalog` | `preview/component-badges.html` + `preview/component-progress.html` + `preview/component-kpi.html` | 7 色 type 徽章色相（klines→info 蓝、aggTrades→success 绿等）；coverage bar 扫光动画；KPI 数字 mono 字体 | [ ] |
| 8 | `/data-catalog`（FetchDialog） | `preview/component-inputs.html` | Select 输入框样式；下拉选项字体 | [ ] |
| 9 | `/strategies` | `preview/component-sidebar.html`（若适用） | 策略行 3px accent 左边框 active 态；font-mono 策略名 | [ ] |
| 10 | `/trading` | `preview/component-tabs.html` + `preview/component-badges.html` | TabNav accent 下划线；StatusBadge 状态徽章（rounded-full + QDS 颜色） | [ ] |
| 11 | `/research` | `preview/type-section-label.html` + `preview/component-kpi.html` | section-label 小 caps + accent 橙 + 1px 灰线延伸；Factor KPI 数字分层；heatmap（若存在）色阶 | [ ] |
| 12 | `/research/report/[id]` | `preview/type-data.html` + `Charts Spec.html` | 数据值 font-mono；ReferenceLine label 位置；报告标题层级 | [ ] |
| 13 | `/analytics` / `/optimization` / `/orders` / `/watchlist` | `preview/color-semantic.html` + `preview/component-row.html` | 正值绿/负值红仅用于 P&L 语义场合，无装饰性红绿；行布局间距一致 | [ ] |
| 14 | `/settings` | — | 表单布局（`qds-input` / `qds-label`）；主题切换按钮可用 | [ ] |

**反馈路径**: 若发现视觉偏差，在 verify 阶段向主 agent 说明具体页面与差异描述。主 agent 派 agent 回迁对应 sN subtask（不阻塞 PR 合并）。

---

## AC-3 · dark / light 双主题验证（14 页 × 2 主题）

通过页面右上角 `ThemeToggle` 切换 dark / light 主题，对以下各页面执行检查。

### 检查维度

| 维度 | 说明 |
|---|---|
| **文字对比度** | light 模式下正文是否清晰可读（接近 WCAG AA 4.5:1） |
| **色板切换** | dark 的深棕灰背景（`--bg-s`）是否正确切换为 light 浅色；`--t0` 正文跟随切换 |
| **accent 表现** | 焦橙（`--acc`）在 dark 与 light 下饱和度一致，无洗白或过深 |
| **图表可见度** | Recharts `CartesianGrid` 在 light 下不过浅；Tooltip 背景对比度充足 |
| **徽章色** | `bg-qds-*-dim` 背景在 light 下与文字颜色对比充足 |

### 逐页检查

| # | 路由 | dark 状态 | light 状态 |
|---|---|---|---|
| 1 | `/` | [ ] 通过 | [ ] 通过 |
| 2 | `/backtest` 列表 | [ ] 通过 | [ ] 通过 |
| 3 | `/backtest` 详情（含图表） | [ ] 通过 | [ ] 通过 |
| 4 | `/data-catalog` | [ ] 通过 | [ ] 通过 |
| 5 | `/strategies` | [ ] 通过 | [ ] 通过 |
| 6 | `/trading` | [ ] 通过 | [ ] 通过 |
| 7 | `/research` | [ ] 通过 | [ ] 通过 |
| 8 | `/research/report/[id]` | [ ] 通过 | [ ] 通过 |
| 9 | `/analytics` | [ ] 通过 | [ ] 通过 |
| 10 | `/optimization` | [ ] 通过 | [ ] 通过 |
| 11 | `/orders` | [ ] 通过 | [ ] 通过 |
| 12 | `/watchlist` | [ ] 通过 | [ ] 通过 |
| 13 | `/settings` | [ ] 通过 | [ ] 通过 |
| 14 | `/strategies/[name]`（EditorClient） | [ ] 通过 | [ ] 通过 |

**反馈路径**: 若 light 模式下某 token 缺 override（如某元素颜色不切换），回迁到 s12 在 `globals.css` 的 `html.light { }` 作用域补对应 token override。

---

## AC-5 · StatusBadge barrel 视觉差异

s11 将 `components/StatusBadge.tsx` 改为 barrel re-export，内部切换到 `components/qds/status-badge.tsx` 的 QDS 实现。QDS 实现使用 `rounded-full` + `<span>` 结构，与原 shadcn `<Badge>` 的 `rounded-md` 外观不同。

### 检查页面

| # | 路由 / 位置 | 说明 |
|---|---|---|
| 1 | `/backtest` 列表每行的 run status 徽章 | 对比 barrel 切换前后的圆角与 padding |
| 2 | `/optimization` 每行 opt run status | 同上 |
| 3 | `/data-catalog` JobQueue 列表（queued / completed / failed） | QDS StatusBadge locale="zh" 显示"等待中"/"完成"/"失败" |
| 4 | `/research` 历史 Job 行（若有 status） | 同上 |

### 用户决策

- **可接受**（`rounded-full` + QDS 颜色看起来正常）: 保留 barrel 直通方案，在 verify 阶段确认后关闭 AC-5
- **不可接受**（圆角或 padding 差异过大）: 通知主 agent，主 agent 派 agent 执行 §3.3.9 fallback 方案（保留顶层 Badge 外观 + 内部改用 QDS label/color map），追加工作量 0.5-1h

---

## Post-task Memory 更新清单

> 本节供主 agent 在 verify phase 向用户确认后执行。用户确认后主 agent 更新以下 memory 文件。

### 需要作废/更新的 memory 文件

| Memory 文件 | 当前主张 | 建议操作 |
|---|---|---|
| `feedback-bt-card-classes.md` | bt-cd/bt-cd-header/bt-cd-body 强制用 backtest 专属 class | **作废**（2026-04-19）：改为 shadcn `<Card>/<CardHeader>/<CardContent>`；在文件顶部追加"[已废止 2026-04-19: DS 标准化任务取代，改用 shadcn Card]" |
| `feedback-use-existing-css.md` | 优先使用 globals.css 已有 class | **作废**（2026-04-19）：bt-*/dc-*/factor-research class 已从 globals.css 删除；在文件顶部追加"[已废止 2026-04-19: DS 标准化任务取代，禁止使用遗留 class]" |
| `feedback-css-class-naming.md` | 使用 HTML 参考的原始 class 名（cd/ctbl/sl） | **作废**（2026-04-19）：factor-research 原语已全面迁移；在文件顶部追加"[已废止 2026-04-19: DS 标准化任务取代，factor-research 原语已删除]" |
| `feedback-pixel-perfect.md` | 像素级还原，使用 inline style 框架 | **部分保留**：像素级还原目标保留；但实现路径改为 Tailwind 语义类 + QDS 组件；追加说明"inline style fontFamily/fontSize 已禁止，用 font-mono/font-sans/text-[size] 替代" |

### 需要新增的 memory 条目

在 `MEMORY.md` 追加：

```
- [DS standardization](project-ds-standardization.md) — Completed (2026-04-19): 12 subtasks, R1-R14 compliance scan script, bt-*/dc-*/factor-research classes deleted from globals.css (~1202 lines), all 14 pages migrated to Tailwind + QDS components, CHART_LEGEND_STYLE/CHART_LABEL_STYLE added to chartTheme.ts
```

### 确认流程

1. 主 agent 向用户展示本清单
2. 用户完成 AC-2 视觉对照 + AC-3 双主题验证 + AC-5 StatusBadge 决策
3. 用户确认 memory 作废列表
4. 主 agent 更新 `/Users/ouzhuohao/.claude/projects/-Users-ouzhuohao-TinoHelm/memory/MEMORY.md` 和相关 feedback-*.md 文件
5. 关闭任务

---

*生成时间: 2026-04-19 | 任务: 2026-04-19-frontend-ds-standardization / s12*
