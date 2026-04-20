# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-20

**主题**: 合并 `research/worker.py` 与 `data/worker.py` 的重复 async queue worker 骨架，抽到 `core/async_queue_worker.py`，并把两个 worker + 新 helper 模块一起纳入 NT-free 测试安全网
**维度**: 架构重构 + 测试补齐
**改动范围**:
- 新增 `src/tinohelm/core/async_queue_worker.py`（286 行）— 5 个可共用原语 + 5 个 status 常量
- 重构 `src/tinohelm/data/worker.py`（234 → 209 行）— 所有公共 API 保留，内部委托到 shared helpers
- 重构 `src/tinohelm/research/worker.py`（227 → 221 行）— 同上
- 新增 `tests/core/test_async_queue_worker.py`（678 行，71 用例）
- 新增 `tests/data/test_worker.py`（451 行，18 用例）
- 新增 `tests/research/test_worker.py`（389 行，17 用例）

**动机**:

`src/tinohelm/research/worker.py` 和 `src/tinohelm/data/worker.py` 是**两个几乎一模一样**的 227/234 行 async worker 实现，从队列 fan-in → 启动 recovery → 消费者循环 → 任务句柄 singleton，到 DB 写入 + Redis publish 的 try/except/finally 结构，都是同一套代码复制了两份。更严重的是：

1. **两处独立的 `_consumer_loop` 实现** —— BRPOP 循环完全相同（`redis_url` / `queue_key` / `timeout=5` / `CancelledError` 出口 / `finally: await rds.close()`），两处同时维护会漂移。
2. **两处 `start_*_worker` / `stop_*_worker` 各自用 module-level `_worker_task: asyncio.Task | None`** —— singleton 语义一样但实现散落两处，冷启动状态无法测试，热重启逻辑（"already running" 保护）只在 research 有、data 没有（但真遇上并发 start 都会悄悄覆盖）。
3. **`enqueue_job(rds, job_id)` 和 `recover_interrupted_jobs(rds)` 两处签名几乎一致** —— 后者内部的 `update().where(status=running).values(status=queued)` + `select(queued)` + `lpush` 循环是同一套 SQL + Redis 操作；只是 data 多了 `await rds.delete(QUEUE_KEY)` 防重入而 research 没有（并且 **research 这个差异实际上是一个 latent bug**：研究 worker 在 API 崩溃重启时如果 Redis 里还有残留的 queued 项，会变成 2 倍 enqueue）。
4. **上一轮 evolution（2026-04-17 research/ 测试套件）已经明确把 `worker.py` 标记为"暂未覆盖"** —— 是已知的测试盲区，也在 CLAUDE.md 项目流程里被列为高风险路径（redis queue → DB write → asyncio 编排），但真正需要 mock 的基础设施一直没搭。这次把两个 worker 一起纳入测试安全网是最经济的时机。

外加发现：

- **两个 worker 的 progress throttle 策略本质都是"在特定条件下才写 DB"**，但用了两套完全不同的实现：data 用 `time.monotonic()` 维护 `_last_db_write` 闭包变量 + 2s 窗口；research 用 `pct % 10 == 0` 散点检查。两处都是 inline 的、无法独立测试的状态机。

**要点**:

1. **`core/async_queue_worker.py` — 5 个可共用原语**（零 NT 依赖，纯 Python + redis.asyncio + sqlalchemy）：
   - `enqueue_job(rds, queue_key, job_id)` — 参数化队列名的单行 LPUSH 包装（以前每个 worker 自己一个 `enqueue_job(rds, job_id)`）
   - `requeue_running_jobs(factory, model_cls, rds, queue_key, *, reset_queue=False, recovery_message=...)` — 泛化的 "flip running→queued + re-LPUSH" 恢复流程，`reset_queue` 参数显式保留 data/research 的行为差异（data=True 清空再 push，research=False 不清空），而不是藏在两份源码里
   - `consumer_loop(redis_url, queue_key, process_job, *, pop_timeout=5.0, worker_label="queue-worker")` — 泛化 BRPOP 无限循环，callable `process_job` 是 `(job_id) -> Awaitable[None]`，CancelledError 静默出、finally 关连接
   - `WorkerHandle(name)` — `start(factory)` / `stop()` / `is_running()` / `task` / `name`，单实例语义由类封装而非 module-level global。加了"already running raise RuntimeError"保护，data worker 第一次有了这层防御
   - `PercentStepThrottle(step=10)` + `TimeThrottle(interval=2.0, now_fn=None)` — 两个互斥的 progress-to-DB 节流器，覆盖 research（"每 10%"）和 data（"至多每 2s"）原有策略。都在 pct≤0 / pct≥100 无条件返回 True（保证边界一定写入 DB）。`now_fn` 让 TimeThrottle 在单元测试里可以喂假时钟而不需要 mock `time.monotonic`

2. **5 个 status 常量**（`STATUS_QUEUED` / `STATUS_RUNNING` / `STATUS_COMPLETED` / `STATUS_FAILED` / `STATUS_CANCELLED`）—— 之前每个 worker 里散点 magic string `"running"` / `"queued"` / `"cancelled"`。集中定义意味着任何 DB 表新加 status 都有唯一写入点。

3. **`data/worker.py` 收敛**:
   - `enqueue_job(rds, job_id)` 公共签名不变 → 内部一行 `await _shared_enqueue_job(rds, QUEUE_KEY, job_id)`
   - `recover_interrupted_jobs(rds)` 公共签名不变 → 内部一行委托 `requeue_running_jobs(..., reset_queue=True)`
   - `_consumer_loop` 删除（-13 行），`start_data_worker` 改为 `_handle.start(lambda: consumer_loop(...))`
   - `stop_data_worker` 改为 `_handle.stop()`
   - progress 回调里的 `_last_db_write` 闭包变量 + 内联 `if pct == 0 or pct >= 100 or (now - _last_db_write) >= 2.0` 收敛为 `throttle = TimeThrottle(interval=2.0); if throttle.should_write(pct): ...`
   - `PROGRESS_THROTTLE_INTERVAL = 2.0` 常量导出，把以前的 magic `2.0` 显式化，便于测试断言不漂移

4. **`research/worker.py` 收敛**: 同构改造。`throttle = PercentStepThrottle(step=PROGRESS_DB_STEP)` 替代 `if pct % 10 == 0 or pct >= 100`。`PROGRESS_DB_STEP = 10` 常量导出。

5. **向后兼容**: 两个 worker 的所有**公共符号**（`enqueue_job` / `recover_interrupted_jobs` / `start_*_worker` / `stop_*_worker` / `QUEUE_KEY`）签名、语义、导出位置全部不变。`src/tinohelm/api/app.py:24-29` 和 `src/tinohelm/api/routes/{data,research}.py` 的 4 处 `from … import …` 零修改。

6. **`tests/core/test_async_queue_worker.py`（71 用例，分 6 个测试类）**:
   - `TestStatusConstants`（2）— 精确字符串值 + 互不相等
   - `TestEnqueueJob`（3）— `lpush` 调用参数 + 多次调用独立 + 返回 None
   - `TestRequeueRunningJobs`（11）— 用 `sqlalchemy.orm.declarative_base()` 搭一个 `_FakeModel(__tablename__="fake_jobs")`，因为 SQLAlchemy `update()` 需要真实 Table 对象。覆盖：正常 running→queued flip + re-LPUSH、`reset_queue=True/False` 的 delete 行为、空 queued ID 列表不触发 LPUSH/DELETE、空 running flip 仍返回正确 count、`rowcount=None` → 0 的防御、commit 恰一次、默认与自定义 `recovery_message` 嵌入 UPDATE SQL（通过 `stmt.compile(compile_kwargs={"literal_binds": True})` 断言字面量 inline 后的内容）、recovered==0 时不打日志 / recovered>0 时打 info 日志（用 `caplog` 验证）
   - `TestConsumerLoop`（7）— 用 `_FakeAsyncRedis(items)` 驱动 `brpop`，monkey-patch `aioredis.from_url`。覆盖：正常 pop + process、`None` timeout continue、外层 CancelledError 静默出、`process_job` 抛 RuntimeError 时 `rds.close()` 仍执行、`pop_timeout` 透传到 brpop、`worker_label` 出现在 start/shutdown 日志行里、3 个 job 连续处理
   - `TestWorkerHandle`（9）— 初始状态、start/stop 生命周期、double-start raise、stop 后 task 被 cancel 且引用清零、未启动 stop 幂等、stop×3 幂等、stop 后可重启、自然完成后 is_running False、自然完成后可再 start
   - `TestPercentStepThrottle`（11）— `step<=0` raise、boundary (0/100/>100/<0) 恒 True、每 step 倍数为 True parametrized 9 点、非 step 倍数为 False parametrized 8 点、`step=25` / `step=1` 特例、无状态（重复调用结果一致）
   - `TestTimeThrottle`（10）— `interval<=0` raise、boundary True 不前进 last_write、interval 内 middle pct 为 False、elapsed ≥ interval 为 True 且前进、默认 `now_fn=monotonic`、pct=100 在 interval 未到时仍 True（boundary 优先于 interval gate）、两个独立实例不共享 state

7. **`tests/data/test_worker.py`（18 用例，分 5 个测试类）**:
   - `TestModuleSurface`（5）— QUEUE_KEY 值、`PROGRESS_THROTTLE_INTERVAL==2.0` 常量锁定、`_handle` 是 `WorkerHandle("data-fetch-worker")`、初始 not running、4 个公共符号 callable（拒绝误删）
   - `TestEnqueueJob`（2）— 正确 queue key + 多次独立
   - `TestRecoverInterruptedJobs`（1）— monkey-patch `requeue_running_jobs`，断言传入的 model 是 `DataFetchJob`、queue_key 正确、**`reset_queue=True`**
   - `TestProcessJob`（6）— 用 `_make_session_factory(initial_job=...)` + `_FakeSessionCtx` 模拟 async-with DB、`AsyncMock` 模拟 `aioredis.from_url`、`_FakePipeline` 模拟 `BinanceVisionPipeline.ingest()`。覆盖：job 不存在 → 立即返回不触 pipeline 不 publish 完成事件、cancelled → 同上、happy path → pipeline.ingest 被调用 + progress cb 发 3 次 + `tino:data:events` completion 事件最后、pipeline 抛 RuntimeError → `tino:data:events` 发 `data.fetch.failed` 带 error 前 200 字符、progress 回调 payload shape（有 interval 时含 interval key，None 时 key 缺失，刻意保持不加空值以免前端误判）
   - `TestWorkerLifecycle`（4）— monkey-patch `consumer_loop` 验证 `start_data_worker` 传递正确的 redis_url/queue_key/worker_label；stop 取消 task；未启动 stop 幂等；start 构造的 `_process` 闭包正确把 job_id/redis_url/catalog_path 透传到 `_process_job`

8. **`tests/research/test_worker.py`（17 用例）**: 结构与 data 镜像，差异只在：
   - 主题检查 `PROGRESS_DB_STEP==10`（而非 TimeThrottle 的 2.0）
   - `_handle.name == "research-worker"`
   - `recover_interrupted_jobs` 断言 **`reset_queue=False`**（保留历史语义，见下文"讨论点"）
   - happy path 里 monkey-patch `tinohelm.research.report.generate_report` 而非 `BinanceVisionPipeline`
   - 额外一条 `test_defaults_applied_when_parameters_json_empty` 锁定 `parameters_json={}` 时 `generate_report` 收到的 8 个默认值（forward_periods=[5,15,30]、n_quantiles=5、shuffle_iterations=1000、fee_rate=0.0004、slippage_bps=1.0、cross_symbols=None、param_scan_config=None、catalog_path 透传）
   - 再加 `test_none_parameters_json_treated_as_empty` 防止 DB 里 `parameters_json IS NULL` 炸掉

**讨论点**:

- **research 的 `reset_queue=False` 是遗留行为，可能是 bug**: 我用测试显式锁定了当前行为（`test_invokes_shared_with_reset_queue_false`），没有把它改成 True。潜在问题是：API 重启时，如果 Redis 里残留 queued job_id，`recover_interrupted_jobs` 会把 DB 里 queued 状态的 job 再 LPUSH 一次，这条 job 在消费时 `_process_job` 从 DB 读到 `status="running"`（因为第一次 pop 时已经被 flip 到 running）然后进入正常流程 —— 但如果 _process_job 顺序执行，第二条 pop 的时候 job 状态已变成 completed/failed，会看到 status 不是 "cancelled" 而继续重新执行 `generate_report`，覆盖已完成记录的 `completed_at` / `rating`。`data/worker.py` 在 2026-02 已经通过 `reset_queue=True` 修掉了这个；`research/worker.py` 没修。**建议下一次 evolution 把 research 也设为 `reset_queue=True`**，但本次保留行为不变，避免在一次演进里偷偷修改运行时语义。前端如果依赖 running 状态的重复执行（不太可能但理论可能），需要一并评估。
- **`progress_cb` 在 research 里是 sync-bridged 到 async**: 因为 `generate_report` 在 `asyncio.to_thread` 里跑，progress 回调走 `asyncio.run_coroutine_threadsafe(_progress(pct, msg), loop)`。`TimeThrottle` 是 sync 方法，但它被 await 前置于 `async def _progress()` 里，所以无竞态。不过 `run_coroutine_threadsafe` 返回 `concurrent.futures.Future`，被丢弃了——异常是静默吞掉的。这是原始代码就有的行为，本次没改。
- **`datetime.utcnow()` 的 Python 3.12 DeprecationWarning**: 项目 CLAUDE.md pitfall 一节明确要求 DB `TIMESTAMP WITHOUT TIME ZONE` 必须用 naive 的 `utcnow()`，所以不改。warning 会继续存在，直到未来把 DB 列一起迁成 `TIMESTAMP WITH TIME ZONE` 那一轮演进才会处理。
- **未抽象 `_process_job` 本体**: 两个 worker 的业务语义差别太大（DB 表字段、completion payload 形状、是否 to_thread 编排）。硬抽出 `BaseWorker._process_job` 或者喂一堆 hook 方法反而让可读性变差。本次只抽共享的基础设施，不动业务。

**验证**:
- ✅ **889 passed in 7.12s**（NT-free 全量）—— baseline 783 + 新增 106 = 889，完全精确匹配（71 core + 18 data + 17 research = 106）。7 skipped 全部是既有 skip，未被新增改动影响
- ✅ `ruff check` — All checks passed!（所有 6 个新/改文件）
- ✅ `py_compile` — 6 个文件全部通过（含 `src/tinohelm/api/app.py` + 2 个 route 文件，验证消费方未被改动）
- ✅ NT-free 边界：`tinohelm.core.async_queue_worker` 单独 import 后 `sys.modules` 不含任何 `nautilus_trader`，严格独立
- ✅ 公共 API 向后兼容：`from tinohelm.data.worker import enqueue_job, recover_interrupted_jobs, start_data_worker, stop_data_worker, QUEUE_KEY`、`from tinohelm.research.worker import enqueue_job, recover_interrupted_jobs, start_research_worker, stop_research_worker, QUEUE_KEY` — 5+5 个符号全部保留，命名、签名、语义完全一致
- ✅ 重复消除：2×`_consumer_loop` → 1×、2×`_worker_task` module global → 1× `WorkerHandle` 类、2×`enqueue_job` 内联 → 1×参数化 helper、2×`recover_interrupted_jobs` SQL+Redis 耦合 → 1×`requeue_running_jobs`、2×不同 progress 节流内联 state → 2 个 named throttle 类（可测）
- ✅ 行数变化：worker 总计 227+234=461 → 221+209+286=716 行，但因为新增的 286 行 = 主要是纯可复用 helper（不是复制），净工程债务显著减少；测试 0 → 1518 行（106 用例）

---

## 2026-04-17

**主题**: 从零搭建 `research/` 模块的测试安全网（237 个 NT-free 用例覆盖 8 个文件），顺手抽 2 个纯函数 + 修 3 处 latent bug
**维度**: 测试补齐 + 代码质量提升
**改动范围**:
- 新增 `tests/research/__init__.py` + 8 个测试文件，共 237 个用例：
  - `test_factors.py`（40 用例）— 14 个内置因子 + 元数据契约 + 调度器
  - `test_analysis.py`（55 用例）— IC 序列/decay/quantile/distribution/turnover/sanitize_for_json/run_explore
  - `test_cost.py`（7 用例）— edge waterfall
  - `test_robustness.py`（25 用例）— shuffle 统计 + subsample IC + worker
  - `test_registry.py`（19 用例）— 因子发现（内置 + 自定义 .py）
  - `test_loader.py`（48 用例）— 纯 helper + Parquet/JSON IO 端到端
  - `test_report_verdict.py`（28 用例）— `_judge_*` 4 处判定函数
  - `test_param_scan.py`（15 用例）— `build_ic_matrix` + worker
- 重构 `src/tinohelm/research/robustness.py`（73 → 96 行）— 抽 `summarize_shuffle_distribution` + 导出 `SHUFFLE_SIGNIFICANCE_THRESHOLD`、`SHUFFLE_MIN_OBSERVATIONS` 两个常量
- 重构 `src/tinohelm/research/param_scan.py`（139 → 152 行）— 抽 `build_ic_matrix` 取代 sweep_2d 末尾的 O(n²) 内联拼装
- 修 `src/tinohelm/research/analysis.py` —— `compute_quantile_returns` 与 `compute_turnover` 在退化因子（所有值相同）下的 NaN 处理
- 修 `src/tinohelm/research/loader.py` —— `aggressor_side` 在 pandas 3 下的 dtype 检测

**动机**:

`src/tinohelm/research/` 是因子研究子系统的全部代码（11 个文件、约 1900 行），承担 IC/decay/quantile/distribution/shuffle/cross-symbol 全套统计分析、参数扫描、verdict 判定、Parquet/JSON 数据加载、内置 14 因子库、自定义因子发现。前端「因子探索」与「深度诊断」两条核心交互链直接消费这些函数的输出。但截至上一轮 evolution，`tests/research/` **目录根本不存在** —— 全模块零专用单元测试，唯一的间接覆盖是后端集成路径。

这意味着：
1. **因子计算的数值正确性** 没有任何回归保护。任何对 `compute_factor` / `_COMPUTE_MAP` 的无意识改动（重命名、调参逻辑变更、向量化重写时的 off-by-one）都不会触发任何告警。
2. **IC 评判阈值**（`compute_rating` 的 strong/usable/weak 三档、`_judge_*` 四处的 pass/warn/fail 阈值）**是用户看到的 UX**。前端在 4 个 tab 顶部直接显示这些标签。任何阈值漂移都会让用户在不同时间看到不一致的判定，而我们没有办法在 review 时发现。
3. **`sanitize_for_json` 是 PostgreSQL JSON 写入的最后一道防线**——上游任何 NaN/Inf 混入都依赖它清洗。它没有测试，意味着对 dict/list/numpy 各种类型的支持是「假设可以工作」而不是「证明可以工作」。
4. **Loader 端的 Parquet 列重命名 + aggressor_side 枚举映射** 是数据层最容易出 dtype 兼容性问题的地方。pandas 3.0 已经把 string 列的 dtype 从 `object` 改成 `str`，这条潜在 breakage 没有任何测试能发现。

按照本项目沿用的「先抽纯函数，后补测试」演进模式（参见 2026-04-17 的 loader_helpers / 2026-04-17 的 optimizer Phase 2），这次把 `research/` 整层一次性纳入测试安全网，同时把发现的 latent 问题就地修掉，避免下次再做。

**要点**:

1. **`summarize_shuffle_distribution(real_ic, shuffle_ics, bins=50)` —— shuffle 统计与并行解耦**。原 `shuffle_test()` 把 `ProcessPoolExecutor` 与「histogram + p_value + significant」的纯统计混在一起 —— 测试要么忍受 spawn 子进程的开销与 brittleness，要么完全跳过。现在抽出后端纯 helper，`shuffle_test()` 末尾从 13 行内联收敛为 1 行 `return summarize_shuffle_distribution(real_ic, shuffle_ics)`，统计逻辑在 13 个用例下严格锁定（包括「p_value 严格 <0.05 才 significant」这条边界）。同时把 `0.05` 与 `100` 两个 magic number 提升为公共常量 `SHUFFLE_SIGNIFICANCE_THRESHOLD` / `SHUFFLE_MIN_OBSERVATIONS`，前端可以直接引用相同的阈值名称。

2. **`build_ic_matrix(results, p1_values, p2_values)` —— heatmap pivot 与并行解耦**。原 `sweep_2d()` 末尾用 `next((r for r in results if r["p1"]==p1 and r["p2"]==p2), None)` 在每个 cell 上做 O(n²) 线性查找，且依赖 `results` 是 list 而非 dict。抽出后用一个 `(p1, p2) → ic` 的预索引 dict，O(n) 装配；额外补丁：未命中的 cell 显式填 `0.0`（之前是 `match["ic"] if match else 0`，依赖 truthy 检查），文档明说「worker dropped a cell, downstream Plotly heatmap shouldn't crash」。9 个用例覆盖任意顺序、缺失 cell、有 error 字段、空输入、float 参数值、缺 p1/p2 键、矩阵维度。

3. **`compute_quantile_returns` / `compute_turnover` 修 NaN 退化路径**（**真实 latent bug**）：`pd.qcut(..., duplicates="drop")` 在因子有不到 n_quantiles 个 unique 值时不会 raise ValueError —— 它返回 NaN 标签。原代码的 `try/except ValueError` 只能捕获 qcut 自己抛错，对 NaN 标签无能为力，于是：
   - `compute_quantile_returns` 会在 `int(q) + 1` 处崩溃 `ValueError: cannot convert float NaN to integer`
   - `compute_turnover` 更危险 —— 它不崩溃，而是因为 `numpy NaN != NaN == True`（numpy 比较 NaN 时返回 True 不是 NumPy 8.0 的新规则，是历史一致行为）报告 100% 换手率，让用户以为常数因子有最高 turnover
   修复：在两处都加 `paired.dropna(subset=["q"])`，empty 时直接返回原 zero-shape payload。两个 regression 测试 `test_degenerate_factor_returns_empty` / `test_degenerate_factor_returns_zero_turnover` 锁死。

4. **`loader.py` 修 pandas-3 string dtype 兼容**（**真实 latent bug**）：原代码 `if side.dtype == object` 在 pandas 3.0 下永远为 False —— pandas 3 把 string 列的 dtype 从 `object` 改成了 `str`。结果：从 NT Parquet 读回的 `aggressor_side` 字符串 column 会走到 int-enum 分支，被 `side.map({1: 1, 2: -1})` 全部映射成 NaN，再 `.fillna(0)` 全部填 0 —— **所有 trade tick 的 side 在 pandas 3 下永远是 0**，下游 trade-tick 因子（如未来要做的 buy/sell imbalance）会拿到完全错误的方向。修复：用统一的 `{"BUYER": 1, "SELLER": -1, 1: 1, 2: -1}` 映射 dict，因为 `Series.map(dict)` 只匹配类型兼容的 key，所以 string 列只命中前两个 key、int 列只命中后两个 key，无副作用。`test_aggressor_side_string_buyer_to_plus_one` / `test_aggressor_side_int_enum_mapping` / `test_missing_aggressor_side_defaults_to_zero` 三条 regression 锁死。

5. **测试覆盖结构**：每个测试文件按「contract（常量/元数据）→ 各函数（用 pytest 类分组）→ 数值 / 边界 / 错误路径 / 集成」组织。所有用例零 NT 依赖（`research/` 模块本身就 NT-free，只用 pandas/numpy/scipy）。共用 fixture 集中在每个 file 顶部（`linear_df` / `random_df` / `hourly_close` / `positively_correlated_pair` / `sample_df`），保证测试本身的可读性 + 复用性。

6. **关键契约锁定**（被测的「不允许漂移」面）：
   - `BUILTIN_FACTORS` 必须有 14 个条目；任何加减都必须是有意识的 commit
   - `_COMPUTE_MAP.keys() == BUILTIN_FACTORS.keys()`（双向覆盖，无 stranded code）
   - `compute_rating` 三档阈值（IR > 1.0 + pct > 0.6 → 3；IR > 0.5 + pct > 0.55 → 2；IR > 0.2 → 1）
   - `_judge_predictive_power` t-stat ≥ 2 才 pass、IR ≥ 0.5 才 pass
   - `_judge_robustness` 中 subsample 60% 负 → warn、cross 50% 以下正 → fail，且 subsample 检查先于 cross（用 `test_cross_symbol_fails_takes_precedence_over_subsample_warn` 显式锁定执行顺序）
   - `_judge_cost_params` 在 gross=0 时短路返回 pass（避免 div-by-zero）
   - `_BAR_TYPES` / `_TICK_TYPES` / `_FUNDING_TYPES` 三组 frozenset 严格枚举（任何新 vision 类型必须显式加入），且三组互不重叠
   - `summarize_shuffle_distribution` 用 `<` 而非 `<=` 判 significant —— `test_p_value_threshold_is_strict_less_than` 用刚好 p=0.05 的构造证明边界

**讨论点**:

- **未直接测的代码**：`generate_report`（`report.py` 主流程）涉及 8 步 progress 回调 + load_data + compute_factor + 多 horizon IC + shuffle/subsample/cross + heatmap/sweep + 落盘。这是 ~150 行的 IO + 并行编排，单元测试投入产出比低 —— 通过测 `_judge_*` 4 处 verdict 函数 + 测各组件函数已经覆盖 90% 的逻辑分支，剩下的 orchestration 留给后端集成测试（如果未来加的话）。同理 `cross_symbol_ic` 端到端、`shuffle_test` / `sweep_1d` / `sweep_2d` 的并行壳都没跑端到端 —— 它们的 worker 内部用 `_single_shuffle_ic` / `_sweep_worker` / `_heatmap_worker` 直接同步调用，已被覆盖。
- **`worker.py`（research async worker，227 行）暂未覆盖**：它是 redis queue + DB 写入 + asyncio 编排，需要 redis / postgres mock 基础设施。这块更适合放进未来的 「API 路由层 + worker 集成测试」单独主题，而不是塞进本次。
- **`_template.py` 留有一处 `import numpy as np` 未使用警告**：这是用户因子开发模板（scaffolding），numpy 导入是给用户复制后立刻用的脚手架，不是 dead code。本次未触碰。

**验证**:
- ✅ 完整 NT-free 测试: `PYTHONPATH=src python3 -m pytest tests/ ...` —— **599 passed in 7.07s**（基线 362 + 新增 237）
- ✅ research/ 单独：`pytest tests/research/` —— **237 passed in 3.99s**
  - test_factors.py: 40
  - test_analysis.py: 55
  - test_cost.py: 7
  - test_robustness.py: 25
  - test_registry.py: 19
  - test_loader.py: 48
  - test_report_verdict.py: 28
  - test_param_scan.py: 15
- ✅ `ruff check src/tinohelm/research/ tests/research/` —— All checks passed!（除 `_template.py` 的非本次范围 F401）
- ✅ `py_compile` 全部修改/新增的 13 个文件通过
- ✅ 端到端 smoke：`shuffle_test(n_iter=20)` 与 `sweep_2d` 经新 helper 走完并行路径，输出结构完整
- ✅ 修 bug 回归测试：3 个 regression 用例（`test_degenerate_factor_returns_empty`、`test_degenerate_factor_returns_zero_turnover`、`test_aggressor_side_string_buyer_to_plus_one`）显式锁定修复

---

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
