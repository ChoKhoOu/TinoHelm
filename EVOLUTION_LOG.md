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


## 2026-04-17

**主题**: 补齐 `node/strategy_registry.py` 全量单元测试 + `node/lifecycle_controller.py` bundle 级生命周期测试 — 把 live/sandbox 最关键的运行时路径纳入测试安全网
**维度**: 测试补齐
**改动范围**:
- 新增 `tests/node/test_strategy_registry.py`(622 行,59 个用例)— 针对 `StrategyRegistry` 与 `_derive_tag` 的纯 Python 全覆盖
- 扩充 `tests/node/test_lifecycle_controller.py`(536 → 1347 行,44 → 97 用例,+53)— 补齐 `dispose` / `pause_all` / `resume_all` / `_pause_strategy_id` / `_resume_strategy` / `pause_strategy(name)` / `resume_strategy(name)` / `flatten_stop_strategy(name)` / `check_flatten_stop_completion` / `cancel_order` / `start_strategy(name)` + rollback + `get_state` with registry

**动机**:

`strategy_registry.py`(271 行)和 `lifecycle_controller.py` 的 bundle-级方法(~350 行)一起承担了 live/sandbox node 最要命的运行时职责:策略发现/状态机/tag 分配、L1~L4 生命周期控制、flatten-stop pending 队列、订单取消。但这两个模块此前的覆盖情况是:

1. **`StrategyRegistry` 零专用测试** — 这是全项目最"它必然 NT-free"的模块(头部注释明写"Pure Python class with zero NT dependencies — fully testable with plain pytest"),居然没有一个 `tests/node/test_strategy_registry.py`。状态机转移、tag 前缀冲突、allocate 偏移量溢出、scan 删除保护(运行中不可删)、`restore_was_running` 序列化回放等关键契约全部只能靠调用方间接保障。任何无意的行为漂移都不会被发现。
2. **`LifecycleController` 只测了 L1~L4 原子动作** — 既有 44 个用例覆盖了 `pause_strategy_id` / `resume_strategy_id` / `flatten` / `halt` / `unhalt` / `shutdown` / `_resolve_strategy_id` / `_on_risk_guard_breach` / `get_state`(无 registry 场景)。但 bundle 级(`pause_strategy(name)`、`resume_strategy(name)`、`flatten_stop_strategy(name)`、`check_flatten_stop_completion`)、`start_strategy` 及其 rollback 路径、`cancel_order` 全部没测。这是前端 `tino node strategy start|pause|resume|flatten-stop` 的**直接后端**,也是 HealthActor 自动续跑(auto-resume)依赖的入口。

即是说,live/sandbox 最贴近真实交易的一层没有测试兜底 — 只要 NT API 小变动或重构手抖,生产路径立刻静默走样。

**要点**:

1. **`tests/node/test_strategy_registry.py`(59 用例,分 7 个测试类)**:

   - `TestDeriveTag`(12 用例)— 锁定 `_derive_tag` 契约:首字母缩写、`vNN` 版本号保留数字、纯数字段不变、大写 `V` 不当版本标记、大写字母自动 lower、连续/首尾下划线 skip、空字符串返回 `""`。`test_consecutive_underscores_are_skipped` 防的是"`part[0]` IndexError",这是一条不写测试就容易埋的分支。
   - `TestStrategyEntry`(2 用例)— 验证 dataclass 默认值 + `default_factory=list` 不跨实例共享(防意外共享 list)。
   - `TestRegister`(6 用例)— 自动 tag / manual_tag 优先 / 幂等返回已存在 entry / manual collision / auto-derive collision / 第二方未被添加(原子性)。
   - `TestAllocateTags`(9 用例)— 未注册策略报错 / 单 tag 格式 `prefix+000` / 多 tag 顺序 / 全局 offset 跨策略递增 / count=0 不前进 / collision with `-{tag}` 后缀 / collision 不污染 offset / 超过 999 溢出 / 碰撞只匹配精确后缀(防 substring 误杀,例如 `Cls-m0001` 与 `m000` 不碰撞)。
   - `TestStateTransitions`(6 用例)— mark_starting/running/paused/flattening/stopped 五条转移 + mark_stopped 清空 `_strategy_to_bundle` + 未知 name 为静默 no-op(与代码注释"used by event handlers where races with deletion are possible"一致)。
   - `TestQueries`(4 用例)— `get` / `available` / `get_bundle_for_strategy` / `get_all_states` 形状契约。
   - `TestSerialization`(7 用例)— `to_dict()` 空字典、was_running 包含 running+paused+flattening 但不含 available、`next_tag_offset` 正确回写、`restore_was_running` 只翻转 `was_running` 不改状态(关键!HealthActor 会后续通过 `start_strategy` 重新启动)、未知名 skip、缺 key 默认 []、空 saved_state 无副作用。
   - `TestScan`(9 用例 via `patched_scan` monkeypatch)— 目录缺失返回 []、空目录 no-op、添加新策略、删除 available 策略释放 prefix、`deleted_but_running` 保留 running/paused/flattening 策略的 entry **和** prefix、`starting` 状态被 scan 清理(因为还没 add_strategy 到 trader)、第二次相同 scan 无变化、"添加→删除→再添加"完整闭环释放并复用 prefix、单次 scan 同时 add+remove。

2. **`tests/node/test_lifecycle_controller.py` 扩充(+53 用例,分 12 个新测试类)**:

   - `TestDispose`(2 用例)— 退订 `RISK_GUARD_STATE` 主题 + 异常被 swallow(double-dispose 安全)。
   - `TestPauseAll` / `TestResumeAll`(6 用例)— 广播 pause/resume、空 strategies 仍发 ack、resume_all 清空 `_paused_strategies`。
   - `TestInternalPauseResume`(4 用例)— `_pause_strategy_id` / `_resume_strategy` 不发 ack、错误 ID 只记 log 不 raise(bundle pause 依赖这一点 — 单个成员失败不能炸掉整个 bundle)。
   - `TestGetStateWithRegistry`(2 用例)— 有 registry 时 `state["strategies"]` 存在,无 registry 时 key 缺失(前端可以区分这两种场景)。
   - `TestPauseStrategyBundle` / `TestResumeStrategyBundle`(9 用例)— `pause_strategy(name)` 批量发 L1 pause、`registry.mark_paused` 调用、无 registry 报错、非 running 状态报错、resume 对称、resume 清理 `_paused_strategies` 只清本 bundle 成员(不污染其它独立 pause 的 SID)。
   - `TestFlattenStopStrategy`(9 用例)— 批量 market_exit、写 pending 记录、发"flattening"ack、`mark_flattening` 调用、**paused 成员先 resume 再 flatten**(否则 paused 策略不会响应 market_exit)、`market_exit` 异常被 swallow(pending 仍写入,超时兜底依然有效)、三条 precondition 错误路径。
   - `TestCheckFlattenStopCompletion`(7 用例)— 全部 flat → remove_strategy + mark_stopped + "ok" ack + 清空该 bundle 的 paused 成员、仍有仓位且未超时 → no-op、**超时 60s 强制 remove + "timeout" ack + log.critical**、positions_open 异常当"not flat"、空 pending 表 no-op、remove_strategy 抛错被 swallow。
   - `TestCancelOrder`(4 用例)— 订单不在 cache → "not_found"、已关闭 → "already_closed"、策略找不到 → "strategy_not_found"、happy path → `strategy.cancel_order(order)` + "submitted"。这是 TUI "取消订单"按钮的直接后端。
   - `TestStartStrategyHappyPath`(2 用例)— 调用顺序(mark_starting → allocate_tags → add_strategy → add_actor → start_strategy → mark_running)、"ok"ack 含 strategy_ids。
   - `TestStartStrategyPreconditions`(3 用例)— 无 registry / 未知策略 / 非 available 状态。
   - `TestStartStrategyRollback`(5 用例)— 这是**最关键**的 5 个用例,把 atomic registration 的回滚契约锁死:
     - bundle load 失败 → `mark_stopped` + error ack
     - `add_strategy` 第二个抛错 → 第一个已添加的被 `remove_strategy` 撤回
     - `start_strategy` 抛错 → actor + strategy 全部 remove
     - strategy.id 已在 trader 上 → `add_strategy` **从未被调用**(在 add 之前就炸)
     - rollback 阶段 `remove_*` 再抛错 → swallow 不 reraise,外层 error ack 正常发出

3. **测试辅助模式**:

   - `_make_controller_with_registry()` — 建一个带 MagicMock `StrategyRegistry` 的 controller,默认 `state="running"` + `strategy_ids=["Alpha-000", "Alpha-001"]`,bundle-级测试一行到位。
   - `_install_start_strategy_mocks(monkeypatch, strategies=, actors=, raise_on=)` — 封装 `tinohelm.portfolio.config.load_strategy_bundle` / `tinohelm.strategy.loader.{create_strategies,create_actors}` 三个内部 import 的 monkeypatch,`raise_on` 参数让 5 个 rollback 测试一行切换失败点。
   - `patched_scan` fixture(scope=function)— 把 `tinohelm.strategy.module_loader.scan_valid_strategy_files` 替换为返回 `{name: Path}` 字典的 fake,`set_files(mapping)` 就地改变下一次 `scan()` 看到的"目录内容",无需真实写文件。

4. **没有修改任何 src/ 代码** — 这是一次纯测试补齐,不改 behavior。所有断言都基于**现有**实现:
   - `_derive_tag("")` 返回 `""`(边缘但当前合法)
   - `scan()` 清理 `starting` 状态策略(当 file 消失时)— 这是设计选择,因为 `mark_starting` 到 `add_strategy` 之间若 file 被删,外层 `start_strategy` 的 except 会 `mark_stopped`;scan 同时并发清理是单线程 NT event loop 下无风险的冗余
   - `check_flatten_stop_completion` 的异常降级策略:`positions_open` 抛错 → 当作"未 flat",等 60s 超时兜底(这比静默 mark_stopped 更安全)

**讨论点**:

- **`StrategyEntry.tag_offset` 是 dead field** — 在 `register()` 和 `allocate_tags()` 写入,但从未被读取(`to_dict()` / `get_all_states()` / 外部调用方全部不访问)。历史遗留,属于清理点但不在本次测试补齐主题内。下次如果做 registry 序列化格式演进,可以一并移除,届时需同步更新 `StrategyEntry` dataclass + register()/allocate_tags() 两处赋值。
- **`scan()` 对 "starting" 状态的删除策略** — 当前代码允许 scan 清理 starting 状态的 entry,配合 `start_strategy` 的 outer except → mark_stopped 形成双重安全。如果未来 start_strategy 变为异步(NT 事件循环跨 task),这里可能存在 race,需要重新评估。本次用 `test_scan_removes_starting_state_strategy` 显式锁定当前行为,任何语义变动都会触发测试失败提醒。

**验证**:
- ✅ 全量 `pytest tests/`:1007 → 1119(+112 = 59 strategy_registry + 53 lifecycle),**全部通过**,耗时 10.45s
- ✅ `ruff check tests/node/test_strategy_registry.py tests/node/test_lifecycle_controller.py` — All checks passed
- ✅ 基线对比验证:`git stash && pytest tests/node/test_lifecycle_controller.py` → 44 passed(pre-change baseline);pop 后 97 passed,差值 +53 与新增用例数精确匹配
- ✅ `strategy_registry.py` 覆盖统计:7 个公开 API 方法(`scan`, `register`, `allocate_tags`, 5× `mark_*`, `get`, `get_bundle_for_strategy`, `available`, `get_all_states`, `to_dict`, `restore_was_running`)+ `_derive_tag` 私有辅助 + `StrategyEntry` dataclass,**全部有专用测试类**
- ✅ `lifecycle_controller.py` 覆盖统计:21 个公开方法里 19 个有测试(剩 2 个是 trivial ack-only path);所有 4 个 precondition guard + 3 条 rollback 路径 + 2 条 timeout 路径 + 3 条 cancel_order 错误分支全部显式断言
- ✅ 测试速度:`tests/node/test_strategy_registry.py` 0.17s(纯 Python);整个 node 测试包 2.10s
