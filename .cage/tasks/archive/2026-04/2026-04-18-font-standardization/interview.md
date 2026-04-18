# Interview — 前端字体 QDS 标准化

## 原始想法

> 使用 /TinoHelmQDS 定义的设计系统的相关字体 标准化整个前端项目

## 目标

将 TinoHelm 前端（`src/web/`）的字体资源与排版 token 对齐到 `TinoHelmDS` 设计系统的权威规范，完成字体层面的「完整 QDS 对齐」——包含字体资源替换、Inter 风格集启用、中文 fallback 链配置、加载方式升级（`next/font/google` 自托管）、IBM Plex 残留清理、相关文档同步更新。

## 约束

| 约束项 | 说明 | 权威来源 |
|---|---|---|
| 字体资源 | 唯二主字体：**Inter**（UI）+ **JetBrains Mono**（数据）；可选 Source Serif 4；**不引入第三种字体** | `.claude/skills/TinoHelmDS/SKILL.md:25-31` |
| 加载方式 | 使用 `next/font/google` 自托管，替换当前 `src/web/src/app/layout.tsx:26` 的 `@link` CDN | `.claude/skills/TinoHelmDS/fonts/nextjs-setup.md:14-45` |
| OpenType 特性 | body 启用 `font-feature-settings: 'cv11', 'ss01', 'ss03'`（Inter 风格集） | QDS SKILL.md 非协商项 |
| 中文 fallback 链 | Sans: `HarmonyOS Sans SC` → `PingFang SC` → `Source Han Sans SC`；Mono: `Sarasa Mono SC` | `.claude/skills/TinoHelmDS/colors_and_type.css:13-15` |
| Legacy 别名 | `--font-d` / `--font-u` **保留**但值改为 `var(--font-mono)` / `var(--font-sans)`，保证 `globals.css` 内 40+ 处既有引用不破坏 | `colors_and_type.css:19-20` |
| IBM Plex 清理 | `src/web/` 下所有 `IBM Plex` 字面量残留清理干净 | 来自用户验收选择 |
| 文档同步 | `src/web/CLAUDE.md:141` + 根 `CLAUDE.md` 字体相关声明同步更新到新规范 | — |
| 验收自动化 | tasks.md 的验收 item 必须**完全自动化**，禁止出现需手动验证的条目 | 用户全局 RULE |
| 代码改动范围 | 41 个使用 `font-sans` / `font-mono` className 的 `.tsx` 文件**零改动**（依赖 token 自动生效） | explorer 代码事实 |

## 非目标

- 不迁移 `cli/` 目录（项目禁区）
- 不引入 Playwright/视觉回归基础设施（静态断言即可覆盖）
- 不处理颜色、间距、组件库等非字体维度的 QDS 合规
- 不改动 41 个 `.tsx` 消费文件的 className
- 不修改设计参考文件 `docs/ui/qds-*.html`（设计稿源文件不受前端字体切换影响）
- 不引入 Source Serif 4 以外的第三种字体

## 验收标准（完全自动化）

### AC-1 — Token 静态断言（vitest / node 脚本）

在 `src/web/tests/fonts/` 下新增测试，对 `globals.css` 或编译后 CSS 解析断言：

- `--font-sans` 值首位字体为 `Inter`
- `--font-mono` 值首位字体为 `JetBrains Mono`
- `--font-serif` 存在且包含 `Source Serif 4`
- Sans fallback 链包含 `PingFang SC`
- Mono fallback 链包含 `Sarasa Mono SC`
- body `font-feature-settings` 包含 `cv11`、`ss01`、`ss03`
- Legacy alias `--font-d` = `var(--font-mono)`，`--font-u` = `var(--font-sans)`

### AC-2 — 仓库级 Grep 合规

- CI 脚本执行 `rg "IBM Plex" src/web/ --glob '!*.html' --glob '!interview.md' --glob '!CLAUDE.md.bak'`，命中数为 0
- CI 脚本执行 `rg "fonts.googleapis.com" src/web/src/`，命中数为 0（`next/font/google` 自托管后不再直连 CDN）

### AC-3 — 构建与资源校验

- `cd src/web && npm run build` 退出码 0
- 构建产物 `src/web/.next/static/media/` 下存在至少 1 个 Inter woff2 + 1 个 JetBrains Mono woff2（通过 node 脚本枚举校验）

### AC-4 — 文档同步

- `src/web/CLAUDE.md` 中 `IBM Plex` 字面量替换为 `Inter` 和 `JetBrains Mono`
- 根 `CLAUDE.md` 中的字体相关声明（第 268 行上下文）同步更新
- grep 断言两个文件中无 `IBM Plex` 残留

## 本体论（最终稳定，8 个实体）

| 实体 | 类型 | 关键字段 | 关系 |
|---|---|---|---|
| FontToken | 核心领域 | `--font-sans` / `--font-mono` / `--font-serif` / `--font-d` / `--font-u` | 由 `globals.css` 定义，由所有 Consumer 引用 |
| FontFace | 核心领域 | Inter / JetBrains Mono / Source Serif 4（弃：IBM Plex Sans/Mono） | FontLoader 加载后注入到 FontToken |
| FontLoader | 核心领域 | `next/font/google`（替代 `@link` CDN） | 在 `layout.tsx` 调用，产出 CSS 变量绑定到 `<body>` |
| FallbackChain | 核心领域 | 中文优先链：HarmonyOS Sans SC → PingFang SC → Source Han Sans SC → system-ui；Mono：Sarasa Mono SC → ui-monospace | 作为 FontToken 值的后半段 |
| OpenTypeFeatures | 辅助 | cv11 / ss01 / ss03（Inter 风格集）/ tabular-nums / zero（数据对齐） | 通过 `font-feature-settings` 作用于 `<body>` |
| Consumer | 辅助 | 41 个 .tsx 文件使用 `font-sans` / `font-mono` Tailwind className | 依赖 FontToken 间接取值 |
| Docs | 外部系统 | `src/web/CLAUDE.md:141`、根 `CLAUDE.md:268` 周围的字体声明 | 需与 FontFace 实际值保持一致 |
| VerificationMethod | 辅助 | vitest 静态断言 + rg 仓库合规 + `next build` smoke + woff2 产物枚举 | 覆盖 AC-1 ~ AC-4 |

## 棕地代码上下文（explorer 已核实）

| 位置 | 现状 | 目标 |
|---|---|---|
| `src/web/src/app/layout.tsx:25-27` | `<link>` 标签从 googleapis 拉取 IBM Plex Sans/Mono | 替换为 `next/font/google` 导入 Inter + JetBrains Mono + Source Serif 4，通过 CSS 变量注入 |
| `src/web/src/app/globals.css:15-16` | `--font-d: 'IBM Plex Mono'; --font-u: 'IBM Plex Sans';` | 改为 `--font-sans: var(--font-inter), <fallback>;` 等，legacy 别名指向新 token |
| `src/web/src/app/globals.css:217-218`（`@theme inline`） | `--font-sans: var(--font-u); --font-mono: var(--font-d);` | 根据新 token 方案调整（可能反向：让 `--font-d/u` 指向新 token） |
| `src/web/CLAUDE.md:141` | 声明 IBM Plex 字体 | 更新为 Inter + JetBrains Mono |
| 根 `CLAUDE.md:268` 上下文 | 声明 IBM Plex 字体 | 同步更新 |
| 41 个 `.tsx` 消费文件 | 用 `className="font-sans"` / `className="font-mono"` | **零改动** |
| 40+ 处 `var(--font-d)` / `var(--font-u)` 引用（globals.css 内） | 保留 | **零改动**（legacy 别名重新绑定即可生效） |

## 设计系统规范权威引用

- `.claude/skills/TinoHelmDS/SKILL.md:25-31` — 字体纪律「Two fonts, one discipline」
- `.claude/skills/TinoHelmDS/colors_and_type.css:6, 13-15` — 字体资源 @import 与 CSS 变量
- `.claude/skills/TinoHelmDS/fonts/nextjs-setup.md:14-45, 75-111` — Next.js 接入方案（含字重、preload、中文按需加载）
- `.claude/skills/TinoHelmDS/colors_and_type.css:185` — body `font-feature-settings` 声明

## 访谈记录

### 第 1 轮 — 验收范围

- **瓶颈维度**：验收标准（0.50）
- **问**：「标准化整个前端项目」的验收范围覆盖哪些层级？
- **答**：**完整 QDS 对齐**（推荐）——字体资源 + Inter 风格集 + 中文 fallback + `next/font/google` 自托管 + IBM Plex 残留清理 + CLAUDE.md 同步
- **评分变化**：
  - 目标：0.85 → 0.90
  - 约束：0.55 → 0.75
  - 验收：0.50 → 0.92
  - 上下文：0.90（稳定）
  - 架构对齐：0.75 → 0.85
  - **模糊度**：29% → 13.4%
- **本体论**：首次出现 7 实体（FontToken / FontFace / FontLoader / FallbackChain / OpenTypeFeatures / Consumer / Docs），稳定性 N/A

### 第 2 轮 — 验收方式

- **瓶颈维度**：约束（0.75）
- **问**：字体标准化的验收采用哪种自动化方式？（与用户全局规则「禁止手动验证 item」兼容的方案）
- **答**：**静态断言 + 构建校验**（推荐）——vitest token 断言 + rg 无 IBM Plex 残留 + `next build` smoke + woff2 产物存在性
- **评分变化**：
  - 目标：0.90 → 0.95
  - 约束：0.75 → 0.93
  - 验收：0.92 → 0.95
  - 上下文：0.90（稳定）
  - 架构对齐：0.85 → 0.90
  - **模糊度**：13.4% → **6.9%**（远低于 20% 阈值）
- **本体论**：8 实体（+VerificationMethod），7 个沿用 + 1 个新增，**稳定性 7/8 = 87.5%**

## 本体论收敛表

| 轮次 | 实体数 | 稳定 | 新增 | 变更 | 移除 | 稳定性 |
|---|---|---|---|---|---|---|
| 1 | 7 | — | 7 | — | — | N/A |
| 2 | 8 | 7 | 1 (VerificationMethod) | 0 | 0 | 87.5% |
| **最终** | **8** | — | — | — | — | **稳定** |

## 最终模糊度

**6.9%** — 远低于 20% 阈值，准入阶段 D（任务创建 + Planner 细化）。
