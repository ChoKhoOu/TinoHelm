---
name: tinohelm-tdd
description: "用 TDD 实现 TinoHelm 胶水代码（config 装配 / bridge actor / reporting actor / notifier / CLI / Make-Compose）。行为测试先行，用 spy/test-double 绝不起真实 TradingNode，代码风格严格对齐现有模块，绝不重复实现 NT 已有能力，绝不硬编码 NT 版本号。触发：实现/写代码、加命令或 actor 或 venue 接线、改 config 装配、修 bug、加测试。"
---

# TinoHelm TDD — 胶水层实现法

TinoHelm 是分布式胶水，bug 多在边界。TDD（行为测试先行）+ 贴合现有风格，是保证胶水正确且可演进的方式。本 skill 是落地节奏与约定。

## 核心循环（红-绿-重构）

1. **红** —— 先写行为测试，断言**外部可观测行为**，不是内部实现。范式：`tests/test_bridge_actor.py`——"pause 信封到达 → trader.stop_strategy 被调一次且只一次、参数是配置的 StrategyId"。运行确认失败。
2. **绿** —— 写最小实现让测试过。
3. **重构** —— 抽 pure-ish 函数、对齐风格、补 docstring。保持绿。
4. `make test` + `make lint` 全绿才算完成。

## 测试约定：绝不起真实 TradingNode

单测是行为关注点，不是集成关注点。起一个 TradingNode 要 msgbus/cache/clock 全套注册，慢且脆。改用 **spy/test-double**：

- **绕过 NT 注册**：`Actor.__new__(Cls)` 造实例，手动塞 `_on_command` 真正读的属性，再用子类 `@property` 把 `trader`/`log`/`msgbus` 指向 spy（见 `tests/test_bridge_actor.py` 的 `_PatchedBridgeActor`）。
- **spy 记录调用**：`TraderSpy` 把每次方法调用 append 到 `calls`，断言 `calls == [("stop_strategy", StrategyId("FOO-001"))]`。
- **纯函数直接测**：业务逻辑抽成与 runtime 解耦的纯函数（`build_positions_report_payload`、`route_channel`、`validate_command_channel`、`build_daily_summary`、`detect_protocol_drift`），喂假数据断言返回值——这是首选，最干净。
- **Redis 用 fakeredis**：`fakeredis>=2.23` 已在 dev 依赖，测 CLI/notifier 的 Redis 交互（见 `tests/test_cli_redis.py`、`test_announce.py`）。
- **pytest-asyncio**：`asyncio_mode = "auto"`，async 测试直接 `async def test_...`。

设计上若一段逻辑"非起真实 NT 不能测"，那是信号：把可测的纯函数从 runtime 接线里拆出来（找 shell-architect 调规格），而不是降低测试标准。

## 代码风格（照抄现有约定）

- `from __future__ import annotations` 打头；类型注解齐全（mypy，`ignore_missing_imports=true`、非 strict 但要过）。
- **模块顶 docstring** 说明"这块胶水补 NT 的什么缺口"；关键设计决策**引 NT 源码佐证**——Python 端 `module.py:NNN`，Rust 端 `crates/.../x.rs:NNN`。这是本仓库的签名风格，照做。
- **业务逻辑抽 pure-ish 函数**，与 NT runtime 解耦（便于上面说的纯函数测试）。
- 中文 Discord 文案用**全角标点**（，。（）），ruff 已 ignore RUF001/002/003，别改成半角。
- 行宽 100；ruff `select = ["E","F","I","B","UP","SIM","RUF"]`，`ignore = ["E501","RUF001","RUF002","RUF003"]`。
- 防御性 `except` 配 `# pragma: no cover` 注释意图（见 reporting_actor 的 shutdown 竞态处理）。

## 两条铁律落到代码

- **绝不重复造 NT 的轮子。** 实现中若发现某逻辑 NT 已有，停手、知会 shell-architect/nt-scout，删掉自研那段改用 NT。复用 NT 类型按其公开 API 用。
- **绝不硬编码 NT 版本号。** 要版本就 `nautilus_trader.__version__` 动态读（已用于 announce 握手）。对可能漂移的接缝（event 字段名、topic、CSV 列名）写宽容解析 + 降级，不写 `if nt_version == ...`。

## 改动前后

- 改一个被多处调用的函数前，用 `codegraph_impact`/`codegraph_callers` 看爆炸半径，别盲改。
- 改完跑 `make test`（`uv run pytest`）和 `make lint`（`ruff check` + `ruff format --check` + `mypy tinohelm`）。失败必须修到绿，不得标完成。
- 写实现摘要到 `_workspace/{phase}_glue-builder_summary.md`：改了哪些文件、复用/新增哪些 NT 类型、新增测试清单、`make test`/`make lint` 结果。

## 典型场景：加一个控制命令（如新 action）

1. 红：在 `test_bridge_actor.py` 加测试——新 action 信封 → 期望的 Trader 调用 / msgbus 发布。
2. 绿：在 `bridge_actor.py` 的 `ACTIONS` 加 action，`_on_command` 加分支（参照 `report` action 如何复用 `reporting_actor` 的纯函数）。
3. 接线：CLI（`cli.py` 加 `@app.command`）+ Discord（notifier `_build_discord_client` 加 `@tree.command`）两端都发同一信封到 `tinohelm:control:{id}`。
4. 边界自查：CLI/Discord 发的信封形状，`_extract_action` 一定解得出（这正是 boundary-reviewer 要查的接缝）。
