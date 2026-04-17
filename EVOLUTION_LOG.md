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


## 2026-04-17

**主题**: optimizer 提取 Phase 2 —— 补齐上一轮(2026-04-16 (6))未消除的 7 处内联模式 + 锁定 16-key result schema 契约
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/optimizer_helpers.py` — 452 → 707 行(+255 行,新增 7 个 helper + 3 个常量),包含 `PatienceTracker`、`serialize_trial`、`select_best_params`、`build_walk_forward_fold_record`、`auto_patience`、`build_progress_payload`、`build_full_result`、`PROGRESS_STATUS_RUNNING`、`PROGRESS_STATUS_COMPLETED`、`FULL_RESULT_BASE_KEYS`
- `src/tinohelm/backtest/optimizer.py` — 812 → 792 行(-20 行净);其中 `run()` 末尾 60 行内联 `full_result` dict 构造收敛为 17 行声明式调用,两条 Redis 发布路径从两个独立字面量 dict 合一到 `build_progress_payload(...)`,`_PatienceCallback` 从 30 行内联状态机缩为 14 行 thin shim,`_cleanup_shared_engine()` 提取消除 3 处 try/except 重复
- `tests/backtest/test_optimizer_helpers.py` — 682 → 1220 行(+538 行,从 81 个测试增至 140 个,+59 测试覆盖 7 个新 helper)
- `tests/backtest/test_optimizer_shim.py` — 新建 161 行 / 37 测试,覆盖 `_PatienceCallback` shim 行为 + 11 个 backward-compat 别名同一性 + 21 个公共 re-export 可访问性

**动机**:

上一轮(2026-04-16 (6))做了 optimizer 第一阶段提取:把 12 个最容易抽出的纯 helper(`split_dates`、`walk_forward_windows`、`extract_fitness`、`auto_n_trials/sampler/workers`、`slim_result`、`compute_dsr/sensitivity/stability`、`filter_completed_trials`、`_norm_ppf/_cdf`)放进 `optimizer_helpers.py`,加了 81 个 NT-free 测试。这次是 **Phase 2 —— 把上一轮明显应该一起做但没做的 7 处内联模式收尾**:

1. **两条 Redis 发布路径仍是两个独立字面量 dict**(运行中 vs 完成)。`objective()` 内 8 行字面量构造 + `run()` 末尾 8 行字面量构造,字段一致但顺序不同(同样的 "duplicate + drift" 反模式,跟上一轮 runner.py 提取 `build_progress_payload` 之前一模一样)。
2. **60 行 `full_result` dict 内联** + 6 处 `full_result["xxx"] = ...` 散点赋值,前端 OptimizationDetail 16 个顶层 key 完全没有 schema 测试兜底。任何无意识的字段重命名(比如 `"parameter_stability_score"` 改成 `"stability"`)都不会触发任何告警。
3. **`_PatienceCallback` 30 行内联状态机**(`self._best`、`self._no_improve_count`、`threading.Lock` 全部锁在 Optuna `__call__(study, trial)` 签名里),纯逻辑无法独立测试。
4. **`_suggest_params_from_trial(trial)` 静态方法 + `{k: v for k, v in trial.params.items() if k in self.param_ranges}` 字典推导**在 `objective()`(线 456-460)和 `run()` 收尾(线 521-524)各出现一次,2 处可漂移。
5. **walk-forward 折记录 6-key 字面量构造**内联在 `for fold_idx, ... in enumerate(wf_windows)` 循环里,`{"fold": idx+1, "train_start": iso, ...}` 8 行字面量,无法独立测试 1-based 索引 + ISO 格式化的契约。
6. **`auto_patience` 启发式** `if self.patience <= 0 and self.n_trials >= 40: self.patience = max(10, self.n_trials // 4)` 仍然内联在 `run()`,跟其它 `auto_*` 助手不一致,无法测试。
7. **`for t in study.trials: trials_data.append({...})` 8 行内联**做 Optuna `FrozenTrial` → dict 适配,与 `filter_completed_trials` 期望的 dict shape 是隐式契约,没有 adapter helper 兜底。
8. **`run()` 异常路径 + 取消路径分别复制 5 行 `_shared_engine.dispose()` 清理**,`_cleanup_shared_engine()` 没提出来。

外加 4 个 robustness 数学函数(`_compute_dsr`、`_compute_param_sensitivity`、`_compute_param_stability`、`extract_fitness`)上一轮已经抽出了,但 **`build_full_result` 这一层装配** 仍然在主流程里散点赋值,前端契约没有锁。

**要点**:

1. **`PatienceTracker(patience).observe(value) -> bool` —— 状态机/Optuna 类型解耦** —— 把 `_best`、`_no_improve_count`、`threading.Lock` 全搬进纯 `PatienceTracker` 类。`_PatienceCallback.__call__` 缩为 4 行 shim:`if self._tracker.observe(trial.value): study.stop()`。`PatienceTracker` 现在 10 个测试覆盖:首次必改进、严格大于(等值不重置)、改进重置、None 计入 no-improve、累计触发、idempotent stop、patience=1 边界、4 线程并发 smoke。一旦 Optuna API 变化,只需更新 4 行 shim,纯逻辑零变动。

2. **`build_progress_payload(...)` —— 6-key 强一致** —— 把两条发布路径(running / completed)统一到单一 helper,5 个 key:`optimization_id, trials_completed, total_trials, best_value, best_params, status`。`PROGRESS_STATUS_RUNNING = "running"` / `PROGRESS_STATUS_COMPLETED = "completed"` 作为 wire-format 常量同时导出,前端可以 `status === PROGRESS_STATUS_COMPLETED` 而不是 magic string。`best_params` 经 `dict(...)` 防御拷贝,callers 之后修改 dict 不会污染已发布 payload。测试:`test_completed_payload_same_shape` 直接断言 `set(running.keys()) == set(completed.keys())`——任何漂移立即失败。

3. **`build_full_result(*, ...) -> dict` + `FULL_RESULT_BASE_KEYS: frozenset` —— 16-key schema 契约锁定** —— 关键键全部 keyword-only(防止未来加字段时位置错位),16 个基础 key 永远存在,`walk_forward_results` 唯一条件 key(只在 walk-forward 模式 added,与历史序列化保持一致防止前端 `if "walk_forward_results" in result` 检查失效)。所有可变输入 (`best_params`、`trials`、`param_importances`、`convergence_history`、`walk_forward_results`) 防御拷贝。`FULL_RESULT_BASE_KEYS` 是导出的 `frozenset`,被前端可以静态校验。`TestBuildFullResult` 14 个测试 + `TestFullResultBaseKeys` 3 个测试一起锁定:`frozenset(out.keys()) == FULL_RESULT_BASE_KEYS` 严格相等、空 list vs None 区分(空 list 仍添加 key)、所有 6 处防御拷贝独立验证、关键 key 名单 spot-check、`walk_forward_results` 必须不在基础集中。

4. **`select_best_params(trial_params, param_ranges) -> dict`** —— 把 `objective()` 和 `run()` 收尾两处 4 行字典推导收敛到单一 helper。原来一处用 `self._suggest_params_from_trial(trial).items()`(经过 staticmethod 一层),另一处用 `best_trial.params.items()`,实际逻辑相同——现在两处都用 `select_best_params(dict(trial.params), self.param_ranges)`。`_suggest_params_from_trial` 静态方法删除(作为不再需要的间接层)。5 个测试覆盖:正常过滤、空 param_ranges、空 trial、缺失 param、不变性。

5. **`serialize_trial(trial)` —— Optuna FrozenTrial → dict adapter** —— 把 `for t in study.trials: trials_data.append({...})` 8 行内联缩为 1 行 list comp:`trials_data = [serialize_trial(t) for t in study.trials]`。helper duck-typed(`getattr(state, "name", state)`),既支持 Optuna `TrialState` enum 也支持已经 stringified 的 state。最有价值的 `test_filter_completed_trials_round_trip` 验证 `serialize_trial` 输出能直接喂给 `filter_completed_trials`——把两个 helper 的隐式 shape 契约变成显式同一性测试。

6. **`build_walk_forward_fold_record(*, ...) -> dict`** —— 把 walk-forward 折记录 6-key 字面量提出来,锁定:`fold_idx`(0-based 输入)→ `fold`(1-based 输出)的语义,4 个日期字段一致用 `.isoformat()`。`test_fold_indexing_is_one_based` parametrize 5 个 `fold_idx` 值,锁定永远 +1 的契约。

7. **`auto_patience(n_trials) -> int`** —— `n_trials < 40` 返回 0(不启用),阈值以上返回 `max(10, n // 4)`(地板 10,quarter 启发式)。一致命名(跟 `auto_n_trials/sampler/workers` 同形)。5 个 parametrize 测试覆盖阈值上下、floor 边界、O(n) 缩放。

8. **`_cleanup_shared_engine()` 方法提取** —— `run()` 异常路径(`except Exception:` 后的 4 行 `try: dispose()`)和取消路径(同样 4 行)和成功路径末尾(同样 4 行)三处复制粘贴,合并到一个 idempotent 方法。`if self._shared_engine is None: return` 守卫让重复调用 safe。

9. **`tests/backtest/test_optimizer_shim.py` 新建** —— 37 个测试覆盖:
   - `_PatienceCallback` 5 个集成测试:首次不停 / patience 后停 / 改进重置 / None 计入 / idempotent
   - `TestBackwardCompatAliases` 11 个 `is` 同一性测试 —— 别名(`optimizer._FAIL_VALUE`、`_split_dates`、`_walk_forward_windows`、`_extract_fitness`、`_auto_n_trials`、`_auto_sampler`、`_auto_workers`、`_slim_result`、`_compute_dsr`、`_compute_param_sensitivity`、`_compute_param_stability`)与 helpers 模块的对应 public symbol **同一对象**。注释里明确解释为什么用 `is` 而非 `==`:防止别名在重构中被替换为重新实现。
   - `TestPublicRexports` 21 个 `hasattr` 测试 —— 锁定 21 个公共 re-export 都通过 `from tinohelm.backtest.optimizer import X` 可访问,新代码不需要绕过 optimizer.py 直接 import 内部 helpers 模块。

10. **完整回归**: `.venv/bin/python -m pytest tests/` 从 761 增至 854(+93),全部通过。

**讨论点**:

- **保留 `_suggest_params_from_trial` 静态方法 vs 删除** —— 评估后删除。原方法只有一行 `return dict(trial.params)`,被一个调用点用,不构成抽象价值;`select_best_params` 已经显式接受 `dict(trial.params)`,意图更清晰。无外部 caller 依赖此方法(grep 全项目零结果)。
- **`run()` 仍然 343 行** —— 主流程剩下的都是真正的 orchestration:Optuna 配置、shared engine 准备、4 段 `_run_backtest` 串联(walk-forward 每折 + validation + train_validation + walk-forward 折详情),不可避免的串行流程。继续往下抽就是为抽象而抽象,本次不动。
- **`build_full_result` 16 keys 严格 frozenset** —— 当未来需要添加新顶层字段(比如下一个评估指标)时,**必须同步更新 `FULL_RESULT_BASE_KEYS` 常量** 和 `build_full_result` 签名,以及前端 TS 类型。`test_base_keys_strictly_match` 会立即失败提醒——这是设计意图,不是缺陷。

**验证**:
- ✅ 854/854 pytest 全通过(`PYTHONPATH=src python3 -m pytest tests/`,从 761 增至 854,+93 = 59 helpers + 37 shim - 3 重叠)
- ✅ 字节码编译检查通过(`py_compile` on 4 个修改/新建文件)
- ✅ `optimizer_helpers.py` 在 `sys.meta_path` blocker 下独立导入通过 —— 加载后 `sys.modules` 不含任何 `nautilus_trader / optuna / redis / sqlalchemy`,证实零依赖
- ✅ 检查 TODO/FIXME/XXX 注释清零
- ✅ optimizer.py: 812 → 792 行(-20 行净;`run()` 内 60 行 full_result + 30 行 _PatienceCallback + 8+8 行两条 Redis dict + 8 行 trials_data 装配 + 8 行 wf_fold_record 装配 ≈ 122 行内联代码全部替换为单次声明式调用,但同时新增 `_cleanup_shared_engine` 15 行 + 10 行新 import)
- ✅ optimizer_helpers.py: 452 → 707 行(+255 行,7 个新 helper + 3 个常量)
- ✅ 新增 538 行测试 (`test_optimizer_helpers.py`) + 161 行测试 (`test_optimizer_shim.py`)
- ✅ 重复模式消除:Redis publish 字面量 2→1,select_best_params 字典推导 2→1,_shared_engine cleanup try/except 3→1,full_result 装配 60 行 散点赋值→1 个声明式调用
