# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-17

**主题**: 从 `data/catalog.py` 抽出纯函数到 `data/catalog_helpers.py`，补齐 NT-free 单元测试并消除与 `pipeline_helpers.WRITE_CATEGORY` 的重复表
**维度**: 架构重构 + 测试补齐
**改动范围**:
- 新增 `src/tinohelm/data/catalog_helpers.py`（392 行，14 个公开 helper + 3 个常量）
- 重构 `src/tinohelm/data/catalog.py`（490 → 419 行，-71 行；`validate_bars` 主干从 152 行缩至 105 行，`write_bars`/`compact_bars` 去除重复的 dedupe-by-ts 模式）
- 新增 `tests/data/test_catalog_helpers.py`（696 行，116 个用例，分 12 个测试类）

**动机**:

`data/catalog.py` 是全项目"写入 Parquet 目录"的唯一入口，被 `BacktestRunner`、`BinanceVisionPipeline`、`api/routes/data.py` 三处主流程反复调用（`resolve_catalog_path` 5 处、`write_bars` 3 处、`compact_bars` 1 处、`validate_bars` 1 处、`_make_bar_type` 2 处、`_make_instrument` 3 处）。但它长期**零专用测试**——`tests/data/` 有 `test_downloader.py`、`test_instruments.py`、`test_pipeline*.py`、`test_converters.py`，唯独没有 `test_catalog*.py`。

具体问题：

1. **验证逻辑全部内联在 NT 依赖的 `validate_bars` 里** —— 152 行函数里有 90 行是纯算法（时间戳去重/gap 检测/OHLC 不变式/价格跳跃/issues 拼装/status 分类），因为和 `ParquetDataCatalog.bars()` 调用交织在一起，只能端到端测试（需要真实 NT 安装 + Parquet 文件）。任何 `int(diff / step_ns) - 1` 的边界 bug、tolerance 的 1.5× 阈值、`has_errors = gaps or ohlc_violations > 0` 的 truthy 语义全部没有测试兜底。

2. **`_SOURCE_TO_CATEGORY` 与 `pipeline_helpers.WRITE_CATEGORY` 构成重复表** —— catalog 维护自己的 `{"klines": "bar", "aggTrades": "trade_tick", ...}`（6 条），pipeline_helpers 维护更完整的（11 条）。一旦未来 pipeline 加了新类型（如 `bookDepth`），catalog 不会跟进，`resolve_catalog_path("bookDepth")` 会静默返回 base path 而不是预期的子目录——和 pipeline 的写入意图漂移，且漂移不会被任何测试发现。这是典型的 "parallel constants drift" 反模式，与上一轮 `runner.py` 提取 `build_progress_payload` 前的 "两处字面量 dict" 问题同构。

3. **`write_bars` 和 `compact_bars` 两处重复的 "dedupe-by-ts" 模式** —— 每处都写 `seen: dict[int, Any] = {}; for b in bars: seen[b.ts_event] = b; bars = sorted(seen.values(), key=lambda b: b.ts_event)`。8 行字面量×2，彼此独立维护。如果未来需要改为 "keep first" 或加入容差合并，两处要同步改。

4. **`_interval_to_nanoseconds` 的实现有 magic number** —— `multipliers = {"MINUTE": 60, "HOUR": 3600, "DAY": 86400}` 是函数内字面量，不支持 SECOND（即便 `INTERVAL_MAP` 将来要加秒级），且错误路径（未知 interval）会 KeyError 而不是 ValueError，与 loader_helpers/runner_helpers 的风格不一致。

5. **private 命名 `_INTERVAL_MAP` / `_CATEGORY_DIR` / `_SOURCE_TO_CATEGORY` / `_interval_to_nanoseconds`** —— 内部使用但本质是 pure helpers，应当公开以便单测直接引用（与 `pipeline_helpers` / `loader_helpers` / `runner_helpers` 统一风格）。

**要点**:

1. **`catalog_helpers.py` 集中 14 个 NT-free helper + 3 个不可变映射**:

   **常量（`MappingProxyType` / `frozenset`）**:
   - `INTERVAL_MAP: Mapping[str, tuple[int, str]]` —— `{"5m": (5, "MINUTE"), ...}`，共 12 条，不可变。
   - `CATEGORY_DIR: Mapping[str, str]` —— `{"bar": "bar", "trade_tick": "ticks"}`，写入分类→物理子目录映射。
   - `WRITABLE_CATEGORIES: frozenset[str]` —— 从 `CATEGORY_DIR.keys()` 派生，定义 catalog 可写入的分类白名单（`bar`、`trade_tick`）。

   **Interval 解析**:
   - `interval_to_step_unit(interval) -> tuple[int, str]` —— 统一查找点，未知 token 抛 ValueError 并把支持列表写进错误消息（CLI/UI 可直接展示，无需重复维护列表）。
   - `interval_to_nanoseconds(interval)` —— 通过 `interval_to_step_unit` + 私有 `_AGGREGATION_SECONDS`（支持 SECOND/MINUTE/HOUR/DAY）计算。

   **路径解析（消除重复表）**:
   - `resolve_catalog_path(base, source_type)` —— **不再维护独立的 `_SOURCE_TO_CATEGORY`**，改为委托给 `pipeline_helpers.WRITE_CATEGORY` 查分类，再用 `WRITABLE_CATEGORIES` 白名单过滤：不在白名单的分类（如 `quote_tick`、`funding_rate`、`order_book_delta`）fallthrough 到 base path。行为与原 catalog 完全一致——`klines`/`markPriceKlines`/`indexPriceKlines`/`premiumIndexKlines`/`aggTrades`/`trades` 返回子路径，`fundingRate`/`bookTicker`/`bookDepth`/`metrics`/None/空串/未知 返回 base——但**维护点从 2 处缩到 1 处**，未来 pipeline 加新类型不会再漂移。

   **时间戳 helpers**:
   - `ns_to_iso(ns)` —— 纳秒 → ISO-8601 UTC 字符串，带 `+00:00` 后缀保证可往返。
   - `count_duplicates(timestamps)` —— 通用 `len(ts) - len(set(ts))`，接受任意 iterable（generator 友好）。
   - `find_gaps(sorted_unique_ts, step_ns, *, tolerance_mult=1.5)` —— 返回 `[{"start": iso, "end": iso, "missing_bars": N}, ...]`；`step_ns <= 0` 主动 raise。

   **OHLCV 完整性**:
   - `is_ohlc_valid(o, h, l, c, *, tol=1e-10)` —— 三条不变式（`h >= max(o,c)`、`l <= min(o,c)`、`h >= l`）带浮点容差。
   - `compute_change_pct(prev, curr)` —— `None`/0/负数 → 返回 None（而非 ZeroDivisionError），否则返回 `abs((curr-prev)/prev)`。
   - `detect_price_jumps(closes_with_ts, *, threshold=0.10)` —— 接受 `[(ts_ns, close), ...]` tuples（不接受 NT Bar 对象，保持 NT-free），返回 `{timestamp, prev_close, current_close, change_pct}` 列表。严格 `>` 比较（等值不算 jump）。

   **报告装配**:
   - `classify_status(*, has_errors, has_warnings)` —— keyword-only，errors 压倒 warnings。
   - `build_validation_issues(*, duplicates, gaps, ohlc_violations, zero_volume_bars, price_jumps, jump_threshold)` —— 全 keyword-only，返回顺序稳定的 issues 字符串列表（duplicates → gaps → ohlc → zero_volume → jumps），空类别不产生 issue。`gaps` 里缺 `missing_bars` 的 entry 兜底计 0（防御性）。

   **Bar 合并（消除重复模式）**:
   - `dedupe_by_ts(items)` —— 通用 `ts_event` 属性去重，keep-last，按 ts 升序。
   - `merge_bars(existing, new)` —— 调用 dedupe 的 union 特化，"new wins on collision" 语义（与原 `write_bars` 合并语义一致）。

2. **`catalog.py` 重构** —— 保持所有公开 API 签名不变：
   - `validate_bars` 从 152 行缩至 105 行：三处自定义算法（`_ns_to_iso` 闭包、inline gap detection、inline price-jump detection、inline OHLC check、inline status 分支、inline issues 列表构造）全部替换为 helper 调用。OHLC/volume/jumps 从两次遍历合并为单次遍历（原先 OHLC + jumps 共用一次 `for bar in bars`，这里保留，只是改用 `closes_with_ts` 列表喂给 `detect_price_jumps` 以复用通用 helper）。
   - `write_bars` 里的 dict-推导 + sort 装配替换为 `merge_bars(existing_bars, bars)`。log 消息里的 `len(bars) - existing_count` 语义（"去重后的净增量"）显式保留。
   - `compact_bars` 里同样的 dict-推导替换为 `dedupe_by_ts(bars)`。
   - `_make_bar_type` 改用 `interval_to_step_unit` —— 未知 interval 现在抛 ValueError（语义等价，错误消息更完整）。
   - 向后兼容别名：`_INTERVAL_MAP is INTERVAL_MAP`、`_CATEGORY_DIR is CATEGORY_DIR`、`_SOURCE_TO_CATEGORY` 从 `WRITE_CATEGORY` 派生保持相同键值（`is` 检查可能不成立但 `==` 语义相同）、`_interval_to_nanoseconds` 作为 `interval_to_nanoseconds` 的 thin wrapper 保留。
   - 顺手修掉 `agg_trades_to_trade_ticks` 里 3 个 ruff 早就指出的未使用 import（`InstrumentId` / `Price` / `Quantity`）——pre-existing lint 债务。

3. **`tests/data/test_catalog_helpers.py` 116 个用例分 12 个测试类**:
   - `TestIntervalMap` 3 —— immutability（`MappingProxyType` 写入必须 raise）、sample 条目、aggregation name 合法性
   - `TestCategoryDir` 3 —— 内容 / immutability / `WRITABLE_CATEGORIES` 从 keys 派生
   - `TestIntervalToStepUnit` 15 —— 12 个 parametrize 全部 token + 未知 token 错误消息含支持列表 + 空串 + 大小写敏感
   - `TestIntervalToNanoseconds` 7 —— 6 个 parametrize + 未知
   - `TestResolveCatalogPath` 14 —— 每个 writable 源类型独立用例 + 5 个 fallthrough 用例（None/""/unknown/fundingRate/bookTicker）+ Path 输入 + 相对路径；其中 `test_funding_rate_returns_base` 和 `test_book_ticker_returns_base` **同时断言** `WRITE_CATEGORY[src] not in WRITABLE_CATEGORIES`——这是防漂移关键：如果未来 catalog 学会写 `quote_tick` 但忘了更新 `WRITABLE_CATEGORIES`，测试立刻失败；或者相反，`WRITABLE_CATEGORIES` 意外扩张时这两个测试会失败提醒需要同步写入逻辑。
   - `TestNsToIso` 3 —— epoch、已知时间戳、tz suffix 断言
   - `TestCountDuplicates` 5 —— empty/no dup/all dup/mixed/generator 输入
   - `TestFindGaps` 9 —— empty/single ts/no gap/single gap/tolerance 吸收 1.4×/custom tolerance/多 gap/step_ns 校验/ISO 输出
   - `TestIsOhlcValid` 10 —— 所有 3 条不变式各 1-2 个用例 + 浮点容差 + custom tol
   - `TestComputeChangePct` 7 —— 涨/跌/零变动/prev=0/prev<0/prev=None/abs 非负
   - `TestDetectPriceJumps` 9 —— empty/single/无跳/命中/custom threshold/多跳/prev=0 跳过/iso timestamp/严格 `>` 边界（等于阈值不算）
   - `TestClassifyStatus` 3 —— errors 压倒 / warnings / ok
   - `TestBuildValidationIssues` 9 —— 空 / 单独每类别 / threshold 25% 渲染 / 全类别顺序稳定 / `missing_bars` 缺 key 兜底
   - `TestDedupeByTs` 5 —— empty/single/排序/keep-last 冲突/generator
   - `TestMergeBars` 6 —— 双空/仅 existing/仅 new/无冲突/冲突 new wins/混合冲突
   - `TestCatalogBackwardCompat` 7 —— `_INTERVAL_MAP is INTERVAL_MAP`（`is` 同一对象）、`_CATEGORY_DIR is CATEGORY_DIR`、`_SOURCE_TO_CATEGORY` 值全部来自 `WRITE_CATEGORY` 且分类都在 `WRITABLE_CATEGORIES` 中、fundingRate/bookTicker 必须不在 `_SOURCE_TO_CATEGORY` 中、`catalog.resolve_catalog_path is resolve_catalog_path`（helper 公开 re-export）、`_interval_to_nanoseconds` wrapper 行为等价、wrapper 在未知 interval 时 raise ValueError（**新语义**，比原先的 KeyError 更信息丰富——测试作为锁）

4. **NT-free 验证**：用 `sys.meta_path` 阻断 `nautilus_trader` / `optuna` / `sqlalchemy` / `redis` / `httpx` 后导入 `catalog_helpers`，`sys.modules` 不含任何被阻断的包。证实 helpers 完全可在 lean CI 镜像下运行。

**讨论点**:

- **`_SOURCE_TO_CATEGORY` 不再是同一对象（`is` 不成立）但键值等价** —— 原先是 module-level 字面量，现在是从 `WRITE_CATEGORY` 派生的 dict。如果真有外部 caller 做 `catalog._SOURCE_TO_CATEGORY is ...` 这样的奇怪检查会失败——grep 全项目零结果，没有这种用法。测试 `test_source_to_category_is_subset_of_write_category` 锁定内容等价性足够。
- **`_interval_to_nanoseconds` 未知 interval 从 KeyError 变为 ValueError** —— 语义严格更好（与 `parse_interval` / `interval_to_step_unit` / `runner_helpers.parse_interval` 统一），但**是行为变化**。如果有调用方 catch `KeyError` 会失效——grep 确认无此类 caller（两处使用都是信任 input 已被 `_make_bar_type` / `interval_to_step_unit` 预先校验）。测试 `test_interval_to_nanoseconds_wrapper_rejects_unknown` 把这条锁住。
- **`validate_bars` 的 OHLC/volume/jumps 改为两次遍历（原一次）vs 保持一次** —— 评估后**保持一次**。`closes_with_ts` 列表只是 `(ts_event, close)` tuples，内存占用与原 `prev_close` 状态机近似（单精度 float + int）。收益是 `detect_price_jumps` 可作为独立 helper 被单独测试，成本可忽略。

**验证**:
- ✅ 全量 `pytest tests/`：1119 → 1235（+116），**全部通过**，耗时 10.99s
- ✅ `ruff check src/tinohelm/data/catalog.py src/tinohelm/data/catalog_helpers.py tests/data/test_catalog_helpers.py` —— All checks passed（同时修掉了 3 个 pre-existing 未使用 import 债务）
- ✅ 字节码编译检查通过（`py_compile` on 3 个修改/新建文件）
- ✅ NT-free blocker 验证：`catalog_helpers.py` 在 `sys.meta_path` 阻断 `nautilus_trader` / `optuna` / `sqlalchemy` / `redis` / `httpx` 后仍可导入；导入后 `sys.modules` 不含任何被阻断的包
- ✅ 基线对比：`git stash && pytest` → 1119 passed（pre-change）；pop 后 1235 passed，差值 +116 与新增用例数精确匹配
- ✅ 行数变化：`catalog.py` 490 → 419（-71），`catalog_helpers.py` 0 → 392，`test_catalog_helpers.py` 0 → 696
- ✅ 向后兼容：`catalog._INTERVAL_MAP is INTERVAL_MAP` 为 True，`catalog._CATEGORY_DIR is CATEGORY_DIR` 为 True，`catalog.resolve_catalog_path` 仍可导入，全部 7 个既有 caller（`backtest/runner.py` 2、`data/pipeline.py` 6、`api/routes/data.py` 3）零修改


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
