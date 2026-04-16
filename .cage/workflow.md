<Project_Handbook>

<Overview>
Cage 是一个 P-E-V（Plan-Execute-Verify）工作流框架，通过结构化的多阶段流程和自动 spec 注入，确保代码符合项目规范。

本文件是**框架知识层** — 解释 Cage 是什么、怎么运作。
行为脚本在 `<instructions>` 中，本文件不规定"收到什么消息该做什么"。
</Overview>

<Core_Principles>
## 核心原则

| 原则 | 说明 |
|------|------|
| **先读后写** | 理解上下文再动手。先读 spec，再写代码 |
| **遵循规范** | `.cage/spec/` 中的指南是项目标准，不是建议 |
| **增量开发** | 一次完成一个任务，不并行无关工作 |
| **及时记录** | 完成后立即记录 session，不拖延 |
| **规范注入** | spec 通过 Hook 自动注入 agent 上下文，不依赖记忆 |
| **绝不自动提交** | 所有 git commit 必须通过 `/cage:commit` 手动触发 |
</Core_Principles>

<Directory_Structure>
## 目录结构

```
.cage/
├── config.yaml          # 验证配置（required_passes）、spec_root
├── workflow.md          # 本文件（框架知识层）
├── instructions.md      # Agent 行为脚本
│
├── spec/                # 项目规范体系
│   ├── index.md         # 规范总览 + 组织方式说明
│   ├── guides/          # 通用指南
│   │   ├── index.md     # 指南索引
│   │   └── conventions.md  # 编码规范（模板，需用户填写）
│   └── {domain}/        # 领域规范（按业务模块组织）
│       ├── index.md
│       └── *.md
│
├── knowledge/           # 项目知识沉淀
│   ├── README.md        # 知识库说明
│   ├── decisions/       # 架构决策记录（ADR）
│   │   └── 000-template.md
│   ├── lessons/         # 经验教训
│   │   └── 000-template.md
│   ├── patterns/        # 已建立的模式
│   │   └── 000-template.md
│   └── pitfalls/        # 已知陷阱
│       └── 000-template.md
│
├── tasks/               # 任务目录
│   ├── {YYYY-MM-DD-name}/    # 每个任务一个目录
│   │   ├── task.json    # 元数据 + current_phase + subtasks DAG
│   │   ├── 1-requirements.md  # 需求文档
│   │   ├── 2-research.md     # 调研文档（可选）
│   │   ├── 3-tech-design.md  # 技术设计
│   │   ├── 4-tasks.md        # 任务清单
│   │   ├── execute.jsonl     # scout 输出：executor 的 spec 路径
│   │   ├── verify.jsonl      # scout 输出：verifier 的 spec 路径
│   │   └── plan-review/      # 深度审查文件（按轮次）
│   └── archive/
│
├── state/               # 运行时状态
│   ├── session.json     # autopilot、kickback_round、max_kickback_rounds
│   └── verify-*.json    # 验证结果
│
└── workspace/           # Session 工作空间
    └── {YYYY-MM-DD}-{hex}/  # Session 文件夹（如 2026-04-10-a3b1c2）
        ├── session.md       # Session 记录
        ├── changes.diff     # 变更差异
        └── trace.jsonl      # 追踪日志
```
</Directory_Structure>

<Spec_System>
## Spec 体系

Spec 是项目编码规范的结构化存储，按两个维度组织：

**guides/**：跨领域的通用指南（编码规范、命名约定、错误处理模式）。`guides/index.md` 是入口，列出所有指南文件。

**{domain}/**：按业务模块的领域规范（如 `auth/`、`api/`、`database/`）。每个子目录有 `index.md` 作为入口。

**发现机制**：Scout agent 在 Plan 阶段完成后扫描 `.cage/spec/` 和 `.cage/knowledge/`，将与任务相关的 spec 路径写入 jsonl 文件。之后 context-inject hook 在派遣 subagent 时自动注入。

**编写建议**：
- 用具体代码示例，不只写抽象原则
- 说明 WHY，不只是 WHAT
- 包含 DO 和 DON'T 对比
- 每个文件 < 500 行
</Spec_System>

<Task_System>
## Task 系统

每个任务是一个目录，核心是 `task.json`。

**生命周期**：`create → active → archive`

| 状态 | 说明 |
|------|------|
| `active` | 正在处理的任务。一次只有一个 |
| `completed` | 所有子任务完成，验证通过 |
| `archived` | 移入 `tasks/archive/` |

**目录命名**：`{YYYY-MM-DD-name}`（如 `2026-04-08-auth-refactor`）

**task.json 核心字段**：
```json
{
  "id": "04-08-auth-refactor",
  "title": "重构认证模块",
  "status": "active",
  "current_phase": "execute",
  "subtasks": [
    {"id": "1", "title": "提取 JWT 工具函数", "status": "done", "deps": []},
    {"id": "2", "title": "重写 middleware", "status": "pending", "deps": ["1"]}
  ],
  "parallel_groups": [["1"], ["2"]]
}
```

`parallel_groups` 是预计算的 DAG 执行顺序：每组内的子任务可并行，组间串行。
</Task_System>

<Hook_Injection>
## Hook / 注入机制

### session-start（SessionStart hook）

触发时机：startup、compact、clear。
注入 5 个标签到主 agent 上下文：

| 标签 | 内容来源 |
|------|----------|
| `<current-state>` | session、phase、autopilot 状态、活跃任务 |
| `<workflow>` | `.cage/workflow.md`（本文件） |
| `<guidelines>` | `.cage/spec/guides/*.md`（全量加载） |
| `<instructions>` | `.cage/instructions.md`（行为脚本） |
| `<task-status>` | 活跃任务进度 + 可用命令 |

### context-inject（PreToolUse hook）

触发时机：主 agent 派遣 subagent（Agent/Task 工具调用）。
按活跃任务的 `current_phase` 加载对应 jsonl：

```
execute 阶段 → execute.jsonl → <cage-specs phase='execute'>
verify  阶段 → verify.jsonl  → <cage-specs phase='verify'>
```

**jsonl 格式**：每行 `{"path": "/abs/path/to/spec.md", "reason": "为什么相关"}`

注入流程：读取 jsonl → 逐个读取 spec 文件 → 拼接为 `<cage-specs>` 标签 → 追加到 subagent prompt 末尾。

idle/plan 阶段不注入（通用 guides 已在 session-start 注入）。

### 其他 Hook

| Hook | 类型 | 作用 |
|------|------|------|
| command-router | UserPromptSubmit | 识别 `/cage:*` 命令，注入阶段上下文 |
| verify-loop | SubagentStop | 解析 VerifyPass 标记，强制 E↔V 循环 |
| review-loop | SubagentStop | 解析 ReviewPass 标记，强制双 APPROVE |
| persistent-mode | Stop | autopilot 运行时阻止 agent 停止 |
</Hook_Injection>

<Commit_Convention>
## 提交规范

格式：`type(scope): description`

| Type | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(auth): 添加 JWT 刷新` |
| `fix` | Bug 修复 | `fix(router): 修复嵌套路由` |
| `refactor` | 重构 | `refactor(db): 提取连接池` |
| `test` | 测试 | `test(api): 补充集成测试` |
| `docs` | 文档 | `docs(readme): 更新安装步骤` |
| `chore` | 构建/依赖 | `chore(deps): 升级 typescript` |
| `style` | 格式 | `style(lint): 统一引号` |
| `perf` | 性能 | `perf(query): 添加索引` |

scope 取受影响的模块名。description 以动词开头，不超 50 字符。

**原子提交规则**：3+ 文件 → 2+ 提交，5+ 文件 → 3+ 提交，10+ 文件 → 5+ 提交。
</Commit_Convention>

<Commands_Reference>
## 命令参考

### 用户 Skill 命令 `[USER]`

| 命令 | 别名 | 说明 |
|------|------|------|
| `/cage:plan` | `/cage:p` | 苏格拉底式规划 → PRD + 技术设计 + 任务 DAG |
| `/cage:exec` | `/cage:e` | 按 DAG 执行子任务（最多 5 并发 executor） |
| `/cage:verify` | `/cage:v` | 4 阶段验证流水线 + E↔V 回退 |
| `/cage:debug` | `/cage:d` | 因果链分析 → 用户确认 → 修复 |
| `/cage:record` | `/cage:r` | 派遣 recorder → 写入 session.md/changes.diff 到 session 文件夹 |
| `/cage:commit` | `/cage:c` | 原子提交拆分（Conventional Commits） |
| `/cage:autopilot` | `/cage:ap` | 全自主 P-E-V 循环 |

### AI 脚本 `[AI]`

| 脚本 | 参数 | 用途 |
|------|------|------|
| `init-project.js` | `[--project-root <dir>]` | 创建 `.cage/` 目录结构 + 复制模板 |

所有脚本路径前缀：`node "${CLAUDE_PLUGIN_ROOT}/dist/scripts/"`
</Commands_Reference>

<Best_Practices>
## 最佳实践

### 应该做

- 开发前读 `.cage/spec/` 中的相关指南
- 遵循 `<cage-specs>` 标签注入的规范
- 复用现有代码，写新代码前先搜索（防屎山协议）
- 每轮 P-E-V 结束后 record session
- 在 `.cage/knowledge/` 中沉淀经验和决策
- 验证时展示新鲜的测试输出（不是假设）

### 不应该做

- 跳过读 spec 就开始编码
- 同时开发多个无关任务
- 提交有 lint/test 错误的代码
- 在任何模式下自动 git commit
- 修改任务范围外的"顺手"问题
- 在代码中留下 console.log、TODO、HACK、debugger
</Best_Practices>

</Project_Handbook>
