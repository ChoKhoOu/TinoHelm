# 2 · 调研文档 — 前端 DS 标准化

## 2.1 调研结论

**本任务不进行额外外部调研**。

理由：

1. **设计系统事实来源唯一**：`.claude/skills/TinoHelmDS/` 是 TinoHelm Web 设计语言的唯一事实源。该 skill 本身已由独立流程维护并版本化，其下 `SKILL.md` / `README.md` / `colors_and_type.css` / `Web UI Kit.html` / `Charts Spec.html` / `preview/*.html`（21 个组件级卡片）构成设计、组件、token、排版、动画的完整定义。本任务执行方只能**对齐**该事实源，不能修改。
2. **Tailwind / shadcn / Recharts 技术栈已稳定**：`src/web/package.json` 已锁定 Next.js 16 + React 19 + Tailwind v4 + shadcn/ui v4（base-nova preset）+ Recharts；`src/web/CLAUDE.md` 已给出技术映射表（QDS token ↔ Tailwind class）。
3. **迁移模式已在中后期页面验证**：`/analytics` / `/trading` / `/strategies` 等页面已使用目标模式（Tailwind + shadcn + QDS 业务组件 + `chartTheme.ts` 常量 spread），说明模式可行、无需重新调研。

## 2.2 事实来源一览（**Round 2 修正 — 移除 docs/ui/ 残留引用**）

| 事实来源 | 位置 | 扮演角色 |
|---|---|---|
| TinoHelmDS skill 主入口 | `.claude/skills/TinoHelmDS/SKILL.md` | 设计哲学、10 条不可妥协的规则、快速起步模板、禁区清单 |
| skill 组件 preview | `.claude/skills/TinoHelmDS/preview/*.html`（21 个） | 组件级视觉对照卡片（brand / color / component / spacing / type 五大族） |
| skill 页面级装配参考 | `.claude/skills/TinoHelmDS/Web UI Kit.html` + `.claude/skills/TinoHelmDS/Charts Spec.html` | 页面级完整 frame + Recharts 样式规约 |
| 既有技术说明 | `src/web/CLAUDE.md` | Tailwind 映射表、QDS 业务组件清单、Chart theme 使用说明、4-Layer Notification |
| 设计 token 底层 | `src/web/src/app/globals.css` L13-L150（`:root`） | 47 个 QDS 短 token + shadcn oklch 变量映射 |
| 图表常量 | `src/web/src/lib/chartTheme.ts` | Recharts Tooltip / Grid / Axis / Colors / Animation 的 React.CSSProperties 常量 |
| QDS 业务组件 | `src/web/src/components/qds/`（7 个） | StatCard / PageHeader / SectionLabel / InlineError / StatusBadge / HelpTip / ShimmerBar |
| shadcn 原语 | `src/web/src/components/ui/`（28 个） | Button / Card / Dialog / Table / Tooltip（@base-ui）等 |

> **Round 1 修正（已在 r1 Revision Notes 记录）**：页面级装配参考从 `docs/ui/qds-*.html`（已验证不存在 @ 2026-04-19：`ls /Users/ouzhuohao/TinoHelm/docs/ui/` → No such file or directory）改为 `.claude/skills/TinoHelmDS/Web UI Kit.html` + `.claude/skills/TinoHelmDS/Charts Spec.html`。
>
> **Round 2 修正**：本文件 L19 原残留 `docs/ui/qds-*.html（既有）` 表述（r1 修复漏网），已修订为当前 skill 下的两个 HTML 参考；全文 `docs/ui/` 引用已 0 命中。

## 2.3 备选方案对比（非外部调研，仅本地权衡）

### 2.3.1 扫描工具：bash + ripgrep vs Node ESLint 规则

| 维度 | bash + ripgrep | ESLint 自定义规则 |
|---|---|---|
| 编写成本 | 低（正则即可） | 中（需 AST 遍历、jsx-plugin） |
| 运行速度 | 极快（ripgrep 并行） | 中（需解析 AST） |
| 集成到 CI | 一条 bash 命令 | 需 `npm run lint` 配置 |
| 精度 | 多行 JSX 匹配需 `-U --multiline-dotall` 技巧 | AST 精度更高 |
| 既有生态 | `src/web/scripts/check-grep-fonts.sh` 已有 bash 先例 | 引入新依赖 |
| 维护成本 | 规则即正则，易读易改 | 需要理解 ESTree |

**选择**：bash + ripgrep。与 `check-grep-fonts.sh` 一致，零新依赖，执行快。精度缺口由"两阶段匹配（rg + grep -v）"补齐（R6 / R7 / R9 均采用此模式）。

### 2.3.2 拆分方式：组件内抽 vs 拆成独立文件

| 维度 | 内部抽取 sub-component | 独立子文件 |
|---|---|---|
| blame 稳定 | 高（不改文件） | 中（需 `git mv`） |
| 重用性 | 低（仅本文件内） | 高（可跨组件） |
| 行数削减 | 低（只是重排） | 高（物理拆分） |
| 建议场景 | 700-800 行边界情况 | ≥ 1000 行 |

**选择**：≥ 1000 行的文件（backtest/page / PerformanceTab / research/page / ReportClient）独立拆分；754-817 行（strategies / OverviewTab / TradesTab）酌情处理（优先内部抽取，确实减不下来再拆）；677 行（OverviewGreyTab）评估与 OverviewTab 合并。

## 2.4 不调研的反向论证

为确认"不调研"是合理决策，逐项反向检查：

| 潜在需要调研的方向 | 是否需要 | 理由 |
|---|---|---|
| Tailwind v4 迁移指南 | 不需要 | 项目已在 v4；`@theme inline` 与 `@custom-variant dark` 已就位 |
| shadcn/ui v4 API 变更 | 不需要 | 本任务不升级 shadcn；仅使用既有原语 |
| Recharts wrapperStyle / contentStyle 接口 | 不需要 | 类型为 React.CSSProperties，向后兼容 |
| 新字体引入 | 不需要 | 禁区：不引入第三种字体 |
| 视觉回归工具（Percy / Chromatic） | 不需要 | 验收退化为扫描脚本 + preview 对照矩阵；如未来需要可独立引入 |
| 替代合规扫描方案（Biome / Oxc） | 不需要 | 见 §2.3.1 的权衡结论 |

## 2.5 结论

本任务的事实来源、技术栈、迁移模式、验收方法均已在本地既有材料中收敛完毕。**无需外部调研**，直接进入 `3-tech-design.md` 与 `4-tasks.md` 的方案执行。
