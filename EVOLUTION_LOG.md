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

**主题**: 把 `data/pipeline.py` 中的纯逻辑下沉到新建的 `pipeline_helpers.py`,统一三处语义漂移的 `_WRITE_CATEGORY.get()` 兜底,补齐零覆盖的进度数学/日期边界/Vision 文件名解析
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/data/pipeline_helpers.py` — 新建 259 行,导出 9 个纯函数 + 4 个 canonical 映射(用 `MappingProxyType` 锁不可变)+ 2 个进度带常量
- `src/tinohelm/data/pipeline.py` — 892 → 858 行(-34 行,-3.8%);删除 ~100 行内联逻辑替换为 ~65 行声明式调用;打破对 `datetime`/`timezone` 的直接依赖(下沉到 helpers)
- `src/tinohelm/api/routes/data.py` — 把 `from tinohelm.data.pipeline import _WRITE_CATEGORY` 改为 `from tinohelm.data.pipeline_helpers import resolve_db_category`,API 端点更新到 canonical 名
- `tests/data/test_pipeline_helpers.py` — 新建,90 个无 NT/pandas/sqlalchemy/httpx 依赖的单元测试

**动机**:

按照过去三轮 evolution(`runner_helpers`、`optimizer_helpers`、`extract.py` sections)
的成功范式审视项目剩余未触碰的大文件,`data/pipeline.py`(892 行)是
**整个 data 子系统的核心枢纽**——FastAPI 路由 + BacktestRunner 子进程 +
data worker 后台任务三条路径都直接依赖它,但它本身有六个重叠的健康问题:

1. **三处语义漂移的 `_WRITE_CATEGORY.get(data_type, ???)` 兜底**——同一个
   字典查找在三个调用点用了**三个不同的 default**:
   - `_write_objects` (line 517): `... "custom"` —— 用作 catalog writer 派发,
     未知类型回退到 `"custom"` 然后 logger.warning + skip
   - `_clean_overlapping_parquet` (line 723): `... )` (no default → None)
     —— 只关心 `"bar"` / `"trade_tick"` 两个分支,其余走 `else: return`
   - `_update_db_catalog` (line 856): `... data_type` —— 把原 type 名直接
     塞进 DB 的 `data_type` 列,保持记录可发现
   - `api/routes/data.py:671`: `... dt` —— 同上,但显式拷了一份
   四份独立的兜底语义,没有名字、没有测试、注释里也没说明意图。任何一份悄悄
   漂移都不会有任何 lint/类型/测试报错。这是典型的"用不同 magic value 表达
   同一组业务规则"。

2. **三处复制粘贴的进度百分比公式**——`5 + round(85 * x / total_tasks)`
   出现 3 次(`_download_one`/`_chunk_cb`/`_convert_consumer`),
   `chunks / (chunks + 2)` 内插一次,`78 + int(12 * (csv_idx + 1) / len(csv_paths))`
   再来一次。`5` 和 `85` 是无名 magic number,为什么取这两个数字、
   后面的 `92`/`96`/`100` 各自代表什么阶段——全部需要从 caller 上下文反推。

3. **三处复制粘贴的"date → ns UTC 边界"装配**——
   `int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1_000_000_000)`
   出现两次(`_clean_overlapping_parquet` 起止边界);`datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)`
   再出现两次(REST fallback 的 start_dt/end_dt)。"end 是 d+1 天的零点"
   这种半开区间约定散落在多处,没有一个共享的命名。

4. **零覆盖的 Vision 文件名解析逻辑**——`_detect_vision_coverage_end` 内联了
   34 行 stem→date 解析(daily 末三段 → ISO date,monthly 末两段 → 当月最后
   一天,12 月特殊处理跨年减一天),只有 4 个高层 mock 测试覆盖,其中
   "无效月份"/"零月份"/"非数字"/"empty stem"/"空 granularity" 等边界都没有。
   一旦 Binance 改命名约定,我们要靠生产爆掉才知道。

5. **顶层 `_klines_fetch_map = {...}` dict literal 在 `_rest_fallback` 函数
   体内**——每次调用都重新构造一个 dict,合理位置应该是模块级常量。

6. **`_detect_header` 把"判定文本头"和"打开文件读首行"耦合在一起**——
   核心判定 `not first[0].isdigit()` 一句话,但写在一个 file-I/O 包装里,
   测试要么 mock 文件要么写真文件,无法直测判定本身。

把这 6 个问题一起做掉,且**只做这一件事**。

**要点**:

1. **新建 `pipeline_helpers.py` —— 13 个导出符号(9 函数 + 4 映射)+ 2 常量**:
   - `WRITE_CATEGORY` / `INTERVAL_CONVENTION` / `KLINES_REST_FETCH_FN` ——
     用 `MappingProxyType` 包成不可变映射(测试 pin 了 `TypeError on __setitem__`)
   - `REST_FALLBACK_TYPES` —— `frozenset`,canonical 单一事实源
   - `DOWNLOAD_PROGRESS_BASE = 5` / `DOWNLOAD_PROGRESS_SPAN = 85` ——
     给 magic number 起名字,文档化"剩余 10% 留给 REST fallback + DB catalog"
   - **`resolve_write_category(dt) -> str`** —— 统一第一种语义,unknown → `"custom"`
   - **`resolve_db_category(dt) -> str`** —— 统一第二种语义,unknown → 输入本身
   - **`resolve_db_interval(dt, interval) -> str`** —— 三层优先级(显式 >
     convention > "tick"),空字符串作"未提供"处理(原代码 `interval if interval`
     的隐式行为现在显式化)
   - `is_rest_fallback_supported(dt) -> bool` —— 谓词形式,语义自描述
   - `compute_stage_pct(done, total, *, base=5, span=85) -> int` —— 单一进度
     公式;`total <= 0` 返回 base 而不是崩溃;`done` 自动 clamp 到 [0, total]
   - `compute_chunk_subprogress(stage_done, total, chunks, *, base, span) -> int`
     —— 把 `chunks/(chunks+2)` 内插封装,保证返回值严格 < 下一个 stage
   - `date_start_dt(d)` / `date_end_dt(d)` —— `datetime` 边界,end = d+1 天零点
   - `date_start_ns(d)` / `date_end_ns(d)` —— ns 边界,通过 `*_dt` 复用
   - `parse_vision_coverage_end(granularity, stem) -> date | None` —— 完全
     pure,接受 primitive 入参便于测试
   - `csv_has_header(first_line) -> bool` —— pure 谓词,空字符串/纯空白都
     正确返回 False(原代码的边界)

2. **`pipeline.py` 减重 3.8%(892 → 858 行)**:
   - 三处 `5 + round(85 * x / total_tasks)` → `compute_stage_pct(x, total_tasks)`
   - `_chunk_cb` 内的两条 base/next 行 + 内插 + clamp 共 5 行 → `compute_chunk_subprogress(...)` 一行
   - `_clean_overlapping_parquet` 的 8 行 datetime 装配 → `date_start_ns` / `date_end_ns` 两行
   - `_rest_fallback` 的 5 行 dt 装配 + 6 行 `_klines_fetch_map` literal →
     `date_start_dt` / `date_end_dt` + module 常量 `KLINES_REST_FETCH_FN` 引用
   - `_detect_header` 从 5 行(打开 + 读 + 字符判定)缩为 3 行,核心判定下沉
   - `_detect_vision_coverage_end` 从 34 行缩为 5 行,完全委托给 `parse_vision_coverage_end`
   - `_update_db_catalog` 的两行 `_WRITE_CATEGORY.get(..., data_type)` /
     `_INTERVAL_CONVENTION.get(..., "tick")` → `resolve_db_category` /
     `resolve_db_interval` 两个**自描述**的命名调用
   - 顶部 `_REST_FALLBACK_TYPES = REST_FALLBACK_TYPES` / `_WRITE_CATEGORY = WRITE_CATEGORY` /
     `_INTERVAL_CONVENTION = INTERVAL_CONVENTION` 三个 backward-compat 别名保留,
     避免破坏任何外部反射式访问

3. **`api/routes/data.py` 主动迁移到 canonical 名**——把
   `from tinohelm.data.pipeline import _WRITE_CATEGORY`
   改为 `from tinohelm.data.pipeline_helpers import resolve_db_category`,
   `db_category = _WRITE_CATEGORY.get(dt, dt)` 改为 `db_category = resolve_db_category(dt)`。
   API 端点契约不变(返回 JSON shape 一致),意图更清晰。

4. **`test_pipeline_helpers.py` —— 90 个 NT/pandas/sqlalchemy/httpx-free 测试**:
   - `TestModuleIsolation` (2): 模块身份 + **源码扫描禁用 import**(防止有人
     未来把 pandas/NT 加进 helpers 破坏 lean CI 假设)
   - `TestCanonicalMappings` (8): 三个映射的 `TypeError on __setitem__` + 全部
     canonical key/value pin + 进度带常量
   - `TestResolveWriteCategory` (5): 已知/未知/空字符串
   - `TestResolveDbCategory` (5): 已知/未知/空字符串/**与 write_category 的语义
     差异显式锁定**(`"exoticType"` 一个 → `"custom"`,另一个 → `"exoticType"`)
   - `TestResolveDbInterval` (7): 显式优先 / convention 兜底 / "tick" 兜底 /
     空字符串当 missing / klines 边界
   - `TestRestFallbackSupport` (13): parametrize 6 supported + 7 unsupported
   - `TestComputeStagePct` (11): zero done / total≤0 / 完整完成 / 1/2/1/3/1/4
     边界 / negative clamp / over-total clamp / custom band / 返回 int
   - `TestComputeChunkSubprogress` (7): 严格小于 next slice / chunks→∞ 渐近
     next-1 / 0 chunks 等价 1 chunk / 完整完成无内插空间 / total=0 / chunk
     单调性 / **公式精确 pin**(0,2,1=19, 0,2,2=26, 0,2,4=33,把 banker's
     rounding 的微妙性钉死)
   - `TestDateBoundaryHelpers` (8): UTC midnight / next-day / 月边界 / 年边界
     / 已知 epoch 数值 / start↔end 差恰好 86400s / 返回 int / 闰日支持
   - `TestParseVisionCoverageEnd` (16): daily aggTrades / daily klines (含
     interval token)/ 无效日期 / 太少段 / monthly 各月 / 闰年 2 月 / 12 月
     跨年减一天 / 1 月 / 月份 13 / 月份 0 / 非数字 / 太少段 / 未知 granularity
     / empty stem / empty granularity
   - `TestCsvHasHeader` (7): 文本头 / 数字数据 / **负数边界**(`"-1.5"` 当
     成 header,这是当前规则,显式锁定让任何未来变更都是有意识的)/ 空 /
     纯空白 / tab 分隔 / 字母列
   - `TestCrossReferenceWithPipeline` (1): pipeline.py 的 backward-compat
     别名 `_WRITE_CATEGORY is WRITE_CATEGORY` 等同性

5. **完整回归**: 在无 NT 的 lean CI 镜像下跑
   `PYTHONPATH=src python3 -m pytest tests/data/test_pipeline*.py` —— 110/110
   全过(`test_pipeline.py` 20 + `test_pipeline_helpers.py` 90)。
   全套 `tests/` 跑下来,通过项 +90(就是新加的 helper 测试),
   失败项数量与改动前完全相同(35 项,均为环境无 NT 导致,与本次改动无关)→
   **零回归**。

6. **NT/pandas/sqlalchemy/httpx-free 验证** —— 用 `sys.meta_path` blocker
   屏蔽 nautilus_trader / pandas / sqlalchemy 后,`pipeline_helpers` 仍能
   完整导入并执行所有函数(已运行 smoke 验证,8 个核心 helper 调用全部通过)。
   这意味着将来在 lean CI 任务里(只装 pytest)就能跑这 90 个测试,
   不需要任何重型依赖。

**讨论点**:

- 三个 backward-compat 别名 `_REST_FALLBACK_TYPES = REST_FALLBACK_TYPES` 等
  目前没有外部消费者(grep 全仓只剩 pipeline.py 自身和 CLAUDE.md 文档引用)。
  保留是为了避免未来有人引用而不是出于已知需求。下一轮可以考虑 `dep deprecation`
  注释,再下一轮删除。

- `csv_has_header` 对 `"-1.5"` 返回 `True`(因为 `-` 不是 digit)。这并不是
  Vision CSV 实际遇到的形态(数据行第一字段都是非负整数 epoch ms),但既然
  helper 是 pure 的,这个边界值得显式锁定测试。如果未来有 source 用负数开头,
  helper 需要扩展到 `first_char.isdigit() or first_char in "-+"`。

- `compute_stage_pct(1, 2)` 因 banker's rounding 返回 47 而不是 48。生产
  路径里 progress 整数显示给用户,46/47/48 之间的视觉差异为零,不影响行为;
  测试已经把这个隐藏的 Python 行为显式 pin 住,任何切换到 `math.ceil` 或
  `int(round(...))` 都会被测试检出。

**验证**:
- `PYTHONPATH=src python3 -m pytest tests/data/test_pipeline.py tests/data/test_pipeline_helpers.py` —— 110/110 全过
- `PYTHONPATH=src python3 -m pytest tests/ --ignore=tests/actors --ignore=tests/node` —— 通过 +90 全部为新增,失败项与改动前完全一致(35 项均为本机无 NT,改动前后完全相同)
- 字节码编译检查通过(`py_compile` on 4 个文件)
- `pipeline_helpers` 在 `sys.meta_path` blocker 下独立导入并执行成功 —— 证实零 NT/pandas/sqlalchemy/httpx 依赖
- 所有受影响文件 `grep -n 'TODO\|FIXME\|XXX'` 返回空
- pipeline.py 行数: 892 → 858(-34 行,-3.8%);**内联非声明式代码**从 ~110 行降至 ~10 行(剩下的都是必要的异步编排和 I/O)
- 新增 259 行纯 helpers + 513 行 NT-free 测试
- 三处语义漂移的 `_WRITE_CATEGORY.get(..., ???)` 调用 → 三个命名 helper(`resolve_write_category` / `resolve_db_category` / `resolve_db_interval`)
- 三处重复的进度公式 → 一个 `compute_stage_pct` + 一个 `compute_chunk_subprogress`
- 三处重复的 date→ns UTC 装配 → 一对 `date_start_ns` / `date_end_ns` + `_dt` 变体
- 内联 `_klines_fetch_map` dict literal → module 级 canonical `KLINES_REST_FETCH_FN`

