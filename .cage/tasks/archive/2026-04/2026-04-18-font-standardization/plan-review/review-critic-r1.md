# Critic Review — Round 1

**VERDICT: REVISE**

## 总体评估

规划质量高于平均水平：苏格拉底访谈产出的最终模糊度 6.9%，文档完整性、可追溯矩阵、非目标边界、DAG 划分、100% 自动化验收都认真对待了用户规则。但存在 **3 个 CRITICAL 问题会直接阻塞验收闭环**（1 个与 architect 同向但严重性被低估，2 个 architect 未发现），另有 6 个 MAJOR 和若干 MINOR。

本轮审查与 `review-architect-r1.md` 同轮独立进行。对其已发现的 CRITICAL/MAJOR 我会引用其编号避免重复、并在必要处升级严重性。我还独立发现若干其未覆盖的问题（集中在测试 fixture 的实际可执行性、AC 验收命令的语义漏洞、`test -le` 空管道陷阱）。

## 预判 vs 实际

**预判**（阅读前）：
1. 「Source Serif 4 加载方式」与 Next.js `axes` 参数的适配 —— OpenType 风格集（cv11/ss01/ss03）无法通过 next/font 参数启用
2. `rg` AC 命令的空管道语义陷阱
3. `.font-sans { font-family: var(--font-u) }` 这种 CSS 类覆写的处理策略缺失
4. Next.js 16 font loader 的实际文件名格式可能破坏 `verify-build-fonts.mjs` 正则
5. 用户全局 RULE（"手动验证" 禁令）的违反

**实际**：
- 预判 1：部分应验（风格集只能通过 body CSS 注入，plan 做对了这一点）；但 Source Serif 4 AC 的「否则跳过」分支违反不可跳过规则（architect M4）
- 预判 2：**已确认 BLOCKER**（见 C3）
- 预判 3：**已被 architect C1 覆盖**（严重性 CRITICAL，同向）
- 预判 4：**已被 architect M3 覆盖**，但我认为严重性应升级为 CRITICAL（见 C4）
- 预判 5：**扩展发现** —— 虽然没有字面上的"手动验证"字样，但 AC-3.2 的「否则跳过」分支等价于"无论如何都会 PASS"，实质等同于跳过自动化（见 C5）
- **新增发现**：tokens.test.ts 的 `extractBlock(css, 'body')` 正则会匹配错误的 `body` 块（CRITICAL，见 C1）

## Critical 发现（阻塞执行）

### C1 — `extractBlock(css, 'body')` 会匹配到错误的 `html, body` 块，导致所有 body 断言失败（**新发现**，architect 未识别）

- **证据**：`3-tech-design.md:250-258` 定义的 `extractBlock(css, selector)` 使用正则 `${selector}\\s*\\{([\\s\\S]*?)\\}`（lazy）。实际 globals.css 在 `@layer base` 内有两处 `body` selector：
  - `globals.css:179`: `html, body { height: 100%; }`（**第一次出现** `body\s*{`）
  - `globals.css:206`: `body { @apply ... font-family: var(--font-u); ... }`（第二次）
- **运行模拟**：`re.match(css)` 返回第 **179 行**的 `body\s*{` 到 `}` 之间内容，即 `\n    height: 100%;\n  `。
- **影响**：`tokens.test.ts:313-316` 的三条断言 `expect(body).toMatch(/font-feature-settings.*cv11|ss01|ss03/)` 在 **`"\n    height: 100%;\n  "`** 上执行，**全部 FAIL**。
- **置信度**：HIGH（已通过代码与正则行为逐步推演验证）
- **影响链**：
  1. t5 `npm run test:fonts` 退出码非 0 → t17 汇总验证失败 → 整个 P-E-V 循环卡在 verify 阶段
  2. 即使 executor 正确写入了 `font-feature-settings`，测试仍会失败 → 假的"执行失败"信号 → debug 浪费时间
- **修复**：`extractBlock` 必须在 selector 正则前加「selector 开始符」或 lookbehind，排除多选择器场景。例如：
  ```ts
  const re = new RegExp(`(?:^|[\\s,;\\}])${escaped}\\s*\\{([\\s\\S]*?)\\}`, 'm');
  ```
  或者使用更精确的 `^\\s*${escaped}\\s*\\{` + multiline flag。最稳妥的方案：**放弃 ad-hoc 正则，改用 postcss 的 AST 解析**（`postcss` 已在 `@tailwindcss/postcss` 依赖链中，无额外 npm cost）。
- **关联**：必须同步修复 `extractBlock(css, ':root')`（html.light 块也以 `:root` 为变体，但当前 CSS 无此写法，暂安全）以及未来扩展任何嵌套 `@layer`/`@media` block 的提取。

### C2 — task.json 所有 subtasks 的 `depends_on` 全为 `null`，DAG 被拍平（**architect C2 已报**，同意 CRITICAL）

- **证据**：`task.json:10-103` 16 个 subtask 的 `depends_on` 全为 `null`。4-tasks.md 明确每个任务的 `depends_on`，但 task.json 未同步。
- **影响**：如果 executor 以 `subtasks[].depends_on` 为 DAG 真源，t5 会和 t3/t4/t9 同时启动，tokens.test.ts 将读到未改写的 globals.css（原 IBM Plex 状态），断言全 FAIL。如果 executor 改用 `parallel_groups`，则 OK —— 但两个字段不一致是明确缺陷，依赖 executor 偏好不可靠。
- **置信度**：HIGH
- **修复**：见 architect C2 给出的精确填充表。**同时**将 t5 的 `depends_on` 由 `["t3","t4","t9"]` 更正为 `["t3","t4","t8","t9"]`（补 t8）。

### C3 — t10/t11 的"至少 N 个 var(--font-mono) 命中"断言在 0 匹配时**静默 PASS**（**新发现**，architect 未识别）

- **证据**：`4-tasks.md:176` / `4-tasks.md:189`
  ```bash
  rg -c 'fontFamily:\s*"var\(--font-mono\)"' src/web/src/app/analytics/page.tsx | xargs test 2 -le
  ```
  - `rg -c "notfound" file` 无匹配时：**无 stdout 输出 + 退出码 1**。
  - `xargs` 对空输入**默认不执行命令，直接退出 0**（GNU xargs, BSD xargs 同此行为）。
  - 即使 rg 退出码非 0，因为管道退出码由最后一个命令决定 → 整条命令 PASS。
- **实测验证**：
  ```
  $ rg -c "notfound" /nonexistent 2>/dev/null | xargs test 2 -le; echo exit=$?
  exit=0
  ```
- **影响**：如果 executor 忘记替换字面量或替换错误（例如替换成 `var(--font-u)` 或其他字符串），t10/t11 验收**仍然 PASS**。只有 t17 汇总里的 `! rg -q "IBM Plex"` 部分能兜底，但 t10/t11 自身的细粒度断言失效。
- **置信度**：HIGH（已通过 shell 实测）
- **影响链**：
  1. 如果 executor 漏替换某一行（例如 OverviewTab.tsx 只替换了 3/4 行），t11 断言「至少 4 处 var(--font-mono)」写的是 `test 4 -le $count`，若 `$count=3`，xargs 送入的命令是 `test 4 -le 3`，退出码 1，管道失败 —— **此路径能正确失败**。
  2. 但如果 executor 把 `"IBM Plex Mono"` 替换成了错误的字符串（例如 `"var(--font-d)"` 或空字符串），`rg -c 'fontFamily:\s*"var\(--font-mono\)"'` 匹配数为 0，xargs 空管道 PASS。
  3. **更糟的场景**：如果 executor 删除了整个 fontFamily 块（如认为"var 应自动 fallback"），rg 0 匹配，验收 PASS，但运行时 Recharts 不识别 var() 字符串语法（某些场景下 Recharts tick.fontFamily 需要逐字字体名，不能是 CSS var 引用——需验证）。
- **修复**：改为显式 grep 命令 + 数字比较：
  ```bash
  count=$(rg -c 'fontFamily:\s*"var\(--font-mono\)"' src/web/src/app/analytics/page.tsx 2>/dev/null || echo 0)
  test "$count" -ge 2 || { echo "expected >=2, got $count"; exit 1; }
  ```
  或者改为直接断言字符串存在：`rg -q 'fontFamily:\s*"var\(--font-mono\)"' file && rg -cq ... | ... ` 明确检查至少一次出现。

### C4 — `verify-build-fonts.mjs` 正则必然失败（**architect M3 已报，严重性应升级**）

- **证据**：`src/web/node_modules/next/dist/build/webpack/loaders/next-font-loader/index.js:77`
  ```js
  const interpolatedName = loaderUtils.interpolateName(this, `static/media/[hash]${isUsingSizeAdjust ? '-s' : ''}${preload ? '.p' : ''}.${ext}`, opts);
  ```
  文件名格式为 `[hash]{-s|}{.p|}.woff2`，**绝不包含** `inter` / `jetbrains` / `source-serif` 字符串。
- 对比：`next-image-loader/index.js:15` 使用 `[name].[hash:8].[ext]` —— 仅图片有 `[name]`。
- **影响**：`3-tech-design.md:369-373` 的正则 `/inter.*\.woff2$/i`、`/jetbrains.*mono.*\.woff2$/i`、`/source.*serif.*\.woff2$/i` **无论如何都不会命中**。AC-3 的 `verify:build:fonts` 100% 失败。
- **置信度**：HIGH（已通过阅读 Next.js 16 源码确认，项目本地 package version 为 `next@16.1.6`）
- **严重性升级原因**：architect 定为 MAJOR，但这是 **AC-3 的硬阻塞** —— 无论代码改得多对，这个脚本都会失败，执行阶段根本不可能通过验收，**必然进入死循环**。因此应定为 CRITICAL。
- **修复**：采用 architect 提出的"两层防御"思路，但**必须以「用 CSS @font-face 解析 font-family 字面量」为强证据**，纯文件计数可能不准（next/font 会为 latin + latin-ext + sizeAdjust 生成多个 woff2，一个字体可能有 4+ 文件）。推荐实现：
  ```js
  // 步骤 1：找到 .next/static/css/*.css
  // 步骤 2：断言 CSS 文本包含 font-family: 'Inter' + 'JetBrains Mono' + 'Source Serif 4' 字面量（Next.js 会生成带 fallback family 的 @font-face，family 名字保留）
  // 步骤 3：断言 woff2 文件数 >= 2（sanity check）
  ```

### C5 — AC-3.2 Source Serif 4 的「否则跳过」分支等价于"跳过自动化验证"（**architect M4 已报，严重性应升级**）

- **证据**：`1-requirements.md:148`：「若启用 Source Serif 4，至少 1 个匹配 `/source.*serif.*\.woff2$/i`（**否则跳过此断言**）」。同时 `3-tech-design.md:384-385` verify-build-fonts.mjs 中 optional 列表里 Source Serif 4 「unlimited PASS」。
- **影响**：用户全局 RULE 明确禁止"手动验证"类 item。"否则跳过"等价于：无论产物是否正确，此 AC 恒为 PASS。这违反了「100% 自动化且不可跳过」的规则本意。
- **Plan 内部矛盾**：
  - `3-tech-design.md:138` layout.tsx 强制加载 Source Serif 4（`preload: false` 但仍然 import）
  - AC-3.2 却允许跳过其存在性检查
  - 两者不能同时成立 —— 要么强制加载+强制断言，要么完全不加载+不断言
- **置信度**：HIGH
- **修复**：既然 t6 的 FR-1 要求加载 Source Serif 4，AC-3.2 就必须强制断言 woff2 存在。删除 optional/skip 逻辑。或者：如果接受"Source Serif 4 不加载"（走 `preload: false` 但 subsets: []），则 AC-3.2 的断言改为"强制断言 Source Serif 4 **不在**产物中"（反向断言）。两种都可接受，但不能模糊。

---

## Major 发现（导致显著返工）

### M1 — globals.css `.font-sans`/`.font-mono`/`.font-heading` 类覆写未处理（architect C1，同意 MAJOR 或 CRITICAL）

architect 定 CRITICAL，我同意。证据与修复见其 C1 详述。**补充一个 architect 未提的细节**：`@layer base { .font-sans { font-family: var(--font-u); } }` 在 Tailwind v4 生成的 utility `.font-sans { font-family: var(--font-sans); }` 之后定义，由于**同 selector + 同 layer，源码顺序决胜**，手写的在 `@layer base` 内后出现，**胜出**。所以消费方 `className="font-sans"` 实际命中的是**手写覆写**，多绕一跳 legacy alias。删除才是正解。

### M2 — t5 缺 t8 依赖（architect M2）

无独立补充。

### M3 — chartTheme.ts 未纳入影响域文档（architect M1）

无独立补充。

### M4 — check-grep-fonts.sh 路径漂移（architect M5）

无独立补充。

### M5 — `@theme inline` 自引用的 Tailwind v4 文档证据缺失（architect M4）

**补充**：我实际检查了 Tailwind v4 源码（`src/web/node_modules/@tailwindcss/postcss/dist/index.mjs`）和官方行为描述，`@theme inline` 的语义是"将该 variable 的值直接内联进 utility 定义"。对于 `--font-sans: var(--font-sans);` 的自引用，Tailwind v4 应该会展开为 `.font-sans { font-family: var(--font-sans); }`，其中 `var(--font-sans)` 在运行时解析 `:root` 的 `--font-sans`。但这依赖具体实现 —— `t9` 的 `npm run build` smoke 确实是唯一有效验证。建议在 tech-design 明确：若出现编译循环错误，回退方案是把 `@theme inline` 里展开为完整 fallback 链字面量（放弃 identity 转发，代价是重复）。

### M6 — `rg` 命令版本差异（**architect 未报**，新发现）

- **证据**：`1-requirements.md:126` 的 `rg "IBM Plex" src/web/ --glob '!*.html' --glob '!*.bak'`。这些命令假设 ripgrep >= 某版本支持 `--glob '!pattern'` 否定语法。
- **潜在问题**：项目 CI/执行环境的 ripgrep 版本未在任何文档声明。不同版本行为差异：
  - rg 13+ 默认 respect .gitignore，若 src/web/node_modules 未被 gitignore（通常是 gitignore 的）就会搜索
  - `.cage/tasks/.../interview.md` 含 IBM Plex 字面量 —— 默认情况下 rg 搜索 `src/web/` 不会遍历到 `.cage/tasks/`（路径隔离），**不是问题**
- **影响**：次等；架构上应在 package.json scripts 或 check-grep-fonts.sh 声明 rg 最低版本。
- **修复**：在 `check-grep-fonts.sh` 开头加 `command -v rg >/dev/null || { echo "rg not installed"; exit 1; }`，或文档化 rg 13+ 要求。

---

## Minor 发现（次优但可工作）

### m1 — 数字口径不一致（architect m1 已报）

97 vs 96 vs 40+；44 vs 41。累积令读者困惑。

### m2 — tokens.test.ts 缺 "字段被注释掉" 的 sanity 检查（architect 权衡分析中提到）

推荐补一条：`expect(css).not.toMatch(/\/\*[\s\S]*?--font-sans:[\s\S]*?\*\//)` 防 executor 误注释。

### m3 — t5 验收命令应加 `--reporter=verbose`（architect m3）

### m4 — AC-2.1 与 check-grep-fonts.sh 的 glob 集合不一致（architect m4）

### m5 — `.font-heading` 语义未定义（**新发现**）

`globals.css:189-191` 定义 `.font-heading { font-family: var(--font-u); }`，但规划文档中**完全没提**这个类。三种处理策略：
1. 视为"legacy class 保护域"，零改动（合理）
2. 将右值改为 `var(--font-sans)`（语义清晰）
3. 删除（如果无消费方）

grep 结果表明有 **3 个 .tsx 文件**使用 `font-heading` className（architect m1 数据），所以不能直接删除。plan 文档应明确选 1 还是 2。

### m6 — 未验证 ESLint 行为对 `next build` 影响（**新发现**）

- package.json `"lint": "eslint"`。`next build` 在 Next.js 16 默认开启 lint-during-build，如果 layout.tsx 改动触发新的 lint 规则（例如 `const inter = Inter(...)` 未使用警告、如果 variable 未在 className 中引用），build 可能失败。
- **置信度**：LOW（设计良好的实现不会触发 lint 错误，但值得 executor 警惕）
- **影响**：次等；不升级为 MAJOR。
- **建议**：在 tech-design 加一句 "t6/t7/t8/t9 的改动需通过 `npm run lint` 验证"，或在 t17 的 verify chain 加 `npm run lint`。

### m7 — Next.js `output: 'export'` 静态导出对 next/font 的影响未验证（**新发现**）

- 证据：`src/web/next.config.ts:3` 配置 `output: "export"`（静态导出到 out/）。
- 疑问：静态导出模式下，next/font 的字体文件是否仍然输出到 `.next/static/media/`？还是只到 `out/_next/static/media/`？
- 当前 verify-build-fonts.mjs 只扫描 `.next/static/media/`。若静态导出把字体文件跳过 `.next/` 直接产出到 `out/`，脚本会失败。
- **置信度**：MEDIUM（Next.js 官方文档未明确说明静态导出的字体产物路径）
- **修复**：verify-build-fonts.mjs 应同时扫描两个路径（`.next/static/media/` 和 `out/_next/static/media/`），任一有命中即通过。或改为 `npm run build` 后 copy 阶段断言。

---

## 缺失项

1. **Tailwind v4 的 `@theme inline --font-sans: var(--font-sans)` 自引用安全性** —— 无官方文档引用，只靠 `next build` smoke
2. **Next.js 16 静态导出场景下字体产物路径** —— 未验证
3. **`.font-heading` CSS 类的处理策略** —— 规划完全遗漏
4. **CI 环境的 rg/git 版本要求** —— 未声明
5. **`layout.tsx` 改动是否触发 eslint/type check 失败** —— 未在 verify chain 中覆盖
6. **offline build 场景（next/font/google 无法连接 Google）的兜底** —— 只在 research §8 提及，不在 AC 验收内；可接受但应显式在 tech-design 记录 "本任务不处理 offline build，若目标 CI 无外网访问则先不启动执行"
7. **回滚验证命令的完整性** —— 回滚策略说 `rm -rf .next node_modules && npm ci && npm run build`，但未验证 .cage 状态需同步回滚（或不需要，文档未说）

## 歧义风险

- `"--font-sans 首位字体为 Inter"` —— 解读 A：首位字体族名字面量 `Inter`（匹配 `/^Inter\b/`）；解读 B：首位变量指向 Inter（`var(--font-inter)`，需要运行时解析）。plan 最终采用 B（`/^var\(--font-inter\)/`），与 interview AC-1 的文案"首位字体为 Inter"有歧义。建议 interview 也写成"首位字体变量为 `var(--font-inter)`"。
- `"包含 PingFang SC"` —— `expect(value).toContain('PingFang SC')` 在"HarmonyOS Sans SC, PingFang SC, Source Han Sans SC" 里成立。但如果 executor 写成 `'PingFang_SC'`（下划线）就漏过。建议断言用正则带 word boundary：`/\bPingFang\s+SC\b/`。
- `"IBM Plex 残留清理"` 的豁免列表 —— 目前明确豁免 `*.html` 和 `*.bak`，但 `src/tinohelm/backtest/tearsheet.py` 有一处 `'IBM Plex Sans'` 字符串字面量用于 HTML 生成模板。这个文件在 `src/` 下但不在 `src/web/`，AC-2.1 的 `rg "IBM Plex" src/web/` 不会扫到。这是**正确但未说明的豁免**，规划应在范围边界里显式提一句"`src/tinohelm/backtest/tearsheet.py` 的 IBM Plex 引用属于后端 Python tearsheet 模板，非本任务范围"。

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| next/font/google 生成的 woff2 文件名包含字体族名 | **FRAGILE** | **已证伪**：实际格式为 `[hash]...{.p}.woff2`，不含族名。C4 |
| `@theme inline --font-sans: var(--font-sans)` 自引用不循环 | REASONABLE | 依赖 Tailwind v4 实现，build smoke 可捕获，M5 |
| `extractBlock(css, 'body')` 精确匹配预期 body 块 | **FRAGILE** | **已证伪**：会先匹配 `html, body {...}`，C1 |
| `xargs test N -le` 在 0 匹配时正确失败 | **FRAGILE** | **已证伪**：xargs 空管道直接 PASS，C3 |
| Next.js 16 静态导出将字体输出到 `.next/static/media/` | REASONABLE | 未验证，m7 |
| `rg` 在目标环境可用且版本 ≥ 13 | REASONABLE | 未声明，M6 |
| Source Serif 4 的加载是可选项（可跳过断言） | **矛盾假设** | t6 强制 import，AC-3.2 允许 skip，不一致，C5 |
| globals.css 中 `.font-sans` 类覆写可以忽略 | **FRAGILE** | 会绕一跳 legacy alias，M1 |
| globals.css 中 `.font-heading` 类可忽略 | FRAGILE | 未在任何文档声明，m5 |
| 41 个 .tsx 文件零改动，依赖 token 自动生效 | VERIFIED | 逻辑正确，但数字口径应统一为 44 |
| `var(--font-d/u)` 的 96 处引用零改动 | VERIFIED | 数字应为 97 |
| npm run build 退出码 0 → 字体 woff2 必定生成 | REASONABLE | 需 m7 验证 |
| 国内用户访问 next/font/google 自托管后无阻塞 | VERIFIED | 自托管由 Next 构建时处理 |
| Inter cv11/ss01/ss03 通过 body CSS 生效 | VERIFIED | Inter 官方文档确认这些风格集通过 font-feature-settings 启用 |
| next/font 无 `features` / `fontFeatureSettings` API | VERIFIED | 已通过 `next/dist/compiled/@next/font/dist/google/index.d.ts` 的 Inter 类型确认 |
| QDS skill 「Two fonts, one discipline」被遵守 | VERIFIED | 除 Source Serif 4（明确备用）外无第三种字体 |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| next/font/google 在构建机无法访问 Google | 部分（research §8） | 非本任务范围，但有明确 fallback；acceptable |
| woff2 文件名不含 `inter` 等族名（实际发生） | **No** | C4 — 脚本必然失败 |
| tokens.test.ts `extractBlock('body')` 匹配错误 | **No** | C1 — 测试必然失败 |
| executor 遗漏某处 IBM Plex 字面量 | Yes | AC-2.1 grep 会捕获 |
| executor 替换 IBM Plex 成错字符串（如 `var(--font-d)`） | **部分** | t10/t11 的 xargs 陷阱（C3）让某些场景静默 PASS |
| globals.css 循环依赖（`var(--font-sans)` 链条断开） | Yes | t9 next build 会失败 |
| Tailwind v4 `@theme inline` 自引用报错 | Yes | t9 next build 会失败 |
| Source Serif 4 产物未生成（preload:false 导致按需加载） | **No** | AC-3.2 「否则跳过」让此场景静默 PASS，C5 |
| executor 把 `className="font-heading"` 的消费方误删 | No | 规划未提 font-heading，且无断言 |
| executor 误把 legacy alias `--font-d/u` 当"应删除"清理掉 | **No** | 96 处 `var(--font-d/u)` 引用无保护断言；虽然 CSS parse 会报错但不在测试覆盖内 |
| Node 18 / Node 20 的 `vitest ^3.0` 兼容性 | Yes | research §7 说 "Node 18+"；OK |
| CI 环境无 `rg` | **No** | M6 |

## 多视角笔记

### Executor 视角

- **t6 改动不够具体**：`3-tech-design.md` §模块 1 给出完整代码，但没说"在 layout.tsx 的第几行插入"。executor 若不读 interview + tech-design 可能选错位置（`metadata` 定义前 vs `import` 后）。建议 t6 描述里加"插入位置：第 1-2 行之间，即现有 import 列表之后、metadata 定义之前"。
- **t7 的精确替换范围**：替换 globals.css 第 14-16 行，但新增代码是 6 行（3 个新 token + 2 个 legacy alias + 1 行注释）。行号漂移后，t8 的「第 206-213 行 body 块」行号**不再准确**。建议 tech-design 用"block identifier"而非"行号"引用位置。
- **t9 的 @theme inline 起始行 217**：t7/t8 改动可能让行号变化；若 executor 严格按行号操作会定位错误。同样问题。
- **t10 的精确替换**：executor 看到 `analytics/page.tsx:337` 的实际代码是 `wrapperStyle={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}`，但规划要求只换 `"IBM Plex Mono"` → `"var(--font-mono)"`，保留其他属性 —— 这是明确的。OK。
- **整体**：规划对 executor 友好度较高，但**行号引用在连续改动中有漂移风险**，应改为"section anchor"或"regex-based insert"。

### Stakeholder 视角

- 用户原始诉求是「使用 QDS 定义的设计系统的相关字体标准化整个前端项目」。解读为"完整 QDS 对齐"合理。但**真实问题**是「现在页面上显示的字体不对/难看/与设计稿不符」吗？规划未明确这点。如果用户关心的是"设计稿 vs 实际页面的视觉对齐"，纯静态 token 断言+build smoke **无法证明** 实际渲染与 QDS 设计稿视觉一致。
- 这不是对规划的批评 —— interview 明确非目标里"不引入视觉回归基础设施"，用户接受了这个 tradeoff。但 stakeholder 应被告知：**"token 正确 ≠ 渲染正确"**。如果未来发现渲染不对，不是本任务失职。
- 成功标准：11 条 token 断言通过 + 2 处 grep 0 命中 + build 成功 + woff2 产物存在。这些是**代理指标**，不是目标指标本身。**可接受**，但建议在 requirements.md 背景章节加一句 "本任务的成功定义是代理指标全通过，用户负责在后续 QA 阶段视觉验收（不在本任务范围）"。

### Skeptic 视角

- **"新 token 权威，legacy alias 反指"决策**：最强的反对论点是"96 处 `var(--font-d/u)` 引用多走一跳 var，CSS 解析深度 +1"。plan 虽然选择了这个方向，但未反驳"为什么不反过来：legacy 权威，新 token alias"。虽然 research §5 提到 "与 QDS 文档一致" 作为理由，但 QDS 文档本身是参考实现，项目可以有**合理的偏离**（向 legacy 靠拢减少改动面）。现有论证**偏薄**。
- **"vitest + 静态 CSS 解析"决策**：最强的反对论点是"postcss AST 更稳健"。plan 用 ad-hoc 正则（architect 和我都发现了其脆弱性）。research §7 未反驳 postcss。我认为 postcss 才是正解 —— 零额外依赖（已在依赖链），更稳健。
- **"不加载 Noto SC"决策**：最强的反对论点是"Windows 用户 + 无中文字体的 CI 截图回归会挂"。plan 的理由（目标用户 macOS + 鸿蒙）**不可验证** —— 无用户画像数据支撑。但这是次要决策，非阻塞。
- **"next/font/google 自托管 vs @fontsource"决策**：research §1 比较了方案 B vs C，选择 B 因"Next.js 集成度高"。但**@fontsource 的优势被低估**：offline build 直接可用、锁 npm 版本、不依赖构建时网络。对于内部 CI 来说 @fontsource 其实更稳。plan 未充分论证。
- **总体**：决策基本合理，但论证面薄。Skeptic 不会因此拒绝，但期待 research 章节对被拒方案给出更有力的反驳。

## 上轮修改验证

不适用（第 1 轮）。

## 修改要求（REVISE）

### BLOCKER（必须修，阻塞 APPROVE）

1. **C1 — 修复 `extractBlock(css, 'body')` 正则 bug**
   - 在 `3-tech-design.md §7.1` 替换 extractBlock 实现，使用 postcss AST 解析（推荐）或加入 selector 开始符/word boundary 的更严格正则
   - 在 `tokens.test.ts` 的 body 断言前加 sanity check：`expect(body).toContain('@apply')`，确认取到的是正确的 body 块而非 `html, body { height: 100%; }`
   - 同步更新 AC-1 相关断言的 extractBlock 调用
   - 期望结果：`npm run test:fonts` 在 executor 正确写入 font-feature-settings 后能 PASS

2. **C2 — 填充 task.json subtasks 的 depends_on 字段**
   - 按 architect C2 给出的填充表精确写入
   - **同步将 t5 的 depends_on 从 `["t3","t4","t9"]` 更正为 `["t3","t4","t8","t9"]`**
   - 同步更新 4-tasks.md t5 条目
   - 期望结果：task.json 的 subtasks 字段与 4-tasks.md 完全一致

3. **C3 — 修复 t10/t11 的 xargs 陷阱**
   - 替换 `rg -c ... | xargs test N -le` 为显式 bash 变量 + test 比较：
     ```bash
     count=$(rg -c 'fontFamily:\s*"var\(--font-mono\)"' <file> 2>/dev/null || echo 0)
     test "${count:-0}" -ge <N>
     ```
   - 适用于 4-tasks.md t10、t11 两处
   - 期望结果：0 匹配时验收正确失败，而非静默通过

4. **C4 — 重写 `verify-build-fonts.mjs` 避开字体文件名依赖**
   - 改为**解析 `.next/static/css/*.css`** 中 `@font-face` 的 `font-family: 'Inter' | 'JetBrains Mono' | 'Source Serif 4'` 字面量作为强证据
   - 次级：woff2 文件总数 `>= 2`（Inter + JBM 必须至少各一个，加上 Source Serif 4 更多）
   - **或**：调用 Next.js 输出的 `.next/next-font-manifest.json`（若存在）做结构化断言
   - 移除旧的 `/inter.*\.woff2$/i` 类正则
   - 期望结果：`node scripts/verify-build-fonts.mjs` 在正确构建后退出码 0

5. **C5 — 删除 AC-3.2 的「否则跳过」分支，强制断言 Source Serif 4 存在**
   - 既然 t6 强制 import Source Serif 4，产物中必有对应 @font-face（至少 1 个 woff2）
   - 修改 `1-requirements.md:148` 去掉"若启用"和"否则跳过"
   - 修改 `3-tech-design.md §7.4` optional 列表转为 required 列表
   - 期望结果：AC-3.2 变为硬性断言，100% 自动化不可跳过

### MAJOR（必须修，但不阻塞 APPROVE 如果 planner 说明了原因）

6. **M1 — globals.css:185-195 `.font-sans`/`.font-mono`/`.font-heading` 类覆写处理**（= architect C1）
   - 推荐：**删除**这三个类，依赖 Tailwind utility
   - 同步新增 tokens.test.ts 断言：`expect(css).not.toMatch(/\.font-(sans|mono|heading)\s*\{\s*font-family:\s*var\(--font-[du]\)/)`
   - 期望结果：消费方 `className="font-sans"` 直接命中 Tailwind 生成的 utility，不再绕 legacy alias

7. **M2 — t5 depends_on 加 t8**（= architect M2）

8. **M3 — chartTheme.ts 纳入「legacy alias 保护域」显式豁免**（= architect M1）

9. **M4 — check-grep-fonts.sh 用 `git rev-parse` 锁定仓库根**（= architect M5）

10. **M5 — tech-design 补 Tailwind v4 `@theme inline` 自引用安全性证据 + t9 smoke 证据闭环**（= architect M4）

11. **M6 — check-grep-fonts.sh 开头加 `command -v rg` 前置**（新增，C6 等价）

12. **m5 — `.font-heading` 处理策略显式化**（新增，介于 MAJOR/MINOR）
    - 规划应明确声明：`.font-heading` 类**保留**，作为 legacy 保护域的一部分
    - 或：删除 `.font-heading` 类，要求 consumer 改用 Tailwind `font-sans`（不推荐，有 `.tsx` 消费方）
    - 或：将 `.font-heading` 语义化为 `var(--font-sans)`（而非 `var(--font-u)`）

### MINOR（可选改）

13. 统一数字口径：97 / 44（architect m1）
14. tokens.test.ts 加字段注释检测（architect 权衡分析）
15. t5 验收加 `--reporter=verbose`（architect m3）
16. AC-2.1 与 check-grep-fonts.sh 的 glob 集合统一（architect m4）
17. 添加 `src/tinohelm/backtest/tearsheet.py` 在范围边界的"明确排除"里
18. `PingFang SC` 断言用 `\bPingFang\s+SC\b` 正则（防下划线变体）
19. verify-build-fonts.mjs 兼容 `.next/static/media/` 和 `out/_next/static/media/` 两个路径（静态导出适配，m7）
20. t17 verify chain 加 `npm run lint`（m6）

## 判决理由

**REVISE**（不是 REJECT，因为修复代价可控）。

规划的**核心架构决策（token 分层、legacy alias 反指、next/font/google 自托管、静态断言）**全部正确。但**可执行性层面**有多个致命缺陷：

1. **测试 fixture 会必然失败**（C1 `extractBlock` bug） —— 这是 executor 无法独立解决的规划级错误
2. **验收脚本会必然失败**（C4 woff2 文件名正则） —— 同上
3. **AC 命令有静默通过的漏洞**（C3 xargs 陷阱） —— 让规划给执行阶段制造假阳性
4. **DAG 与文档不一致**（C2 depends_on 全空） —— 让执行阶段拍平并行
5. **AC 内部矛盾**（C5 强制加载 vs 允许跳过） —— 违反用户全局 RULE 的精神

每一个都不致命（都有明确修复路径），但累加让整个 verify chain 失去可信度。**architect 已经识别了 5 个 CRITICAL/MAJOR 里的 4 个**，我**独立识别了 3 个 architect 未发现的问题**（C1 `extractBlock` body 匹配错误、C3 xargs 陷阱、m5 `.font-heading` 未处理）。

**未升级为 ADVERSARIAL 模式** —— 因为发现的问题都是具体的、可修复的、非系统性的；不是规划思路错误，是细节执行错误。规划师在下一轮修复后大概率能 APPROVE。

**现实检查**：C1、C3、C4 如果不修，执行阶段必然陷入"代码改对但验收失败"的调试黑洞，浪费 executor 资源。这比用户看到"代码改错"更糟糕（后者至少能被修）。因此 REVISE（而非对规划者更轻的 MINOR 意见）。

## Open Questions（未评分）

1. Source Serif 4 是否真的需要加载？QDS 明确"备用，不主动用"。项目中目前没有任何长文档页面。加载后 preload:false 仍会占 ~100 KB 产物。**建议**：planner 应在 revise 时确认"是否当前没有任何页面需要 serif"，若是则**完全不加载**（`Source_Serif_4` 不 import），删除相关 token 与 AC，简化验收。
2. chartTheme.ts 的 4 处 `var(--font-d)` 未来是否应该迁移到 `var(--font-mono)`？这是代码风格/技术债务问题，不影响本任务。但应在 tech-design 里明确记录"技术债已知"。
3. `globals.css` 的 `.font-sans`/`.font-mono`/`.font-heading` 类覆写若被删除，是否会影响非 Tailwind 上下文（比如 email 模板、第三方嵌入）？如果有，需要保留；如果没有（explorer 未找到这类场景），安全删除。需要 planner 给出明确答复。
4. 回滚策略说 `git revert <commit-range>`，但本任务通过 Cage P-E-V 执行，是**原子提交**还是**多提交**？如果是后者，revert range 的起止点应在 tech-design 中明示。
5. offline build 场景是否需要在本任务内处理？research §8 说"不在本任务验收内"，但若用户的 CI 无外网，任务会在执行阶段直接失败。建议 planner 确认用户 CI 是否有外网访问。

---

ReviewPass: critic
VERDICT: REVISE
