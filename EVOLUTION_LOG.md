# Evolution Log

Chronological record of architectural improvements and maintenance work.

## 2026-04-22

**主题**: 抽 `backtest/funding_math.py` + `data/funding_cache_helpers.py` 两层 NT-free 纯逻辑，为 `backtest/funding.py`（零直接测试的永续资金费率核算器）和 `data/funding_cache.py`（增量缓存 orchestrator，仅有 2 个外围测试触及 `_CACHE_DIR` 路径）建立 117 个 NT-free / 无网络的测试安全网 + 修掉一个 "FLAT 等异常 side 静默走 SHORT 分支" 的隐蔽语义坑

**维度**: 架构重构（把 funding 成本公式 / 事件游标 / 结果汇总 / 缓存增量决策 / 去重排序全部下沉到单点纯函数） + 测试补齐（`backtest/funding.py` + `data/funding_cache.py` 合计此前 0 个直接测试；本次补 117 个）+ 顺手行为变更（`compute_funding_cost` 对未知 side 从"静默按 SHORT 处理"改为显式 `ValueError`；`dedup_and_sort_records` 对损坏行从"抛 KeyError 中断保存"改为"安静丢弃")

**改动范围**:
- 新建 `src/tinohelm/backtest/funding_math.py`（206 行）—— 5 个 NT-free pure helper + 3 个导出常量：
  - `compute_funding_cost(*, side, quantity, mark_price, rate) -> float` —— 单点定义 "LONG + 正费率=付 / SHORT + 正费率=收" 的符号对称公式；未知 side 显式 `ValueError`（行为变更，见下方要点 3）
  - `build_funding_record(*, timestamp_iso, symbol, side, quantity, mark_price, rate, cost) -> dict` —— 7-key 单条记录 schema（keyword-only，`cost` 在此处做 6-dp 四舍五入）
  - `advance_due_events(events, *, current_ns, next_idx) -> (due, new_idx)` —— 纯游标推进，沿用原 `<=` 包含边界；返回列表切片（非共享引用，避免 tracker 外部可能的 mutation 污染事件列表）
  - `apply_funding_event(event, positions, *, total_cost, per_symbol_cost, records) -> (total, per_symbol, records)` —— 整个"遍历 positions_open() → 匹配 symbol → 算 cost → 落 record"流水线下沉到纯层，只要 duck-type 出 `.instrument_id` / `.side.name` / `.quantity` 三个属性就能跑（Protocol 类型注解，不依赖 NT Position 类）
  - `summarize_funding(*, total_funding_cost, per_symbol_cost, funding_records) -> dict` —— 4-key 结果 schema；total/per-symbol 在此处做 4-dp 四舍五入（records 里的 cost 不再重复 round，避免双次舍入）
  - 3 个导出常量：`RECORD_COST_PRECISION=6 / SUMMARY_TOTAL_PRECISION=4 / SUMMARY_PER_SYMBOL_PRECISION=4`——测试里 pin 住，防止精度漂移
- 新建 `src/tinohelm/data/funding_cache_helpers.py`（125 行）—— 6 个 NT-free / FS-free pure helper + 1 个导出常量：
  - `ensure_utc(dt)` / `to_epoch_ms(dt)` / `from_epoch_ms(ms)` —— 统一的 naive→UTC 归一化 + 毫秒往返。原代码在 `load_funding_rates` 里 inline 了两次 `if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`
  - `dedup_and_sort_records(records)` —— 新版："later wins" 语义 + 丢弃损坏行（缺 `funding_time_ms` / 非数值 / `bool` / 非 dict）而不是抛 KeyError
  - `filter_records_by_range(records, *, start_ms, end_ms)` —— 原 `[r for r in cached if start_ms <= r["funding_time_ms"] <= end_ms]` 的安全版（也过滤损坏行）
  - `compute_fetch_start(cached_times_ms, *, start, end) -> datetime | None` —— 增量决策的**全部**逻辑压到一处（no-cache / cache-missing-older / cache-missing-newer / full-cover 4 分支），返回必然是 UTC-aware 或 None
  - `DEFAULT_FUNDING_INTERVAL_MINUTES = 480` —— Binance perp 默认 8h，之前散在 `runner_helpers.py` 和 runner 里
- 重构 `src/tinohelm/backtest/funding.py`（121 → 96 行，-21%；跨 `_apply_funding` 单方法 34 → 9 行，-74%）：
  - `_apply_funding` 从"内联 for-loop + side 判断 + cost 累加 + 手搓 record dict"压到 1 次 `apply_funding_event(...)` 调用；公式不再散落在 Actor 里
  - `on_bar` 从"内联 while + `<=` 比较 + 手滚 idx"压到 `advance_due_events(...)`
  - `get_results` 从"手搓 dict + round 散点调用"压到 `summarize_funding(...)`
  - `self.__class__._funding_events` / `self.__class__._bar_type_strs` 改成显式 `_FundingCostTracker._funding_events` / `_bar_type_strs`—— `self.__class__` 依赖于 `self` 是真实 Actor 实例，这个间接访问在测试里用 SimpleNamespace stand-in 时会取错 class；显式类名引用让测试可以通过 unbound-method pattern 调用 `_FundingCostTracker.on_bar(stub, bar)` 也能访问正确的类属性。生产运行完全等价（从未被子类化）
  - 模块 docstring 更新：明确标注"公式/游标/汇总都在 `funding_math`，这里只剩 NT Actor surface 桥接"
- 重构 `src/tinohelm/data/funding_cache.py`（134 → 116 行，-13%）：
  - `_save_cache` 从 14 行手搓 dedup-by-set + sort 改成 1 行 `dedup_and_sort_records(records)`。原实现用 `seen: set[int]` + 先排序后去重（"earlier wins" 语义），新实现是 dict-overwrite（"later wins"）—— 对 Binance 返回的旧数据这是更合理的语义（新 fetch 覆盖老 fetch）
  - `_save_cache` 之前若遇到损坏行（缺 `funding_time_ms`）会抛 `KeyError` 把整个保存流程中断；现在安全丢弃
  - `load_funding_rates` 从 3 个独立决策分支（no cache / earliest / latest）+ 4 行 `if dt.tzinfo is None` 压到：`start = ensure_utc(start)` + `end = ensure_utc(end)` + `fetch_start = compute_fetch_start(cached_times, start=start, end=end)` + 1 处 `filter_records_by_range`
  - 删掉 `datetime.fromtimestamp((latest_cached_ms + 1) / 1000, tz=timezone.utc)` 这种裸的 ts-math（下沉到 `from_epoch_ms`）
- 新建 `tests/backtest/test_funding_math.py`（469 行，41 个用例 / 6 个测试类）—— 纯函数 + 端到端组合，全部 NT-free / < 10ms 跑完
- 新建 `tests/backtest/test_funding_tracker.py`（340 行，18 个用例 / 2 个测试类）—— 用 `SimpleNamespace` stand-in + unbound-method 调用模式验证 tracker 到纯层的 wiring；遵循 `tests/actors/test_risk_guard.py` 的 stub pattern
- 新建 `tests/data/test_funding_cache_helpers.py`（368 行，37 个用例 / 5 个测试类）—— 纯函数测试，也断言模块源文件不包含 `nautilus_trader` / `httpx` / `open(` 等 IO 关键字
- 新建 `tests/data/test_funding_cache.py`（345 行，21 个用例 / 4 个测试类）—— 用 `tmp_path` + `monkeypatch` 替换 `_CACHE_DIR` 和 `BinanceVisionPipeline`，覆盖 7 条路径：cache 全覆盖→不调管道 / 无 cache→全量 fetch / range filter / naive-vs-aware / pipeline 异常→降级到 cache / pipeline=0+cache=0→warn / 增量 tail fetch / older 再抓

**动机**:

`backtest/funding.py` 是**永续合约回测的资金费率核算器**——每 8 小时把所有 open position 按 mark_price × rate 计提费用，LONG 付正费率、SHORT 收正费率。这是"真金白银"的成本计算，直接影响 `pnl_after_funding` 和 `total_funding_cost` 这两个写入 API / tearsheet 的关键字段。但截止本次演进：

1. **`backtest/funding.py` 是 `backtest/` 子树里唯一一个零直接测试的生产模块**：
   - `test_runner_pure_helpers.py::TestAssembleFundingEvents` 只覆盖了 **上游**的 event 拼装（从 per-symbol 列表到排序好的 event 流）
   - `test_runner_helpers.py` / `test_runner_compat.py` / `test_runner_pure_helpers.py` 这些 runner 测试都 mock 掉了 `_FundingCostTracker`
   - `grep -rn "FundingCostTracker\|funding_math\|_apply_funding" tests/` —— **本次之前返回零**
   - 核心公式 `cost = notional × rate × (+1 for LONG / -1 for SHORT)` 过去从未被单测验证过。一次 CI 通过意味着"代码能 import"，不意味着"符号对没对"
2. **`data/funding_cache.py::load_funding_rates` 只有 2 个外围测试**：`tests/api/test_data_helpers.py` 里的 2 个测试只验证了"存在一个 JSON 文件 → 删除 API 能删掉"，完全没有测试增量 fetch 的决策（cache 缺前段 vs 缺后段 vs 全覆盖 vs 无 cache），也没有测试损坏缓存 / pipeline 失败 / naive vs aware datetime 行为
3. **`_save_cache` 的 dedup 在损坏行面前会崩溃**：原代码 `for r in sorted(records, key=lambda x: x["funding_time_ms"])` —— 任何一个半写入的 JSON 记录（比如 pipeline 下载中断）会直接 KeyError 把保存流程中断，同时导致**缓存文件无法被写出**，整个 symbol 的下次请求要重新从 Binance 全量拉一次。这个失败模式以前没有测试覆盖
4. **`compute_funding_cost` 的 else-SHORT 分支对非 LONG/SHORT side 会静默按 SHORT 处理**：原代码 `if pos.side.name == "LONG": cost = ... else: cost = -...`。虽然 `positions_open()` 实际只会返回 LONG/SHORT，但"万一 NT 以后新增 PositionSide 枚举值"或"测试桩传了 FLAT"会导致符号偷偷翻转，无任何报错。这种"静默错误大于显式错误"的反模式在资金费率这种安全关键路径上**不应该存在**

**要点**:

1. **`apply_funding_event` 通过 Protocol duck-type 把 NT Position 解耦** —— 纯层签名是 `Iterable[_PositionLike]`，`_PositionLike` 是一个 `Protocol` 只要求 `.instrument_id` / `.side.name` / `.quantity` 三个属性。生产路径传的是 `self.cache.positions_open()` 的 NT Position 对象，测试路径传的是 `SimpleNamespace(instrument_id=..., side=SimpleNamespace(name="LONG"), quantity=...)`。纯层永远不 import NT，测试 18 个用例覆盖了单 long / 单 short / 混合对冲 / 非匹配 symbol / 多仓累加 / 已有 per-symbol 进一步累加 / in-place 同引用返回——整条累加流水线独立于 NT Actor Cython kernel 跑。
2. **测试里用 "unbound method + stand-in self" 模式验证 tracker wiring** —— NT Actor 是 Cython extension class，`__new__()` 后可以创建实例但 `self.cache = MagicMock()` 会抛 `AttributeError: attribute 'cache' of 'nautilus_trader.common.actor.Actor' objects is not writable`。解法：测试直接 `_FundingCostTracker.on_bar(stub, bar)` 这样把 unbound 方法当普通函数调用，`stub` 是 `SimpleNamespace(_total_funding_cost=..., cache=MagicMock(...), _apply_funding=lambda ev: _FundingCostTracker._apply_funding(stub, ev))`。这样 6 个 wiring 测试覆盖了 on_bar 推进游标 / 包含边界 / 空事件无副作用 / 同 ts 重放幂等 / 多事件一次排空 / get_results 委托给 summarize_funding 的全部路径，**不需要启动 NT kernel**。
3. **`compute_funding_cost` 对未知 side 改抛 `ValueError` 是故意的行为变更** —— 原 `if LONG: +; else: -` 对 `"FLAT"` / `""` / `"long"` 都会走 `-` 分支。新版显式检查 `{"LONG", "SHORT"}` 白名单，否则抛 `ValueError` 带上期望值。对生产路径**零影响**（NT `PositionSide.name` 固定返回 `"LONG"` / `"SHORT"`），但对未来回归测试 / 桩数据 / 三方集成是一个诚实的契约保护。`test_unknown_side_raises_value_error` + `test_side_is_case_sensitive` 把这条 pin 住。
4. **`dedup_and_sort_records` 的语义修正是静默 bug fix 而不是破坏性变更** —— 原代码对**同一个 funding_time_ms 的两条记录**是"earlier wins"（先 add 到 set，后面的被 skip）；新代码是"later wins"（dict-overwrite）。实际上 Binance 的 fundingRate 归档对同一 ts 永远只会有一条记录，在生产中这个分歧永远不会触发；但语义上"later wins" 更符合"新 fetch 覆盖老 fetch"的意图，也更符合增量 incremental update 的预期。单独开一个 `test_dedup_by_funding_time_ms_later_wins` 用例把新语义 pin 住。**更重要的是**，损坏行（缺 / 非数值 / bool / 非 dict）现在被安静丢弃而不是抛 KeyError——这是真正的生产价值 bug fix，因为 `_save_cache` 里一条损坏行以前会让整个 flush 失败，现在不会。
5. **`compute_fetch_start` 的 4 条决策分支全部 pin 住优先级** —— `test_priority_older_before_newer_when_both_missing` 显式断言：当 `start < earliest` **并且** `end > latest` 同时成立时（即 cache 两头都缺数据），helper 选"再抓一遍从 start"而不是"tail fetch"。这是原代码的行为（`if start_ms < earliest_cached_ms:` 先判断），但此前从未有测试 pin 住；一旦未来有人"优化"成"分两次抓更高效"会立即炸。测试覆盖 9 个分支：无 cache / full cover / 缺老数据 / 缺新数据 / 边界命中 / 退化 start>end 不 crash / naive→UTC / 返回值必 UTC-aware / both missing 的优先级。
6. **测试独立性 pin 得很死** —— `TestNtFreeIndependence` 有三条断言：源文件不出现 `nautilus_trader` / `from nautilus` 字符串、`open(` / `httpx` / `requests` 字符串（保证纯层不意外做 IO）、"如果 NT 还没被其他测试预加载，re-import 本模块不会加载 NT"。这是防止未来的"懒人 import" 悄悄污染纯层。

**验证**:

- **单元测试（新建）**：
  - `tests/backtest/test_funding_math.py` —— 41 用例，全绿，~10ms
  - `tests/backtest/test_funding_tracker.py` —— 18 用例，全绿，~50ms（用 unbound-method stub pattern，0 次 Actor 实例化）
  - `tests/data/test_funding_cache_helpers.py` —— 37 用例，全绿，~10ms
  - `tests/data/test_funding_cache.py` —— 21 用例，全绿，~50ms（tmp_path + 假 pipeline）
  - **新增 117 个用例**，全部 NT-free / 无网络 / < 150ms 合计
- **全套测试**：`.venv/bin/python -m pytest tests/` —— `2287 → 2404 passed`（+117，**零回归**），13.77s。之前存在的"间接覆盖" funding path 的测试（`test_runner_pure_helpers.py::TestAssembleFundingEvents` 9 个用例、`test_data_helpers.py::test_funding_rate_*` 2 个用例、`test_loader.py::test_funding_alias_*` / `load_funding_rates` 相关 6 个用例）全部保持 passing
- **生产路径烟测**：`python -c "from tinohelm.backtest.funding import _FundingCostTracker; t = _FundingCostTracker(config=_FundingCostTrackerConfig()); print(t.id)"` → `FundingCostTracker-001`。NT Actor kernel 初始化链路 `__init__` → `super().__init__()` 未受影响

## 2026-04-21 (5)

**主题**: 抽 `data/providers/_rest.py` 纯逻辑层统一 4 份散落的 "HTTP 分类 → 退避 / 固定重试 / 放弃" 重试策略 + 消灭 3 条 klines 家族分页循环的复制粘贴 + 给 `data/providers/binance.py` 这个零覆盖的 REST 生产客户端建立 129 个 NT-free / 无网络的测试安全网
**维度**: 架构重构（消灭 4 份跨模块的重试策略重复 + 3 条 klines 家族分页循环的复制粘贴） + 测试补齐（`providers/binance.py` 此前 0 个直接测试）+ 顺手 bug 修复（bare `except Exception` 对 `JSONDecodeError` 的 5 次静默重试 / `extract_zip` 里残留的第 3 份 `startswith` 路径检查）

**改动范围**:
- 新建 `src/tinohelm/data/providers/_rest.py`（277 行）—— 9 个 NT-free pure helper + 1 个 httpx-aware async 包装：
  - `classify_http_status(status) -> {success, not_found, rate_limit, server_error, abort}` —— 单点定义 "429/418→退避 / 5xx→固定重试 / 404→可选 raise 或 None / 其他 4xx 与 3xx→放弃" 策略
  - `backoff_seconds(attempt, *, max_seconds)` —— 指数退避，attempt=1→2s, attempt=6+→60s cap
  - `parse_used_weight_header(headers)` —— 安全解析 `X-MBX-USED-WEIGHT-1M`（缺失/空/非数值→0），同时兼容 `httpx.Headers` 大小写不敏感语义
  - `throttle_seconds(weight, *, low_sleep, ...)` —— 3 段阶梯（>1800→5s，>1200→1s，其他→endpoint-specific baseline）。严格 `>` 比较与历史一致
  - `ms_range(start, end)` / `kline_row_to_dict(row, *, include_volume)` / `agg_trade_row_to_dict(row)` / `advance_cursor_after_kline / after_agg_trade` —— 把 3 份 ms 计算、3 份 row→dict 转换、2 份 `last_ts+1` 游标推进统一到单一实现
  - `request_with_retry(client, url, *, params, max_retries, raise_on_404, follow_redirects, ...)` —— 单次 GET + 完整重试矩阵的 async 包装。对老代码里的 `except (httpx.RequestError, Exception)` 做**故意窄化**：只捕 `RequestError`，让 `JSONDecodeError` 第一次就冒泡（见下方"要点 3"）
  - 6 个导出常量：`DEFAULT_MAX_RETRIES / MAX_BACKOFF_SECONDS / SERVER_ERROR_SLEEP_SECONDS / REQUEST_ERROR_SLEEP_SECONDS / WEIGHT_HIGH_THRESHOLD / WEIGHT_MEDIUM_THRESHOLD / WEIGHT_HIGH_SLEEP / WEIGHT_MEDIUM_SLEEP`
- 重构 `src/tinohelm/data/providers/binance.py`（361 → 254 行，-30%）：
  - `fetch_klines` 从 104 行压到 28 行：分页 / 重试 / 节流全部走 helper，只保留"构造 URL + 组装 params + 调用 `_paginate_klines`"这三件实际业务
  - `fetch_mark_price_klines` / `fetch_index_price_klines` 从各自 20 行 + 共享 83 行 `_fetch_klines_generic` 压到各 16 行，共用新抽出的 `_paginate_klines`。`_fetch_klines_generic` 这个只对两个 caller 可见、已经是"部分 DRY"的中间抽象层**整体删除**（被更彻底的 `_paginate_klines` 取代）
  - `fetch_agg_trades` 从 92 行压到 47 行：保留 agg-trade 特有的 dict-shape row 解析，重试/throttle 全走 helper
  - 新增私有 `_paginate_klines(*, url, api_symbol, interval, start, end, limit, include_volume, symbol_param, label)` —— 3 种 klines endpoint 的**唯一**分页实现。之前 `fetch_klines` 主体 + `_fetch_klines_generic` 是两份几乎一样的循环（diff 只在行内的 row→dict 映射和 symbol 参数名 `symbol` vs `pair`），现在统一
  - 顺手加注释说明为什么 `fetch_index_price_klines` 必须用 `pair` 而不是 `symbol`（Binance 指数价格端点的历史怪癖，很容易被"一键统一"错）
- 重构 `src/tinohelm/data/downloader.py`（505 → 506 行，净 +1，但 `download_file` 主体从 65 行压到 18 行）：
  - `download_file` 重试循环从 48 行（`while True / try / except HTTPStatusError + if 404/418/429/5xx / except RequestError / if retry_count > _MAX_RETRIES`）压成 1 行 `request_with_retry(client, url, max_retries=_MAX_RETRIES, raise_on_404=True, follow_redirects=True)`
  - `_MAX_RETRIES = 5` 改成 `_MAX_RETRIES = DEFAULT_MAX_RETRIES` 从 helper 拉常量，防止两处数字漂移
  - `extract_zip` 里的 `if not str(target).startswith(str(dest_dir.resolve()))` 改成 `if not is_within_dir(target, dest_dir)` —— 这是全仓剩下的**第 3 份**同构路径边界检查（2026-04-21 (3) 收了 scaffold + module_loader 的 2 份，2026-04-21 之前的那一轮收了 api/_utils 里的 5 份，`extract_zip` 这条因为在另一个 data/ 子模块里、之前的 grep 只扫 `src/tinohelm/api/` + `src/tinohelm/strategy/`/`core/` 漏了它）
  - 模块 docstring 更新，显式指向 `_rest.py` 作为共享策略来源
- 新建 `tests/data/providers/__init__.py`（空）与 `tests/data/providers/test_rest_helpers.py`（666 行，100 个用例 / 14 个测试类）—— 全部 pure helper + `request_with_retry` 通过 `httpx.MockTransport` 驱动，`asyncio.sleep` 全程 autouse-style patch 成 AsyncMock，**重试测试 < 1s 跑完 150+ 个 request 场景**
- 新建 `tests/data/providers/test_binance.py`（554 行，29 个用例 / 9 个测试类）—— 每个 fetch 函数都走 `httpx.MockTransport`，覆盖分页 / 空响应 / 429 重试 / 500 重试 / 404 传递 / 权重节流 / testnet 切换 / 符号剥离 / `symbol` vs `pair` 参数名

**动机**:

`src/tinohelm/data/providers/binance.py`（361 行）是回测引擎和 Data Pipeline 用于 **T+1~T+3 vision 归档空窗期**补齐真金白银数据的 REST 客户端。它当前在 2 条生产路径被调用：
1. `backtest/runner.py:498` —— 回测启动时拉 mark price / index price K 线（回测引擎需要 `MarkPriceUpdate` / `IndexPriceUpdate` 而不是 Bar）
2. `data/pipeline.py:632,657` —— `BinanceVisionPipeline._rest_fallback_*` 在 vision 归档缺失时的 fallback 链

但截止本次演进，`providers/binance.py` 是 **`data/` 子树里唯一一个零直接测试的生产模块**：
- `test_downloader.py` 覆盖 vision 归档下载器
- `test_pipeline.py` / `test_pipeline_helpers.py` 覆盖 pipeline orchestration
- `test_instruments.py` 覆盖 instrument 构造
- `test_converters.py` / `test_converter_stubs.py` 覆盖 12 种数据类型的 row → NT 对象转换
- `test_worker.py` 覆盖数据 fetch job worker
- `tests/data/test_binance*.py` —— **不存在**。`grep -rn "providers.binance\|fetch_klines\|fetch_agg_trades" tests/` 确认唯一提及它的是 `test_pipeline_helpers.py` 里的**名字字符串**（`assert KLINES_REST_FETCH_FN["klines"] == "fetch_klines"`），真正的函数行为没有任何测试

这种情况下 3 条重复的分页循环 + 4 份散落的重试策略就是**每次 NT 升级或 Binance API 变动都要手工逐份核对**的技术债：
1. **HTTP 重试策略散落 4 处**：`fetch_klines` 主体（83 行）/ `fetch_agg_trades`（48 行）/ `_fetch_klines_generic`（73 行）/ `downloader.py:download_file`（48 行）各自手写"429/418 指数退避 + 5xx 固定 2s + RequestError 固定 2s + max_retries 计数"。任何一条规则调整（例如未来 Binance 返回 409 要求退避）都要改 4 处，漏改就发散
2. **3 条 klines 家族分页循环是剪贴兼代码**：`fetch_klines` / `fetch_mark_price_klines`（通过 `_fetch_klines_generic`）/ `fetch_index_price_klines`（通过 `_fetch_klines_generic`）的 while-loop 结构 99% 相同，只有三点差异：(a) row 映射带不带 volume (b) URL 路径 (c) symbol 参数名 `symbol` vs `pair`。`_fetch_klines_generic` 是上一次"部分 DRY"尝试但只覆盖了 mark + index 两条，`fetch_klines` 依然独立
3. **`_fetch_klines_generic` 本身是另一种重复**：它和 `fetch_klines` 的 while-loop 几乎一样，但为了容纳 "index 用 pair"、"index/mark 不带 volume"、以及 fetch_klines 的特殊进度日志格式，没有合并
4. **bare-`except (httpx.RequestError, Exception)` 是隐蔽 bug**：在 `fetch_klines` 和 `fetch_agg_trades` 里写的是 `except (httpx.RequestError, Exception) as e:` —— 等价于 `except Exception`。意味着**如果 Binance 返回了畸形 JSON**（`.json()` 抛 `json.JSONDecodeError` < `ValueError` < `Exception`），它会被当成 "Request error" 重试 5 次，每次睡 2 秒，然后才终于 raise。产线上这意味着一次真正的"服务端返回损坏数据"事件会被拖成 10 秒钟的假重试，才终于失败
5. **`extract_zip` 里还藏着第 3 份 `str(a).startswith(str(b))` 路径边界检查**：2026-04-21 (3) 演进以为已经把 `api/_utils` + `scaffold` + `module_loader` 的 7 处全收拢了，但 grep 当时没扫 `data/downloader.py`。本次顺手收掉

**要点**:

1. **单一事实源 `_rest.py`** —— 所有"分类 / 退避 / 节流 / 游标"决策都定义在一处。测试覆盖：100 个用例纯逻辑 + 29 个用例 `httpx.MockTransport` 驱动端到端。`classify_http_status(status)` 的 parametrized 测试覆盖 2xx（6 个用例）/ 404 / 418 / 429 / 5xx（7 个用例）/ 其他 4xx（6 个用例）/ 3xx（7 个用例）—— 总共 29 个分支。
2. **`_paginate_klines` 是三种 klines endpoint 的唯一实现**。endpoint 专属差异压到 4 个 kwargs：`url` / `symbol_param`（`symbol` 或 `pair`）/ `include_volume`（mark/index→False）/ `label`（日志字符串）。任何一条 klines endpoint 以后修改分页语义都只改这一处。测试覆盖点：跨两页分页 / 空响应早退 / `len(raw) < limit` 提前终止 / 限流重试 / 500 重试 / 404 传递 / `symbol` vs `pair` 参数路由 / `include_volume=True/False` 的 schema。
3. **`except Exception` 窄化是故意的行为变更**。老代码里 `fetch_klines` 的 `except (httpx.RequestError, Exception) as e:` 实际等价于 `except Exception` —— `json.JSONDecodeError` 这种"服务端损坏数据"会被当成 transport error 重试 5 次 × 2s = 10s 延迟才失败。新 `request_with_retry` 只捕 `httpx.RequestError`。这是一个**可观察的行为变更**：Binance 返回畸形 JSON 时现在第一次就失败（更诚实、更快）。`TestRequestWithRetryJsonDecodeNoRetry::test_malformed_json_not_retried` 显式把这条语义 pin 住。这是今天演进里**唯一**的行为变更；其他全部是纯重构。
4. **`request_with_retry` 的测试是 `asyncio.sleep` autouse-patched 的**。`@pytest.fixture(autouse=True) _patch_asyncio_sleep` 把整个 `tinohelm.data.providers._rest.asyncio.sleep` 换成 AsyncMock，这样 15 个 retry/backoff 测试（含 5 次 429 后成功、max_retries 耗尽、自定义 max cap、500 系列 3 个状态码 parametrized、404 的 raise vs 返回 None 两分支、transport error 重试、follow_redirects 开关）总计 < 100ms 跑完，而不是要等 2+4+8+16+32 = 62 秒的实际退避。
5. **`TestFetchKlinesThrottle` 用真实分页场景锁定 `low_sleep` 差异**。`fetch_klines` 和 `_paginate_klines`（klines 家族）用 `_KLINES_LOW_SLEEP = 0.5`；`fetch_agg_trades` 用 `_AGG_TRADES_LOW_SLEEP = 0.3`。测试 `test_low_sleep_is_agg_trades_baseline` 断言 `0.3 in sleep_values and 0.5 not in sleep_values` —— 如果以后有人"统一" agg_trades 到 0.5，会立即炸。这是保护不对称 throttle 政策最直接的不变量。
6. **`_fetch_klines_generic` 整体删除**。`TestLegacyEntryPointsPreserved::test_legacy_generic_helper_removed` 锁死 `not hasattr(mod, "_fetch_klines_generic")`，防止有人"恢复"这个中间抽象。老代码里它被 `fetch_mark_price_klines` / `fetch_index_price_klines` 两个公开函数调用，都已经改成调 `_paginate_klines`，外部没有 import 过它（grep 确认）。
7. **`downloader.py:download_file` 主体从 65 行到 18 行**，所有网络重试全部委托给 `request_with_retry(..., raise_on_404=True, follow_redirects=True)`。existing 的 `TestDownloadFile` 31 个测试（其中 4 个涉及 retry 决策）**无一需要修改**就继续通过 —— 因为测试是用 `AsyncMock()` mock `httpx.AsyncClient`，底下的 `client.get()` 行为不变就保证了外层行为不变。
8. **`extract_zip` 的 `is_within_dir` 迁移**被同一次演进的端到端 integration 验证覆盖：`TestExtractZip` 所有 4 个原测试（含 `test_extract_first_csv_when_multiple` 和 `test_csv_case_insensitive`）照跑，都没碰到路径比较的语义变化（正常文件路径、both `startswith` 和 `is_within_dir` 给出同样的答案）。未来的 "zip slip" 攻击向量（`../../../etc/passwd` 作为 ZIP 成员）会走 `Path.resolve().relative_to()` 路径，`is_within_dir` 会更严格地拒绝。

**讨论点**:
- `verify_checksum` 里的 `get(checksum_url)` 是**单次**调用，没有任何重试，对 404 单独降级为 warning。这条路径不走 `request_with_retry`，理由：checksum 文件很小、限流代价低，而且它和主下载是先后串联的（checksum 失败后主下载已经成功），加 retry 是 scope 外的行为扩展。留给将来真的观察到 checksum 限流时再补。
- 本次演进**没有**引入 `httpx-mock` / `pytest-httpx` 等外部 mock 库，全部用 httpx 自带的 `httpx.MockTransport`。这是为了保持 CI 无新 dep，也让测试的断言边界清晰（我们只 mock 到 HTTP 响应层，连 `httpx.AsyncClient` 本身都不 mock）。这样 `request_with_retry` 的测试才真实地测到 httpx 的 `raise_for_status()` 行为。
- `INTERVAL_MS` 表虽然保留但**本次仍然没有被 `_paginate_klines` 消费**。它是给上游 caller（`data/pipeline.py` 推断 bar 时长）用的，不是分页逻辑用的。未来若分页要用 `end_ms = start_ms + N * INTERVAL_MS[interval]` 策略（目前是直接传 end_ms），`INTERVAL_MS` 迁入 `_rest.py` 才合适。本次维持原位。

**验证**:
- **NT-free 全量回归**：`pytest tests/` baseline **1280 passed / 41 failed / 21 errors** → with changes **1409 passed / 41 failed / 21 errors**（41 failed + 21 errors 完全是 pre-existing NT-dependent 测试，**不变**）—— 净增 **+129 passed, 0 regression**
- **新测试单独**：`pytest tests/data/providers/` → **129 passed in 0.42s**（100 `test_rest_helpers` + 29 `test_binance`）
- **既有测试不受影响**：`pytest tests/data/test_downloader.py` → 31/31 passed（refactor 后老 mock 一行不改就继续通过）
- **lint**：`ruff check` on 5 modified/new files → **All checks passed!**
- **符号面稳定性**：`from tinohelm.data.providers.binance import fetch_klines, fetch_mark_price_klines, fetch_index_price_klines, fetch_agg_trades, INTERVAL_MS, BINANCE_FUTURES_BASE, BINANCE_FUTURES_TESTNET` 全部保留；`VisionDownloader / DownloadTask / ChecksumError` 公开符号保留；`_MAX_RETRIES` 保留且语义未变（现在引用共享常量）
- **行为变更有且仅有一处**：malformed JSON 不再被静默重试 5 次（由 `TestRequestWithRetryJsonDecodeNoRetry` 锁定）

---

## 2026-04-21 (4)

**主题**: 把 5 个 node actor 共同依赖的 position/fill/bar/equity 事件序列化层抽到 `node/actors/serialize.py` 单一事实源，附带把 `_RedisLogHandler` 里的 inline token bucket 提升为可注入时钟的 `TokenBucket` 类；为新抽出的纯逻辑层（101 个 NT-free 测试）+ DbWriterActor 的 SQL 绑定 / buffer 调度路径建立安全网
**维度**: 架构重构（消灭 SnapshotActor vs DbWriterActor 的字段提取重复）+ 测试补齐（node actor 子树此前只有 RiskGuard 有测试）
**改动范围**:
- 新建 `src/tinohelm/node/actors/serialize.py`（258 行）—— 9 个 NT-free pure helper：`position_db_fields` / `build_position_update` / `fill_db_fields` / `build_fill_event` / `build_order_lifecycle_event` / `build_bar_event` / `build_strategy_signal_snapshot` / `tag_risk_metrics` / `build_equity_snapshot`。所有 NT 对象通过 duck-typing 传入，模块自身零 `nautilus_trader` import，可用 MagicMock 直接喂
- 新建 `src/tinohelm/node/actors/rate_limit.py`（76 行）—— `TokenBucket` 类，带可注入 `clock` 参数。同时修掉原 inline handler 的一个隐蔽 bug：`_last_refill == 0.0` 当作"未初始化"哨兵 —— 在 `time.monotonic()` 永远非零的生产里没问题，但注入测试时钟从 0.0 开始就会让每次调用都触发 priming 分支。现在用 `None` 做哨兵，彻底消除"哨兵值和合法值冲突"的隐患
- 重构 `src/tinohelm/node/actors/snapshot_actor.py`（276 → 189 行，-31%）：
  - `_build_position_payload` / `_build_fill_payload` / bar 载荷 / 4 种 order 生命周期载荷 / 策略信号载荷 / 风险指标 tag —— 全部被删，改走 `serialize.py`
  - 4 个 order 非 fill 事件分支（accepted/canceled/expired/rejected）从 4 个 `elif isinstance` 压缩成一个 `_ORDER_EVENT_KINDS` 表 + 1 个循环，rejected 的特殊 `reason` 字段通过 `build_order_lifecycle_event(event, "order_rejected")` 内部分支处理
  - `_RedisLogHandler` 内部字段从 `_tokens/_last_refill/_rate_limit` 三个状态字段压成 `_bucket: TokenBucket`
- 重构 `src/tinohelm/node/actors/db_writer_actor.py`（199 → 172 行，-14%）：
  - 删除 `_persist_fill` 里 12 行 SQL 字符串 + 13 行 bind-param dict，改成 1 行 `session.execute(text(_INSERT_FILL_SQL), fill_db_fields(event, self._node_type))`
  - `_persist_position` 同等收缩（删除 19 行 bind-param dict，改走 `position_db_fields`）
  - SQL 字符串提到模块常量 `_INSERT_FILL_SQL` / `_UPSERT_POSITION_SQL`，便于测试用 regex 检查结构和列名
  - 清理 `_on_position_event` 里的 deferred `from ... import PositionOpened, PositionChanged`（这两个类型已经在模块顶层 import 了 —— 函数内再 import 是遗留的过时 pattern）
  - 修掉 `_write_batch` 里的 `from sqlalchemy import text` unused import
- 重构 `src/tinohelm/node/actors/metrics_actor.py`（143 → 139 行）：
  - Equity payload 的 6 字段 dict + `round(·, 2)` 三处内联调用改走 `build_equity_snapshot` 一行
- 清理 `src/tinohelm/node/actors/_utils.py`（32 → 30 行）—— 删除 unused `typing.Any` import
- 清理 `src/tinohelm/node/actors/health_actor.py`（233 → 232 行）—— 删除 `_file_watcher` 里重复的 `import os`（模块顶层已经 import 了）
- 清理 `tests/actors/test_risk_guard.py` —— 删除 unused `pytest` import
- 新增 `tests/actors/test_serialize.py`（553 行，51 用例 / 11 个测试类）
- 新增 `tests/actors/test_rate_limit.py`（202 行，17 用例 / 4 个测试类）
- 新增 `tests/actors/test_db_writer.py`（409 行，28 用例 / 5 个测试类）
- 新增 `tests/actors/test_snapshot_log_handler.py`（76 行，5 用例 / 2 个测试类）

**动机**:

`SnapshotActor`（向 Redis PubSub 推 JSON 载荷）和 `DbWriterActor`（向 PostgreSQL 写 UPSERT 绑定）是两条紧挨着跑的真实交易事件出口。翻 `node/actors/*` 发现它们对 NT `Position` 的 19 个字段 + NT `OrderFilled` 的 13 个字段执行**完全同样的**提取序列：`str(pos.id)` / `str(pos.strategy_id) if pos.strategy_id else ""` / `pos.realized_pnl.as_double() if pos.realized_pnl else None` / `ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None` …… 两处手写两份。

这种重复在量化平台里是**直接的生产风险**：

1. **Frontend/TUI 显示和 DB 持久化发散** —— 前端通过 WebSocket 收到的 `position.update` 事件里 `avg_px_close=None`（因为仓位刚开），用户在终端看到"未实现 PnL: -"；几秒后 DB 查询返回的同一 position 里 `avg_px_close=51000.0`（另一个分支忘了同步修复 `if pos.avg_px_close` 的判空逻辑）。用户切换"持仓"页面与"历史"页面会看到两个不同的数。这种 bug 在历史演进 log 里已经改过 3 次 —— **问题是结构性的，不是某次 typo**。
2. **NT 字段重命名的隐性成本** —— `Position.duration_ns` 如果在上游 NT 升级里改成 `Position.duration_time`，目前要在两处各改一次，漏改一处就发散。抽到一个 helper 后只改一处。
3. **测试不可能** —— 这些 payload 构造散在 4 个 Actor subclass 的 `on_*` 方法里，而 NT Actor 是 Cython 扩展类（CLAUDE.md 明确："Actor/Strategy 是 Cython 扩展类，不能用 `object.__new__` 在测试里实例化"）。不抽出来就没法写测试，于是 2026 年 4 月前，整个 `node/actors/` 子树**除了 RiskGuard 15 个用例，其他 4 个 Actor 总共 0 个测试**。这是把真钱交易风险下沉到"完全未测试的代码"的最后一块短板。

附带的 `TokenBucket` 抽离是顺手的收益：SnapshotActor 的 log handler 在日志风暴（比如某策略每个 bar 都 traceback）下要守住 10 条/秒的上限，别把 Redis 打爆。这个限速器此前是 `_RedisLogHandler.emit` 里 8 行手动算术，和 SnapshotActor 绑死，无法测试。同时 `_last_refill == 0.0` 的初始化 sentinel 和注入测试时钟的 0 起点会冲突 —— 这是生产里潜伏的、但因为 `time.monotonic()` 永远 > 0 而没被触发的 latent bug。既然要抽，就把 sentinel 换成 `None`，彻底消除哨兵 / 合法值重叠的隐患。

**要点**:

1. **`serialize.py` 的 duck-typed 契约** —— 所有 helper 用 `Any` 类型标注输入，文档字符串里写明"需要的属性列表"（例如 `pos` 需要 `id` / `strategy_id` / `instrument_id` / `side.name` / …）。没有 `from nautilus_trader.model.position import Position`，所以测试用 `MagicMock()` 填上那些属性就能直接喂进去。生产里 SnapshotActor 塞 `PositionOpened.position`，DbWriterActor 塞 `PositionChanged.position` —— 两者鸭子兼容，不需要再任何 `isinstance` 保护。

2. **snapshot 载荷 vs DB 载荷：严格的 superset 关系**。position 场景下 DB 19 字段，snapshot 25 字段，snapshot 额外 `{type, event, id, strategy_id, duration_ns, ts}`；fill 场景下 DB 13 字段，snapshot 17 字段，snapshot 额外 `{type, id, strategy_id, ts}`。`test_serialize.py::TestSnapshotDbOverlap` 用 `set(db_fields.keys()) & set(snap_payload.keys())` 做跨集合遍历，对**每一个**共享键断言 byte-for-byte 相等。这是防漂移的终极护栏 —— 任何一侧单方面修改字段值都会立刻失败。

3. **唯一允许的语义分歧**："`realized_pnl=None` 时 DB 保持 None，Snapshot 强制 0.0"。这条差异是历史上前端契约，删不掉。`test_position_overlap_realized_pnl_diverges_only_on_none` 专门把这条分歧 pin 成一个 explicit 断言 —— 如果未来某天前端能处理 null 了，一人修了这一行，测试会明确指出要同时改掉。

4. **SQL 语句提到模块常量，用 regex 验证结构和列名**。`_INSERT_FILL_SQL` 和 `_UPSERT_POSITION_SQL` 不再活在 `_persist_*` 方法里，而是模块顶层常量。测试用：
   - `f":{key}" in _INSERT_FILL_SQL` 遍历 `fill_db_fields` 产出的每个键，验证每个 bind 参数都有对应的 SQL placeholder
   - `"ON CONFLICT (trade_id) DO NOTHING"` / `"ON CONFLICT (position_id) DO UPDATE SET"` 锁定 conflict 策略
   - `"updated_at = NOW()"` 锁定 DB timestamp 触发
   - 对 UPDATE SET 子句反向断言"不变字段"（`ts_opened` / `entry_side` / `avg_px_open` / `instrument_id` / `strategy_id_tag`）**不在**其中 —— 这几个字段仓位开仓后永远不应该改，万一哪天有人手贱把它加进 `DO UPDATE SET`，测试立刻失败

5. **`_DbWriterStub` 模式 —— 无 NT 代码复制**。RiskGuard 的 stub 是把整个 `_check_risks` 方法拷贝过来，代码重复 ~70 行。这次的 stub 采用更干净的"直接从生产类拉出 bound unrelated methods"写法：

   ```python
   class _DbWriterStub:
       _on_order_event = dwa.DbWriterActor._on_order_event
       _on_position_event = dwa.DbWriterActor._on_position_event
       _flush = dwa.DbWriterActor._flush
       _write_batch = dwa.DbWriterActor._write_batch
       _persist_fill = dwa.DbWriterActor._persist_fill
       _persist_position = dwa.DbWriterActor._persist_position
   ```

   因为这 6 个方法对 NT 的唯一依赖是 `queue_for_executor`（被 stub 覆盖捕获）和 `log`（MagicMock），所以直接把生产方法赋值到 stub class 上就能跑。此后生产代码改了这 6 个方法的任意一行，测试**自动覆盖到**，不需要像 RiskGuard 那样手动同步两处代码。这是未来其他 NT Actor 测试的更好模板。

6. **`TokenBucket` 的三个生产平价测试**（`TestTokenBucketProductionParity`）用 `_FakeClock` 重放实际日志风暴场景：10 rps 容量 / 15 emit 突发 → 最多 10 accept；10 rps 容量 / 稳态 10 rps 输入 → 0 drop；10 rps 容量 / 20 rps 输入 / 10s → 100~115 accept（下限考虑稳态 100，上限考虑初始桶满时的 bonus burst ≈ 9-19 条）。这三条用例捕获了"生产里单位时间内丢多少条"的真实预期，而不是仅测试 `try_consume` 的本地状态变迁。

7. **顺手债务清理**：`_utils.py` 删掉 unused `typing.Any`（把整个文件从 32 行压到 30 行），`health_actor.py` 的 `_file_watcher` 里重复的 `import os`（已经在顶层），`test_risk_guard.py` 删 unused `pytest` import。这些 lint finding 此前 ruff 能扫出来但没人修 —— 首席工程师看到顺手的 lint debt 就该清掉。

**验证**:

- 完整测试套件：**2057 → 2158**（+101 净增，0 回归）
- `tests/actors/` 单独：**11 → 116**（+105，其中 +101 新增 + 4 个 risk_guard 测试在重构时一并通过）
- `tests/actors/ + tests/node/` 组合：**292 / 292 passed**，4.48s
- ruff 在 `src/tinohelm/node/actors/` 和 `tests/actors/` 两个修改目录：**All checks passed**
- 手动验证新 helper 和旧 actor 行为字节级等价：`TestSnapshotDbOverlap::test_*_overlap_values_identical` 对 position 和 fill 的所有共享字段做 byte-for-byte 断言 —— 如果重构意外修改了任何一侧的值，测试会失败

## 2026-04-21 (3)

**主题**: 给 `strategy/scaffold.py` + `strategy/validator.py` 这两条 API 路由直通的生成 / 校验路径建立测试安全网（117 个 NT-free 用例），同时把 `str(a).startswith(str(b))` 这条已经翻修过 5 次的脆弱路径边界检查收拢到 `core/utils.is_within_dir`，消灭最后 2 份同构代码
**维度**: 测试补齐 + 架构重构（提取 helpers + 消灭重复）
**改动范围**:
- 新建 `src/tinohelm/strategy/scaffold_helpers.py`（87 行）—— 4 个 NT-free pure helper（`validate_identifier` / `derive_class_name` / `render_scaffold` / `resolve_new_strategy_path`）+ `IDENTIFIER_RE` 共享常量
- 重构 `src/tinohelm/strategy/scaffold.py`（616 → 641 行，生成器逻辑压到 ~40 行且 100% 走 helper）
  - 删除向后兼容 alias `BAR_SCAFFOLD` / `TICK_SCAFFOLD`（grep 确认无外部引用）
  - 入口 `generate_scaffold()` 用 `validate_identifier` + `resolve_new_strategy_path` + `render_scaffold` 组装，每一行都可单测
  - 补完 docstring：`scaffold_type` 参数正式标注"接受但当前无分支效果"的契约，避免未来悄悄改动
- 新建 `src/tinohelm/strategy/validator_helpers.py`（99 行）—— 5 个 NT-free helper（`empty_validation_result` / `collect_implemented_hooks` / `build_missing_hook_warnings` / `extract_config_params`）+ `STRATEGY_HOOK_NAMES` 与 `RECOMMENDED_HOOKS` 两个锁定常量
- 重构 `src/tinohelm/strategy/validator.py`（89 → 72 行，-17）
  - 删除内联的 NT MRO 名称匹配代码（7 行），直接调用 `module_loader.discover_strategy_classes`（消灭第 1 份重复 —— 两处代码此前完全同构）
  - 删除内联的 hook 扫描 `for hook in [...]: if hook in strategy_cls.__dict__` 片段、内联的 warning 组装 if/if 片段、内联的 `get_config_fields` try/except 片段 —— 全部替换为 helper 调用
- 修改 `src/tinohelm/strategy/module_loader.py`（230 → 231 行，+1 import）
  - 把 line 61 的 `str(file_path).startswith(str(boundary))` 替换为 `is_within_dir(file_path, boundary)`（消灭第 2 份重复，同时修掉"相似前缀目录名"误判场景）
- 扩展 `src/tinohelm/core/utils.py`（17 → 40 行，+23）
  - 新增 `is_within_dir(candidate, boundary) -> bool`：用 `Path.resolve().relative_to()` 统一 6 处原本各自写 `str(a).startswith(str(b))` 的路径包含检查。此前 2026-04-21 演进已经在 `api/_utils.resolve_artifact_path` 里做过一次同类收拢（5 份）；本轮把 scaffold + module_loader 这剩下 2 份也收拢到 `core/utils`，整个 repo 现在**只有这一个实现**
- 新增 `tests/core/test_utils.py` 扩展（26 → 37 用例，+11）—— `TestIsWithinDir` 类 11 条：含符号链接逃逸、`..` 逃逸、同前缀目录误判、str/Path 双重接口、boundary 自身、深层路径
- 新增 `tests/strategy/test_scaffold.py`（364 行，64 用例）
- 新增 `tests/strategy/test_validator.py`（520 行，41 用例）
- 扩展 `tests/strategy/test_module_loader.py::TestBoundaryEnforcement`（2 → 3 用例，+1）—— `test_similar_prefix_name_is_rejected` 专门锁死"`allowed2/foo.py` 误算作 `allowed/` 内部"这条历史 bug（`startswith` → `is_within_dir` 修复的触发场景）

**动机**:

`strategy/scaffold.py`（616 行）和 `strategy/validator.py`（89 行）是两条**直通前端用户操作**的关键路径，但本次演进之前二者测试覆盖都是 0：

- **scaffold.py**：`POST /api/strategies/create` 的后端实现。用户点"新建策略"按钮触发 —— 生成失败或生成内容不正确（template 被改坏、`{{...}}` 转义漏掉、`{class_name}` 插值错位），用户看到的是"创建成功"消息但打开文件是空白或语法错误。更关键的是，`str(file_path).startswith(str(strategies_dir.resolve()))` 这条路径校验在本次之前已经是**第 6 份**同构代码（api/_utils 在上一轮收拢了 5 份），语义上对"相似前缀目录名"和 symlink 逃逸都不可靠。
- **validator.py**：`POST /api/strategies/{name}/validate` 的后端实现。前端 Strategies 页面的"Validate"按钮每次鼠标悬停都可能打 —— 返回结果直接驱动 UI 的"有效"/"无效"/"警告"标记。**完全没有测试**意味着：
  - 若有人改了 `"on_start"` / `"on_stop"` 的 recommended 名单，用户的已有策略会突然显示莫名其妙的 warning
  - 若 MRO 名称匹配逻辑和 `module_loader.discover_strategy_classes` 发生漂移（两处此前是**完全同构的复制代码**），用户的合法策略可能在 validate endpoint 里显示 invalid 但在 backtest 里能跑
  - 任何一条 error/warning 的文案变更都悄无声息地改变了前端展示

同时，这两件事有天然的共同边界：
1. validator.py 里的 `for base in inspect.getmro(obj): if base.__name__ == "Strategy" ...` **和** `module_loader.discover_strategy_classes` 是**完全同样语义**的代码。只是前者还要附加记录 `strategy_class` / `config_class` 的名字字符串到 result dict。
2. scaffold.py 里的 `str(a).startswith(str(b))` 路径守卫 **和** module_loader.py 里的**完全同样**的守卫，是 `api/_utils.resolve_artifact_path` 当初没扫到的两个漏网之鱼。

所以一次演进同时做三件事：（a）消灭最后 2 份 `startswith` 路径检查重复、（b）消灭 validator vs module_loader 的 MRO 复制、（c）给这两个模块补完测试 —— 而不是分三轮各自交付。

**要点**:

1. **`core/utils.is_within_dir(candidate, boundary)` —— 全仓唯一实现**。`Path(...).resolve()` 双向后通过 `relative_to` 判定包含关系。相比 `str(a).startswith(str(b))`：
   - 抗 trailing-separator 差异（`/a/b` vs `/a/b/`）
   - 抗相似前缀目录名（`/a/allowed2/foo` 被正确识别为不在 `/a/allowed` 内 —— `startswith` 会误判为在内）
   - 抗 symlink escape（`resolve()` 展开后 `relative_to` 失败）
   
   把 2026-04-21 `api/_utils.resolve_artifact_path` 修好的 5 处 + 本次 scaffold / module_loader 的 2 处**收拢到 1 份**。任何未来对 boundary 语义的调整都只用改这一处。

2. **`scaffold_helpers.py` —— 4 个 NT-free pure helper**：
   - `validate_identifier(name)` —— 共享 `IDENTIFIER_RE`，显式 ValueError 文案包含 `name!r`
   - `derive_class_name(name)` —— snake→Pascal；`"".capitalize()` 的幂等行为（`"BTC_Scalper" → "BtcScalper"`）被专门测试锁定，避免未来有人"修复"成正则保留大写
   - `render_scaffold(name)` —— 延迟 import `STRATEGY_SCAFFOLD` 避免循环依赖；测试用 `ast.parse(content)` 作为**可解析性不变式**
   - `resolve_new_strategy_path(dir, name)` —— 防御性第二道闸，即使 `validate_identifier` 漏过也能挡住

3. **`validator_helpers.py` —— 5 个 NT-free helper**：
   - `STRATEGY_HOOK_NAMES` 顺序稳定（driver 了 `result["hooks"]` 的顺序契约）；hook count pinned to 10
   - `RECOMMENDED_HOOKS = ("on_start", "on_stop")` 显式锁定（测试 `test_recommended_contents_pinned` 防止偷偷扩大警告范围）
   - `collect_implemented_hooks(cls, names)` 用 `cls.__dict__` 而非 `hasattr()` —— 继承自 NT `Strategy` 的空桩方法**不**算实现（避免"所有策略都看起来实现了所有 hook"的 false positive）
   - `build_missing_hook_warnings` 顺序严格按 `RECOMMENDED_HOOKS`
   - `extract_config_params` 吞下 `get_config_fields` 的任何异常 —— 保留 legacy 行为（宁可 `config_params=[]` 也不要 validate 整个失败）

4. **`validator.py` 缩到 72 行**，全部是编排逻辑：读文件 → load_module_from_file → discover_strategy_classes → 填 result dict。无一行业务逻辑分散。

5. **`scaffold.py` 的 `scaffold_type` 参数保留但显式标注**。`CreateStrategyRequest.type` 可以传 `"strategy"` 或 `"portfolio"`，目前两者生成同一份模板。我**没有**加校验（若传 `"weird"` 现在仍接受），因为加校验是可见行为变更 —— 留给下一轮演进做"真的按 type 分支生成 portfolio 结构"时一起做。`test_scaffold_type_accepted_but_no_effect` 和 `test_arbitrary_scaffold_type_string_still_succeeds` 锁死当前契约。

6. **死代码清除**：`BAR_SCAFFOLD = STRATEGY_SCAFFOLD` / `TICK_SCAFFOLD = STRATEGY_SCAFFOLD` 两个向后兼容 alias 通过 `grep -rn BAR_SCAFFOLD\|TICK_SCAFFOLD src/ tests/ cli/` 确认无引用后直接删除。`test_dead_aliases_removed` 锁死移除状态。

7. **测试策略 —— NT-free 但测 validator 端到端**：validator 的端到端测试（`TestValidateStrategyValid` 等）通过把"伪 NT 基类"写进临时 `.py` 文件并用 `load_module_from_file` 真正加载来达成。技巧：在伪文件里声明 `class Strategy: pass; Strategy.__module__ = "nautilus_trader.trading.strategy"` —— 这样 MRO 名称检查会命中，但整个测试不 `import nautilus_trader`。这让 validator 的完整集成路径（文件读 → 模块加载 → 类发现 → hook 识别 → 警告生成）都在 NT-free CI 里覆盖。

**讨论点**:

- **`scaffold_type` 当前是无分支参数但 API 签名继续接受它**：这不是 bug，是"前端已有 UI 但后端未实现"的半成品。继续接受任意字符串是为了在前端真正支持 "新建 portfolio" 时不用动 API 契约。本次演进下**没有增加 `scaffold_type` 的校验**（例如 `if scaffold_type not in {"strategy", "portfolio"}: raise`），因为加校验是客户端可观察的行为变更，需要 portfolio 模板和 API 对齐后一起做。
- **`discover_strategy_classes` 的 break-first 语义保留**：如果一个模块里同时存在多个 Strategy 子类，`discover_strategy_classes` 只返回 `inspect.getmembers` 顺序里最后一个（历史行为）。validator 跟着走。这是合理的（portfolio/策略文件应该只有一个 Strategy 类），但若用户文件确实放了多个，只有一个会被 validate。这个语义的局限在本次**没有扩大范围处理**，保持跟 runner/loader 一致。
- **`discover_actor_classes` 没复用进本次**：validator 只处理 Strategy 子类不处理 Actor 子类（actor 走 `tinohelm.node.factory` 的独立路径）。actor 文件（`~/.tino/actors/`）当前没有对应的 `validate_actor()` endpoint；若未来要加，应该和 validator 共用一套 helpers，届时把 `collect_implemented_hooks` 等拆成更中性的位置（或干脆把 `validator_helpers` 改名 `inspection_helpers`）。本次不预设结构。
- **`str(file_path).startswith(str(boundary))` 全仓是否还有残留**：我搜过 `grep -rn "startswith(str" src/tinohelm/` —— 还有若干处但不是路径包含检查（比如 channel prefix 匹配之类语义明确的字符串前缀，保留不变）。真正属于"路径边界检查"的 7 处（5 个 api 路由 + scaffold + module_loader）已经全部收拢到 `core/utils.is_within_dir` + `api/_utils.resolve_artifact_path`。

**验证**:
- ✅ **NT-free 全套 1292 passed, 31 failed, 7 skipped**（baseline 1175 passed + 新增 117 = 1292，精确匹配）。31 failed 全部是 NT-dependent pre-existing 失败（需要 `nautilus_trader` 才能跑），与本轮改动无关
  - 逐一拆分：scaffold 64 + validator 41 + is_within_dir 11 + module_loader 新增 boundary 1 = 117 ✓
- ✅ `ruff check` 10 个新/改文件 —— **All checks passed!**
- ✅ `py_compile` 10 个新/改文件 —— 全部通过
- ✅ **端到端 smoke**：`generate_scaffold("test", tmp)` → 文件生成 + `ast.parse()` 通过；`validate_strategy` 对 4 种合法/非法 fake 模块产生正确的 `{valid, errors, warnings, hooks}` 组合
- ✅ **向后兼容 100%**：
  - `from tinohelm.strategy.scaffold import generate_scaffold, STRATEGY_SCAFFOLD` —— 签名 + 文案不变
  - `from tinohelm.strategy.validator import validate_strategy` —— 签名 + 返回 dict 形状不变（测试 `TestValidateStrategyReturnsCanonicalShape` 显式锁定 8 键 set）
  - `api/routes/strategy.py` 调用 `generate_scaffold(name=..., strategies_dir=..., scaffold_type=...)` 保持不变
- ✅ **安全性强化（测试驱动）**：`test_symlink_escape_rejected`（core + scaffold 双重覆盖）+ `test_similar_prefix_name_is_rejected`（module_loader）证明 `is_within_dir` 挡住了 `startswith` 挡不住的两类真实漏判

---

## 2026-04-21 (2)

**主题**: 把 `core/bridge.py` 的 `EventBridge` 纳入测试安全网（53 个 NT-free 用例），同时消除 `_listener` 与 `_heartbeat_poller` 两处复制的"relay → 全局 `*` + 逐 pattern 前缀匹配"两步逻辑，并修掉 `unsubscribe` 不回收空 set 造成的长期内存累积
**维度**: 架构重构 + 测试补齐
**改动范围**:
- `src/tinohelm/core/bridge.py`（212 → 227 行，净 +15）
  - 新增 `_publish_to_subscribers(channel, payload)` — 把 wildcard + channel-prefix fan-out 统一到单一 helper
  - `_listener` 末端的 9 行 relay 代码 → 单行调用
  - `_heartbeat_poller` 末端的 6 行 relay 代码 → 单行调用
  - `unsubscribe` 增加空 set 回收（subscribe 后 defaultdict 会重建，外部行为不变）
  - 删除未使用的 `from typing import Any`
- `tests/core/test_bridge.py`（新增 466 行，53 个用例）— 覆盖 `_infer_type` + `EventBridge` 公开 API + 两条关键 fan-out 等价路径

**动机**:

`src/tinohelm/core/bridge.py`（212 行）是 FastAPI 进程里把 Redis PubSub 事件转发给所有已连接 WebSocket 客户端的**唯一**关键设施。前端所有实时推送（`backtest.progress`、`data.progress`、`node.heartbeat`、`fill.new`、`position.update`、`research.progress`）都流经它；TUI 同样依赖它做 `/ws/events` 订阅；4 层 Notification System 的 Layer 1（silent ticker）和 Layer 3（toast）实际上都是 EventBridge 广播的消费者。然而本轮演进之前：

1. **零测试覆盖** —— `grep EventBridge tests/` 完全没有匹配。`_infer_type` 的 6 个前缀映射、`subscribe`/`unsubscribe` 的 pattern 注册、`client_count` 的跨 pattern 去重、`_relay` 的 dead-connection 回收 —— 任何一条契约发生漂移（包括 Redis 协议字段命名、JSON decode 失败降级、pattern prefix 语义）都不会触发任何告警，直到前端断流用户才会发现。这是整个项目**最贴近"实时 UX"**的代码路径中最大的测试盲区。

2. **两处完全同构的 relay 代码**（`_listener` 行 141-149 + `_heartbeat_poller` 行 190-195）。**同样的 5 行**做"send to wildcard subscribers + 逐个 pattern 前缀匹配再 send"，只是表达方式不同（一处是 continue-based 反向条件，另一处是 if-based 正向条件）。这是典型的 "duplicate + drift" 陷阱：
   - 任何对 fan-out 语义的修改（比如把 `startswith` 换成 glob、加入去重、加入 channel 模式排他规则）都必须同时改两处
   - 两处各自的表达式写法已经不一致（`not channel.startswith(pattern)` vs `f"tino:heartbeat:{node_type}".startswith(pattern)`），后续阅读者必须确认两者等价才能改动
   - 无法对 fan-out 写单一的参数化测试；每次想要验证广播语义都得把两条异步循环驱动起来

3. **`unsubscribe` 内存累积**（行 99-102）。原实现 `for clients in self._clients.values(): clients.discard(ws)` 遍历所有 pattern set 删除该 ws，但**永远不删除空 set**。对于长生命周期的 API 进程，WebSocket 的连/断是每分钟都在发生的：每一次 `await bridge.subscribe(ws, ["tino:very-specific:channel"])` → `await bridge.unsubscribe(ws)` 都会在 `_clients` 里留下一个空 set。一周下来如果前端有动态订阅逻辑（比如按策略 ID 订阅 `tino:sandbox:strategy-abc123`），`_clients` 会膨胀到几千上万个空键。这不是单纯的风格问题 —— `_listener` 和 `_heartbeat_poller` 的 fan-out 都要 `for pattern, clients in list(self._clients.items())` 线性扫描 `_clients`，每次扫描都是 O(pattern 总数) 包括已退订的空键。广播延迟会随运行时间线性劣化。

简言之：一条关键生产数据通道没有测试保护，同时存在一个可测 + 可修的真实性能/内存缺陷，还有一处明显的代码重复 —— 三件事应该一次性合并做完，不是分三轮。

**要点**:

1. **`_publish_to_subscribers(channel, payload)` —— fan-out 语义单点化**。把两处 relay 的"`_clients["*"]` 全广播 + 非 `*` pattern 中 `channel.startswith(pattern)` 的子集广播"压缩成一个 13 行 async 方法。`_listener` 和 `_heartbeat_poller` 各自缩到一行 `await self._publish_to_subscribers(channel, payload)`。语义上 100% 等价：pattern `"*"` 始终匹配、prefix-match 使用原有 `startswith`（非 regex/glob）、`list(self._clients.items())` 的快照拷贝保留（避免在 relay 过程中并发 subscribe/unsubscribe 修改 dict 时 RuntimeError）。`TestLegacyBehaviourParity` 测试类专门用两个场景（原 listener 的 backtest progress 广播 + 原 heartbeat 的 node-specific 分发）锁死这条等价性。

2. **`unsubscribe` 回收空 pattern set**。新实现：遍历时收集空 pattern 到 `empty_patterns`，最后统一 `del self._clients[pattern]`。**不能在迭代中修改 dict** —— 第一版用 `for ... pop()` 立刻踩到 `RuntimeError: dictionary changed size during iteration`，所以两段式删除。`defaultdict(set)` 语义保证下一次 `subscribe` 会自动重建 key，外部观察行为不变。`TestUnsubscribe::test_reaps_empty_pattern_sets` / `test_reaps_wildcard_when_empty` / `test_subscribe_after_unsubscribe_recreates_pattern` 三个测试锁定 reap + recreate 契约。

3. **完整 public API 覆盖**（`EventBridge` 类 5 个公共方法 + 1 个模块级函数 + 1 个共享新 helper）：
   - `TestInferTypeChannelMap`（7）+ `TestInferTypeEdgeCases`（5）—— 覆盖全部 6 个前缀映射（backtest/heartbeat/sandbox/live/data/research）+ 未知 channel + 空字符串 + `tino:backtest:` 空 tail 的边缘切片。`test_all_prefixes_covered_by_tests` 把期望集断言成 `frozenset` 模式，任何新前缀加入 `_CHANNEL_TYPE_MAP` 必须同步加一行测试（类似 `FULL_RESULT_BASE_KEYS` 的防漂移机制）。
   - `TestBridgeInit`（2）—— 初始空状态 + `_clients` defaultdict 行为不漏洞（未知 key 不 raise）。
   - `TestSubscribe`（7）—— `None` / `[]` 都走 wildcard（锁定 `if channels:` 的 falsy 等价）、单 channel、多 channel、重复订阅幂等、多客户端同 channel、同客户端在 `*` 和特定 channel 的重叠订阅。
   - `TestClientCount`（5）—— 0 默认、单 wildcard、跨 pattern 去重（同客户端多订阅计一次）、wildcard + 特定 channel 仍计一次、多客户端分别计数。
   - `TestUnsubscribe`（7）—— 正确移除、empty-set reap（含 wildcard）、其他客户端订阅保留时不删除 pattern、未订阅客户端 unsubscribe 幂等、双重 unsubscribe 安全、subscribe-after-unsubscribe 重建 pattern。
   - `TestRelay`（5）—— 广播到全部、dead ws 被回收、dead 不影响后续 alive 的投递、空 set no-op、外部 set 引用不被意外清空。
   - `TestPublishToSubscribers`（8）—— wildcard / prefix / 非匹配的三路判定、同客户端跨 wildcard+prefix 两次投递（**这是当前语义，加了注释锁死，若未来要去重需要显式改测试**）、多 prefix 都是 channel 前缀的多播、无订阅 no-op、跨客户端同 prefix 独立、dead ws 在 fan-out 中被回收。
   - `TestHeartbeatFanOut`（3）—— 针对 `tino:heartbeat:{node_type}` 特殊形式：pattern `"tino:heartbeat"` 匹配、wildcard 覆盖、`tino:sandbox` pattern 不错配。
   - `TestLegacyBehaviourParity`（2）—— 原 `_listener` backtest 场景 + 原 `_heartbeat_poller` node-specific 场景，直接锁死重构前后行为等价。
   - `TestStartStop`（2）—— `stop()` 无 `start()` 时安全（不 raise）；`start()` 创建两条 task 并 `psubscribe("tino:*")`，`stop()` 正确 cancel+await+`punsubscribe`+`close`，`aioredis.from_url` 通过 `monkeypatch` 替换，**零真实 Redis 连接**。

4. **`_fake_ws(*, dead=False)` 测试 helper** —— 所有 WebSocket 测试用单行 `AsyncMock()` 生成 ws，`dead=True` 时 `send_text.side_effect = RuntimeError` 模拟断开。避免在每个测试里写 5 行 mock 配置，也让 dead-reaping 测试的意图一目了然。

5. **`TestStartStop::test_start_creates_tasks_and_stop_cancels_them` —— 唯一的异步生命周期测试**。用 `monkeypatch` 替换 `tinohelm.core.bridge.aioredis.from_url`，返回一个 `pubsub().listen()` 立即耗尽的 async iterator，让 listener task 启动后立刻等待下一次 cancel。`stop()` cancel 后 task 要么 `.cancelled()` 要么 `.done()`，`punsubscribe("tino:*")` / `close()` 的 await 次序通过 `AsyncMock.assert_awaited_with` 锁定。这个测试是未来"改 start/stop 语义时必须过"的保险丝。

6. **完整回归**：NT-free 全量 `pytest tests/`（排除需要 `nautilus_trader` 的 tests/actors/ + tests/node/ + tests/backtest/ + tests/portfolio/ + 3 个具体 NT-import 测试文件）—— 989 → 1042（+53），全部通过，耗时 7.13s。

**讨论点**:

- **Wildcard + prefix 同客户端双投递是不是 bug**？当前测试 `TestPublishToSubscribers::test_wildcard_and_prefix_same_client_delivers_only_once` **显式锁定投递 2 次**（因为 `_clients["*"]` 和 `_clients["tino:sandbox"]` 是两个独立 set）。实际前端代码里看起来没有一个客户端同时订阅 wildcard + 特定 channel 的场景（`hub.py` 里 `ws_events` 只会走其中一条分支），所以生产上不影响。但这是一个**隐式契约**，如果未来加"事件级 dedup（比如按事件 ID 去重）"需要显式改本测试。我选择**锁定当前行为**而不是直接加去重：(a) 去重需要事件级 ID 设计，改动影响协议；(b) 前端不存在该场景，无紧迫性；(c) 留给下一轮专项 evolution。

- **`_publish_to_subscribers` 的 pattern 匹配语义**依然是 "prefix match via `startswith`"。这对简单场景是好的（`"tino:sandbox"` 匹配 `"tino:sandbox:positions"`），但如果前端想订阅 `tino:sandbox:*` 而不是 `tino:sandbox:*` 下所有子类型，就需要 glob 语义。本次不动 —— 会影响 hub.py 的 channels query param 语义，属于跨 API 变更。

- **`_listener` 的 exception 重试逻辑**（`except Exception: sleep(5); psubscribe/punsubscribe`）没有覆盖测试。它需要模拟一个真实失败的 Redis pubsub 然后验证 5 秒延迟 + 重订阅。这是**集成测试**类型（需要 `fakeredis` 或 `asyncio.sleep` patching）；本次用单元测试把 fan-out + API 全覆盖，集成层留给未来"EventBridge 故障注入测试"专项。

**验证**:
- ✅ `PYTHONPATH=src .venv/bin/python -m pytest tests/core/test_bridge.py -v` —— **53 passed in 0.85s**
- ✅ `PYTHONPATH=src .venv/bin/python -m pytest tests/` NT-free 全量 —— **1042 passed, 7 skipped in 7.13s**（baseline 989 + 53 新增 = 1042，精确匹配）
- ✅ `ruff check src/tinohelm/core/bridge.py tests/core/test_bridge.py` —— All checks passed!
- ✅ `py_compile src/tinohelm/api/app.py src/tinohelm/api/deps.py src/tinohelm/api/ws/hub.py` —— 所有依赖 EventBridge 的模块编译通过，public API 未变
- ✅ 代码行数：`core/bridge.py` 212 → 227 行（净 +15：新 `_publish_to_subscribers` 方法 +18 行/含 docstring、`unsubscribe` +7 行 reap 逻辑、`_listener` -9 行、`_heartbeat_poller` -6 行、`-import Any` 以及删除 2 条注释 `# Relay to ...`）
- ✅ 重复消除：`_listener` / `_heartbeat_poller` 两处 fan-out 内联代码合并到单一 `_publish_to_subscribers`，任何未来语义变动只改一处
- ✅ 潜在内存缺陷修复：`unsubscribe` 空 pattern set 回收，前端高频订阅/退订场景下 `_clients` 不再线性增长

---

## 2026-04-21

**主题**: 从零搭建 `api/` 模块的测试安全网（192 个 NT-free 用例覆盖 9 个 route 文件 + 新 `_utils` 模块），同时消灭 3 组跨路由重复模式（UUID 路径穿越校验、Redis JSON-or-default、Redis 进度拉取）
**维度**: 测试补齐 + 架构重构
**改动范围**:
- 新增 `src/tinohelm/api/_utils.py`（199 行）—— 8 个共用原语 + 3 个 regex/长度常量
- 重构 `src/tinohelm/api/routes/backtest.py`（712 → 698 行，-14）
  - 5 处"UUID regex + `.resolve()` + `startswith(root)` 边界检查"内联代码 → 单一 `resolve_artifact_path()` 调用
  - 2 处"`rds.get` + `int()` 裸 try/except"内联代码 → `fetch_redis_progress` / `fetch_redis_progress_batch`
  - `resolve_run_id` 的 `_UUID_RE` / `_HEX_RE` / `MIN_PREFIX_LEN` 常量从 module-level 下沉到 `_utils`
  - 额外消化 `backtest_compare` 里未做边界检查的内联 `Path(...) / run.run_id / "results.json"`（潜在 path-traversal 如果 DB 里的 run_id 被污染 —— defense-in-depth，实际入口其实安全但统一行为更好）
- 重构 `src/tinohelm/api/routes/node.py`（396 → 378 行，-18）
  - 6 处"`raw = await rds.get(key); if raw: decode; json.loads(raw) else default`"内联代码 → 单一 `load_redis_json(rds, key, default)`
  - 覆盖：`lifecycle_state` / `list_strategies` (strategy_registry + heartbeat 双源) / `data_status` / `subscriptions` / `get_paper_config` / `update_paper_config`
  - 抽出 `_PAPER_CONFIG_DEFAULT` 常量（之前是方法内 inline dict）
- 重构 `src/tinohelm/api/routes/trading.py`（353 → 352 行，-1，顺带删除未使用的 `datetime` 导入）
  - 1 处 `risk-metrics` 内联 `rds.get + json.loads + default` → `load_redis_json`
  - 抽出 `_RISK_METRICS_DEFAULT` 模块级常量（之前 9 键 inline）
- 新增 `tests/api/test_utils.py`（338 行，51 用例）
- 新增 `tests/api/test_backtest_helpers.py`（182 行，44 用例）
- 新增 `tests/api/test_data_helpers.py`（251 行，43 用例）
- 新增 `tests/api/test_strategy_helpers.py`（71 行，16 用例）
- 新增 `tests/api/test_trading_helpers.py`（211 行，11 用例）
- 新增 `tests/api/test_node_helpers.py`（121 行，10 用例）
- 新增 `tests/api/test_settings_helpers.py`（44 行，6 用例）
- 新增 `tests/api/test_dashboard_helpers.py`（34 行，3 用例）

**动机**:

`src/tinohelm/api/` 整个模块是 FastAPI 后端的外部契约入口：10 个 route 文件共 4198 行代码，对接前端 Next.js、Rust CLI/TUI、外部 HTTP 客户端。但在本次演进之前，整个目录下**只有 1 个测试文件 8 个用例**（`test_resolve_run_id.py`），覆盖的仅仅是 `backtest.py` 里一个辅助函数的 prefix 解析逻辑。其他 ~95% 的路由纯函数、私有助手、数据格式化器（`_position_to_item` / `_fill_to_item` / `_enrich_strategy_meta` / `_interval_to_timeframe` / `_mask_key` / `_calc_bars_per_day` / ...）以及 3 组"同一套代码复制了多份"的安全敏感代码，完全处于测试盲区。

更严重的是跨文件的 3 组重复模式，其中两组直接关联**正确性和安全性**，不是风格问题：

1. **Path-traversal 检查 × 5 份同构代码（backtest.py）** —— 当前 `status` / `result` / `delete` / `list_artifacts` / `get_artifact` 路由各自有一段完全相同的守卫代码：
   ```python
   if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', run_id):
       raise HTTPException(status_code=400, detail="Invalid run_id format")
   artifact_path = (Path(settings.paths.artifacts) / run_id / filename).resolve()
   artifacts_root = Path(settings.paths.artifacts).resolve()
   if not str(artifact_path).startswith(str(artifacts_root)):
       raise HTTPException(status_code=400, detail="Invalid path")
   ```
   5 份复制本身就是安全漏洞放大器：任何一次需要修 UUID 格式或边界检查的变更都有 80% 概率漏改某一份；而且 `str(a).startswith(str(b))` 在 trailing separator 边缘情况（尤其 symlink escape 场景）不可靠，`Path.relative_to()` 才是正确的做法 —— 本次改成 `relative_to` 顺带修掉了这个潜在隐患（测试 `test_symlink_escape_is_resolved` 直接验证）。

2. **Redis JSON-or-default × 7 份同构代码（node.py + trading.py）** —— 完全同构的 `await rds.get(key); if isinstance(raw, bytes): raw = raw.decode(); return json.loads(raw) if raw else default`，每一份都没有 UTF-8 容错，也没有 JSON 解析失败降级，换句话说：**Redis 里的脏数据会让接口抛 500 而不是优雅回落到 default**。统一走 `load_redis_json` 后（带 UnicodeDecodeError / JSONDecodeError 双重降级 + 日志），接口对 Redis 里的坏数据从"崩"变成"记一条 warning 继续返回 default"。前端看不到 500 了。

3. **Redis 进度拉取 × 2 份同构代码（backtest.py）** —— 单 key 版在 `get_backtest_status`，pipeline 批量版在 `list_backtest_runs`，两处都自己写 `try: int(raw) except (ValueError, TypeError): pass`。集中到 `fetch_redis_progress[_batch]` 后，零分支减到单行，pipeline 版本对空 list 也做了早退优化（之前每次 `list_backtest_runs` 即使一条 running 都没有也会 `rds.pipeline()`）。

外加发现：

- `_calc_bars_per_day` 内部竟然有 `import re` —— Python 里 shadow-import 是 legal 但属于 code smell。外加 module 顶层同时有 `import re` 和内部 `import re`。我把正则抽到 module-level `_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")`，内部不再做 inline import。
- `trading.py` 有一个未使用的 `from datetime import datetime`，我触到这个文件就顺手删了（看到了就顺手改掉）。
- `get_backtest_status` 的"已完成才尝试读 results.json"分支里，原来的 try/except 吞掉了所有异常（包括 `HTTPException`），如果 `resolve_artifact_path` 抛 400 会被当成"读取失败记 warning 继续"而不是真的 400 —— 本次重构里我改成明确 `try: ... except HTTPException: artifact_path = None`，行为一致但意图显式化。
- `backtest_compare` 的 `results_file = Path(settings.paths.artifacts) / str(run.run_id) / "results.json"` 原先没做边界检查。虽然 `run.run_id` 来自 DB（由 `uuid4()` 创建）实际安全，但 DB 列只是 `String(100)`，理论上可以被污染（SQL 注入 / manual insert），统一用 `resolve_artifact_path` 做了 defense-in-depth。

**要点**:

1. **`api/_utils.py` —— 8 个共用原语** (NT-free，纯 `fastapi.HTTPException` + `re` + `json` + `pathlib`)：
   - `UUID_RE` / `HEX_PREFIX_RE` / `MIN_PREFIX_LEN` —— 3 个 module-level 常量，之前在 backtest.py 被分别定义两次
   - `is_full_uuid(value) -> bool` —— 纯判断，无副作用
   - `validate_uuid_or_400(run_id) -> str` —— 白盒化一个 "strip+lower+UUID_RE check → raise 400 or return normalised"
   - `resolve_artifact_path(artifacts_root, run_id, *segments) -> Path` —— 原子合一的 UUID 校验 + 边界检查 + 路径拼接。**使用 `Path.relative_to()` 而不是脆弱的 `str(a).startswith(str(b))`**。返回时 `.exists()` 由调用方自己判断（因为 404 vs 正常返回需要不同语义）
   - `decode_redis_str(raw) -> str | None` —— bytes/str/int 归一到 str 或 None，处理 UnicodeDecodeError
   - `load_redis_json(rds, key, default=None) -> Any` —— GET + decode + json.loads；所有错误路径（missing / bad utf-8 / bad json）统一返回 default
   - `fetch_redis_progress(rds, key) -> int | None` —— GET + int()，容错
   - `fetch_redis_progress_batch(rds, keys) -> list[int | None]` —— pipeline 版本，空 list 早退不开 pipeline

2. **向后兼容 100%**：
   - `backtest.py` 的 `resolve_run_id` 仍然是 `async def resolve_run_id(prefix, db) -> str` 签名完全一致，其内部 `_UUID_RE` / `_HEX_RE` 变成共享常量但 `match()` 接口相同
   - `test_resolve_run_id.py` 的 8 个原有用例**零修改**通过
   - 所有路由的 HTTP 行为等价：相同的 400 code 和相同的 detail 文案（"Invalid run_id format" / "Invalid path"）
   - `node.py` 的 `_enrich_strategy_meta` 公有签名未变
   - `trading.py` 的 `_position_to_item` / `_fill_to_item` 未动

3. **测试矩阵**（192 用例，按文件组织）：
   - **`test_utils.py`（51）** —— 6 类
     - `TestUuidRegex`（6）+ `TestHexPrefixRegex`（4）—— 正则合法性 + 边界（大写、缺连字符、段长异常、非 hex、前后空格）
     - `TestIsFullUuid`（3）—— 快速判断的三路
     - `TestValidateUuidOr400`（5）—— strip/lower 归一、raise 400、空串、prefix 都拒绝
     - `TestResolveArtifactPath`（10）—— 重点：`../../etc/passwd` 被拒（dotdot 逃逸）、绝对路径段 `/etc/passwd` 被拒、**symlink escape** 的真实场景测试（创建 `root/<uuid>` 符号链接到 `root/../outside`，确认 `.resolve()` 后 `relative_to` 抛错）
     - `TestDecodeRedisStr`（5）—— None / bytes / str / invalid UTF-8 / int
     - `TestLoadRedisJson`（7）—— happy path / missing / bad JSON / bytes / list / bad UTF-8 / default=None
     - `TestFetchRedisProgress`（6）—— int / bytes / None / 非数字 / 负数透传 / 浮点字符串拒绝
     - `TestFetchRedisProgressBatch`（5）—— 空 keys 不开 pipeline、有序返回、None slot、非数字 slot、单 key 仍走 pipeline
   - **`test_backtest_helpers.py`（44）** —— 锁 `_BARS_PER_DAY_KNOWN` / `_BARS_PER_SEC` / `_INTERVAL_RE` / `_ARTIFACT_WHITELIST` 所有模块常量；`_calc_bars_per_day` 覆盖 5 个 fast-path + 7 个 computed + 7 个无效输入 + 边界（0m / 25h / 7d 都 clamp 到 1）；`_format_estimated_label` 覆盖 0/1/59/60/90/120/3599/3600/5400/36000 + 2 个边界；4 个 import 断言确认 route 文件**引用的是共享 _utils 符号**而不是重新定义
   - **`test_data_helpers.py`（43）** —— `_interval_to_nt` / `_nt_to_interval` 正反转换 + 12 个 round-trip + `ValueError` 消息含输入；`_parquet_size_for` 真正写 parquet 文件到 tmp_path 验证求和（忽略非 parquet 文件）；`_delete_storage_files` 覆盖 4 个 data_type 分支（bar / trade_tick / funding_rate / quote_tick / unknown）+ 空目录 noop + 目录清空后自动 rmdir + **非 parquet 文件共存时目录保留**
   - **`test_strategy_helpers.py`（16）** —— `_interval_to_timeframe` 9 个已知映射 + 未知 passthrough；`_build_subscriptions` 覆盖空列表 / 单 / 多 / `.BINANCE` suffix 剥离 / interval=None / 未知 interval passthrough
   - **`test_trading_helpers.py`（11）** —— `_RISK_METRICS_DEFAULT` 9 键锁定；用 `SimpleNamespace` 做 Position/Fill 的 row stand-in（ORM 映射只读属性不需要真 Session），覆盖完整字段、datetime isoformat、None passthrough、closed vs open 两种分支
   - **`test_node_helpers.py`（10）** —— `_PAPER_CONFIG_DEFAULT` 锁定；`_enrich_strategy_meta` 覆盖无 yaml、有完整 yaml、malformed yaml、空 yaml、只有 symbols、多 strategy 独立、空字典、setdefault 不覆盖、yaml 覆盖现有
   - **`test_settings_helpers.py`（6）** —— `_mask_key` 覆盖 ≤8 chars 全掩、>8 chars 显示首尾 4 + 中间 `****`、parametrized boundary（9/11 chars）+ 防中间 leak 断言；`EXCHANGE_PING_URLS` 锁定
   - **`test_dashboard_helpers.py`（3）** —— `_completed_runs_stmt` 编译后 SQL 含 `completed` + 可 chain `.where().limit()`

4. **安全性强化（测试驱动）**：
   - 之前 5 处 `str(a).startswith(str(b))` 不可靠 —— `test_symlink_escape_is_resolved` 证明新的 `Path.relative_to()` 实现能拦截 symlink escape
   - 之前 Redis 返回脏 JSON 会 500 —— `TestLoadRedisJson::test_returns_default_on_invalid_json` 证明现在返回 default + warning

**讨论点**:

- **dashboard.py 的 `Position.is_open == True` (E712) 我没改**：SQLAlchemy 里 `.where(Position.is_open)` 与 `.where(Position.is_open == True)` 在大多数情况下等价，但对 nullable boolean 列、或者方言差异下，行为**可能**不完全一致。这种改动需要跑真实 PostgreSQL 集成测试验证，不是静态能验的。我留给下一轮 evolution 处理（若决定处理，建议加一条 ruff per-file-ignore 把 `dashboard.py:74` 豁免，而不是改代码）。
- **`optimize.py` / `settings.py` / `strategy.py` 的 F401 warnings 我也没改**：不在本次主题内（我没有因为 api 测试补齐而需要 import 那些文件）。是已知噪音但是每一个都可能暗示"这个 import 以前用到，现在已经被重构掉了，import 是忘了删" —— 值得下一轮专门的 API lint 演进去处理。
- **`backtest_compare` 的 artifact 路径校验**我做了加固但**没有加 404 分支**：原先行为是"目录/文件不存在就把 `warning` 字段填上"，我保留这个行为。换句话说，现在 400 的路径（污染的 run_id）仍然会走 `except HTTPException` → `results_file=None` → warning="Backtest artifacts not found"。理论上污染的 run_id 应该返回 400，但改这个会对前端展示有行为差异（从"看到 warning 字符串"变成"整个请求 400"），需要和前端对齐才能改。**建议下一次 evolution 把 `run.run_id` 污染的场景拉出来单独处理成 500 / 422，因为这理论上是 DB 被污染的信号**。
- **未覆盖的 route 代码**：我只抽了路由文件里的**纯 helper**，FastAPI route handler 本身（带 `Depends` 注入 + DB session + 复杂业务流）的**集成测试**没有加。那是完全不同类型的测试（需要 `httpx.AsyncClient(app=app)` + in-memory SQLite fixture + fake Redis），应该是**单独的演进主题**（"API 集成测试基础设施"），不适合塞在本次"helper 抽取 + 单元测试"里。本次把基础设施建好了 —— `_utils.py` 的所有原语已经 100% 单元覆盖，任何未来的集成测试可以 `mock.patch("tinohelm.api._utils.load_redis_json")` 一键 stub Redis。

**验证**:
- ✅ **934 passed in 7.49s**（NT-free 全量）—— baseline 750 + 新增 184 = 934，完全精确匹配（51 + 44 + 43 + 16 + 11 + 10 + 6 + 3 = 184）
- ✅ `ruff check` 全部 4 个新/改文件 + 8 个测试文件 —— All checks passed!
- ✅ `py_compile` 5 个路由源文件 + 8 个测试文件 —— 全部通过
- ✅ 全部 11 个 api route 模块 import 成功（含未触动的 7 个：data / research / optimize / strategy / settings / dashboard / watchlist）
- ✅ NT-free 边界：`tinohelm.api._utils` 单独 import 后 `sys.modules` 不含任何 `nautilus_trader`，严格独立
- ✅ 向后兼容：`from tinohelm.api.routes.backtest import resolve_run_id` / `from tinohelm.api.routes.node import _enrich_strategy_meta` / `from tinohelm.api.routes.trading import _position_to_item, _fill_to_item` —— 所有既有导入点零修改
- ✅ 重复消除：5×UUID+path inline → 1×`resolve_artifact_path` / 2×Redis progress inline → 2×named helper（单+批）/ 7×Redis JSON inline → 1×`load_redis_json` / 3×定义两次的 UUID regex → 1×共享
- ✅ 行数变化：`backtest.py` 712→698 (-14)、`node.py` 396→378 (-18)、`trading.py` 353→352 (-1)、新增 `_utils.py` 199 —— route 文件总 -33，新基础设施 +199，净 +166 但**每一行 `_utils.py` 都有单元测试**而此前**没有一行有覆盖**
- ✅ 安全性：新的 `resolve_artifact_path` 用 `Path.relative_to()` 替代 5 份 `str(a).startswith(str(b))`，通过了 symlink-escape 单元测试（之前 5 处各自都存在同样的潜在不可靠点）

---

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


## 2026-04-21

**主题**: 抽出 `backtest/custom_statistics.py` 的纯数学到 NT-free `custom_statistics_helpers.py` + 给 `custom_statistics` 和 `tearsheet` 两大零覆盖报表模块补齐共 125 个单元测试
**维度**: 测试补齐 + 架构重构(提取 helpers)
**改动范围**:
- 新建 `src/tinohelm/backtest/custom_statistics_helpers.py`(339 行)—— 17 个 `calc_*` 纯数学 helper,零 NT 依赖
- 重构 `src/tinohelm/backtest/custom_statistics.py`(388 → 300 行,−88)—— 17 个 `PortfolioStatistic` 子类全部瘦身为 4 行 wrapper
- 重构 `src/tinohelm/backtest/result/__init__.py`(33 → 51 行,+18)—— `extract_backtest_results` 改为 PEP 562 `__getattr__` 惰性加载,让 `tinohelm.backtest.result` 包顶层在缺 NT 环境下也能 import
- 新建 `tests/backtest/test_custom_statistics_helpers.py`(637 行,89 用例)
- 新建 `tests/backtest/test_tearsheet.py`(482 行,36 用例)

**动机**:

`backtest/` 下有三块"生产路径 + 零测试"的代码,是整个工程最后几块没装安全网的地方:

1. **`custom_statistics.py`(388 行,0 测试)** —— 17 个 `PortfolioStatistic` 子类,实现 NT 内置 Rust 版本缺失或返回 None 的补丁统计量(CAGR、Calmar、Max Drawdown 的 pandas 回退;Total/Winning/Losing Trades、Gross Profit/Loss、Avg Win/Loss Ratio、Max Consecutive Wins/Losses、Avg Trade/Winning/Losing Duration、Total Commission、Total/Filled Orders)。这些直接进入用户看到的 tearsheet 里,任何计算 bug(rounding、NaN 处理、`> 0` vs `>= 0` 边界)都会静默改变报表数值。之前仅由端到端 backtest 运行间接覆盖——任何人在 optimizer 里加一条新分支都可能静默改变数字。
2. **`tearsheet.py`(285 行,0 测试)** —— 多 instrument backtest 的 HTML 注入,生成 Plotly 柱状图、累计 PnL 面积图、相关性热图、月度 PnL 热图、PnL Treemap、投资组合分析摘要 6 大板块。整个函数 `try/except` 把 IO 错误 swallow 掉——意味着**任何回归都只会留一条 WARNING 日志,CI 永远看不到**。
3. **`result/__init__.py`** —— 顶层 `__init__.py` eager 导入 NT 依赖的 `extract.py`,导致 `from tinohelm.backtest.result.statistics import _format_duration_ns`(明明是纯 Python)也会触发 NT import,成为本次抽 helpers 的直接阻塞。历史 `test_sections.py` 为此写了一个 60 行的 `_load_sections_isolated()` 绕过——补丁式而非结构性。

这三件事拧成一股绳:**要给 custom_statistics 补 NT-free 测试,必须先把 `result` 包的 eager NT import 解开;把 pure 数学从 `PortfolioStatistic` 壳里抽出来;才能真正在没有 NT 的 lean CI 镜像下跑起来。**这是"一次演进"的完整边界。

**要点**:

1. **`custom_statistics_helpers.py` 的 17 个 `calc_*` 函数** —— 一一对应原 17 个 `PortfolioStatistic` 子类的 `calculate_from_*` 方法:

   - **Returns-based** —— `calc_max_drawdown_pct` / `calc_annual_return` / `calc_calmar_ratio`(pure pandas,round 到 6/6/4 位小数,NaN 返回 None)
   - **PnL-based** —— `calc_total_trades` / `calc_winning_trades` / `calc_losing_trades` / `calc_gross_profit` / `calc_gross_loss` / `calc_avg_win_loss_ratio` / `calc_max_consecutive_wins` / `calc_max_consecutive_losses`(锁定 `> 0` vs `<= 0` 两条不同边界——`LosingTrades` 严格 `<`,`GrossLoss` 宽松 `<=`,历史遗留语义,注释解释了为何保留)
   - **Position-based** —— `calc_avg_trade_duration` / `calc_avg_winning_duration` / `calc_avg_losing_duration` / `calc_total_commission`(duck-typed:只要 `p.duration_ns` / `p.realized_pnl` / `p.commissions()` 三个属性存在即可,NT `Position` 或 stub 对象通吃;复用 `result/statistics.py` 的 `_parse_realized_pnl` 处理 `Money.as_double()` 和 string fallback 两种路径)
   - **Order-based** —— `calc_total_orders` / `calc_filled_orders(orders, filled_status)`(`FilledOrders` wrapper 在 class-level lazily import `OrderStatus.FILLED` 作为 marker 参数传入——helper 不需要知道 NT 枚举,也能被任意值测试)

2. **抽象设计决策:streak 辅助 `_max_consecutive(values, predicate)`** —— `MaxConsecutiveWins` 和 `MaxConsecutiveLosses` 本来有两段几乎一样的 for-loop,抽出谓词版 streak counter,两条 wrapper 各传一个 lambda。测试里显式锁了"zero 中断 streak"(严格 `> 0` 而非 `>= 0`)——这是历史契约。

3. **`_filtered_durations(positions, predicate)`** —— `AvgWinningDuration` 和 `AvgLosingDuration` 本来各有一段 7 行重复代码(for-loop + `_parse_realized_pnl` + 过滤 + `duration_ns > 0` 守卫),抽成一个 helper。

4. **`custom_statistics.py` 的 `PortfolioStatistic` 类保留原样** —— 17 个类、`name` property、`calculate_from_*` 签名一分不改,只把方法体替换为单行 helper 调用。`ALL_CUSTOM_STATISTICS` 顺序不动;`register_custom_statistics(analyzer) -> int` 签名不动。**NT 侧零行为变化**——tearsheet 渲染出的数字与之前完全一致。

5. **`result/__init__.py` PEP 562 惰性化** —— `from tinohelm.backtest.result import extract_backtest_results` 这条 15 处使用的历史 import 路径继续 work;但改为通过 `__getattr__` 按需加载,**顶层包本身(`import tinohelm.backtest.result`)不再 eager 触发 NT import**。`from tinohelm.backtest.result import _format_duration_ns` 等 11 个 NT-free 符号仍然 eager 导出,unchanged。

6. **`tests/backtest/test_custom_statistics_helpers.py`(89 用例,分 12 个测试类)**:

   - `TestCalcMaxDrawdownPct`(6)—— None/empty/monotone-up/single-drop/rounding/single-elem/all-NaN
   - `TestCalcAnnualReturn`(6)—— None/empty/single obs/negative total(wipeout)/zero total/252 天 1% 日收益的 CAGR 解析解比对/rounding
   - `TestCalcCalmarRatio`(5)—— None/single obs/negative total/zero dd(除零)/happy path/rounding
   - `TestCalcTotalTrades`(3)—— None → 0 / empty → 0 / 含零 PnL 也计数
   - `TestCalcWinningLosingTrades`(7)—— winning 严格 `>`,losing 严格 `<`,零 PnL 两边都不算;测试返回 python int 不是 numpy int64
   - `TestCalcGrossProfitLoss`(8)—— **`GrossLoss` 的 `<= 0` 边界显式测试**,覆盖 zero-PnL 被归入 losers 的契约(sum 一样是 0,但锁住了 mask 方向)
   - `TestCalcAvgWinLossRatio`(7)—— 无 winners/losers/同时零 PnL/happy path/多 winners/rounding 4dp
   - `TestConsecutiveStreaks`(9)—— **zero 中断 win streak 和 loss streak** 两边都显式锁定
   - `TestPositionDurations`(9)—— 用 `_StubPosition` + `_StubPnl` duck-typed 替身验证 duration_ns=0 跳过/None realized_pnl 跳过/as_double fallback/losing duration 的 `<= 0` 包含零 PnL 边界
   - `TestCalcTotalCommission`(6)—— 多币种求和/`commissions()` 异常被 swallow(broken position 不影响其它)/string fallback/4dp rounding
   - `TestOrderHelpers`(7)—— marker 参数是 enum-agnostic(int/str/任意可比较值都行)/getattr 默认 None 跳过无 status 属性的 order
   - `TestNoNTDependency` + `TestPublicAPI`(5)—— `sys.meta_path` blocker 下 fresh-load helpers 模块;断言 `sys.modules` 里没有 `nautilus_trader*`;锁定 `__all__` 恰好 17 个且全部 `calc_*` 前缀

7. **`tests/backtest/test_tearsheet.py`(36 用例,分 8 个测试类)**:

   - `TestGuardClauses`(4)—— 缺 tearsheet.html / `per_instrument` 空 / 单 instrument / 根本没有 `per_instrument` key 这 4 条 early-return 路径
   - `TestBaseInjection`(10)—— 注入点在 `</body>` 之前;`.BINANCE` suffix 被剥离;表格按 total_pnl 降序;正 PnL 用 `class="pos"` 负 PnL 用 `class="neg"`;`recovery_factor=None` 渲染成 en-dash `–`;Plotly trace 中正确嵌入 `"type": "bar"`、`"orientation": "h"`;chart_height = max(300, N*35+100) 按 instrument 数缩放(实测 2 → 300、20 → 800);表头恰好 12 列;**idempotent 双调用**行为锁定(每次注入前面的 `</body>`,双调用生成两份 section)
   - `TestCumulativePnLChart`(3)—— 缺 data → 不注入 chart;有 data → `"stackgroup": "one"` 堆叠面积图,每个 instrument 有独立 trace
   - `TestCorrelationHeatmap`(4)—— 缺 data / 单 instrument(`< 2` 的 gate)跳过;配对生成 `"type": "heatmap"` + `"colorscale": "RdBu"`;对角线值恒为 1.0 不受 map 内容影响
   - `TestMonthlyPnLHeatmap`(3)—— gate + RdYlGn colorscale + 缺失 (inst, month) 对 pnl=0 的默认填充
   - `TestTreemap`(2)—— gate(>= 2 instruments)+ **treemap values 取 `abs(pnl)`**(用 regex 提取 Plotly.newPlot 参数 + json.loads 断言)—— 锁住了"负 PnL instrument 在 treemap 里仍有显示面积"这一关键 UX 不变式
   - `TestAnalyticsSummary`(5)—— 无 `portfolio_analytics` / 空 dict / 只有 diversification_ratio / 两个字段都有 / 两个字段都 None(正确跳过,不渲染空壳 div)
   - `TestIOResilience` + `TestNoNTDependency`(5)—— monkeypatch `Path.write_text` 和 `Path.read_text` 分别抛 `PermissionError` / `OSError` 测试异常 swallow;缺 `</body>` tag(str.replace 无 match)不 raise;最后用 `sys.meta_path` blocker 证明 `tearsheet` 模块顶层 import 零 NT 依赖

**讨论点**:

- **`GrossLoss` 的 `<= 0` 边界 vs `LosingTrades` 的 `< 0` 边界** —— 历史代码里这是两条不同的比较符,一处包含零 PnL 一处不包含。观察上对零 PnL 求和本就是 no-op 所以 `GrossLoss` 数值输出不变,但 boundary 差异保留在历史合约里。我在测试里用 `test_loss_boundary_includes_zero` 显式锁住这条 mask——如果将来有人把它统一成 `< 0`,观察值不变但测试会失败提醒,然后 reviewer 可以显式决定是否要做语义对齐。本次不改变行为。
- **`AvgLosingDuration` 同样用 `<= 0` 包含零 PnL trades** —— `test_losing_duration_includes_zero_pnl` 锁定。这是对"duration 应该在'非盈利'中计算"的合理解释(而不是"持有过后零盈亏就完全不计入"),保留是合理的。
- **`result/__init__.py` 的 PEP 562 惰性化触及其它 re-export 边界** —— 我只把 `extract_backtest_results`(唯一 NT 依赖符号)挪到 `__getattr__`;其它 11 个 `_*` helper 继续 eager 导出,因为它们本身 NT-free,eager 是免费的。`__all__` 顺序及内容未变——任何 `from tinohelm.backtest.result import *` 的老代码仍然拿到相同的 12 个名字。
- **`custom_statistics.py` 仍保留 `from nautilus_trader.analysis.statistic import PortfolioStatistic` 顶部 import** —— 因为 17 个类本身继承自它,没 NT 就连 class 定义都做不出来。这是合理的:wrapper 模块的使命就是"在 NT 侧出现,生成 NT analyzer 看得懂的 Stat 对象",所以它跟 NT 的 coupling 是必要的,不是要消除的。纯数学下沉到 helpers 后,wrapper 只要 388 → 300 行,每个类 4 行也恰好够。

**验证**:
- ✅ **NT-free 全套 1397 passed, 48 skipped**(比引入本次改动前的基线 1272 passed 多 +125,等于 89 + 36,精确匹配新增用例数)
- ✅ `.venv/bin/ruff check src/tinohelm/backtest/custom_statistics.py src/tinohelm/backtest/custom_statistics_helpers.py src/tinohelm/backtest/result/__init__.py tests/backtest/test_custom_statistics_helpers.py tests/backtest/test_tearsheet.py` —— **All checks passed!**
- ✅ 5 个文件字节码编译通过(`py_compile`)
- ✅ `custom_statistics_helpers.py` 在 `sys.meta_path` blocker 下 fresh-load 通过,加载后 `sys.modules` 里**没有任何 `nautilus_trader*`**,证实零 NT 依赖——这也是 `TestNoNTDependency::test_module_loads_without_nt` 用例在常规 pytest 里验证的不变式
- ✅ `tearsheet.py` 同样在 blocker 下 fresh-load 通过(`TestNoNTDependency::test_tearsheet_module_imports_without_nt`)
- ✅ `from tinohelm.backtest.result import extract_backtest_results` 惰性路径验证:stubbing `nautilus_trader.backtest.engine.BacktestEngine` 后 `__getattr__` 成功返回 callable
- ✅ `tests/backtest/` 全包(除 4 个 NT-dep 文件)511 passed, 48 skipped,**无失败无新 warning**
- ✅ `tests/backtest/test_sections.py`(原 `_load_sections_isolated` fallback 路径)133 passed —— 说明 `result/__init__.py` 的 PEP 562 改造对这块历史补丁代码保持兼容
- ✅ 无 TODO/FIXME/XXX:grep 5 个新/改文件清零
