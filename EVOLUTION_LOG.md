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
