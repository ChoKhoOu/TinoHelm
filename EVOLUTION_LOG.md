# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-16

**主题**: 提取 extract.py 中最后的大型内联分析块(section 12b 全部 + section 14 Robustness),并建立结果 schema 锁定集成测试
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/result/sections.py` — 新增 7 个纯计算助手 (`compute_trade_scalar_metrics`、`compute_trade_pnl_distribution`、`compute_cumulative_trade_pnl`、`compute_trade_pnl_scatter`、`compute_holding_time_distribution`、`compute_mae_mfe`、`compute_robustness`) + 1 个内部工具 (`_histogram_bins`),共 +325 行
- `src/tinohelm/backtest/result/extract.py` — 1055 → 942 行(-113 行,-10.7%);section 12b 从 ~210 行缩为 ~95 行声明式调用;section 14 Robustness 从 43 行缩为 18 行;新增 `_collect_bars_by_instrument()` 辅助函数封装 NT bar cache 打平逻辑
- `tests/backtest/test_sections.py` — 新增 53 个单元测试覆盖 7 个新助手(含 `compute_mae_mfe` 的 8 个边界 case)
- `tests/backtest/test_result_schema.py` — 新建,8 个集成测试锁定 `extract_backtest_results` 的 33 个顶层 key + 65 个 statistics key + robustness PSR/MBL/MC key 形状

**动机**:
上一轮 evolution log (2026-04-16 (4)) 将最大的 section 9b / 11f 提取完毕后,
extract.py 还剩两个内联分析块未处理:
1. **Section 12b 交易级分析 (~210 行)** — 9 个标量指标(median/std/fill_rate/
   avg_trades_per_day/recovery/sqn/kelly/k_ratio/expectancy_r)+ 5 个图表数据
   数组(PnL 分布、累积 PnL、PnL 散点、MAE/MFE、holding time 分布)。
   K-Ratio 计算嵌套 14 行 OLS 回归和残差计算,MAE/MFE 需要遍历 `engine.cache.bar_types()`
   并筛选时间窗口内的 bar — 完全无单元测试,是本文件最后一块非平凡数学内联。
2. **Section 14 Robustness (~43 行)** — 组装 PSR / MBL / Monte Carlo,虽然
   子计算 (`_compute_psr`、`_compute_monte_carlo` 等) 都在 statistics.py,
   但组装逻辑仍内联在 extract.py 主函数里,无法独立测试。

上一轮 evolution log 也明确建议"对 `extract_backtest_results` 本体建立集成测试,
锁定各 section 输出的 key schema(防止字段重命名破坏前端契约)" —
这一轮一起完成。前端 `BacktestResult` TypeScript 类型依赖 60+ 个 key,
没有测试兜底就可能在无意识重命名中破坏前端渲染。

**要点**:

1. **`compute_trade_scalar_metrics()`** — 签名 `(pnls, *, n_orders, n_filled_orders,
   n_returns_periods, total_trades, total_pnl, max_drawdown, starting_balance,
   win_rate, avg_win, avg_loss, expectancy) -> dict[9 keys]`。一次调用返回全部
   9 个标量指标,空 `pnls` 返回全 None。K-Ratio 的 14 行 OLS 回归被完整封装,
   退化输入(log 定义域外、方差为 0、MSE ≤ 0)通过本地 `try` 退回 None。

2. **`compute_mae_mfe(positions, bars_by_instrument)` + 轻量 BarProvider 契约** —
   没有引入正式的 `Protocol` 抽象,而是采用更轻量的 primitive 契约:
   `positions` 是 `{instrument, ts_opened, ts_closed, entry_price, side, pnl}`
   字典列表,`bars_by_instrument` 是 `dict[str, list[tuple[ts, high, low]]]`。
   `extract.py` 通过新的 `_collect_bars_by_instrument(engine)` 辅助函数
   把 NT bar cache 打平为 primitive tuples 再调用 pure helper。这样既避免了
   Protocol/abc 的样板代码,又让 MAE/MFE 的核心逻辑(时间窗口筛选 + 多头/空头
   公式反转)完全 NT 无关,CI 可独立测试。

3. **`compute_trade_pnl_distribution()` / `compute_cumulative_trade_pnl()` /
   `compute_trade_pnl_scatter()` / `compute_holding_time_distribution()`** —
   全部接受 primitive 输入(`list[float]` 或字典列表)返回 JSON-safe 字典列表。
   4 个 helper 共享新的 `_histogram_bins(n_items, cap=30, floor=10)` 私有工具,
   消除 section 12b 中两处重复的 `min(30, max(10, n // 5))` bin 数量计算。
   `compute_trade_pnl_scatter` 内置 `_format_ns_timestamp_local` 以避免跨模块
   import 循环在隔离测试加载路径下失败(与 `test_sections.py` 已有的
   `_load_sections_isolated()` 机制兼容)。

4. **`compute_robustness()`** — 签名 `(trade_pnls, starting_balance, *,
   daily_sharpe, n_days, skewness, kurtosis) -> dict`。`skewness`/
   `kurtosis` 接受 `None` 自动降级为 0.0;PSR/MBL 四个 key 始终存在,
   MC 五个 key 仅在 `len(trade_pnls) >= 2` 时附加 — docstring 明确了
   这一契约,`test_result_schema.py` 测试了该分支。

5. **Extract.py 声明式化** — section 12b 原本是一个"巨 `try` 内包 9+ 子 `try`"的
   嵌套结构(最外层的 `except` 吃掉所有子错误,根本不知道哪段出了问题),
   重构后拆为 9 个并列的顶层 `try/except`,每段的错误定位粒度提升到单个 section。
   section 14 从 43 行缩为 18 行。

6. **`test_result_schema.py` — 前端契约的安全网** —
   - 使用 `MagicMock` 构造最小 NT engine(positions + orders + bar_types +
     accounts 都是空/最小),跑完整 `extract_backtest_results`
   - 锁定 33 个顶层 key (frozenset 严格等式)
   - 锁定 65 个 statistics key (frozenset 严格等式)
   - 锁定 robustness 的 PSR/MBL 四个 key 始终存在,MC 五个 key 按
     `len(pnls) >= 2` 条件存在
   - 锁定顶层容器类型:哪些 key 必须是 dict、哪些必须是 list、哪个是 str
   - 覆盖 `compute_robustness=False` 时 `robustness` 为 `None`
   - 覆盖 **空 positions** 的情况,证明即使无任何交易,schema 仍完整
     且标量 metric 为 None 而非缺失 key
   以后任何前端字段改名都必须先更新这个锁定测试 — 把隐式契约变成显式契约。

7. **61 个新测试覆盖 7 个助手 + schema**(全部通过隔离文件路径加载,CI 可独立运行):
   - `TestTradeScalarMetrics` (17): 空输入 / median & std / 单 trade std=None /
     fill_rate 百分比和 0-orders / avg_trades 和 0-periods / recovery_factor
     四种边界 / SQN 正系统和零方差 / Kelly 正常和无 avg_loss / K-Ratio 正常和
     退化 log 域 / Expectancy-R 正常和 None / JSON 安全性(无 NaN/Inf)
   - `TestTradePnlDistribution` (4): 空 / bin 数量下限 / bin 数量上限 /
     counts 总和 = 样本数 / bin 单调
   - `TestCumulativeTradePnl` (3): 空 / 1-based 编号 / 累积值正确
   - `TestTradePnlScatter` (5): 空 / 时间戳格式化 / 0 时间戳转 null / 缺失时间戳转 null / 全字段保留
   - `TestHoldingTimeDistribution` (5): 空 / 忽略 0 和负 / 忽略 None / ns→hours
     转换 / bin counts 总和
   - `TestMaeMfe` (8): 空 positions / 缺 bars / 缺 ts / BUY / SELL(反转公式)/
     窗口外 bar 忽略 / 完全窗口外 position 跳过 / 端点包含
   - `TestRobustness` (7): 基础 key 存在 / 无 daily_sharpe→PSR=None / 短
     backtest→sufficient=False / 负 sharpe→MBL=None / None skew/kurt 安全 /
     多 trades→MC key 存在 / 单 trade→MC key 缺失
   - `TestResultSchema` (8 集成): 顶层 key 严格匹配 / statistics key 严格匹配 /
     robustness 基础 key 始终存在 / robustness MC key 条件存在 /
     `compute_robustness=False` 时 robustness=None / 顶层容器类型正确 /
     空 positions 仍保持完整 schema / 有 trades 时标量 metric 被填充

8. **完整回归**: `.venv/bin/python -m pytest tests/` 从 540 个测试增至 601 个,
   全部通过(+ 53 单元测试 + 8 schema 测试)。零回归。

**验证**:
- 601/601 pytest 全通过(`PYTHONPATH=src .venv/bin/python -m pytest tests/`)
- 字节码编译检查全部通过(`py_compile` on 修改的 4 个文件)
- 检查 TODO/FIXME/XXX 注释清零
- 检查 extract.py 未使用 import 清零
- extract.py 行数: 1055 → 942(-113 行,-10.7%)
- 四次演进累积: 1500 → 1159 → 1055 → 942 (总减 558 行,-37.2%)
- section 12b + section 14 在 extract.py 中的内联数学代码从 ~250 行降至 ~130 行
  (全部为 primitive 装配逻辑,零内联数学)

## 2026-04-16 (5)

**主题**: 将 `backtest/runner.py` 中的纯逻辑下沉到新建的 `runner_helpers.py`,并建立可在无 NT 环境下运行的单元测试层
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/runner_helpers.py` — 新建 245 行,收纳 10 个 NT-free 纯函数/常量
- `src/tinohelm/backtest/runner.py` — 1244 → 1205 行(-39 行,-3.1%),消除 3 处内联 payload 构造重复和 2 处内联 datetime/字符串装配
- `tests/backtest/test_runner_pure_helpers.py` — 新建,76 个无 NT 依赖的单元测试(之前 runner 相关 pure 逻辑测试都要 skipif NT)

**动机**:

上一轮(2026-04-16 (4))将 `extract.py` 压缩到 942 行并锁定了 schema 契约。
按照 `extract.py` 的成功范式,`backtest/runner.py`(1244 行)是本项目
剩下唯一一个内联纯逻辑密度仍然很高的大文件:

1. **`_setup_engine` (166 行)** 混合了 6 种职责 —— 策略 bundle 归一、
   warmup 起点回退、引擎/venue 装配、instrument 加载、bar 解析、benchmark 日收盘
   价计算 + metadata 缓存。其中至少 3 处(warmup 回退、symbols/intervals
   归一、benchmark daily closes)是纯 primitive 运算,只是因为被埋在 166
   行内联代码里而无法单测。
2. **`_resolve_bars`** 内联了两处 NT 字符串装配(候选 source 时间框遍历、
   composite bar type 字符串模板)。后者尤其危险——一旦 NT 的
   `LAST-INTERNAL@...-EXTERNAL` 语法改动,全项目没有一处测试能捕获。
3. **`_ProgressReporter.on_bar` 和 `_report_progress` 构造几乎相同的
   payload dict**(10 个 key,4 份字面量),
   但字段集合不完全一致(`message`  只在后者出现),导致前端
   `NotificationListener` 有时收到不同 shape 的 `backtest.progress`,
   dedupe/显示逻辑里做了隐式兼容。这是一个典型的"重复 + 漂移"问题。
4. **`_load_funding_rates` 里的装配循环** 跟 NT 无关,但跟
   `load_funding_rates` / `fetch_funding_info` 两个真·I/O 函数搅在一块,
   任何"空 mark_price 跳过"之类的边界逻辑都没有测试保护。

最终,`tests/backtest/test_runner_helpers.py` 里所有现有的纯逻辑测试(41 个)
全部 gated on `try: import nautilus_trader; _HAS_NT = True` —— 没有 NT 安装时
CI 会跳过 41 个测试。把真正 NT-free 的逻辑抽出来,可以同时解决"重复/漂移"和
"测试被 skip"两个问题。

**要点**:

1. **新建 `runner_helpers.py`,10 个导出符号** —— `TIMEFRAME_PRIORITY`
   (tuple,不可变的单一事实源)+ 9 个纯函数:
   - `interval_to_minutes(interval)` —— 从 `runner.py` 平移
   - `compute_warmup_adjusted_start(start, interval, warmup_bars)` ——
     新抽取;`start is None` / `warmup_bars <= 0` / `mins == 0` 三种
     no-op 情况都明确返回 `start`  (原代码在 None 时会抛 TypeError)
   - `resolve_symbols_intervals(bundle_symbols, bundle_interval,
     current_symbols, current_intervals)` —— 新抽取;
     runner-level 非空时 win,否则 fallback 到 bundle;始终返回新列表(
     测试锁定了列表身份独立性,防止调用方 mutate 串流)
   - `candidate_source_intervals(target, priority=TIMEFRAME_PRIORITY)` ——
     新抽取;优先级列表可注入,便于测试和将来支持自定义优先级
   - `build_composite_bar_type_str(nt_symbol, source, target, interval_map)`
     —— 新抽取;锁定 `{nt_sym}-{target}-LAST-INTERNAL@{source}-EXTERNAL`
     字符串语法
   - `extract_benchmark_daily_closes(bars)` —— 新抽取;接受
     `Iterable[tuple[ts_ns, close]]` primitive 契约(跟 extract.py 的
     MAE/MFE 一样的契约风格),避免引入 Protocol/abc
   - `compute_bar_progress_fields(bar_count, total_bars, elapsed)` ——
     新抽取;封装 10-90 pct 映射、eta、bars_per_sec 计算,
     负 elapsed 钳到 0、零 total_bars 返回 pct=10 地板
   - `build_progress_payload(run_id, *, pct, elapsed_secs, eta_secs=None, ...)`
     —— 新抽取;canonical payload 形状,10 个 key 全部强制存在
     (弥合了 setup 阶段和 bar 阶段原来不一致的 key 集合)
   - `assemble_funding_events(rates_by_symbol, nt_symbols_by_symbol,
     interval_minutes_by_symbol)` —— 新抽取;把原 `_load_funding_rates` 里
     90 行的"I/O + 装配 + 排序"拆成"I/O → primitive dict → 纯装配",
     零/None mark_price 丢弃、缺失 interval 默认 480 分钟、缺失 nt_sym 降级
     都在纯函数里,可单测

2. **`runner.py` 消重** ——
   - `_ProgressReporter.on_bar` 从 22 行(内含两处独立字段计算 +
     10-key payload literal)缩为 14 行,改为 `fields = compute_bar_progress_fields(...)`
     + `build_progress_payload(...)` 的声明式调用
   - `_report_progress` 从 25 行缩为 18 行,同样使用 `build_progress_payload`
     ;两个调用点输出 payload 的 shape 现在强一致
   - `_resolve_bars` 从 30 行缩为 22 行,composite 字符串装配一行完成
   - `_setup_engine` 从 166 行缩为约 144 行,warmup 分支 + symbols/intervals
     归一 + benchmark 装配三处各削 6-10 行
   - `_load_funding_rates` 从 69 行缩为 42 行,装配逻辑完全下沉到 pure helper
   - `BacktestRunner._TIMEFRAME_PRIORITY` 现在是 `list(TIMEFRAME_PRIORITY)`
     的单一引用,不再是散落的字面量

3. **`test_runner_pure_helpers.py` —— 76 个 NT-free 测试** 覆盖:
   - `TestIntervalToMinutes` (6): parametrize 幸福路径 / invalid / case-insensitive
   - `TestComputeWarmupAdjustedStart` (9): 三种时间单位 / None start / 空 interval
     / 零 warmup / 负 warmup / None warmup / invalid interval
   - `TestResolveSymbolsIntervals` (7): 幸福路径 / 四种 fallback 组合 /
     列表身份独立性(防 mutation 串流)
   - `TestCandidateSourceIntervals` (8): 各层级 target / 1m 无下限 / 1d 全上限 /
     unknown / 空 / 次序保留 / 自定义 priority tuple
   - `TestBuildCompositeBarTypeStr` (5): 两种正常映射 / source 未知回退 /
     target 未知回退 / 空 map 双回退
   - `TestExtractBenchmarkDailyCloses` (6): 空 / 单 bar / 同日多 bar 后者赢 /
     跨月边界 / float 强转 / generator 接受
   - `TestComputeBarProgressFields` (7): 零 total_bars / 50% 中点 / pct 90 上限 /
     起点 floor / 零 elapsed 无 bps / 负 elapsed 钳 0 / elapsed 四舍五入
   - `TestBuildProgressPayload` (4): 最小 shape / 完整 shape / trades 永远 None /
     两次调用等值
   - `TestAssembleFundingEvents` (9): 空 / 单 rate / 零 mark_price 丢弃 /
     多 symbol 时间戳排序 / 每 symbol interval / 默认 480 / 缺失 nt_sym 降级 /
     timestamp_iso UTC / 单 symbol 空 rates

4. **NT-free 单测层的价值** —— 现有 `test_runner_helpers.py` 里所有 41 个
   pure 逻辑测试都 gated on `_HAS_NT`;本轮新建的 76 个测试只要 python 3.11+
   就能跑,不需要 nautilus_trader 轮子。这对构建速度和 CI 资源都是大幅改善
   (NT wheel ~100MB,冷装 ~30s)。同时 `runner_helpers.py` 模块本身
   通过 `sys.meta_path` blocker 验证"不依赖 NT"——跑完 `import_without_nt.py`
   smoke test 全通过。

5. **修复了一个潜在 TypeError** —— 原 `_setup_engine` 的 warmup 回退:
   ```python
   if self.warmup_bars and self.warmup_bars > 0 and self.intervals:
       self.start = self.start - warmup_delta   # ← None 时崩溃
   ```
   新版 `compute_warmup_adjusted_start(None, "5m", 10) is None`,
   对 `self.start=None` 是显式安全的。实际路径里 start 通常不会是 None,
   但原代码的"假设"应该改为"契约"。

6. **`backtest.progress` payload shape 统一化** —— 前端
   `NotificationListener`  和 TUI 都依赖这个事件。在这次重构前,
   setup 阶段和 bar 阶段的两条发布路径字段集合差一个 `message` key,
   dedupe 逻辑里有隐式兼容。现在 10 个 key 每次都存在,
   `message` 默认 None,前端可以放心做 `payload.message` 访问。

7. **完整回归**: `.venv/bin/python -m pytest tests/` 从 601 个测试增至 677 个,
   全部通过(+76 pure helper 测试)。零回归。

**验证**:
- 677/677 pytest 全通过(`PYTHONPATH=src python3 -m pytest tests/`)
- 字节码编译检查全部通过(`py_compile` on 修改/新建的 3 个文件)
- `runner_helpers.py` 在 `sys.meta_path` blocker 下独立导入通过 —— 证实零 NT 依赖
- 检查 TODO/FIXME/XXX 注释清零
- runner.py 行数: 1244 → 1205 (-39 行,-3.1%)
- 新增 245 行纯 helpers + 508 行 NT-free 测试
- `_setup_engine` 内联 datetime/字符串/arithmetic 代码从 ~35 行降至 ~5 行

## 2026-04-16 (6)

**主题**: 将 `backtest/optimizer.py` 中的纯逻辑下沉到新建的 `optimizer_helpers.py`,并建立 Optuna/NT-free 的单元测试层(77 个测试,与上一轮 runner.py 同种打法)
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/optimizer_helpers.py` — 新建 476 行,收纳 11 个 Optuna-free / NT-free 纯函数 + 9 个具名常量 + 内联 `_norm_ppf` / `_norm_cdf`
- `src/tinohelm/backtest/optimizer.py` — 1115 → 793 行(-322 行,-28.9%),仅保留 Optuna 依赖(`_create_sampler`、`_PatienceCallback`)和 NT 依赖(`_run_backtest`、`BacktestOptimizer`)部分
- `tests/backtest/test_optimizer_helpers.py` — 新建 733 行,77 个 Optuna/NT-free 单元测试(此前 optimizer.py 零测试覆盖)

**动机**:

上一轮(2026-04-16 (5))把 `runner.py` 中的纯逻辑下沉到 `runner_helpers.py`,
建立 NT-free 测试层。本项目 `backtest/` 目录下剩下的"大文件、零测试、
混合依赖"问题最严重的就是 `optimizer.py`(1115 行):

1. **零测试覆盖** — 除 `runner`/`extract`/`sections` 之外的最后一个大文件,
   `tests/` 下没有任何 `test_optim*`/`test_walk*` 文件。其中包含:
   - `_split_dates` / `_walk_forward_windows` —— 日期切分和滚动窗口生成,
     边界条件多(`train_pct=100`、`n_folds=0`、`test_ratio≤0`、窗口钳到
     start/end 边界),错一个 off-by-one 就会让整个 WFO 失效
   - `_compute_dsr` —— Bailey & López de Prado (2014) Deflated Sharpe Ratio,
     数学密集,涉及方差、Euler-Mascheroni 常数、annualization、PSR denom
     正性判断 —— 任一步骤出错都可能产生看似合理实则错误的结果
   - `_compute_param_sensitivity` —— numpy 向量化直方图 + 2D heatmap 装配,
     quantile 重复(`len(bin_edges) < 2`)、max_pairs 限制、top-importance
     排序等逻辑复杂,写错一个 index 就是静默数据污染
   - `_compute_param_stability` —— best_params 附近半径内的 stdev,分母
     `max(abs(bv), 1e-9)` 的 epsilon 零除保护是关键,没有测试就容易被
     "优化掉"

2. **Optuna 依赖遮蔽测试执行** — `optimizer.py` 顶部 `try: import optuna`,
   但整个文件无法被"无 Optuna"CI 导入测试。虽然纯函数本身不用 Optuna,
   但它们被混在 Optuna-dependent 的 `BacktestOptimizer` 类中,
   测试就得 `try: import optuna; _HAS_OPTUNA = True` + `skipif`。
   把它们抽出来到 `optimizer_helpers.py` 可以彻底绕过这个限制。

3. **NT 依赖的传递性污染** — 原 `_compute_dsr` 使用
   `from tinohelm.backtest.result import _norm_ppf, _norm_cdf`,
   而 `result/__init__.py` 顶部 `from .extract import extract_backtest_results`,
   `extract.py` 又 `from nautilus_trader.backtest.engine import ...`。
   结果调用 `_compute_dsr` **运行时** 触发 NT import。为了让 helpers 模块
   无论导入时还是运行时都 NT-free,必须打破这条依赖链。

4. **FAIL_VALUE 哨兵值散落使用** — `-999.0` 在 `_compute_dsr` /
   `_compute_param_sensitivity` / `_compute_param_stability` 三处都有
   `t["value"] != _FAIL_VALUE` 的 filter 逻辑重复,抽出
   `filter_completed_trials()` 把"completed + 非 sentinel + 非 None"
   的三重检查变成一处。

5. **魔术数字** — 原代码 hardcode 的阈值(`< 5`、`< 10`、`< 3`、
   `1e-9`、`0.20`、`math.sqrt(252)`)散落各处,只有通过看代码才知道
   "为什么 5 而不是 3"。命名为 `DSR_MIN_TRIALS` / `SENSITIVITY_MIN_TRIALS`
   / `STABILITY_MIN_NEARBY` / `STABILITY_EPSILON` /
   `STABILITY_DEFAULT_THRESHOLD` / `TRADING_DAYS_PER_YEAR` 之后,
   意图显性化、测试可以直接引用常量做断言。

**要点**:

1. **新建 `optimizer_helpers.py`,导出 20 个符号** —— 9 个常量 + 11 个函数:
   - 常量:`FITNESS_METRICS`、`FAIL_VALUE`、`TRADING_DAYS_PER_YEAR`、
     `DSR_MIN_TRIALS`、`DSR_MIN_OBSERVATIONS`、`SENSITIVITY_MIN_TRIALS`、
     `STABILITY_MIN_NEARBY`、`STABILITY_DEFAULT_THRESHOLD`、`STABILITY_EPSILON`
   - 日期:`split_dates`、`walk_forward_windows`
   - Metric:`extract_fitness`
   - Smart defaults:`auto_n_trials`、`auto_sampler`、`auto_workers`
     (`auto_workers` 新增可注入 `cpu_count` 参数,便于测试时不依赖物理 CPU)
   - 装配:`slim_result`、`filter_completed_trials`(新抽,消重三处)
   - 鲁棒性:`compute_dsr`、`compute_param_sensitivity`、`compute_param_stability`
   - 内联:`_norm_ppf`、`_norm_cdf`(从 `result/statistics.py` 复制的
     15 行 Abramowitz & Stegun 近似公式,带显式 comment 标注去重理由和
     drift 检测契约)

2. **`optimizer.py` 变薄到 793 行** —— 顶部改为 `from optimizer_helpers
   import ... as _FAIL_VALUE, ... as _auto_n_trials, ...` 别名进口,
   20+ 个内部调用点 **零修改**。优势:
   - `api/routes/optimize.py` 里的 `from tinohelm.backtest.optimizer import
     _auto_n_trials, _auto_workers` **零影响**(backward compat 测试验证)
   - 仅保留 Optuna-dependent 和 NT-dependent 部分:`_create_sampler`、
     `_PatienceCallback`、`_run_backtest`、`BacktestOptimizer` 类、
     `run_optimization` 入口

3. **`test_optimizer_helpers.py` — 77 个 Optuna/NT-free 测试**:
   - `TestModuleIsolation` (2): 确认 optimizer_helpers 在**子进程**里
     import 后,`sys.modules` 里不出现 `optuna` / `nautilus_trader` /
     `sqlalchemy` / `redis` —— 子进程隔离避免当前进程被其他测试污染;
     常量数值锁定
   - `TestSplitDates` (5): 70%/0%/100% 拆分 / 单日 / 原始身份
   - `TestWalkForwardWindows` (8): 3 folds 正常 / n_folds=0 回退 /
     train_pct=100 回退 / train_pct>100 回退 / 单 fold / test 段不重叠
     / train 钳到 start / test 钳到 end
   - `TestExtractFitness` (12): 四种 objective / 未知 objective / 缺
     statistics / 缺 metric / None value / 非数值 / int 转 float /
     非 dict result / 非 dict statistics
   - `TestAutoDefaults` (13): n_trials 零维 / 单维 / 多维 / 50 floor;
     sampler CMA-ES / 3 维边界 / int 触发 TPE / 多维触发 TPE / 默认 float;
     workers 上界 4 / 下界 1 / 中间 / None 回落 os.cpu_count
   - `TestSlimResult` (4): None / 三 key 保留 / 缺 key 填 None / 空 dict
   - `TestFilterCompletedTrials` (5): 非 COMPLETE 过滤 / None value 过滤
     / sentinel 过滤 / 空 / 全通过
   - `TestComputeDsr` (8): trial 不足 / obs 不足 / None best_sharpe /
     零方差 / 幸福路径返回概率 + 4 位小数 / sentinel 过滤 / 负 denom
     返回 None / 可重现
   - `TestComputeParamSensitivity` (9): trial 不足 / 空 / 单参幸福路径
     单调增 / pair grid / 缺失参数静默跳过 / max_pairs 限流 / sentinel
     全过滤返回 None / 空 importances / grid 值 4 位小数
   - `TestComputeParamStability` (7): 空 best_params / 邻域不足 /
     幸福路径正 stdev + 4 位小数 / 零 best_param 用 epsilon / 缺 best
     key 的 trial 剔除 / 自定义阈值扩大邻域 / sentinel 剔除
   - `TestNormalApproximationsEquivalence` (2): 用 `importlib.util`
     直接加载 `result/statistics.py`(绕过 `result/__init__.py`
     的 NT 污染),比对 `_norm_ppf` / `_norm_cdf` 与我们内联副本逐点
     `abs_tol=1e-12` 等值 —— **把"复制粘贴的 drift 风险"变成显式契约**
   - `TestBackwardCompat` (1): 断言 `optimizer.py` 的
     `_FAIL_VALUE` / `FITNESS_METRICS` / 所有 `_`-前缀函数 `is`
     identity 等于 helpers 里的对应符号,保护
     `api/routes/optimize.py` 的私有 import 面

4. **`_norm_ppf` / `_norm_cdf` 内联的三重保护** —— 避开
   `result/__init__.py` 的 NT 污染是动机,但复制代码有 drift 风险。
   三层保护:
   - 复制处有明确 comment 指向原模块和公式(A&S 26.2.23 + `math.erf`)
   - `TestNormalApproximationsEquivalence` 两个测试用 `importlib.util`
     在测试时加载原始 `statistics.py`,**逐点数值比对** rel_tol=0,
     abs_tol=1e-12 —— 任一侧被修改都会立刻失败
   - 两份都是极短的闭式公式,没有隐式状态

5. **`filter_completed_trials` 抽取消重** —— 原代码三处各有一段:
   ```python
   valid = [t for t in trials if t.get("state") == "COMPLETE"
            and t.get("value") is not None
            and t["value"] != _FAIL_VALUE]
   ```
   现在 `filter_completed_trials(trials)` 一处。修改过滤规则时(比如
   将来支持更多的 trial state)只需改一处。

6. **`auto_workers(cpu_count=None)` 新增可注入** —— 原 `_auto_workers()`
   直接 `os.cpu_count()`,测试只能验证返回值在 `[1, 4]` 区间。
   新签名可注入 `cpu_count` 参数,`None` 时回落 os.cpu_count,
   让"1 核"、"4 核"、"16 核"的行为可以确定性单测。

7. **`extract_fitness` 增强类型防御** —— 原代码在 `result` 不是 dict
   或 `statistics` 不是 dict 时会抛 `AttributeError`。新版用 `isinstance
   (result, dict)` 和 `isinstance(stats, dict)` 先过滤,异常输入返回
   `FAIL_VALUE` 与其他"无效数据"路径一致。测试里显式覆盖两种边界。

8. **完整回归**: `PYTHONPATH=src python3 -m pytest tests/` 从 677 个测试
   增至 754 个,全部通过(+77 helpers 单测)。零回归。
   `api/routes/optimize.py` 未动,其 private import 通过 backward
   compat 别名继续 work。

**验证**:
- 754/754 pytest 全通过(`PYTHONPATH=src python3 -m pytest tests/`)
- 字节码编译检查全部通过(`py_compile` on 修改/新建的 3 个文件)
- `optimizer_helpers` 在 **子进程**下导入后 `sys.modules` 不出现
  `optuna` / `nautilus_trader` / `sqlalchemy` / `redis` —— 证实完全
  Optuna/NT-free(运行时 & 导入时)
- 逐点数值等价性验证:helpers 里的 `_norm_ppf` / `_norm_cdf` 与
  `result/statistics.py` 原版 abs_tol=1e-12 等值
- 行为等价性手测:`split_dates`、`walk_forward_windows` 对
  5 种 train_pct × 3 种 n_folds 组合全部 bit-for-bit 等于
  重新复刻的原版逻辑
- 检查 TODO/FIXME/XXX 注释清零
- optimizer.py 行数: 1115 → 793(-322 行,-28.9%)
- 新增 476 行纯 helpers + 733 行 Optuna/NT-free 测试
- 六次演进累积(backtest/ 三大文件):
  - `extract.py`:1500 → 942(-558 行)
  - `runner.py`:1244 → 1205(-39 行)
  - `optimizer.py`:1115 → 793(-322 行)
  - 合计:3859 → 2940 行(-919 行,-23.8%)
  - 对应三个 *_helpers.py 纯逻辑层累计 ~1165 行 + 1400+ 行
    NT/Optuna-free 测试(可在无 NT、无 Optuna 的 CI 上独立运行)


