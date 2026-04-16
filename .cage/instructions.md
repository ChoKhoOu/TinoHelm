<Agent_Playbook>

<Context>
本文件是**行为脚本层** — 定义收到用户消息后的 if-then 逻辑和步骤编排。
假设你已读过 `<workflow>`（框架知识层），本文件引用其中概念不重复解释。
</Context>

<Initialization>
以下标签已由 session-start hook 预注入，**不要重新读取**：

`<workflow>` `<current-state>` `<guidelines>` `<instructions>` `<task-status>`

**从会话入口开始。**
</Initialization>

<Session_Entry>
## 会话入口 `[AI]`

1. 简要报告上下文（当前任务、phase、进度）
2. 有活跃任务 → 「继续 {任务名}（阶段：{phase}，进度：{done}/{total}）吗？」
3. 无活跃任务 → 「想做什么？」
</Session_Entry>

<Task_Classification>
## 任务分类 `[AI]`

| 类型 | 信号 | 走什么分支 |
|------|------|------------|
| **提问** | 问代码/架构/原理 | → 直接回答 |
| **微调** | 指定了文件+修改，单行级 | → 直接编辑，提醒 `/cage:record` |
| **简单任务** | 目标明确、1-2 文件 | → 确认 `[USER]` → 直接实现 → 建议 `/cage:verify` |
| **复杂任务** | 多文件、目标模糊、架构决策 | → 完整 P-E-V 工作流 |

> **拿不准时走 P-E-V。** 开销小但 spec 注入收益大。
</Task_Classification>

<PEV_Workflow>
## P-E-V 工作流（复杂任务）

### Phase 1：Plan `[AI]` + `[USER]`

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 1.1 | `[AI]` | 苏格拉底式 5 维提问，逐轮降低模糊度 |
| 1.2 | `[AI]` | 派遣 **planner**（opus）生成 PRD + 技术设计 + 任务清单 |
| 1.3 | `[AI]` | 创建任务目录 `.cage/tasks/{YYYY-MM-DD-name}/`，写入 task.json |
| 1.4 | `[USER]` | **审批门** — AskUserQuestion 三选一：开始执行 / 深度审查 / 提意见 |
| 1.5a | `[AI]` | 选「开始执行」→ 派遣 **scout**（sonnet）写入 execute.jsonl / verify.jsonl → Phase 2 |
| 1.5b | `[AI]` | 选「深度审查」→ 派遣 **architect**（opus）+ **critic**（opus），review-loop hook 强制双 APPROVE → 回到 1.4 |
| 1.5c | `[AI]` | 选「提意见」→ 接收反馈，派遣 **planner** 修改 → 回到 1.4 |

### Phase 2：Execute `[AI]`

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 2.1 | `[AI]` | 读取 task.json 的 `parallel_groups`（见 `<workflow>` Task 系统） |
| 2.2 | `[AI]` | 逐组派遣 **executor**（sonnet/opus/haiku 按复杂度），每组最多 5 并发 |
| | | context-inject hook 自动注入 `<cage-specs phase='execute'>` |
| 2.3 | `[AI]` | 每个 executor 完成后更新 subtask 状态（done/failed） |
| 2.4 | `[AI]` | 失败 → 重试 2 次；仍失败 → AskUserQuestion `[USER]`（跳过/修复/终止） |
| 2.5 | `[AI]` | 全部完成 → 设置 `current_phase: "verify"` → Phase 3 |

### Phase 3：Verify `[AI]`

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 3.1 | `[AI]` | **Quality Gates** — 运行构建/类型检查/lint/测试（快速失败） |
| 3.2 | `[AI]` | **多视角验证** — 并行派遣 **verifier**（sonnet）+ **code-reviewer**（opus） |
| 3.3 | `[AI]` | **反熵检查** — 派遣 **code-simplifier**（sonnet） |
| 3.4 | `[AI]` | **共识判定** — 全 PASS → Phase 4；有 FAIL → E↔V 回退 |

### E↔V 回退 `[AI]`

```
FAIL → kickback_round++ → 设置 phase="execute" → 回到 Phase 2（仅重做失败项）→ 再 Verify
最多 max_kickback_rounds 轮（默认 10）→ 超限 AskUserQuestion [USER]
```

**测试持续失败**：派遣 **debugger**（opus，因果链分析）→ 展示根因 → `[USER]` 确认 → **executor** 修复 → 回到 Verify

### Phase 4：收尾

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 4.1 | `[AI]` | 派遣 **recorder**（haiku）收集上下文 → 写入 session 文件夹（session.md, changes.diff） |
| 4.2 | `[USER]` | 运行 `/cage:commit` 原子提交（**绝不自动 commit**） |
</PEV_Workflow>

<Continue_Task>
## 继续已有任务 `[AI]`

当 `<task-status>` 显示有活跃任务：

| current_phase | 动作 |
|---------------|------|
| `plan` | 问用户 `[USER]`：继续规划 or 开始执行？ |
| `execute` | 检查未完成 subtask，从当前 group 继续 `[AI]` |
| `verify` | 运行验证（可能在 E↔V 回退中） `[AI]` |
</Continue_Task>

<Agent_Dispatch>
## Agent 派遣模板 `[AI]`

### planner（规划）
```
Agent(subagent_type="planner", model="opus", prompt="
  用户想法：{用户描述}
  项目类型：{绿地/棕地}
  代码库概况：{explorer 扫描结果}
  请生成：1-requirements.md、3-tech-design.md、4-tasks.md
")
```

### scout（spec 发现）
```
Agent(subagent_type="scout", model="sonnet", prompt="
  任务目录：{taskDir}
  PRD：{prd 摘要}
  请扫描 .cage/spec/ 和 .cage/knowledge/，写入 execute.jsonl、verify.jsonl
")
```

### executor（实现）
```
Agent(subagent_type="executor", model="{按复杂度: haiku/sonnet/opus}", prompt="
  子任务：{subtask title}
  验收标准：{从 PRD 提取}
  请实现变更并验证构建通过。
")
```
> context-inject hook 自动追加 `<cage-specs phase='execute'>`，无需手动注入。

### verifier / code-reviewer / code-simplifier（验证）
```
Agent(subagent_type="{verifier|code-reviewer|code-simplifier}", model="{opus|opus|sonnet}", prompt="
  任务目录：{taskDir}
  变更范围：{modified files}
  请按验收标准验证。
")
```
> context-inject hook 自动追加 `<cage-specs phase='verify'>`。

### debugger（调试）
```
Agent(subagent_type="debugger", model="opus", prompt="
  症状：{错误描述}
  任务目录：{taskDir}
  请执行因果链调试，输出调试报告。
")
```
> debugger agent 通过 context-inject hook 的 Layer 1（base_specs）获取通用规范。

### recorder（记录）
```
Agent(subagent_type="recorder", model="haiku", prompt="
  活跃任务：{taskDir}
  session 标题：{title}
  请收集上下文并生成 Markdown 日志条目。
")
```
</Agent_Dispatch>

<Core_Principle>
## 核心原则

**规范通过 Hook 注入，不依赖记忆。**

你的职责是正确推进阶段（设置 `current_phase`），context-inject hook 自动注入 spec。
框架知识在 `<workflow>` 中，行为指令在本文件中。两者互补，不重复。
</Core_Principle>

</Agent_Playbook>
