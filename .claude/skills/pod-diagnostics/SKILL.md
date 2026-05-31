---
name: pod-diagnostics
description: "诊断 TinoHelm 线上/sandbox 运行故障：策略 pod 不出信号/不成交、Redis Streams 流量异常、Discord notifier 不推送或推错频道、控制命令(pause/resume/flatten)没生效、announce/版本漂移告警。沿数据流链路逐段取证定位，区分策略逻辑/胶水/基础设施/NT版本漂移。触发：诊断/排查/线上、pod 不工作、Redis 流、Discord 没收到、命令没反应、看日志、为什么没成交。"
---

# Pod Diagnostics — TinoHelm 运维诊断法

TinoHelm 是多 pod + Redis Streams + Discord 的分布式系统，故障跨进程/跨 Redis/跨 Discord。诊断 = 沿数据流链路逐段取证，缩小到具体断点，分清故障层。先观测取证，再下结论。

## 数据流链路（取证地图）

```
策略 pod                            Redis Streams                          notifier pod            Discord
publish_signal       ──XADD──→ trader-{id}:stream:data.Signal*    ──XREAD──→ NotifierActor ──→ sandbox/live/logging
order/fill 事件      ──XADD──→ trader-{id}:stream:events.{order,position,account}.*  ─→ route_channel ─→ 按 mode 路由
CLI/Discord 命令     ──XADD──→ tinohelm:control:{id}             ──XREAD──→ BridgeActor  ──→ Trader.{stop,start,market_exit}_strategy
pod 启动             ──XADD──→ tinohelm:announce                 ──XREAD──→ registry + nt_version 握手
```

故障必在某一段。取证就是定位是哪一段断了。

## 诊断顺序

1. **复述症状**，映射到链路的哪一段（信号？成交？命令？推送？路由？）。
2. **在该段取证**——流量、日志、容器状态（见下方工具）。
3. **缩小到接缝**——某 stream `XLEN=0` 是强信号。
4. **分故障层**：策略逻辑（NT 内）/ TinoHelm 胶水 / 基础设施 / NT 版本漂移。
5. **给最小修复**，并判断是否需 glue-builder 改代码。

## 取证工具（用现成的，别重造）

**TinoHelm/Make 门面：**
- `make status` —— notifier 综合状态  ·  `make logs STRATEGY=foo` —— tail pod 日志  ·  `make ps` —— 容器列表
- `tinohelm ping --strategy-id X` —— 验 BridgeActor 活着（应在 pod 日志见 `BridgeActor: ping ack`）
- `tinohelm status [-s X]` / `tinohelm positions [-s X]`

**直连 Redis 取证：**
```bash
docker exec tinohelm-redis redis-cli KEYS 'trader-*:stream:*'        # 有哪些事件流
docker exec tinohelm-redis redis-cli XLEN trader-{id}:stream:events.position.{id}   # 流量多少
docker exec tinohelm-redis redis-cli XREVRANGE <stream-key> + - COUNT 5             # 最近 5 条实际内容
docker exec tinohelm-redis redis-cli XLEN tinohelm:announce                         # pod announce 过没
docker exec tinohelm-redis redis-cli XREVRANGE tinohelm:announce + - COUNT 10       # 各 pod 的 mode/nt_version
docker exec tinohelm-redis redis-cli KEYS 'tinohelm:control:*'                      # 控制流
```

**容器内省：**
```bash
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tinohelm-strategy-foo | grep TINO_   # 看 TINO_MODE 等
```

> **远端服务器**：必须走 `bastion-ssh` skill —— 直接 ssh dev 会丢 stdout。

## 流量为零 = 强信号（按断点查）

| 现象 | 指向 |
|---|---|
| `data.Signal*` 流空 | 策略没发信号 —— NT 内 / 策略逻辑（pod 在跑吗？on_start 触发了吗？看 pod 日志）|
| `events.*` 流空 | 没成交或 exec client 没接上（venue 鉴权？sandbox factory 注册了吗？）|
| `tinohelm:control:{id}` 有写入但无反应 | BridgeActor 没订阅 / pod 挂了 —— `tinohelm ping` 验活；查 pod 日志 `subscribed to commands.tinohelm.{id}.*` |
| `tinohelm:announce` 空 | pod 没起来或 announce 失败 —— notifier 不知道这 pod，路由/版本握手都会缺 |
| 流都有但 Discord 没推 | notifier 侧：token？channel_id 配对？`discord client task crashed` 日志？频道权限？|

## 高频故障模式

- **频道路由错**（sandbox/live/logging 三频道由 announce 的 mode → registry 决定）：事件进错频道 / 命令被拒，先查 `XREVRANGE tinohelm:announce` 里该 strategy 的 mode 是否正确、三个 `DISCORD_CHANNEL_ID_*` 环境变量是否配对。逻辑在 `route_channel`/`validate_command_channel`/`strategies_for_channel`。
- **NT 版本漂移**（用户频繁升级 NT，**优先怀疑**）：升级后 notifier 不显示某字段 / 出 `tinohelm.protocol_mismatch` 告警 / 解析降级成 hex preview —— 高度可疑是 NT event schema 变了。查 announce 的 `nt_version` 对比 notifier 自身版本（`detect_protocol_drift` 内建此告警），怀疑就拉 nt-scout 复核 NT 新版字段/topic。**绝不假设某固定 NT 版本的行为。**
- **命令没生效**：CLI 是 fire-and-forget，发了不等于到了——`tinohelm ping` 验链路通；信封到了但 action 不在 `ACTIONS` 会被 BridgeActor 丢弃并 warning（查 pod 日志）。
- **同步期待异步**：`/positions` 等 120s 超时；CLI `positions` 不等回复（结果在 Discord 看）。别把"CLI 没回显结果"当故障。

## 输出

写到 `_workspace/{phase}_ops-medic_diagnosis.md`：症状 → 取证（每步：命令 + 实际输出 + 解读）→ 链路定位（断在哪段）→ 根因层（策略/胶水/基础设施/NT漂移）→ 最小修复动作 → 是否需代码修复（转 glue-builder，带 file:line）。

## 边界

- 拿不到访问就标 needs input，列出需要的具体访问（哪个 Redis/哪台机/哪个 Discord 频道 ID），不臆测线上。
- 取证不足别把猜测当根因，给"最可能 + 下一步取证"。
- 改动生产前（重启/平仓）先说明影响，除非已授权或明确是 sandbox。
