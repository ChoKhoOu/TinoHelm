# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-16 (6)

**主题**: 把 `backtest/optimizer.py` 中的纯逻辑下沉到新建的 `optimizer_helpers.py`,锁定优化结果 schema 契约,并为之前完全没有测试的优化器建立首条 NT-free 单元测试基线
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/backtest/optimizer_helpers.py` — 新建 604 行,导出 20 个符号(2 常量 + 17 纯函数 + 1 状态机类)
- `src/tinohelm/backtest/optimizer.py` — 1115 → 766 行(-349 行,-31.3%);所有"日期分割 / fitness 提取 / 智能默认 / DSR / 参数敏感度 / 参数稳定性 / payload 装配 / 早停状态机"被抽出后,本文件只剩 Optuna study 编排 + Redis 发布 + DB 持久化 + 共享引擎管理
- `src/tinohelm/api/routes/optimize.py` — 把 `_auto_n_trials` / `_auto_workers` 私有 import 切换到 `optimizer_helpers.auto_n_trials` / `auto_workers` 公共名
- `tests/backtest/test_optimizer_helpers.py` — 新建 862 行,102 个 NT-free / Optuna-free 单元测试

**动机**:

1. **`optimizer.py` 是项目里测试覆盖率最低的大文件** —— 1115 行,
   零测试,负责回测策略调优(walk-forward 滚动窗口、并行 trial、剪枝、
   早停、参数重要性 / 敏感度 / 稳定性 / Deflated Sharpe Ratio)。
   一旦回退,影响面覆盖整条优化产品线,但完全没有 CI 兜底。
2. **DSR 与参数敏感度的数学非常脆弱** —— `_compute_dsr` 内嵌 Bailey & López
   de Prado (2014) 的退化分支(trials < 5 / n_obs < 5 / sr_var ≤ 0 /
   denom_sq ≤ 0),任何一个分支条件被改坏都会让 DSR 静默退回 None
   或抛 NaN。`_compute_param_sensitivity` 用 numpy 做分位数 binning
   + 二维网格,边界(空 trials、单 bin、并列 importance)无任何测试。
3. **重复的 "filter completed trials" 模式** —— 同样的过滤逻辑
   (`state == COMPLETE and value is not None and value != _FAIL_VALUE`)
   在 `_compute_dsr`、`_compute_param_sensitivity`、`_compute_param_stability`
   重复了 3 次,任何一次"漏掉一个 fail_value sentinel"都会污染下游统计。
4. **Redis publish payload 漂移** —— `objective()` 进度发布(running 状态)
   和 `run()` 末尾完成发布(completed 状态)各自手写一份字面量 dict,
   字段集合一致但顺序和构造方式不同。前端 `NotificationListener`
   再次面临"两个 shape"的隐式契约问题(跟上一轮 runner.py 同样的反模式)。
5. **`_PatienceCallback` 状态机和 Optuna 类型耦合** —— 真正纯的 "no-improve
   counter + threadlock" 逻辑被锁在 `__call__(study, trial)` 里,
   trial.value 一行的判断逻辑无法独立测试,本身却是优化早停最关键的
   决策点。
6. **结果 JSON schema 契约缺乏锁定** —— `full_result` 字段集合(16 个
   顶层 key,前端 OptimizationDetail 全部依赖)是 `run()` 末尾一段
   ~60 行的内联 dict 字面量,任何一次重命名都不会触发任何告警。
   上一轮(extract.py)成功用 `test_result_schema.py` 锁定 backtest
   结果 schema,这一轮对 optimization 结果做同样的事。

**要点**:

1. **`optimizer_helpers.py` — 20 个导出符号** —— 全部纯函数 / 数据结构,
   零 NT、零 Optuna、零 Redis、零 SQLAlchemy 依赖:
   - 常量: `FAIL_VALUE`、`FITNESS_METRICS`(从 `optimizer.py` 平移,公共名)
   - 日期: `split_dates`、`walk_forward_windows`(逐字平移,加 docstring)
   - 提取: `extract_fitness(result, objective, *, fail_value)` —— 新增
     `result is None` 安全分支(老版本会抛 AttributeError),
     `fail_value` 关键字注入便于自定义
   - 智能默认: `auto_n_trials`、`auto_sampler`、`auto_workers(cpu_count=None)`、
     `auto_patience(n_trials)` —— `auto_workers` 把 `os.cpu_count()` 调用
     可注入化,`auto_patience` 是新抽取的(原来内联在 run() 里)
   - 装配: `slim_result`、`build_progress_payload`、`build_full_result`、
     `build_walk_forward_fold_record` —— 新增 `build_progress_payload`
     强制保证 7 个 key(含 `message`)始终存在,弥合 running / completed
     两个发布路径的隐式 shape 差异
   - 重复消除: `filter_completed_trials(trials, *, fail_value)`、
     `select_best_params(trial_params, param_ranges)` —— 把 3 处重复的
     "filter completed" 和 2 处重复的 "filter best params keys" 收敛
     到单一实现
   - 适配: `serialize_trial(trial)` —— duck-typed Optuna `FrozenTrial`
     → primitive dict,只要求 `.number / .params / .value / .state.name`,
     可用 `SimpleNamespace` 在测试里构造
   - 状态机: `PatienceTracker(patience)` + `.observe(value) -> bool` ——
     从 `_PatienceCallback` 中提取的纯 "no-improve counter",
     线程安全,完全无 Optuna 依赖
   - 数学: `compute_dsr(*, ..., norm_ppf, norm_cdf)`、
     `compute_param_sensitivity`、`compute_param_stability` ——
     `compute_dsr` 把 `_norm_ppf` / `_norm_cdf` 改为依赖注入,
     避免 helper 模块依赖 `tinohelm.backtest.result.statistics`
     (原来是 `from tinohelm.backtest.result import _norm_ppf, _norm_cdf`
     的局部 import —— 现在 helper 完全不知道 statistics 模块的存在)。
     `compute_param_sensitivity` 引入 `min_trials=10` 关键字,
     之前是写死的字面量。

2. **`optimizer.py` 编排化** —— 1115 → 766 行(-349 行,-31.3%):
   - `objective()` 内的进度发布:从 12 行字面量 dict 缩为
     `json.dumps(build_progress_payload(...))` 一行
   - `run()` 末尾的完成发布:同样收敛
   - DSR 计算块:从 11 行嵌套 `try` + 局部 import 缩为 13 行
     `compute_dsr(..., norm_ppf=_norm_ppf, norm_cdf=_norm_cdf)` 调用
     —— statistics 的局部 import 仍然在 `optimizer.py` 内,helper 模块
     不引入这个依赖
   - `_PatienceCallback` 从 30 行嵌套 lock 状态机缩为 14 行 thin shim
     (内部委托 `PatienceTracker`)
   - `full_result` 装配:从 56 行字面量 dict 缩为 19 行 `build_full_result(...)`
     声明式调用
   - walk-forward 每折记录:从 9 行字面量 dict 缩为 5 行
     `build_walk_forward_fold_record(...)`
   - 抽取 `_cleanup_shared_engine()` 私有方法,消除 3 处 try/except 重复
   - 保留向后兼容的私有名(`_FAIL_VALUE`, `_split_dates`, `_walk_forward_windows`,
     `_extract_fitness`, `_auto_n_trials`, `_auto_sampler`, `_auto_workers`,
     `_slim_result`, `_compute_dsr`, `_compute_param_sensitivity`,
     `_compute_param_stability`)作为 helper 公共名的别名

3. **`routes/optimize.py` 切换到公共 API** —— 之前直接 import 私有
   `_auto_n_trials` / `_auto_workers`,改为 import `optimizer_helpers.auto_n_trials`
   / `auto_workers`。其他 8 个私有名仍保留 alias 是为了不破坏外部测试代码
   (如果有的话),但是新代码不应该再用它们。

4. **修复了一个潜在 AttributeError** —— 老版本 `_extract_fitness`:
   ```python
   stats = result.get("statistics", {})  # 假设 result 不为 None
   ```
   如果 `_run_backtest` 返回 None(理论上不会,但防御性),老代码会抛
   `AttributeError: 'NoneType' object has no attribute 'get'`。
   新版本的 `extract_fitness(None, ...) → FAIL_VALUE`,显式 None-safe。

5. **102 个 NT-free 单元测试** —— `tests/backtest/test_optimizer_helpers.py`:
   - `TestSplitDates` (5): 50% / 70% / 0% / 100% / 端点保留
   - `TestWalkForwardWindows` (8): 折数正确 / 测试段不重叠 / 训练在测试前 /
     0/负 folds fallback / 100% train fallback / train_start clamp / test_end clamp /
     端点不超
   - `TestExtractFitness` (10): parametrize 4 个对象的幸福路径 + 6 个边界
     (unknown / missing stats / None stats / None value / 不可解析 /
     None result / int 转 float / 自定义 fail_value)
   - `TestAutoNTrials` (4): 0/1/3/10 维
   - `TestAutoSampler` (6): 低维 float / 三 float / 四 float / int / 混合 / 空
   - `TestAutoWorkers` (7): parametrize 6 个 CPU 数 + 0 CPU 边界
   - `TestAutoPatience` (5): parametrize 3 个低于阈值 + at-threshold + large + floor
   - `TestSlimResult` (3): 三字段保留 / None / 缺失键变 None
   - `TestBuildProgressPayload` (5): 7 keys / message 默认 None / 显式 message /
     best_params copy / running vs completed 同 shape
   - `TestSerializeTrial` (3): 基础 trial / state 字符串 / params dict copy
   - `TestFilterCompletedTrials` (3): 过滤 5 种状态 / 空 / 自定义 fail_value
   - `TestSelectBestParams` (3): 过滤到 ranges keys / 空 ranges / trial 缺 param
   - `TestBuildWalkForwardFoldRecord` (2): ISO 格式 + 1-based / 第一折 = 1
   - `TestBuildFullResult` (8): 顶层 key 严格匹配(无 WF)/ WF 添加 /
     WF 省略 / 周期 ISO / DSR/sensitivity 默认 None / DSR 透传 /
     列表深拷贝(防 mutation 串流)/ best_params 深拷贝
   - `TestPatienceTracker` (6): 首次 observe / 改进重置 / 累计停止 /
     None value 算非改进 / 等值非改进 / -inf 初始
   - `TestComputeDsr` (7): 太少 trials / 太少 obs / None best_sharpe /
     0 方差 / 过滤 fail_value 与 PRUNED / 正常路径 / denom_sq 退化
   - `TestComputeParamSensitivity` (7): 太少 trials / 返回 single+grid /
     每参数子键 / 顶 pair / max_pairs limit / grid 必需键 / 过滤失败 trial
   - `TestComputeParamStability` (6): 空 best_params / 太少邻居 / 正常 std=1.0 /
     过滤失败 trial / 缺 param 的邻居被排除 / best=0 不发生 ZeroDiv

   全部不依赖 NT / Optuna / Redis / SQLAlchemy,可在标准 Python 3.11+
   环境下运行。

6. **NT-free 验证** —— `optimizer_helpers.py` 在 `sys.meta_path` blocker
   下完整加载,且加载后 `sys.modules` 不含任何
   `nautilus_trader / optuna / redis / sqlalchemy` 模块,
   证实零依赖契约。

7. **完整回归**: `.venv/bin/python -m pytest tests/` 从 677 个测试增至 779 个,
   全部通过(+102 helper 测试)。零回归。

**验证**:
- 779/779 pytest 全通过(`PYTHONPATH=src python3 -m pytest tests/`)
- 字节码编译检查全部通过(`py_compile` on 4 个修改/新建文件)
- `optimizer_helpers.py` 在 `sys.meta_path` blocker 下独立导入通过
- 检查 TODO/FIXME/XXX 注释清零
- optimizer.py 行数: 1115 → 766(-349 行,-31.3%)
- 新增 604 行纯 helpers + 862 行 NT-free 测试
- routes/optimize.py 切换到公共 API,无私有 import

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


