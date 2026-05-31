---
name: boundary-audit
description: "审查 TinoHelm 的跨边界契约一致性 + 防重复造轮子 + 防 NT 版本硬编码。专查分布式接缝 bug：CLI/Discord 命令信封 ↔ BridgeActor 解析、NT event topic ↔ notifier 订阅、信封字段 ↔ 消费端读取、announce 握手两端。方法是『两端同时读、交叉比对』而非『确认存在』。每个模块完成即增量审查。触发：审查/review/QA/验证/检查、边界、契约一致性、NT 升级回归。"
---

# Boundary Audit — TinoHelm 边界审查法

TinoHelm 是多 pod + Redis Streams + Discord 的分布式胶水，bug 几乎都在**接缝**：两端各自"正确"，但契约对不上。本 skill 的核心方法是**两端同时打开比对**，而不是"确认某东西存在"。

## 三类审查（按优先级）

### 1. 重复造轮子审查（最高优先级 · TinoHelm 灵魂）

拿 shell-architect 的**边界裁决表** + nt-scout 的**调研报告**，逐条核对实现有没有越界自研了 NT 已有的能力（account/撮合/order/risk/msgbus/cache/portfolio/adapter/persistence）。任何裁决为"直接用 NT"的子能力，实现里都不该出现平行实现。发现越界 → 这是必修项，且说明源头（裁决错了还是 glue-builder 跑偏）。

### 2. 跨边界契约一致性 —— "两端同时读"

bug 只读一端发现不了。每条接缝**必须同时打开生产端和消费端比对形状**：

| 接缝 | 左（生产者） | 右（消费者） | 查什么 |
|---|---|---|---|
| 控制命令 | `cli.py` `_publish_command` / notifier `_publish` 的 `{action,ts,reason?}` + topic | `bridge_actor.py` `_extract_action` + `ACTIONS` | 信封形状（dict/bytes/str 都覆盖？）、action 在 ACTIONS 里？ |
| 控制流键名 | `control_stream_key()` → `tinohelm:control:{id}` | strategy_runner 注入 `external_streams` 的键 + NT XREAD | 两端键名逐字符一致？ |
| NT event → Discord | NT 自动发布 topic（nt-scout 确认：`events.order.*`/`events.position.*`/`events.account.*`/`data.Signal*`/`events.system.*`） | notifier `NotifierActor.SUBSCRIBE_PATTERNS` + handlers 字段名 | pattern 通配对得上？字段名 NT 实际发的对得上？ |
| 持仓快照 | `build_positions_report_payload` → `{strategy_id,row_count,csv}` | handlers `_fmt_positions_report` 读的键 + CSV 列名 | 键名一致？列名漂移有降级？ |
| announce 握手 | strategy_runner `publish_announce` 写的字段 | notifier `read_new_announces`/`detect_protocol_drift` 读的字段 | 字段名一致？缺字段向后兼容？ |
| 频道路由 | announce 的 mode → registry | `route_channel`/`validate_command_channel`/`strategies_for_channel` | mode 取值与分支匹配？logging 只读？ |

**TinoHelm 特有的接缝 bug 模式**：
- **topic 命名两端差一个 `.`**：一端 `commands.tinohelm.{id}.{action}`，另一端 pattern 写成 `commands.tinohelm.{id}*`——通配错位。
- **信封形状漂移**：NT msgbus 跨 Redis 传 **bytes**、进程内传 **Python 对象/dict**——消费端两条路径都要处理（见 `_extract_action`/`parse_payload` 的多分支）。只处理一种 = 另一条路径静默丢。
- **同步/异步错配**：CLI 是 fire-and-forget（无事件循环，发完就退）；Discord `/positions` 是 request/response 带 120s 超时。别假设 CLI 能等回复。
- **字段名/大小写**：NT event 字段名是 NT 定的，升级可能改——见第 3 类。

### 3. NT 版本硬编码审查

用户**频繁升级 NT**，任何版本硬编码都是定时炸弹：
- `grep -rn` 搜源码里的 NT 版本号字面量（`1.227`、`1.226` 等）、`if.*__version__.*==`、依赖某版本特有内部行为的注释。
- 该动态读版本的地方（announce 握手）是否用 `nautilus_trader.__version__` 而非写死。
- 对 NT 来的数据，是否 schema-tolerant（宽容解析 + 降级）而非假设固定 schema。
- 测试里有没有断言依赖某 NT 版本特有的字段/行为（升级即红）。

## 检索注意

- **优先 codegraph/LSP** 找某 topic/字段/symbol 的**全部**引用点，确保两端都查到不漏（`codegraph_search` 找定义，`codegraph_callers` 找所有消费点）。
- 查 NT 那一端实际发什么（字段名/topic），NT 是 **Cython 包**——用 `nt-capability-probe` 的方式（`nt_probe.py` 或 `rg --no-ignore -g '*.pyx'`），别因 grep 零命中误判。

## 增量审查，不要攒到最后

每个模块/接缝一完成就立即审它 + 它对接的**两端**。缺陷累积会传播到下游，修复成本指数上升。这比"全做完审一次"重要得多。

## 输出与回流

写到 `_workspace/{phase}_boundary-reviewer_report.md`，结论**三分**（绝不混成"看起来没问题"）：

```
## 通过 / 失败 / 未验证
## 重复造轮子审查：[裁决N] 是否越界？证据 file:line
## 跨边界契约：[接缝] 左 file:line 形状 vs 右 file:line 期望 → 一致/不一致
## NT 版本硬编码：grep 结果
## 必修项：file:line + 具体修法
```

**发现即回流**：SendMessage 给对应 agent 提具体修复（file:line + 修法），不积压到报告末尾。边界 bug 要**同时通知接缝两端**的 agent。无法验证的标"未验证"+ 所需条件，绝不标"通过"。与 glue-builder 的修复循环上限 2~3 轮，不收敛就升级 leader。
