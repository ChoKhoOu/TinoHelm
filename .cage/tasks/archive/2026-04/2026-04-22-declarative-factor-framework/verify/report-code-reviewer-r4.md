## 代码审查报告

**审查文件数**：16（factor/ 核心模块 + builtins/）
**问题总数**：9

### 按严重程度

- CRITICAL: 0
- HIGH: 3（应该修复）
- MEDIUM: 4（考虑修复）
- LOW: 2（可选）

---

### Stage 1: 规格合规

**通过**。所有 15 个 US 的核心要求均已覆盖：

- US-001：`@factor` 装饰器 + Registry 自动发现（AC-1.1~1.5）已实现
- US-002：字段别名表在 `alias.py` + `planner._infer_source` 中实现
- US-003：DataLayer Panel 构建已实现
- US-005：PandasBackend 算子集（AC-5.1）完整实现
- US-006：DAG Planner + Scheduler 已实现
- US-007：Evaluator 评估管道覆盖 IC/quantile/turnover/distribution/rating（AC-7.2）
- US-008：L2 磁盘缓存已实现
- US-013：12 个内置因子已用 `@factor` 重写，`trade_imbalance` 保留 NotImplementedError 存根（有明确注释）
- US-010：异步 Worker 已实现，含 DB 状态机和 Redis 进度推送

---

### 问题列表

---

#### [HIGH] `Evaluator._last_close` 是类变量而非实例变量，`batch_run` 多线程场景下存在 data race

**File**: `src/tinohelm/factor/evaluation/evaluator.py:339`

**Issue**:

```python
class Evaluator:
    # 无 __init__
    _last_close: pd.Series | None = None   # ← 类变量（Python 中类体声明的注解+默认值）
```

类文档注释声明 "deliberately stateless — safe to instantiate once and reuse across many factors"。然而 `_prepare_returns()` 每次调用都写 `self._last_close`，这在 `batch_run` 的 `ThreadPoolExecutor` 中（`orchestrator.py:474`）使用同一个 `Evaluator` 实例并发调用 `evaluate()` 时产生竞争：

1. 线程 A 调用 `_prepare_returns()`，写入 `self._last_close = series_A`
2. 线程 B 紧随写入 `self._last_close = series_B`
3. 线程 A 的 `evaluate()` 读 `self._last_close` 时得到 `series_B`

结果：IC decay 曲线静默地使用了错误 symbol 的 close 价格，无异常、无日志，数值看起来合法。

**Fix**: 将 `_last_close` 移入实例初始化，并消除副作用存储。更干净的方式是让 `_prepare_returns` 返回 `(fwd_series, close_or_none)` 元组：

```python
def __init__(self) -> None:
    self._last_close: pd.Series | None = None
```

或重构为纯函数接口，彻底消除实例状态。

---

#### [HIGH] `evaluate_full()` 第二次调用 `_prepare_returns()` 会再次覆盖 `_last_close`，破坏 IC decay 结果

**File**: `src/tinohelm/factor/evaluation/evaluator.py:287-291`

**Issue**:

```python
def evaluate_full(self, ...):
    result = self.evaluate(factor_values, returns, config)  # 内部调用 _prepare_returns，设置 _last_close
    # ...
    fwd_s = self._prepare_returns(returns, config)          # 再次调用，_last_close 再次被写入
    factor_s, fwd_s = factor_s.align(fwd_s, join="inner")
```

`evaluate()` 内部已经调用了 `_prepare_returns()` 并计算了 IC decay（第 229-231 行），此时 `_last_close` 已被正确使用。`evaluate_full` 随后**再次**调用 `_prepare_returns()`——这次调用重置了 `_last_close`（在 `evaluate()` 完成之后，所以对 IC decay 无影响）。但此逻辑依赖了脆弱的调用顺序，并且在上条 HIGH 的并发场景下，状态竞争更难追踪。此外，第二次 `_prepare_returns` 的调用是冗余的（数据已经在 `evaluate()` 中处理过），等于重复了 `forward_returns` 计算。

**Fix**: `evaluate_full` 应该从 `evaluate()` 的内部获取已对齐的 `factor_s` 和 `fwd_s`，或者将它们作为共享计算提取到调用方：

```python
# 重构：_compute_aligned 返回 (factor_s, fwd_s, close_or_none)
factor_s, fwd_s, close = self._compute_aligned(factor_values, returns, config)
result = self._run_fast_eval(factor_s, fwd_s, close, config)
# evaluate_full 继续使用同一组 factor_s/fwd_s 做 robustness
```

---

#### [HIGH] `_cross_symbol_worker` 中 kernel 调用约定与框架不一致，会在所有内置因子上引发 `TypeError`

**File**: `src/tinohelm/factor/evaluation/robustness.py:193`

**Issue**:

```python
sig_panel = kernel(close_df, params=factor_params, panels=sym_panels)
```

框架的标准 kernel 调用约定（`scheduler._call_kernel` 和 `orchestrator._call_kernel`）是：

```python
kernel(**factor_data)          # 位置无关，按 field_name 关键字传递
# 或
kernel(backend, **factor_data) # 仅在 param_names[0] == "backend" 时
```

但 `_cross_symbol_worker` 用的是 `kernel(close_df, params=..., panels=...)` ——位置参数 + 非标准关键字参数。

以 `ret_N` 为例，其签名是 `def ret_N(close: Panel, params=None) -> Panel`。正确调用是 `ret_N(close=close_df)`，而 `_cross_symbol_worker` 调用的是 `ret_N(close_df, params=factor_params, panels=sym_panels)`，其中 `panels` 参数不存在于签名中，必然报 `TypeError: ret_N() got an unexpected keyword argument 'panels'`。

这意味着调用 `evaluate_full(cross_symbol_args={...})` 时，所有内置因子的 `cross_symbol_ic` 都会失败（在 `except` 中被静默吞掉并记录 error），但调用者看到的是 `robustness["cross_symbol"] = [{"error": "..."}]`，可能被误解为数据不足而非代码错误。

**Fix**: 改为框架标准约定：

```python
spec = registry.get_spec(factor_name)
factor_data = {
    inp.field_name: sym_panels[inp.field_name]
    for inp in spec.input_specs
    if inp.field_name in sym_panels
}
sig_panel = kernel(**factor_data)
```

---

#### [MEDIUM] `Scheduler._call_kernel` 每次调用都执行 `inspect.signature()`，批量场景性能冗余

**File**: `src/tinohelm/factor/engine/scheduler.py:264`

**Issue**:

```python
import inspect
sig = inspect.signature(kernel)
param_names = list(sig.parameters.keys())
```

`inspect.signature()` 是较重的反射操作。在 `batch_run` 的 `ThreadPoolExecutor` 中，每个因子的每次执行都调用一次。kernel 调用约定（是否有 `backend` 参数）在 `@factor` 装饰时就可确定，不应在运行时每次检测。

**Fix**: 在 `@factor` 装饰器中检测一次并存入 `FactorSpec`（如 `needs_backend: bool = False`），或用 `functools.lru_cache` 缓存结果（同一 kernel 只反射一次）：

```python
from functools import lru_cache

@lru_cache(maxsize=None)
def _get_first_param(kernel: Any) -> str | None:
    params = list(inspect.signature(kernel).parameters.keys())
    return params[0] if params else None
```

---

#### [MEDIUM] 拓扑排序环路检测的 fallback 行为掩盖真实错误，应抛异常

**File**: `src/tinohelm/factor/engine/planner.py:264-266`

**Issue**:

```python
if not current_layer_names:
    # Cycle detected — fall back: put all remaining in one layer
    current_layer_names = remaining.copy()
```

当 `depends_on_factors` 中存在循环依赖时，所有剩余 spec 被塞入一层并继续执行，结果是未定义的（依赖方拿到的是未初始化的输出 Panel）。目前内置因子全部是 `layer 0`（无 inter-factor 依赖），此 fallback 不会触发。但扩展点文档明确声明支持 `depends_on_factors`，一旦使用且存在环路，错误将静默（只有奇怪的计算结果，无异常）。

**Fix**: 检测到环路时抛出明确异常：

```python
if not current_layer_names:
    raise ValueError(
        f"Cyclic dependency detected among factors: {sorted(remaining)}. "
        "Remove or break the cycle in 'depends_on_factors'."
    )
```

---

#### [MEDIUM] `turnover.py` 的日间 turnover 比较依赖每日样本数相等的假设，多 symbol 场景下可能错误

**File**: `src/tinohelm/factor/evaluation/turnover.py:64-66`

**Issue**:

```python
if prev_q is not None and len(prev_q) == len(curr_q):
    changed = (curr_q.values != prev_q.values).mean()
```

对于多 symbol 面板 `stack()` 后的数据，`len(prev_q) != len(curr_q)` 的情况（某 symbol 当日数据缺失导致样本数不同）会被直接跳过，低估 turnover。更危险的是，即使 `len` 相等，如果两日的 symbol 组成不同（例如周五有某 symbol、周一没有），`curr_q.values != prev_q.values` 会对比错误的 symbol 对，得到虚假的高/低 turnover。

**Fix**: 对齐 index 后再比较：

```python
common_idx = prev_q.index.intersection(curr_q.index)
if len(common_idx) > 0:
    changed = (curr_q.loc[common_idx].values != prev_q.loc[common_idx].values).mean()
    turnovers.append(changed)
prev_q = curr_q
```

---

#### [MEDIUM] `_cross_symbol_worker` 每次调用都重建 `Registry()` + `registry.scan()`，进程池中成本放大

**File**: `src/tinohelm/factor/evaluation/robustness.py:189-190`

**Issue**:

```python
registry = Registry()
registry.scan()
```

每个 worker 进程都重新扫描 factor 目录并重建 Registry。当 `symbols` 较多时（例如 20 个 symbol），这会在进程池中重复执行 `scan()` 20 次，每次扫描磁盘 I/O 并 import 所有 factor 模块。

另外，`factor_params` 通过 args tuple 跨进程序列化传递，如果 `factor_params` 包含不可 pickle 的对象（如 lambda、自定义类实例），会导致进程创建失败，且错误只在运行时出现，无编译时检查。

**Fix**: 在调用前验证 `factor_params` 可 pickle；将 Registry 构建逻辑提取到 worker 初始化阶段（通过 `initializer` 参数）：

```python
with ProcessPoolExecutor(max_workers=max_workers, initializer=_init_registry) as pool:
    ...
```

---

#### [LOW] `forward_returns()` 在 `close = 0` 时产生 `inf`，不受 `_scrub_result` 直接保护

**File**: `src/tinohelm/factor/evaluation/ic.py:34`

**Issue**:

```python
return close.shift(-period) / close - 1
```

当 `close = 0` 时产生 `inf`。加密货币价格正常情况下不为零，但异常数据（爬虫错误、Parquet 写入错误）时有出现。`inf` 会在 `compute_ic_series` 的 `np.isfinite(paired["fwd_ret"])` 过滤（`ic.py:55`）中被清除，所以不会污染 IC 计算。但 `_to_series` 到 `_prepare_returns` 的路径上不会过滤 `inf`，这是隐式依赖下游清理的脆弱设计。

**Fix**:

```python
with np.errstate(divide="ignore", invalid="ignore"):
    ret = close.shift(-period) / close - 1
return ret.replace([np.inf, -np.inf], np.nan)
```

---

#### [LOW] `compute_ic_summary` 使用 `np.std`（有偏，ddof=0）而非样本标准差（ddof=1）

**File**: `src/tinohelm/factor/evaluation/ic.py:110`

**Issue**:

```python
std_ic = float(np.std(ics))  # ddof=0，有偏估计
ir = mean_ic / std_ic
```

IC IR 的行业标准 std 应使用样本标准差（ddof=1）。当 IC 样本数较少（典型月度 IC 约 12-36 个点）时，ddof=0 vs ddof=1 误差约 1-4%，IR 被系统性高估。文档声明 "匹配 legacy 实现"（AC-13.2），若 legacy 也用 ddof=0 则这是向后兼容的约束；但值得在注释中明确声明而非隐式继承，避免未来维护者误以为是 bug。

---

### 正面观察

1. **NaN/Inf 清理设计多层防御**：`_scrub_result` + `_finite_or_none` 递归清理 + `cache._scrub`，三层保护，PostgreSQL JSON 列不会收到非法值。

2. **批量错误隔离设计干净**：Scheduler 和 `batch_run` 均捕获 per-factor 异常，失败因子映射到 `None` 而非中断整个批次，符合 partial-failure 语义。

3. **缓存 key 设计严谨**：`build_key` 包含 `factor_name | code_hash | config_json | data_range`，code_hash 变化自动失效；`_stable_json` 对 dict 按 key 排序、float 截断 10 位小数，确保相同配置产生相同 key。

4. **DAG max-lookback 语义正确**：`merged[key] = max(merged[key], lookback)` 保证所有依赖该字段的因子都有足够的热身数据，lookback closure 实现干净。

5. **`recover_interrupted_jobs` 的 DB-first 顺序正确**：先 commit DB 再操作 Redis，避免崩溃后 double-enqueue，fail-safe 设计。

6. **`parkinson_vol` 的 `1e-12` 正则化覆盖正确**：`low + 1e-12` 防止 `log(0)`，`1e-12` 相对于加密货币价格（> $0.001）是合理的 epsilon（不影响精度）。

7. **`_stable_json` 的确定性设计**：同时处理了 tuple/set/frozenset 序列化、float 精度截断、dict 排序，cache key 在 Python 实现版本间保持稳定。

---

### 判定

COMMENT

三个 HIGH 问题：
- `_last_close` 并发 data race（`batch_run` 多线程场景下 IC decay 结果可能静默错误）
- `evaluate_full` 双重 `_prepare_returns` 调用（脆弱副作用依赖）
- `_cross_symbol_worker` kernel 调用约定错误（当前已经导致所有 `cross_symbol_ic` 请求静默失败，建议优先修复）

其中第三个 HIGH 在当前代码路径中即可触发（任何调用 `evaluate_full(cross_symbol_args=...)` 的场景），建议优先修复。前两个在并发批量运行时有潜在影响，单因子路径不受影响。

VerifyPass: code-reviewer
Verdict: PASS
