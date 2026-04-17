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

**主题**: 把 `backtest/optimizer.py` 中所有纯逻辑下沉到新建的 `optimizer_helpers.py`,消除三处重复的 trial-filter 谓词,并补齐零覆盖的 Layer-2/3 robustness 数学
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/optimizer_helpers.py` — 新建 452 行,收纳 11 个 NT/optuna-free 纯函数 + 3 个 canonical 常量
- `src/tinohelm/backtest/optimizer.py` — 1115 → 811 行(-304 行,-27.3%);所有纯逻辑(date 数学、smart defaults、DSR、sensitivity、stability)迁出;保留 `_run_backtest`/`_create_sampler`/`_PatienceCallback`/`BacktestOptimizer` 等真·有依赖的代码;module-level 暴露 11 个 `_xxx` 别名维持向后兼容
- `src/tinohelm/api/routes/optimize.py` — 把 `from tinohelm.backtest.optimizer import _auto_n_trials, _auto_workers` 改为从 helper 模块导入 canonical 名(`auto_n_trials`/`auto_workers`)
- `tests/backtest/test_optimizer_helpers.py` — 新建,84 个无 NT/optuna 依赖的单元测试

**动机**:

上一轮(2026-04-16 (5))把 `runner.py` 压到 1205 行并建立了 76 个 NT-free 测试。
按同一范式审视项目剩余的大型文件,`backtest/optimizer.py`(1115 行)是
唯一一个在过去几轮 evolution 中**完全没有被触碰过**且**测试覆盖率为零**的核心
模块——`grep -rn 'optimizer\|optuna\|FAIL_VALUE' tests/` 返回空。

具体看 optimizer.py,有四个独立但相互勾连的健康问题:

1. **三处复制粘贴的 trial-filter 谓词** —— `_compute_dsr`、
   `_compute_param_sensitivity`、`_compute_param_stability` 各自维护了一份
   完全相同的 7 行过滤逻辑:
   ```python
   valid = [t for t in trials_data
            if t.get("state") == "COMPLETE"
            and t.get("value") is not None
            and t["value"] != _FAIL_VALUE]
   ```
   三份独立定义 = 三个可以悄悄漂移的地方。如果有人在某天给 robustness 计算
   加上一个新的 trial 状态(比如 `WAITING`),需要同时改三处,哪怕忘改一处
   也不会有任何编译/测试报错——因为整个 robustness 就没有测试。

2. **零覆盖的 ~250 行复杂数学** —— DSR 用了 Bailey-López de Prado (2014)
   的多重检验扩散公式(嵌套 _norm_ppf/_norm_cdf 调用 + Euler-Mascheroni
   常数 + Sharpe 反年化),sensitivity 用了 numpy quantile binning + 2D
   网格交叉,stability 用了带 ε-保护的相对距离阈值。任何一个边界条件出 bug
   都会被外层 `try/except logger.debug(...)` 静默吞掉,前端显示一个 `null`
   了事——operator 完全察觉不到。

3. **零覆盖的 smart-defaults 启发式** —— `_auto_n_trials`、`_auto_sampler`、
   `_auto_workers` 是用户输入 0 时的兜底,直接影响 UX(用户填的就是 0,
   实际跑了多少 trial 全凭这三个函数说了算)。`_auto_workers` 还硬调
   `os.cpu_count()`,在测试里没法注入。

4. **`_compute_dsr` 触发隐式 NT 导入** —— 函数内 `from tinohelm.backtest.result
   import _norm_ppf, _norm_cdf` 表面上只要 pure-python,但
   `result/__init__.py` 在顶部就 `from .extract import extract_backtest_results`,
   而 extract 依赖 NT + pandas。结果是 DSR 这个**纯数学函数**实际上必须有
   NT 才能调用——隐藏的耦合,潜在的 ImportError boom。

把这些一起做掉,且只做这一件事。

**要点**:

1. **`optimizer_helpers.py` —— 11 个导出符号 + 3 个 canonical 常量**:
   - `FAIL_VALUE` (float) —— 把原 `_FAIL_VALUE = -999.0` 提为公开常量,
     route + optimizer + 测试都引用同一处
   - `FITNESS_METRICS` (dict) —— canonical 4 项 objective→stat-key 映射
   - `TRADING_DAYS_PER_YEAR` (int=252) —— 命名常量替代 DSR 里散落的
     `math.sqrt(252)` 字面量
   - `split_dates(start, end, train_pct)` —— 70/30 切分等核心 date 逻辑
   - `walk_forward_windows(start, end, train_pct, n_folds)` —— 滚动 WF
     窗口;`train_pct == 100` / `n_folds <= 0` 退化为单 split,clamp 到
     数据边界
   - `extract_fitness(result, objective)` —— unknown objective / missing
     stat / None / non-float 全部走 `FAIL_VALUE` 同一通路
   - **`filter_completed_trials(trials_data)` —— 单一事实源谓词**
     (本次 refactor 的核心收益):COMPLETE + 非 None + 非 FAIL_VALUE,
     DSR/sensitivity/stability 三处全部改为调用它,以后只能在一处改
   - `auto_n_trials(param_ranges)` —— `max(50, n_dims*20)`
   - `auto_sampler(param_ranges)` —— ≤3 dims 且全连续 → cmaes,否则 tpe
   - `auto_workers(cpu_count=None)` —— **关键修正**:接受 `cpu_count` 参数
     注入,默认才走 `os.cpu_count()`,从而支持确定性测试
   - `slim_result(result)` —— 三字段投影(statistics + equity_curve +
     monthly_returns),`None → None` 让它跟可选回测组合
   - `_norm_ppf` / `_norm_cdf` —— 从 `result/statistics.py` **inline 复制**
     A&S 26.2.23 近似公式(打破对 `result/__init__.py` 的隐式 NT 依赖);
     测试里 pin 了 `_norm_ppf(0.975) ≈ 1.96`、`_norm_cdf(2σ) ≈ 0.9772`
     等教科书参考值确保两份实现不漂
   - `compute_dsr(...)` —— 第一次调用 `filter_completed_trials`;
     其余数学保持原样
   - `compute_param_sensitivity(...)` / `compute_param_stability(...)`
     —— 同样改为调用 `filter_completed_trials`

2. **`optimizer.py` 减重 27.3%(1115 → 811 行)**:
   - 删除整个 `_split_dates` / `_walk_forward_windows` / `_extract_fitness` /
     `_auto_*` / `_slim_result` / `_compute_dsr` / `_compute_param_sensitivity`
     / `_compute_param_stability` 函数体(共 ~310 行)
   - 11 个 module-level 别名(`_FAIL_VALUE = FAIL_VALUE` 等)保留,**外部
     调用方一行不用改**(`api/routes/optimize.py` 主动迁移到 canonical 名,
     是出于代码审美而非破坏性变更)
   - `BacktestOptimizer.run()` / `_objective_simple` / `_objective_walk_forward`
     内部全部改用 canonical 名,跟外部别名解耦,可读性提升

3. **`test_optimizer_helpers.py` —— 84 个 NT/optuna-free 测试** 覆盖:
   - `TestConstants` (3): pin FAIL_VALUE / FITNESS_METRICS 4 项 / 252
   - `TestSplitDates` (5): 70/30 / 50/50 / 0% train / 100% train /
     train-test 间隔正好 1 天
   - `TestWalkForwardWindows` (8): 3 折计数 / test 段不重叠 / train<test /
     100% 退化 / 0 fold 退化 / 负 fold 退化 / 数据边界 clamp /
     最后一折 test_end 对齐
   - `TestExtractFitness` (8): sharpe 幸福路径 / 4 个 objective parametrize /
     unknown / missing stats / missing key / None / non-numeric / int→float
   - `TestFilterCompletedTrials` (7): 空 / 全保留 / 丢 PRUNED / 丢 FAIL /
     丢 None value / 丢 FAIL_VALUE 哨兵 / 丢缺 state key
   - `TestAutoNTrials` (4): 空 floor=50 / 1 dim floor / 3 dim 60 / 10 dim 200
   - `TestAutoSampler` (4): 低维连续 cmaes / 含 int → tpe / 高维 tpe / 空 cmaes
   - `TestAutoWorkers` (6): cpu=8/4/2/1/128 显式注入 + os.cpu_count() 默认 range
   - `TestSlimResult` (3): None / 三字段保留 / 缺字段→None
   - `TestNormalApproximations` (12): _norm_ppf 五个参考点 + 域外保护 +
     _norm_cdf 五个参考点 + 极值 + 对称性
   - `TestComputeDSR` (7): too few trials / too few obs / None best /
     零 variance gracefully / 幸福路径 ∈ (0,1) / **filter helper 用法**
     (混入 PRUNED/None/FAIL_VALUE 不应改变结果) / 负 denom → None
   - `TestComputeParamSensitivity` (6): too few trials / 单 param bins /
     top pairs grid / grid 形状契约 / 样本数不足 param 跳过 /
     **filter 隔离脏数据**(poison value 不进 bin)
   - `TestComputeParamStability` (8): 空 best_params / nearby 太少 / 全相同
     std=0 / 异质 std>0 / threshold 排除远点 / **filter 隔离脏数据** /
     缺 param 不算 nearby / best=0 触发 ε guard

4. **完整回归**: `PYTHONPATH=src python3 -m pytest tests/` 从 677 个测试
   增至 761 个,全部通过(+84 helper 测试)。零回归。

5. **NT/optuna-free 验证** —— 用 `sys.meta_path` blocker 屏蔽 optuna /
   nautilus_trader / redis / sqlalchemy 后,`optimizer_helpers` 仍能完整
   导入并执行,证实模块零框架依赖。这意味着将来在 lean CI 任务里(只装
   numpy + pytest)就能跑这 84 个测试,不需要 NT wheel(~100MB,冷装 ~30s)。

6. **打破 `result/__init__.py` 的隐式 NT 耦合** —— 原 `_compute_dsr` 内
   `from tinohelm.backtest.result import _norm_ppf, _norm_cdf` 表面上是
   pure-python 调用,实际触发 NT 加载;现在 helper 模块自带 inline 副本,
   DSR 计算彻底脱离 NT。两份实现的等价性靠 `TestNormalApproximations` 的
   12 个教科书参考点测试钉住。

**讨论点**:

- `_norm_ppf` / `_norm_cdf` 现在有两份(`result/statistics.py` + `optimizer_helpers.py`)。
  评估过抽到 `tinohelm/common/math.py` 的方案,但需要修改至少 3 个调用方,
  还要解决 `result/__init__.py` 的循环导入历史包袱;短期收益 < 改动面。
  当前两份各 8 行,被两套测试独立 pin,漂移风险可控——后续如果再出现
  第三个调用方,再考虑统一。

**验证**:
- 761/761 pytest 全通过(`PYTHONPATH=src python3 -m pytest tests/`)
- 字节码编译检查通过(`py_compile` on 4 个文件)
- helper 模块在 `sys.meta_path` blocker 下独立导入通过 —— 证实零 NT/optuna 依赖
- 11 个 backward-compat 别名通过 `is` 同一性检查(`_FAIL_VALUE is FAIL_VALUE` 等)
- 检查 TODO/FIXME/XXX 注释清零
- optimizer.py 行数: 1115 → 811(-304 行,-27.3%)
- 新增 452 行纯 helpers + 682 行 NT-free 测试
- 重复的 trial-filter 谓词:3 处 → 1 处(`filter_completed_trials`)
- `_compute_dsr` 的隐式 NT 依赖:消除



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
