---
name: nt-capability-probe
description: "调研 NautilusTrader 是否已提供某能力，把结论钉到已安装 NT 的源码 file:line。任何往 TinoHelm 加能力/改动前，或 NT 升级后做对齐，都先用本 skill 查 NT 现成的 trait/类/服务/adapter/config/topic。以『当前 venv 里实际安装的 NT』为唯一真相，绝不硬编码版本号、绝不凭记忆作答。触发：'NT 有没有 X'、'NT 怎么做 X'、调研先行、加 venue/adapter/actor/命令前、NT 升级兼容性核对。"
---

# NT Capability Probe — NautilusTrader 能力调研法

调研先行是 TinoHelm 的灵魂铁律。本 skill 是把"NT 到底有没有这能力"查到板上钉钉的标准流程。结论决定下游写不写代码——假阳性会导致重复造轮子，假阴性会让该复用的没复用，所以每个结论都要有可复现的 file:line 证据。

## 为什么以"已安装的 NT"为真相之源

用户会**频繁升级 NT**，版本持续漂移（曾经 1.226，现在 1.227，以后还会变）。GitHub 上某个 tag、你的训练记忆，都可能与当前实际跑的代码不符。唯一可信的是 venv 里那份源码——它就是策略 pod 实际 import 的东西。因此：

- **绝不硬编码版本号作为前提断言。** 不写"NT v1.226 有 X"，写"当前安装的 NT（实测 vX.Y.Z）有 X @ file:line"。版本号只是本次实测留痕。
- **绝不凭记忆答"NT 应该有/没有"。** 记忆会过期，源码不会。

## 标准流程

### 1. 锁定真相之源（每次调研第一步）

```bash
# 当前实际版本（仅留痕，不作为前提）
python -c "import nautilus_trader; print(nautilus_trader.__version__)"
# 源码根目录 —— 后续所有 file:line 都相对它
python -c "import nautilus_trader, os; print(os.path.dirname(nautilus_trader.__file__))"
```

脚本已备好：`scripts/nt_probe.py`（打印版本、源码根、adapters 列表、可选 grep 某 symbol）。

### 2. 用 LSP/codegraph 定位 symbol（不要先 grep）

按用户全局规则，结构性查询一律优先 codegraph / LSP：

| 问题 | 工具 |
|---|---|
| X 在哪定义 / 签名 / docstring | `codegraph_search` → `codegraph_node` |
| 谁调用 X / X 调用谁 | `codegraph_callers` / `codegraph_callees` |
| 改 X 会影响什么 | `codegraph_impact` |
| 某能力相关的一片 symbol | `codegraph_context` 再 `codegraph_explore` |

只有查**字符串字面量**（topic 名如 `events.order`、日志文案、config 键名）才用 grep 直接搜源码根。

### NT 是 Cython 混合包 —— grep 查不到 ≠ 不存在

NT 不是纯 Python 包。实测文件构成：`.py`（配置/纯 Python 逻辑）+ `.pyx`/`.pxd`（Cython 实现，msgbus / actor / component / 大量 model 类型都在这里）+ `.pyi`（存根）+ `.so`（编译产物）。**核心类型多数在 `.pyx`/`.pxd`**。两个必踩的坑：

1. **ripgrep 默认跳过 `.pyx`/`.pxd`，且默认遵守 `.gitignore`。** NT 装在 `.venv/`（被 gitignore），所以裸 `rg <symbol>` 在 site-packages 里**静默返回零命中**。必须 `rg --no-ignore -g '*.py' -g '*.pyx' -g '*.pxd' -g '*.pyi'`。
2. **`scripts/nt_probe.py <symbol>` 已内建以上修正**——查不到先用它，而不是凭"grep 没结果"断言 NT 没有。

查不到时的正确升级路径：① `nt_probe.py <symbol>` 全扩展名搜 → ② `codegraph_search`（AST 索引，跨 .pyx 也能定位）→ ③ 试近似名（版本升级可能改名）。三者都空，才考虑"可能真没有"。

### NT 有 Python 端 + Rust 端两套实现 —— 都要查

NT 正全面迁移 Rust，当前是 **PyO3/Cython Python 端 + Rust crate** 并存。调研一个能力时区分两者：

| 端 | 位置 | 调研用途 |
|---|---|---|
| **Python 端** | `site-packages/nautilus_trader/`（.py/.pyx） | **策略 pod 实际跑的路径** —— TinoHelm 复用的就是它。默认先查这里 |
| **Rust 端** | `crates/`（GitHub 或本地缓存） | 判断「上游迁移到哪了 / 跨进程能力是否解禁」。例：`crates/live/src/config.rs` 的 `validate_runtime_support` 是否还 hard-bail msgbus，决定能否切 Rust pod |

实例：`market_exit_strategy` 在 Python 端 `trading/trader.py` 和 `trading/controller.py` 都有；Rust 端在 `crates/system/`。判"TinoHelm 该不该自己写跨进程 pause"，要同时知道：Python 端有这方法（进程内可调）、但 Rust LiveNode 的跨进程 msgbus 还没解禁（所以走 Python pod + Redis 桥接）。**只查一端会得出错误结论。**

### 3. 判定三态

每个能力诉求给出确定结论：

- **已有** —— NT 提供等价 symbol。给出 file:line + 签名 + 怎么用。结论："直接用，不要自研。"
- **部分有** —— NT 有基础但缺接缝。明确"有什么、缺什么、缺的那块是不是 TinoHelm 该补的胶水点"。
- **没有** —— 确认 NT 无等价能力。再确认"这是不是恰好 NT↔Discord/Make 的胶水缺口"（若是，TinoHelm 该写；若不是，可能是 NT 配置就能解决，继续查）。

### 4. 提取"接缝信息"

TinoHelm 胶水层靠这些契约对接 NT，调研时一并记录：
- NT **自动发布的 topic** 及命名规则（msgbus switchboard：`events.order.{strategy_id}` / `events.position.{strategy_id}` / `events.account.{account_id}` / `data.Signal{Name}` / `commands.system.*`）。
- 相关 **config 字段**（MessageBusConfig / CacheConfig / TradingNodeConfig / ImportableStrategyConfig 等）。
- **lifecycle 钩子**（Strategy/Actor 的 on_start/on_stop/on_resume...）。
- **跨进程可达性** —— 进程内可用 ≠ 跨 Redis 可用（这是 TinoHelm 桥接存在的根本原因）。

## Rust 端源码哪里找

Python 端直接在 venv 里读。Rust crate（`crates/...`）不在 venv，按需取：① GitHub `nautechsystems/nautilus_trader` 对应 tag；② 项目 memory `reference-nt-key-source-files` 记录的本地缓存路径（文件名把 `/` 换成 `_`，如 `crates_live_src_config.rs`）。注意 memory 是某次实测留痕、可能已过期——**Rust 端结论同样以"当前安装版本对应的 tag"为准**，不要拿旧 tag 的行号当事实。

## NT 升级对齐场景

NT 升级后用本 skill 做回归调研：
1. 重新执行步骤 1，记录新版本号。
2. 对 TinoHelm 依赖的每个 NT symbol/topic/config 字段，重新定位——**是否改名、挪窝、改签名、改 event schema**。
3. 产出"漂移清单"：哪些接缝变了、影响 TinoHelm 哪个胶水模块、是否需 schema-tolerant 处理或代码调整。交 shell-architect 裁决、boundary-reviewer 回归。

## 输出

写到 `_workspace/{phase}_nt-scout_{topic}.md`，必含：实测环境（版本+源码根）、三态结论、file:line 证据、接缝信息、给胶水层的建议。结论里**不出现版本号作为前提**，只在"实测环境"留痕。

## 反例（不要这样）

- ❌ "NT 1.226 没有 Pause 命令" —— 锁了版本 + 凭记忆。
- ✅ "当前安装 NT（实测 1.227.0）的 ControllerCommand 枚举（crates/system/src/messages/controller.rs，Python 端 system/controller.py）无 Pause/Resume，只有 Start/Stop/ExitMarket Strategy。结论：跨进程 pause 需 TinoHelm 桥接 stop_strategy。"
