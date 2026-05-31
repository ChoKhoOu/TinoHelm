---
name: thin-shell-design
description: "为 TinoHelm 设计胶水层：基于 NT 调研结论，裁决每个能力『直接用 NT vs 写薄壳 vs 等上游解禁』，给出最小胶水的接口/数据流/落点模块。守住 TinoHelm 只做四块胶水（config 装配 / Discord notifier / 控制 topic 桥接 / Make-Compose）的边界，绝不重复实现 NT 已有能力。触发：设计方案、加能力前的架构、'放哪一层'、'怎么接进来'、NT 升级后的结构对齐。"
---

# Thin Shell Design — TinoHelm 薄壳设计法

TinoHelm 的全部价值是四块胶水，此外一律用 NT。本 skill 是把"这个能力该用 NT 还是写薄壳、写的话长什么样"裁决清楚的方法。

## TinoHelm 的边界（背下来）

TinoHelm **只**写这四块，其余全部委托 NT：

| TinoHelm 写 | 对应模块 |
|---|---|
| ① TOML → NT config 装配 | `tinohelm/config.py` |
| ② Discord notifier 业务逻辑 | `tinohelm/notifier/` |
| ③ `commands.tinohelm.*` 控制 topic 的进程内桥接 actor | `tinohelm/bridge_actor.py`、`reporting_actor.py` |
| ④ Make/Compose 编排 + CLI | `Makefile`、`compose.yaml`、`tinohelm/cli.py` |

NT 负责（**绝不自研**）：账户、撮合、订单、风险引擎、msgbus、cache、portfolio、venue adapter、回放、持久化、lifecycle。

## 边界裁决三态

对每个子能力，基于 nt-scout 的调研报告裁决：

- **直接用 NT** —— NT 已有等价能力。最优解。复用其 config 类型/方法/topic，TinoHelm 零代码或仅做装配。
- **写薄壳** —— 仅当：NT 确认没有 **且** 这正好是"NT↔Discord/Make 胶水点"。每个"写薄壳"决定必须在设计文档写明"为什么 NT 不能直接满足"。
- **等上游解禁** —— NT 上游正在迁移、暂不可用（典型：Rust LiveNode 跨进程 msgbus 还 hard-bail）。记为已知约束，保持抽象层不强耦合，等 NT 解禁再切。

**自检三问**（裁决"写薄壳"前必答）：
1. nt-scout 是否确认 NT（Python 端 + Rust 端都查过）真的没有？
2. 这是不是 NT 配置就能解决（零代码）？很多诉求其实是 MessageBusConfig/CacheConfig 的字段。
3. 要写的代码是否落在四块胶水之内？落在之外 = 大概率在重复造轮子。

## 薄壳设计原则

- **复用 NT 的 config 类型，不建平行体系。** TinoHelm 的 config.py 只做 TOML→`MessageBusConfig`/`CacheConfig`/`TradingNodeConfig`/`ImportableStrategyConfig`/`ImportableActorConfig` 的反序列化装配。新增配置项优先映射到 NT 已有字段。
- **跨进程沿用既有桥接通道，不发明第二套。** TinoHelm 的跨进程控制只有一条路：`CLI/Discord → XADD tinohelm:control:{id} → pod 内 BridgeActor 订阅 → 进程内 Trader.{stop,start,market_exit}_strategy`。新控制命令加 action，不另起炉灶。事件流向只有一条：`NT 自动发布 events.*/data.Signal* → Redis Streams → notifier XREAD → Discord`。
- **schema-tolerant 设计应对 NT 升级。** 用户频繁升级 NT，event schema/topic/字段名可能漂移。对 NT 来的数据用宽容解析 + 优雅降级（已有范例：notifier `parse_payload` 试 msgpack→JSON→hex；positions 报表"列名不识别就降级为计数行"；announce 流 `nt_version`/`tino_protocol_version` 握手 + `detect_protocol_drift` 告警）。**绝不在设计/代码里写死 NT 版本号判断。**
- **抽业务逻辑为 pure-ish 函数。** 与 NT runtime 解耦的纯函数（`build_positions_report_payload`、`route_channel`、`build_daily_summary`）便于 glue-builder 用 spy 做单测，不必起真实 TradingNode。设计时就把"可测的纯函数"和"NT runtime 接线"分开。

## 输出

写到 `_workspace/{phase}_shell-architect_design.md`，必含：

```
## 边界裁决表
| 子能力 | 裁决（用NT/写薄壳/等上游） | 复用的 NT 类型·方法·topic | 理由（写薄壳须含"为何NT不够"） |

## 胶水规格（仅"写薄壳"项）
- 落点模块 / 接口签名 / 输入契约 / 输出契约 / 订阅 topic / 发布 topic / 复用的 NT 基类

## 前向兼容
- 哪些接缝对 NT 版本敏感，如何 schema-tolerant

## 给 glue-builder 的 TDD 起点
- 先写哪个行为测试（断言什么外部行为）
```

裁决表是 boundary-reviewer 复查"有没有越界自研"的依据，必须逐条可核对。

## NT 升级对齐场景

NT 升级后，拿 nt-scout 的"漂移清单"重新裁决：哪些原"直接用 NT"的接缝因升级变了签名/字段 → 是 schema-tolerant 已覆盖（无需改），还是需要调整装配/解析。原"等上游解禁"项是否已解禁（可切 Rust pod）。产出修订版裁决表，标出本次变更项。
