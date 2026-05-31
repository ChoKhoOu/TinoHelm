# TinoHelm

NautilusTrader(NT)的薄编排外壳：每策略一个 Docker pod，Redis Streams 跨 pod 通信，单个 Discord bot 推事件 + slash 命令控制，Make 控制面。

## 三铁律（任何改动都遵守）

1. **调研先行** —— 动手前先查 NT 是否已有该能力，钉到源码 file:line。
2. **严禁重复造轮子** —— NT 已有的（账户/撮合/订单/风险/msgbus/cache/portfolio/adapter/persistence）一律直接用。
3. **只写四块胶水** —— config 装配 / Discord notifier / 控制 topic 桥接 / Make-Compose，其余全委托 NT。

**NT 版本不锁。** 用户频繁升级 NT，一切以「当前 venv 实际安装的 NT」为真相之源（`.venv/bin/python -c "import nautilus_trader; print(nautilus_trader.__version__)"`），绝不硬编码版本号。NT 是 Cython + Rust 混合包：grep 查不到 ≠ 不存在（核心类型在 `.pyx`/`.pxd`，且 NT 在被 gitignore 的 `.venv/`，裸 ripgrep 静默零命中）——用 codegraph 或 `rg --no-ignore -g '*.pyx' -g '*.pxd'`。

## 하네스: TinoHelm 工作流

**目标:** 用专家团队统筹 TinoHelm 的功能开发、NT 升级对齐、运维诊断，全程守住三铁律。

**触发:** 给 TinoHelm 加能力/venue/命令/actor、改 config 装配、NT 升级后对齐、线上/sandbox pod 排查时，使用 `tinohelm-harness` skill。后续作业（重跑、部分修改、NT 升级、线上排查）同样走它。简单问答可直接回答，无需组队。

**变更历史:**
| 日期 | 变更内容 | 对象 | 事由 |
|------|----------|------|------|
| 2026-05-31 | 初始构成（Agent Team 模式：5 agent + 6 skill） | 全体 | - |
