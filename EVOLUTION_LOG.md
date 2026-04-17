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

## 2026-04-17

**主题**: 从 `backtest/optimizer.py` 抽取纯逻辑至新建的 `optimizer_helpers.py`,建立 optuna-free 的单元测试层,并提取共享 `_math_primitives` 叶子模块以打破 `result/__init__.py` 的 NT/pandas 依赖链
**维度**: 架构重构 + 测试补齐 + 代码质量
**改动范围**:
- `src/tinohelm/backtest/optimizer_helpers.py` — **新建 600 行**,收纳 17 个 optuna-free + NT-free 纯函数/常量
- `src/tinohelm/backtest/_math_primitives.py` — **新建 40 行**,提供 `norm_cdf`/`norm_ppf` 的单一实现(标准库 only)
- `src/tinohelm/backtest/optimizer.py` — 1115 → 811 行(**-304 行,-27.3%**),所有 smart defaults/日期窗口/trial 过滤/DSR/灵敏度/稳定性助手下沉到新模块
- `src/tinohelm/backtest/result/statistics.py` — 将 `_norm_ppf`/`_norm_cdf` 改为 `from tinohelm.backtest._math_primitives import ...` 的 re-export(保持向后兼容,消除重复实现)
- `tests/backtest/test_optimizer_helpers.py` — **新建 831 行**,87 个 optuna-free 单元测试覆盖 13 个公共 helper + 3 个常量 + 1 个导入隔离契约

**动机**:

1. **optimizer.py 是最后一个未被拆分的大文件**(1115 行)。上两轮(2026-04-16 (4)/(5))
   把 `extract.py` 压缩到 942 行、`runner.py` 压缩到 1205 行并建立了可在无 NT 环境下
   运行的 pure helper 测试层。optimizer.py 包含:
   - **9 个纯函数** (400+ 行):`_split_dates`/`_walk_forward_windows`/
     `_extract_fitness`/`_auto_n_trials`/`_auto_sampler`/`_auto_workers`/
     `_slim_result`/`_compute_dsr`/`_compute_param_sensitivity`/
     `_compute_param_stability` —— 只是因为埋在 1115 行的 Optuna orchestration 里
     而无法独立测试
   - **三处重复的"filter valid trials"推导式** —— 在 DSR / sensitivity / stability
     三个函数中各写了一遍 `[t for t in trials if state == COMPLETE and value != FAIL_VALUE]`,
     shape/语义完全一致,但没有 single source of truth
   - **两处 Redis JSON payload 构造字面量** —— `running` 和 `completed` 分支
     分别写了 6 键字典,字段集合**不完全一致**(`completed` 默认没有显式 `status`
     默认值,easy 漂移点)
   - **零单元测试** —— DSR 的 Bailey & López de Prado 公式、参数灵敏度的 2D 分位数
     分箱、稳定性的 ±threshold 邻域判断,这些数学都没有测试保护,数值回归很容易溜走

2. **`backtest/result/__init__.py` 的 NT/pandas 污染**。`optimizer_helpers` 最初尝试
   `from tinohelm.backtest.result.statistics import _norm_cdf, _norm_ppf`,
   但 Python 包语义要求先执行 `result/__init__.py`,而那个文件 eagerly import
   `extract.py`,而 `extract.py` import `nautilus_trader` 和 `pandas`。
   这意味着任何 pure helper 要复用 `_norm_cdf`/`_norm_ppf` 都会被迫拉 NT + pandas,
   完全违背"可在 lean CI 下测试"的初衷。

**要点**:

1. **新建 `_math_primitives.py` 叶子模块** —— 标准库 only,40 行,提供
   `norm_cdf(x)` 和 `norm_ppf(p)` 两个 Abramowitz & Stegun 近似实现。
   `statistics.py` 改为从这个叶子模块 re-export 为带下划线的别名
   (`_norm_cdf`/`_norm_ppf`),维持现有测试和 `sections.py` 的向后兼容。
   这样新的 `optimizer_helpers.py` 就可以 `from tinohelm.backtest._math_primitives import norm_cdf, norm_ppf`
   而不触发 `result/__init__.py` 的加载链。

2. **新建 `optimizer_helpers.py`,17 个公共导出符号**:
   - **常量**: `FAIL_VALUE`、`FITNESS_METRICS`、`DSR_COMPATIBLE_OBJECTIVES`
     (新增 frozenset,取代之前散落的 `== "sharpe"` 字符串字面量)
   - **Smart defaults**: `auto_n_trials`/`auto_sampler`/`auto_workers(cpu_count=None)`
     /`auto_patience(n_trials, min_patience=10, divisor=4)` —— `auto_workers`
     把之前硬编码的 `os.cpu_count()` 改为可注入参数,单测不再需要 monkeypatch
   - **日期窗口**: `split_dates`、`walk_forward_windows`(移除前导下划线 publicize,
     行为严格等价)
   - **新 helper**: `build_wf_fold_result(fold_index, train_start, ..., test_value)`
     —— 把 optimizer.py 里 8 行的 "WF fold dict 字面量构造" 抽成一行声明式调用,
     并锁定 `fold` key 是 1-based(用户可见编号)而内部索引是 0-based
   - **Metric 提取**: `extract_fitness(result, objective, *, fail_value=FAIL_VALUE)`
     —— 支持注入 fail_value,None result 和空 statistics 都安全返回 FAIL_VALUE
   - **Trial 过滤(消重)**: `is_valid_trial(trial)` + `filter_valid_trials(trials)`
     —— 把三处重复的 `COMPLETE + not None + != FAIL_VALUE` 过滤统一为一处,
     并开放 `fail_value` 参数便于测试自定义 sentinel
   - **结果整形**: `slim_result(result)` —— IS 验证 payload 的 3-key 裁剪
   - **Redis 事件(消重)**: `build_progress_event(optimization_id, *,
     trials_completed, total_trials, best_value, best_params, status="running")`
     —— 单一 canonical payload,6 键每次都存在,取代两处散落字面量。
     同时显式 `dict(best_params)` 拷贝,避免调用方后续 mutate 污染已发布 payload
   - **DSR**: `compute_dsr(best_sharpe, trials_data, skewness, kurtosis, n_obs, *,
     fail_value=FAIL_VALUE, trading_days=252)` —— 把 `trading_days=252`
     提升为参数(文档化假设),使用 Euler-Mascheroni 命名常量而非 magic `0.5772...`
   - **灵敏度**: `compute_param_sensitivity(trials_data, param_ranges, param_importances,
     *, n_bins=10, max_pairs=3, min_trials=10, fail_value=FAIL_VALUE)` ——
     内部拆分为 `_quantile_bin_edges`/`_digitize_inside`/`_bin_mean_histogram`/
     `_pair_grid` 四个辅助,三处重复的"np.percentile + np.digitize + bin centers"
     模式统一。`min_trials` 现在可注入,之前硬编码为 `< 10` 的门槛拒绝了很多合理
     的小搜索空间
   - **稳定性**: `compute_param_stability(trials_data, best_params, *,
     threshold=0.20, min_neighbours=3, fail_value=FAIL_VALUE)` ——
     增加 `min_neighbours` 参数(之前硬编码为 3),并用 `try/except TypeError`
     gracefully 处理非数值参数(例如 string 参数)而不是崩溃

3. **`optimizer.py` 消重与声明式化** ——
   - 删除 200+ 行内联 helper 定义(`_split_dates`/`_walk_forward_windows`/
     `_extract_fitness`/`_slim_result`/`_compute_dsr`/
     `_compute_param_sensitivity`/`_compute_param_stability` 等)
   - Redis progress/completion 两处 `json.dumps({...})` 字面量统一为
     `json.dumps(build_progress_event(...))`,key 集合强一致
   - WF fold 字典字面量替换为 `build_wf_fold_result(...)`
   - `_FAIL_VALUE` 全部改为从 helper 导入的 `FAIL_VALUE`(保留 module-level
     `_FAIL_VALUE = FAIL_VALUE` alias 供外部兼容)
   - `self.fitness_objective == "sharpe"` 的 DSR 门槛改为
     `in DSR_COMPATIBLE_OBJECTIVES`,未来扩展到其他 ratio 只改一处常量
   - `_auto_patience` 的硬编码三元式 `if patience <= 0 and n_trials >= 40:
     patience = max(10, n_trials // 4)` 改为 `auto_patience(n_trials)` 调用,
     逻辑从 3 行散落指令压缩为 1 行 + 1 个"0 不改变"哨兵判断
   - 净变化: 1115 → 811 行(-304 行,**-27.3%**)。nine 演进累积:
     optimizer 从未被拆分 → 今天首次拆分,是本轮最大的代码质量提升

4. **`test_optimizer_helpers.py` — 87 个 optuna-free 单元测试** 覆盖:
   - `TestOptimizerHelpersIsolation` (1): 在 `sys.meta_path` 安装 blocker 并
     `importlib.reload` `optimizer_helpers`,断言 `optuna` 和 `nautilus_trader`
     都不在 `sys.modules` 中 —— **契约测试**,防止未来意外拉入依赖
   - `TestConstants` (3): FAIL_VALUE/FITNESS_METRICS/DSR_COMPATIBLE_OBJECTIVES
   - `TestAutoNTrials` (3): 空 / 低维 floor / 线性 scaling
   - `TestAutoSampler` (5): cmaes 低维 / int 降级 / 高维降级 / 空空间 / 缺 type key
   - `TestAutoWorkers` (5): 1 核 / 64 核 / 中等核 / monkeypatch os.cpu_count /
     None cpu_count 退化
   - `TestAutoPatience` (3): 阈值下关闭 / 线性 scaling / floor
   - `TestSplitDates` (4): 80/20 / 50/50 / 0 train pct / tuple shape
   - `TestWalkForwardWindows` (7): 0 fold 回退 / 100% train 回退 / 5 fold 不重叠 /
     boundary clamp / train < test / list-of-tuple / 单 fold
   - `TestBuildWfFoldResult` (3): 完整 shape / 1-based fold / FAIL_VALUE 保留
   - `TestExtractFitness` (11): 4 objective 幸福路径 / None result / missing
     statistics / None value / missing key / 非数值 / int 转 float / 自定义
     fail_value
   - `TestTrialFiltering` (7): is_valid 5 分支 + filter 2 变体
   - `TestSlimResult` (3): 3 key 裁剪 / 缺 key 默认 None / None 输入
   - `TestBuildProgressEvent` (5): running shape / completed shape /
     key set 一致性 / best_params 拷贝防护(mutate src 后 payload 不变)/
     默认 status
   - `TestComputeDsr` (8): <5 trials / <5 obs / None best / 常数 trials 降级为
     PSR / 有方差 happy path / 忽略 invalid trials / negative denom /
     4 小数四舍五入
   - `TestComputeParamSensitivity` (8): too_few / single_param / grid /
     max_pairs 限制 / 排除 invalid trials / 常数 param dropped / 值四舍五入 /
     min_trials 可注入
   - `TestComputeParamStability` (8): 空 best_params / 太少 neighbours /
     happy path / zero best value / 排除 invalid / threshold 收窄邻域 /
     缺 param key / 4 小数四舍五入 / 非数值 graceful

5. **完整回归**: NT-free 测试从 360 → 447 个(+87),全部通过。`.venv/bin/python
   -m pytest tests/backtest/` 核心 pure helper 子集 382 → 469 个,零回归。
   `test_sections.py`(133 个依赖 statistics.py 隔离加载)和
   `test_rolling_metrics.py`(35 个依赖 statistics.py 文件路径加载)
   在 `_math_primitives` 分离后继续全通过 —— 证明 re-export 改动向后兼容。

6. **意外副作用:`auto_patience` 语义微调** —— 原代码在 `n_trials >= 40`
   时自动设置 patience,< 40 时保持 0(不启用)。新 helper `auto_patience(30) = 0`
   明确编码这个边界,`auto_patience(40) = 10` 命中 floor。调用点改为
   `if patience <= 0: auto_p = auto_patience(n_trials); if auto_p > 0: patience = auto_p`,
   保证与旧行为严格等价(< 40 trials 时不设 patience,保持 0 即禁用)。

**验证**:
- 447 NT-free pytest 全通过(`PYTHONPATH=src /tmp/venv/bin/python -m pytest tests/backtest/test_optimizer_helpers.py tests/backtest/test_rolling_metrics.py tests/backtest/test_runner_pure_helpers.py tests/backtest/test_sections.py tests/core/ tests/portfolio/test_config.py tests/data/test_converters.py tests/data/test_converter_stubs.py tests/strategy/test_pause_support.py tests/strategy/test_state.py tests/node/test_entry_points.py`)
- 字节码编译检查全部通过(`py_compile` on 5 个修改/新建文件)
- `optimizer_helpers.py` 在 `sys.meta_path` blocker(block NT + optuna + pandas)
  下独立导入通过 —— 证实零重依赖
- 检查 TODO/FIXME/XXX 注释清零
- optimizer.py 行数: 1115 → 811 行(**-304 行,-27.3%**)
- 新增: 600 行纯 helpers + 40 行 math 叶子 + 831 行 optuna-free 测试
- 消除 2 处 Redis JSON payload 字面量重复(progress + completion 现在共享 shape)
- 消除 3 处 "filter valid trials" 推导式重复(DSR / sensitivity / stability)
- 消除 3 处 `_norm_ppf`/`_norm_cdf` 的实现(现在单一 source of truth 在
  `_math_primitives.py`,statistics.py 和 optimizer_helpers.py 都 re-export)
- optuna/numpy/NT blocker 契约测试 — future proof


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


