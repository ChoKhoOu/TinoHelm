# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-17

**主题**: 从 `strategy/loader.py` 抽出纯函数到 `strategy/loader_helpers.py`，并补齐 NT-free 单元测试
**维度**: 架构重构 + 测试补齐
**改动范围**:
- 新增 `src/tinohelm/strategy/loader_helpers.py`（309 行）
- 重构 `src/tinohelm/strategy/loader.py`（429 → 351 行）
- 新增 `tests/strategy/test_loader_helpers.py`（498 行，63 个用例）

**动机**:
`strategy/loader.py` 是 BacktestRunner、Sandbox node、Live node **三个**主流程共享的入口，但长期没有自己的单元测试——只能通过 `tests/portfolio/test_loader.py` 里的几个端到端用例间接覆盖。文件 429 行，符号/interval 解析、模块路径解析、actor class_path 边界校验、strategy 参数组装这些纯逻辑全部内联在 NT 依赖的函数里，既难独立测试，也让三处调用方分别维护可能走样的等价代码的风险始终存在。沿用项目既有套路（`runner_helpers` / `optimizer_helpers` / `pipeline_helpers`），把纯函数抽出来集中测试。

**要点**:
- `loader_helpers.py` 集中以下纯函数，全部 NT-free：
  - `parse_interval` / `normalize_symbol` / `make_bar_type_str` — 原 loader 里的 interval/symbol 工具
  - `nt_symbol_to_jesse` — 原私有 `_nt_symbol_to_jesse`，重命名为公开 API
  - `resolve_module_file` — 原 `_resolve_module_file`，搜索顺序可测试（把默认 `/app` 回退暴露为可覆盖的 `extra_search` 参数）
  - `resolve_actor_class_path` — **新抽出**，原先嵌在 `_load_single_actor` 里的 `./mod:Class` / 绝对路径校验逻辑；现在单独成函数，并在过程中补了缺失参数（`home_tino_dir`）与显式错误分支（冒号缺失、类名为空）
  - `build_strategy_params` — **新抽出**，原先散落在 `create_strategies` 里的 ~30 行参数字典组装；现在一个纯函数完整承担「symbols/interval/resolved_bar_types/instrument_id/bar_type/order_id_tag/manage_stop」的注入顺序与字段过滤
  - `check_symbol_profiles` — 原 `_warn_unrecognized_symbols` 里的校验逻辑；剥离了日志副作用，返回 `(symbol, jesse_symbol, reason)` 元组供调用方按需日志，便于测试直接断言
- `loader.py` 只保留 NT-dependent 编排（类型转换 `InstrumentId.from_str` / `BarType.from_str`、实际模块加载 `load_module_from_file`、NT actor base class 检查）。全部符号/参数/路径逻辑转由 helpers 提供。
- 保留所有向后兼容别名（`_normalize_symbol` / `_make_bar_type_str` / `_nt_symbol_to_jesse` / `_INTERVAL_MAP` / `_UNIT_MAP` / `_resolve_module_file`），确保 `runner.py`、`scaffold.py`、`api/routes/data.py`、`tests/portfolio/test_loader.py` 等 7 个现有调用方零修改。
- `resolve_actor_class_path` 在抽取时顺手修了一个小一致性问题：原代码在绝对路径分支里对 `module_file` 做了 `.resolve()` 但后续 `exists()` 检查仍然使用未解析的原始路径；现在统一使用解析后的路径，避免 `a/b/../c.py` 这类场景下两次判断走不同分支。

**验证**:
- `.venv/bin/python -m pytest tests/` —— **914 passed in 8.28s**（基线 851 + 新增 63）
- 新增 63 个用例按被测函数分组：`parse_interval` 8、`normalize_symbol` 3、`make_bar_type_str` 3、`nt_symbol_to_jesse` 7、`resolve_module_file` 5、`resolve_actor_class_path` 8、`build_strategy_params` 15、`check_symbol_profiles` 5、向后兼容别名 3
- `tests/portfolio/test_loader.py`（16 个既有用例）全部通过，证明向后兼容别名工作正常
- 人工 smoke test `parse_interval('7h') == '7-HOUR'`、`normalize_symbol('BTCUSDT-PERP') == 'BTCUSDT-PERP.BINANCE'` 结果一致

