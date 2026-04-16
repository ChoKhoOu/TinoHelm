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
