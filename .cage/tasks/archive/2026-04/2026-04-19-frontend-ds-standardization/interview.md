# 苏格拉底访谈记录 — 前端 DS 标准化

**任务**：目前所有的页面，按照 TinoHelmDS 进行标准化 / 统一化
**项目类型**：棕地（brownfield）
**访谈轮数**：4
**最终模糊度**：14%（阈值 20%）

## 目标（Goal）

将 `src/web/` 下 14 个前端页面全面对齐 TinoHelmDS 设计系统（`.claude/skills/TinoHelmDS/`），一次性完成四个标准化方向：

1. **消灭内联 style 与遗留 class**：清理 900+ 处 `fontFamily: "var(--font-d)"` 内联、`bt-*`/`dc-*` 等 90+ 处调用；迁移到 Tailwind + QDS 业务组件 + shadcn 原语。
2. **视觉对齐 TinoHelmDS 预览**：页面排版、间距、配色、组件形态与 `.claude/skills/TinoHelmDS/preview/*.html` 的视觉参考保持一致。
3. **组件/Token 用法纪律化**：`StatCard`/`PageHeader`/`SectionLabel`/`InlineError` 等 QDS 业务组件强制复用；`chartTheme.ts` 常量强制 spread；新增 lint/grep 扫描脚本防止回潮。
4. **页面结构与信息架构重构**：按 TinoHelmDS 信息层级重新整理页面区块、标题、空状态、密度；允许在必要处拆分文件。

## 约束（Constraints）

- **范围**：14 个页面 × 4 个方向全部在**单个 Cage 任务**内完成（用户选择「全面一次过」）。
- **遗留 class 处理**：迁移调用点 **并完全删除** `globals.css` 中的 `bt-*`/`dc-*` 等遗留定义（最彻底选项）。
- **文件拆分**：允许在必要处拆分超长单文件（`research/page.tsx` 991 行、`backtest/page.tsx` 1754 行）；目标单文件 <700 行；允许伴随出现文件重命名（git blame 会有动）。
- **禁区**：`cli/` 目录不动（项目 CLAUDE.md 规定）。
- **字体**：Inter（`font-sans`）/ JetBrains Mono（`font-mono`）；`var(--font-u)`/`var(--font-d)` 作为遗留别名，新代码禁止直接内联引用。
- **配色语义**：绿/红永远语义化（盈亏、成功/失败），不做装饰性使用。

## 非目标（Non-goals）

- **后端/API**：不修改 FastAPI、Redis、PostgreSQL 相关代码。
- **新功能**：不新增业务功能（只做视觉与结构标准化）。
- **CLI/TUI**：Rust `cli/` 目录完全不碰。
- **tests/**：不新增业务逻辑测试；但可新增 DS 合规的 lint/grep 扫描脚本。
- **`.claude/skills/TinoHelmDS/` 本身**：不修改 skill 内容，仅作为事实来源对照。

## 验收标准（Acceptance Criteria）

用户选择**全部四条验收线**叠加：

### AC-1：代码扫描式验收（自动化）
新增 `src/web/scripts/verify-ds-compliance.sh` 或类似 lint 脚本，执行后：
- 禁止 `style={{ fontFamily: "var(--font-d)" }}` / `"var(--font-u)"` 等内联字体声明
- 禁止 `className="bt-*"` / `"dc-*"` / `"cg"`/`"ca"`/`"cr"` 等遗留 class 出现
- 禁止硬编码颜色（`bg-[#xxxxxx]`、`text-[#xxxxxx]`、`style={{ color: "#..." }}`）
- 强制 Recharts `Tooltip` 使用 `{...CHART_TOOLTIP_PROPS}` spread；`CartesianGrid` 使用 `{...CHART_GRID_STYLE}`
- 脚本 exit 0 = 合规；exit 1 = 失败并打印违规文件/行号

### AC-2：视觉回归对照
- 14 页在 dark mode 下的截图与 `.claude/skills/TinoHelmDS/preview/*.html` 的视觉参考（component / color / spacing / typography / pattern 卡片）对照
- 间距/排版/配色/组件形态偏差 ≤ 2px 或 token 完全一致
- 由用户在验证阶段提供截图对照反馈

### AC-3：Dark + Light 双主题验证
- 14 页切换到 light mode（`html.light` class）下无破色、无对比度不足问题
- 所有 token-based 颜色在两套主题下均可读（尤其 `text-muted-foreground`、`border`、`bg-secondary`）
- 用户在验证阶段亲自切换 light mode 检查

### AC-4：文档事实来源
- `src/web/CLAUDE.md` 补入「标准化后的约束」章节：Tailwind 优先顺序、QDS 业务组件强制列表、`chartTheme` 统一入口、禁区 class 清单
- 可作为今后代码审查与 agent 生成前端代码的依据

## 实体（Ontology）

| 实体 | 类型 | 关键属性 | 关系 |
|------|------|---------|------|
| Page | 核心 | route, file, lines, stdize_level | has many Components, uses DS Tokens |
| DS Token | 核心 | name, value (dark/light), kind | consumed by QDS Class + Tailwind Theme |
| QDS Class | 核心 | selector, category, legacy_flag | defined in globals.css, used by Page |
| Legacy Class | 核心 | prefix (bt/dc/cg/ca/cr), call_sites | subset of QDS Class, targeted for removal |
| Inline Style | 核心 | file, line, property (font/color/size) | anti-pattern, targeted for migration |
| Chart Style | 核心 | const_name, shape | exported from chartTheme.ts |
| Component | 核心 | name, file, tier (qds-business/shadcn/domain) | composed into Page |
| Visual Alignment | 辅助 | preview_file, target_page | mapping between TinoHelmDS preview and Page |
| Information Architecture | 辅助 | page, sections, hierarchy | structural layer of Page |
| Dark/Light Theme | 辅助 | mode, token_overrides | applied globally via `:root` + `@media (prefers-color-scheme: light)` |

## 访谈轮次

### 第 1 轮 — 验收标准瓶颈
- **问题**：「标准化 / 统一化」在你心中完成时长什么样？
- **回答**：全部四项（消灭内联 style + 视觉对齐 preview + 组件纪律化 + 信息架构重构）
- **评分**：模糊度 56% → 44%（最弱：约束）

### 第 2 轮 — 约束瓶颈
- **问题**：14 页 × 4 方向，一次过还是分批？
- **回答**：全面一次过（单个 Cage 任务内 14 页全做）
- **评分**：44% → 36%（最弱：验收标准）

### 第 3 轮 — 验收方式
- **问题**：每个方向用什么操作化规则判定达标？
- **回答**：代码扫描 + 视觉回归 + dark/light 双主题 + 文档事实来源（全部四条）
- **评分**：36% → 21%（最弱：约束）

### 第 4 轮 — 破坏性与拆分
- **问题**：遗留 class 怎么处理？超长文件可否拆？
- **回答**：迁移调用点 + 完全删除 globals.css 定义；允许必要处拆分
- **评分**：21% → **14%**（达标 ✓）

## 本体论收敛

| 轮次 | 实体数 | 新增 | 变更 | 稳定 | 稳定性 |
|-----|--------|-----|------|------|--------|
| 1 | 7 | 7 | 0 | 0 | N/A |
| 2 | 9 | 2 | 0 | 7 | 78% |
| 3 | 10 | 1 | 0 | 9 | 90% |
| 4 | 10 | 0 | 0 | 10 | **100%**（收敛）|

连续 2 轮实体稳定，领域模型已固化。
