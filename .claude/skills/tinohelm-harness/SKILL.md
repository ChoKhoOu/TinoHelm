---
name: tinohelm-harness
description: "TinoHelm 工作流编排器（Agent Team 模式）。统筹 nt-scout / shell-architect / glue-builder / boundary-reviewer / ops-medic 五位专家，按场景组队：①功能开发（调研→设计→TDD实现→边界审查 全流程）②NT 版本升级对齐 ③线上/sandbox 运维诊断。守住三铁律：调研先行、严禁重复造 NT 已有轮子、只写四块胶水。触发：给 TinoHelm 加能力/venue/命令/actor、改 config 装配、NT 升级后对齐、线上 pod 排查；以及后续：再做一遍、重跑、更新、修改、补充、只改某部分、基于上次结果改进、'NT 升级了'、'线上 X 不工作'。简单问答可直接回答无需组队。"
---

# TinoHelm Harness — 工作流编排器

统筹五位专家完成 TinoHelm 的开发/升级/运维。**执行模式：Agent Team**——`TeamCreate` 组队，`TaskCreate` 派活，团员 `SendMessage` 实时协作自调度，leader 监控并汇总。

TinoHelm 的天然工作流是一条 pipeline，但每段都受益于团队实时通信（调研结论实时喂设计、审查发现"重复造轮子"立刻回传实现者形成反馈回环），所以用 Agent Team 而非孤立 sub-agent。

## 三铁律（贯穿所有场景，写进每个团员 prompt）

1. **调研先行** —— 动手前先用 nt-scout 彻查 NT 是否已有该能力。
2. **严禁重复造轮子** —— NT 已有的（账户/撮合/订单/风险/msgbus/cache/portfolio/adapter/persistence）一律直接用。
3. **只写四块胶水** —— config 装配 / Discord notifier / 控制 topic 桥接 / Make-Compose，此外全委托 NT。

**贯穿性约束：NT 版本不锁。** 用户频繁升级 NT。一切以"当前 venv 实际安装的 NT"为真相之源，绝不硬编码版本号；对 NT 接缝 schema-tolerant。

## 团队成员

| 团员 | agent_type | 角色 | 配套 skill |
|---|---|---|---|
| nt-scout | nt-scout | NT 能力调研（Python+Rust 双端，Cython 包检索） | nt-capability-probe |
| shell-architect | shell-architect | 薄壳边界裁决 + 胶水规格 | thin-shell-design |
| glue-builder | glue-builder | TDD 实现胶水代码 | tinohelm-tdd |
| boundary-reviewer | boundary-reviewer | 边界契约审查 + 防重复 + 防版本硬编码 | boundary-audit |
| ops-medic | ops-medic | 线上/sandbox 故障诊断 | pod-diagnostics |

所有 `TeamCreate` 成员与 `Agent` 调用均带 `model: "opus"`。

## 场景路由（Phase 1 据用户请求选一条）

| 场景 | 触发 | 组队 | 模式 |
|---|---|---|---|
| **A. 功能开发** | 加能力/venue/命令/actor、改 config | nt-scout + shell-architect + glue-builder + boundary-reviewer（4人） | pipeline，每段实时通信 |
| **B. NT 升级对齐** | "NT 升级了"、升级后兼容核对 | nt-scout + boundary-reviewer（2人，必要时拉 shell-architect/glue-builder） | 调研漂移→回归审查→按需修 |
| **C. 运维诊断** | 线上/sandbox pod 排查 | ops-medic（1人，定位到代码 bug 时拉 glue-builder±nt-scout） | 取证诊断 |

---

## 工作流

### Phase 0：上下文确认（后续作业支持）

1. 查 `_workspace/` 是否存在。
2. 决定执行模式：
   - **不存在** → 初次执行，进 Phase 1。
   - **存在 + 用户要部分修改**（"只改 X"、"上次的 Y 再改改"）→ 部分重跑：只重新组建/唤醒相关团员，把既有产出路径放进其 prompt，让它读旧结果并据反馈增量改。
   - **存在 + 用户给了新输入**（新能力诉求）→ 新执行：把旧 `_workspace/` 移到 `_workspace_prev/`（命名带时间戳，时间戳由 leader 在 shell 里生成，勿臆造），再进 Phase 1。

### Phase 1：准备与场景路由

1. 解析用户请求 → 选场景 A/B/C。
2. 建 `_workspace/`（新执行则先移走旧的）。
3. 把原始诉求存 `_workspace/00_input/request.md`。
4. 探测当前 NT 版本留痕：`.venv/bin/python .claude/skills/nt-capability-probe/scripts/nt_probe.py` 输出存 `_workspace/00_input/nt_env.md`（仅留痕，非前提断言）。

### Phase 2：组队 + 派活

`TeamCreate(team_name: "tinohelm-team", members: [...])`，每个 member 的 prompt 必须包含：① 三铁律 + NT 版本不锁约束；② 它的配套 skill 名（让它 Skill 调用或 Read）；③ 本次诉求；④ `_workspace/` 读写路径约定。

`TaskCreate` 按场景建任务并标 `depends_on`：

**场景 A（功能开发）任务链：**
```
nt-research:{能力}      → nt-scout         （查 NT 有没有，产 _workspace/02_nt-scout_*.md）
design:{能力}           → shell-architect  （依赖 nt-research；产 _workspace/03_shell-architect_design.md）
build:{能力}            → glue-builder     （依赖 design；改 tinohelm/ + tests/，产 _workspace/04_glue-builder_summary.md）
review:{能力}           → boundary-reviewer（增量：模块完成即审，不等全完；产 _workspace/05_boundary-reviewer_report.md）
```
任务有依赖但**审查增量进行**：glue-builder 每完成一个模块就 SendMessage 通知 boundary-reviewer 审该模块 + 其两端，不必等全部 build 完。

**场景 B（NT 升级对齐）：**
```
nt-research:drift   → nt-scout          （对比上次实测版本，列 symbol/topic/字段/签名漂移清单）
review:regression   → boundary-reviewer （拿漂移清单跑 make test，查哪些接缝实际受影响、有无 schema-tolerant 兜住）
```
若审查发现需改代码 → leader 动态拉 shell-architect（重裁决）+ glue-builder（修），TaskCreate 派生修复任务。

**场景 C（运维诊断）：** 单 TaskCreate `diagnose:{症状}` → ops-medic。定位出代码 bug → ops-medic TaskCreate 派 `build:fix` 给 glue-builder（leader 视情况把 glue-builder 加入团队）。

> 团员当 3~5 人时每人 4~6 个子任务为宜；功能开发常是一条 4 任务链。

### Phase 3：执行（团员自调度）

团员从共享任务列表认领、独立执行，**实时通信规则：**
- nt-scout 调研完 → SendMessage 给 shell-architect 喂结论；发现 glue-builder 在写 NT 已有的东西 → 立即 SendMessage 叫停。
- shell-architect 出规格 → SendMessage 给 glue-builder；裁决依赖不明 → SendMessage 要 nt-scout 补查。
- glue-builder 完成模块 → SendMessage 通知 boundary-reviewer 增量审。
- boundary-reviewer 发现缺陷 → SendMessage 给 glue-builder（file:line + 修法）；若缺陷源于裁决/调研错 → **同时**知会 shell-architect 和 nt-scout（边界 bug 通知接缝两端）。

**leader 监控：** 团员 idle 自动通知 leader；某员卡住用 SendMessage 介入或重派；`TaskGet` 看整体进度。生成-검증 循环上限 2~3 轮，不收敛则 leader 决策。

### Phase 4：收敛与汇总

1. `TaskGet` 确认所有任务完成。
2. Read 各团员 `_workspace/` 产出。
3. **质量门**（leader 亲自确认，不可省）：
   - `make test` 绿、`make lint` 绿（功能开发/修复场景）。
   - boundary-reviewer 报告无未决"必修项"，且"重复造轮子审查"全部通过。
   - 无 NT 版本号硬编码。
4. 向用户汇总：改了什么、复用了哪些 NT 能力（证明没重复造轮子）、测试/lint 结果、未决项（若有）。

### Phase 5：清理

1. SendMessage 通知团员收尾，`TeamDelete` 解散团队。
2. 保留 `_workspace/`（审计追溯用，勿删）。
3. 汇报结果，并征询是否需调整 harness（Phase 7 进化）。

---

## 数据流

```
[leader] TeamCreate + TaskCreate
   nt-scout ──SendMessage(结论)──→ shell-architect ──SendMessage(规格)──→ glue-builder
      ▲                                  ▲                                    │
      └────(版本漂移复核)────────────────┴──(裁决/调研纠错)───── boundary-reviewer ◀─┘ (模块完成通知→增量审, 缺陷回传)
   各团员产出 → _workspace/0X_{agent}_*.md → leader Read 汇总 → 质量门 → 用户
```

数据传递组合：任务列表（调度）+ 文件（`_workspace/` 产出）+ 消息（实时协作）。文件命名 `{phase}_{agent}_{artifact}.{ext}`，最终代码改动直接落 `tinohelm/`、`tests/` 等真实路径，`_workspace/` 只放过程产物。

## 错误处理

| 情况 | 策略 |
|---|---|
| nt-scout 结论"部分有/不确定" | shell-architect 不臆测补全，SendMessage 要求补查，宁阻塞不臆造边界 |
| glue-builder make test/lint 红 | 必须修到绿，不得标完成；失败如实上报 leader |
| boundary-reviewer 发现重复造轮子 | 必修项，回传 glue-builder 删自研改用 NT，并查源头（裁决错？） |
| 团员失败/中止 | leader 检测 → SendMessage 确认 → 重启或重派 |
| 生成-검증 循环 >3 轮不收敛 | leader 介入决策，不无限循环 |
| 团员数据冲突 | 出处并记，不删除 |
| 拿不到线上访问（场景C） | ops-medic 标 needs input，列所需访问，不臆测线上 |

## 测试场景

### 正常流（场景 A：给策略 pod 加一个新控制命令）
1. 用户："给 TinoHelm 加一个 /halt 命令，暂停后撤销所有挂单。"
2. Phase 0：无 `_workspace/` → 初次执行。Phase 1：场景 A，建 _workspace，留痕 NT 版本。
3. Phase 2：TeamCreate 4 人；TaskCreate nt-research→design→build→review 链。
4. Phase 3：nt-scout 查"NT 有没有撤单全平的 Trader 方法"→ SendMessage 给 shell-architect；架构师裁决"撤单用 NT 现成方法，只写桥接 action"→ 规格给 glue-builder；实现者 TDD 加 action + CLI/Discord 接线 → 通知 reviewer；审查"CLI 信封 ↔ _extract_action ↔ ACTIONS"三端一致 + 无重复造轮子。
5. Phase 4：make test/lint 绿，审查通过 → 汇总。
6. 预期：`bridge_actor.py`/`cli.py`/notifier + 对应测试落地，复用 NT 撤单能力。

### 错误流（场景 A：审查发现重复造轮子）
1. Phase 3：glue-builder 自己写了一段持仓盈亏计算。
2. boundary-reviewer 对照裁决表+nt-scout 报告，发现 NT Portfolio 已有该能力 → SendMessage 给 glue-builder（"删掉，用 portfolio.unrealized_pnl”）+ 知会 shell-architect（裁决遗漏）。
3. glue-builder 删自研改用 NT，重跑 make test。
4. Phase 4：复审通过；汇总注明"修正一处重复造轮子"。

## 后续作业（Phase 0 已处理判别）
- "再加一个命令" → 新执行（移走旧 _workspace）。
- "上次那个 /halt 的审查问题改一下" → 部分重跑，只唤醒 glue-builder（+reviewer），喂旧产出路径。
- "NT 升级到 X 了，对齐一下" → 场景 B。
- "线上 FOO-001 不出信号" → 场景 C。
