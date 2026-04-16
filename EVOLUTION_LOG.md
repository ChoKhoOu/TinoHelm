# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-15

**主题**: 建立测试基础设施，覆盖核心基础模块
**维度**: 测试补齐
**改动范围**:
- `pyproject.toml` — pytest 配置（testpaths, asyncio_mode, filterwarnings）
- `tests/conftest.py` — 新增共享测试 fixtures（策略文件、配置目录、环境清理）
- `tests/core/test_utils.py` — 26 个测试，覆盖 `sanitize_for_json` 全部路径
- `tests/core/test_config.py` — 29 个测试，覆盖配置加载、YAML 合并、环境变量覆盖
- `tests/strategy/test_module_loader.py` — 32 个测试，覆盖统一模块加载器
- `.github/workflows/test-python.yml` — 新增 Python 测试 CI 工作流

**动机**:
项目此前没有 `conftest.py`、没有 pytest 配置、没有 Python CI 工作流。
74 个源模块中仅 21 个有测试（28% 覆盖率）。
最关键的基础设施模块（`core/config.py`、`core/utils.py`、`strategy/module_loader.py`）
完全没有测试，而这三个模块被回测、沙盒、实盘等所有模式依赖。
一旦这些模块出现回归，所有功能都会受影响，且无 CI 防护网。

**要点**:
1. **pytest 基础设施**: 在 `pyproject.toml` 中添加 `[tool.pytest.ini_options]`，
   配置 testpaths、asyncio_mode=auto、DeprecationWarning 过滤
2. **共享 fixtures**: 创建 `tests/conftest.py`，提供 `strategies_dir`、`actors_dir`、
   `catalog_dir`、`config_dir`、`clean_env`、`minimal_strategy_file` 等可复用 fixtures，
   消除测试间的 mock 重复
3. **`core/utils.py` 测试 (26 cases)**: 覆盖 NaN/Inf→None 转换、正常 float 保留、
   嵌套 dict/list 递归、混合结构（模拟真实回测统计输出）、不可变性验证
4. **`core/config.py` 测试 (29 cases)**: 覆盖 `_deep_merge` 的所有分支（空/嵌套/类型覆盖/不可变）、
   所有 Settings 子模型默认值、YAML 加载（default→user 优先级）、
   环境变量覆盖 YAML、`get_settings()` LRU 缓存、边界情况（空 YAML、未知 key）
5. **`strategy/module_loader.py` 测试 (32 cases)**: 覆盖模块加载（成功/失败/语法错误）、
   边界目录强制执行、sys.path 清理（成功/失败/已存在）、
   NT Strategy/Actor 类发现、目录扫描、OPTIMIZE 范围提取、高层 `load_strategy_module` API
6. **CI 工作流**: 新增 `.github/workflows/test-python.yml`，在 push/PR 触发时
   自动运行 pytest（仅在 src/tests/pyproject.toml 变更时）

**后续建议**:
- 为 `backtest/result/extract.py` 补测试（17+ 处 bare `except Exception` 需要覆盖）
- 为 `data/catalog.py` 和 `data/worker.py` 补测试（数据管道关键路径）
- 为 API routes 建立 TestClient 测试框架
- 修复 `tests/data/test_downloader.py::test_klines_daily_url_structure` 中的预存 bug
  （URL 格式断言与实际下载器输出不匹配）

## 2026-04-16

**主题**: 消除回测结果提取模块中的滚动指标代码重复，统一 numpy 导入
**维度**: 代码质量
**改动范围**:
- `src/tinohelm/backtest/result/statistics.py` — 新增 6 个可复用的滚动指标计算函数
- `src/tinohelm/backtest/result/extract.py` — 消除 5 处重复滚动窗口代码块，统一 12 个冗余 numpy 导入
- `tests/backtest/test_rolling_metrics.py` — 新增 35 个测试覆盖所有滚动指标函数

**动机**:
`extract.py` 是所有回测结果的唯一提取入口（1553 行），但存在两个严重的可维护性问题：
1. **滚动指标代码重复**: Rolling Sharpe / Sortino / Volatility / Beta / CumReturn 五个计算块
   结构几乎完全相同（遍历 equity curve → 对窗口切片计算指标 → 降采样），但各自独立实现，
   合计约 120 行重复代码。添加新滚动指标需要复制粘贴 15+ 行样板代码。
2. **numpy 导入混乱**: 同一文件中 12 个不同 numpy 导入别名（`_np`, `_np2`, `_np3`,
   `_np_rs`, `_np_rso`, `_np_rv`, `_np_rb`, `_np_bm`, `_np_bmdr`, `np`），每个 try 块
   各自导入，严重影响可读性。而 pandas 已在顶层导入，numpy 必定可用，条件导入完全多余。

**要点**:
1. **`_compute_rolling_series()` 通用引擎**: 接受 `daily_rets` 数组、时间戳列表、窗口配置
   和一个 `metric_fn(rets, start, end)` 回调。内置均匀降采样（默认 500 点上限）。
   所有滚动计算归一为"选窗口 + 插指标函数"的声明式调用。
2. **5 个指标函数**: `_rolling_sharpe_fn`, `_rolling_sortino_fn`, `_rolling_volatility_fn`,
   `_rolling_cumret_fn`, `_make_rolling_beta_fn`（工厂函数，通过闭包绑定 benchmark 数据）。
   每个函数都可独立测试，签名统一 `(daily_rets, start, end) -> float | None`。
3. **numpy 导入统一**: 顶层 `import numpy as np`，删除所有 12 个内联导入和别名。
4. **extract.py 净减 53 行**: 5 个 15-20 行的重复块各缩为 4 行 helper 调用。
5. **35 个新测试**: 覆盖通用引擎（基本输出结构、窗口填充前 None、降采样、多窗口、空输入、
   单点、自定义指标）和每个指标函数（正/负值、边界条件、窗口切片、零方差处理）。
   使用 `importlib.util.spec_from_file_location` 直接加载 statistics.py，避免触发
   依赖 nautilus_trader 的 `__init__.py`，确保 CI 环境可运行。

**后续建议**:
- 将 `extract.py` 中的其他可复用计算（equity curve 构建、per-instrument 分析、extended
  statistics）也提取为独立函数，进一步分解 1500 行的单一函数
- 为 `_safe_float`, `_format_duration_ns`, `_parse_realized_pnl` 等已有 statistics 函数
  补充单元测试
- 考虑将 `_ANN_FACTOR = 365` 提升为可配置参数，支持非加密货币市场（252 交易日）

## 2026-04-16 (2)

**主题**: 消除回测引擎 run()/prepare_engine() 之间的 ~120 行重复代码，删除 275 行废弃方法
**维度**: 架构重构
**改动范围**:
- `src/tinohelm/backtest/runner.py` — 提取 `_setup_engine()` 共享方法，简化 `run()` 和 `prepare_engine()`，删除 `_enhance_tearsheet_DELETED` 废弃方法
- `tests/backtest/test_runner_helpers.py` — 新增 48 个测试覆盖 `_interval_to_minutes`、`_parse_fee`、`_build_latency_model`、`_build_fill_model` 及构造函数边界条件

**动机**:
`backtest/runner.py` 是整个平台最核心的模块 — 所有回测执行和参数优化都经过它。
该文件存在三个严重的可维护性问题：

1. **引擎初始化逻辑重复**: `run()` (288 行) 和 `prepare_engine()` (135 行) 各自独立实现了相同的
   引擎配置流程：构建策略包 → 同步 symbols/intervals → warmup 扩展 → 创建 BacktestEngine →
   构建 fill/latency 模型 → 添加 venue → 加载 instruments → 解析 bar 数据 → 处理缺失 instruments →
   添加数据并排序 → 注入策略参数默认值。每次修改必须同步更新两处，遗漏风险极高。

2. **Bug: 优化路径缺少 fee model**: `prepare_engine()` 没有调用 `_build_fee_model()`，
   导致 Optuna 优化 trial 不应用手续费模型。通过共享 `_setup_engine()` 自动修复。

3. **275 行废弃代码**: `_enhance_tearsheet_DELETED` 方法在名称中标记为 DELETED，
   实际功能已迁移到 `backtest/tearsheet.py:enhance_tearsheet()`，但方法体仍占 275 行空间。

**要点**:
1. **`_setup_engine()` 共享方法**: 提取完整的引擎配置流程，返回 `(engine, strategy_bundle, starting_balance)` 元组，同时在 `self._nt_symbols`、`self._all_bar_type_strs`、`self._loaded_bar_type_strs`、
   `self._total_bar_count`、`self._benchmark_daily_closes` 上存储元数据供后续使用。
2. **`run()` 简化**: 现在调用 `_setup_engine()` 后仅处理策略/Actor 创建、统计注册、
   funding 数据、进度上报、引擎执行和结果提取 — 去掉了所有重复的引擎配置代码。
3. **`prepare_engine()` 简化**: 从 135 行缩减为 17 行 — 仅调用 `_setup_engine()` 并返回。
4. **优化路径 bug 修复**: fee model 现在通过共享路径自动应用；benchmark daily closes 也传播到
   优化 trial 的结果提取中，使 B&H 基准对比在优化模式下也可用。
5. **废弃代码删除**: 净减 275 行，文件从 1619 行降至 1244 行（减少 23%）。
6. **48 个新测试**: 覆盖 `_interval_to_minutes`（15 cases: 分钟/小时/天/秒/无效输入/大小写）、
   `_parse_fee`（8 cases: 百分比/纯数字/空白/零值/大小值）、`_build_latency_model`（6 cases:
   默认/自定义/禁用/高级纳秒参数）、`_build_fill_model`（7 cases: 所有模型类型/回退）、
   构造函数多品种初始化（6 cases）、StrategyBundle 构建（6 cases）。
   使用 `pytest.mark.skipif(not _HAS_NT)` 条件跳过，确保 CI 环境可运行。

**后续建议**:
- 为 `run()` 中剩余的 run-only 逻辑（策略/Actor 注册、funding、progress reporter）
  考虑进一步提取，使 `run()` 更加声明式
- 将 `extract.py` 的 1500 行单函数拆解为 8-10 个独立函数（最大的可维护性债务）
- 为 `_resolve_bars()` 和 `_download_bars()` 补充测试（数据解析关键路径）
