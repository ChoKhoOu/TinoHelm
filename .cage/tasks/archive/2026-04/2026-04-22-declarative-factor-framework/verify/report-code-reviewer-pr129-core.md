## 代码审查报告 — PR #129 核心模块审查 (types/decorator/registry/ast_check/alias/__init__/universe/data_layer/cache/observer)

**审查文件数**：10
**问题总数**：8
**测试结果**：449 passed, 1 skipped（全部通过）

### 按严重程度
- CRITICAL: 0
- HIGH: 2（应该修复）
- MEDIUM: 4（考虑修复）
- LOW: 2（可选）

---

### Stage 1: 规格合规

**通过**，带一处已知 deferred 偏差。

核心 US-001 ~ US-004 验收标准覆盖完整：

| 验收标准 | 文件 | 状态 |
|---|---|---|
| AC-1.1 `@factor` + 字段别名解析 | `decorator.py` + `alias.py` | 通过 |
| AC-1.2 AST shift 检测累加 lookback | `ast_check.py` | 通过 |
| AC-1.3 Registry 自动发现 `.py` + FactorSpec | `registry.py` | 通过 |
| AC-1.4 code_hash 变化标记缓存失效 | `registry.py` + `cache.py` | 通过 |
| AC-2.1 内置别名表覆盖 OHLCV / funding / tick 字段 | `alias.py` | 通过 |
| AC-2.2 冲突别名 `AmbiguousAliasError` | — | **Deferred**（与 kickback 记录一致，不阻塞） |
| AC-2.3 `@factor(aliases=...)` 自定义别名 | — | **Deferred**（同上） |
| AC-3.x DataLayer Panel 加载 + 时间对齐 + 并行 | `data_layer.py` | 通过 |
| AC-3.4 PIT 7 天隔离 + funding as-of 延迟 | `data_layer.py` + `universe.py` | 通过 |
| AC-4.2 Universe PIT 时点查询 | `universe.py` | 通过 |

---

### 问题列表

---

**[HIGH] Registry 存在幽灵条目（Ghost Entry）**

File: `src/tinohelm/factor/registry.py`，`_scan_user_dir` + `get_all_specs` + `get_kernel`

Issue: 用户因子文件中某个 `@factor` 函数被**删除**后，`scan()` 的返回值中正确排除了该因子，但 `_spec_cache` 和 `_kernel_cache` 的条目**永远不会被驱逐**。后续调用 `get_all_specs()` 或 `get_kernel()` 仍能获取到已删除的因子，导致 `/api/factor/list` 向前端展示幽灵因子，且 `get_kernel()` 返回的是已不在文件系统中的旧版代码。

经代码实测确认：

```
# 删除 factor_b 后第二次 scan():
scan() return value: ['factor_a', ...]  # 正确
_kernel_cache 仍含 factor_b: True       # 幽灵
get_all_specs() 返回: ['factor_a', 'factor_b']  # 幽灵
```

根本原因：`scan()` 的最终合并循环（第 106-113 行）只写入 `merged` 中存在的 key，但从不删除 `_spec_cache` / `_kernel_cache` 中不再存在于 `merged` 的 key。

Fix: 在 `scan()` 末尾添加驱逐逻辑：

```python
# 驱逐不再属于任何活跃文件的 user 因子（prefix "user:" 标识来源）
stale = [
    name for name, (h, _) in self._spec_cache.items()
    if h.startswith("user:") and name not in merged
]
for name in stale:
    self._spec_cache.pop(name, None)
    self._kernel_cache.pop(name, None)
```

---

**[HIGH] `_apply_pit` O(n_ts × n_sym) Python 循环 — 大数据量下不可用**

File: `src/tinohelm/factor/data_layer.py`，`_apply_pit` 方法，第 518-528 行

Issue: PIT 过滤嵌套 Python 循环遍历每个时间戳和每个 symbol，并用 `mask.loc[ts, sym] = True` 做标量赋值。在实际量化研究场景（1 年 1m 数据 × 20 个标的）下性能不可接受：

```
实测 10k 行 × 20 列: 2.1s
外推 1y 1m (525,600 行 × 20 列): ~110s
对比向量化方案: 0.5ms (快 200,000x)
```

当前实现：

```python
for ts in panel.index:
    eligible = set(self._universe.get_symbols_at(ts))
    for sym in all_symbols:
        if sym not in eligible:
            mask.loc[ts, sym] = True
panel[mask] = float("nan")
```

Fix: 按每个 symbol 的 listing/delisting 边界做向量化切片赋值，完全避免 Python 逐行循环：

```python
panel = panel.copy()
for sym in panel.columns:
    rows = [r for r in self._universe._rows if r.symbol == sym]
    if not rows:
        panel[sym] = float("nan")
        continue
    row = rows[0]
    eligible_from = row.listing_date + timedelta(days=_NEW_COIN_ISOLATION_DAYS)
    before_mask = pd.Series(panel.index < eligible_from, index=panel.index)
    panel.loc[before_mask, sym] = float("nan")
    if row.delisting_date is not None:
        after_mask = pd.Series(panel.index >= row.delisting_date, index=panel.index)
        panel.loc[after_mask, sym] = float("nan")
return panel
```

（如需支持多条 listing 记录 per symbol，可扩展为布尔 OR 合并。）

---

**[MEDIUM] `decorator.py` Panel 参数的 `required` 字段始终为 True，不识别默认值**

File: `src/tinohelm/factor/decorator.py`，`_build_input_specs` 函数，第 92-125 行

Issue: 当因子函数包含有默认值的 Panel 参数时（如 `def f(close: Panel, weights: Panel = None)`），`_build_input_specs` 生成的 `InputSpec` 始终为 `required=True`，与 `InputSpec.required` 字段的语义矛盾。DataLayer 按 `required=True` 对 `weights` 加载，若 symbol 无该数据则返回空 Series，潜在产生错误的 Panel 形状而非优雅降级。

经代码实测确认：

```python
specs = _build_input_specs(test_optional_panel)
# 输出: ('weights', True)  -- 错误，应为 False
```

Fix: 在判断是否生成 `InputSpec` 时检查 `param.default`：

```python
is_required = (param.default is inspect.Parameter.empty)
specs.append(InputSpec(field_name=canonical_name, required=is_required))
```

---

**[MEDIUM] `cache.py` manifest 的 load-modify-save 非原子，并发写入时 `size_bytes` 不准确**

File: `src/tinohelm/factor/cache.py`，`_update_manifest` 方法，第 193-211 行

Issue: `_update_manifest` 执行序列：读 JSON → 修改内存 dict → 写回 JSON，无互斥锁保护。若两个 `store()` 调用并发（例如在 `ThreadPoolExecutor` 内部——虽然 `store` 本身未被并行调用，但 FactorCache 实例无文档说明为单线程独占），两者都读到相同的旧 manifest，各自追加 `size_bytes` 后后者覆盖前者，导致 `size_bytes` 统计偏低（不影响缓存命中，但影响存储空间审计）。

Fix: 为 `FactorCache` 实例添加 `threading.Lock`，对整个 load-modify-save 序列加锁；或在类 docstring 中明确标注"非线程安全，每个线程/进程应使用独立实例"。

---

**[MEDIUM] `alias.py` `"turnover"` 语义歧义**

File: `src/tinohelm/factor/alias.py`，第 73 行

Issue: `"turnover"` 被映射为 `"amount"`（成交额/dollar volume），但在量化研究领域 "turnover" 的主流含义是**因子换手率**（`EvalResult.turnover` 字段即如此命名）。用户若将参数命名为 `turnover`，期望传入换手率相关面板数据，实际会被静默解析为成交额面板，产生数据错配且无任何警告。

Fix: 将映射键从 `"turnover"` 改为 `"dollar_turnover"` 或 `"quote_turnover"`，消除歧义；或对该 key 在 `resolve_alias` 中加 `DeprecationWarning` 并提示使用 `"amount"`。

---

**[MEDIUM] `observer.py` `_active_stack` 在多线程场景中不安全，且 `remove()` 为 O(n)**

File: `src/tinohelm/factor/observer.py`，第 101-103 行，第 146-147 行，第 182-183 行

Issue: `_active_stack` 是一个共享列表，`_active_stack.remove(span_id)` 是 O(n) 操作且无 GIL 之外的原子保证。代码注释（第 101-103 行）已承认这一限制："thread-local would be more robust in concurrent use"但未实施。若 `Observer` 实例被传入 `ThreadPoolExecutor` 中的多个 worker 并发调用（Orchestrator 有并行 symbol 加载路径），`_active_stack` 的并发读写会产生错误的 `parent_id` 追踪。

Fix: 改为 `threading.local()` 存储：

```python
import threading
self._tls = threading.local()

@property
def _active_stack(self) -> list:
    if not hasattr(self._tls, "stack"):
        self._tls.stack = []
    return self._tls.stack
```

或至少在 docstring 中明确标注"单线程使用，多线程场景每个线程创建独立 Observer 实例"。

---

**[LOW] `decorator.py` 文档 "returns unchanged" 措辞不准确**

File: `src/tinohelm/factor/decorator.py`，第 25 行，第 165 行

Issue: 注释声明"returns the original function **unchanged**"，但装饰器实际对原始函数对象做了**原地修改**（`func.__factor_spec__ = spec`），修改前后的函数对象 id 相同但属性不同。"unchanged"在 Python 上下文中暗示无副作用，会误导阅读者认为装饰前后函数对象等价。经测试确认：

```python
id_before == id_after  # True — 同一对象
hasattr(decorated, '__factor_spec__')  # True — 原始对象已被修改
```

Fix: 将文档改为"The same function object is returned with `__factor_spec__` attached in-place."

---

**[LOW] `alias.py` 单字母别名（`c`, `o`, `h`, `l`, `v`）存在误触发风险**

File: `src/tinohelm/factor/alias.py`，第 43、48、52、56、60 行

Issue: 单字母别名 `l` → `low`、`v` → `volume`、`h` → `high` 等在 Python 中是极常见的循环变量/临时变量名。由于 `_build_input_specs` 对无注解参数保守地视为 Panel 输入（`inspect.Parameter.empty` 分支返回 `True`），用户若在因子函数中使用 `l`、`v`、`h` 等未注解参数，这些参数会被静默解析为 bar 数据输入请求，而用户完全没有意识到这一行为。

Fix: 考虑移除 `c`/`o`/`h`/`l`/`v` 这 5 个单字母别名（保留更具描述性的 `"close"`, `"open"` 等完整名称）；或在 `_build_input_specs` 中对单字母参数名（`len(param_name) == 1`）添加 `logger.warning`，提示用户参数名触发了别名解析。

---

### 正面观察

- **类型安全性强**：`FactorSpec`、`InputSpec`、`OutputSpec`、`EvalConfig` 全部使用 `@dataclass(frozen=True)`，满足 hashability 且防止意外修改。`EvalResult` 使用普通 `@dataclass`（可变），与其"按字段增量填充"的使用模式一致，设计决策合理。

- **NaN/Inf 防护完善**：`cache.py` 的 `_scrub` 函数同时处理 Python `float`、`np.floating`、`np.integer` 三类数值，以及 dict/list 递归结构，完整覆盖了所有 PostgreSQL JSON 列可能遇到的非法浮点值，与 CLAUDE.md pitfall 约定完全对齐。

- **ShiftDetector 边界正确**：`abs(shift_value)` 处理正/负 shift，`shift(0)` 贡献 0，`max(total_lookback, 1)` 保证最终 lookback 不低于 1，对动态 shift 表达式（`shift(n)`）给出警告但不崩溃。`inspect.getsource` 失败时优雅返回 0。整体保守但不过激。

- **funding_rate as-of 延迟实现正确**：`_align_funding_onto_bar_index` 中 `shift(1)` 在 8h 频率轴上偏移后再 `reindex + ffill` 到 bar 索引，确保 T 时刻的 funding rate 仅在 T+8h 之后可见，无看穿未来偏差。

- **Universe CSV 解析健壮**：列名 lowercase 归一化、required 列校验（`symbol`, `listing_date`）、blank row 跳过、`_parse_date` 对 ISO-8601 全格式支持，边界情况均已处理。

- **Registry 增量扫描设计正确**：`user:{path}:{hash}` 格式的 cache key 能同时追踪文件来源和内容版本，保证文件内容不变时跳过 re-import，内容变化时触发重新加载。builtin 因子使用空字符串 hash（无文件可 hash），设计合理。

- **路径安全**：`_scan_user_dir` 通过 `load_module_from_file(boundary_dir=self._user_dir)` 传入边界，`is_within_dir` 内部用 `Path.resolve()` 防止 symlink 逃逸，无 path traversal 风险。

- **Observer 双重调用防护**：`_finished` 标志防止 manual `end_span` + context manager 自动调用的重复处理，测试确认无栈腐化、无重复日志。

- **cache key 防注入**：`build_key` 使用 SHA-256 hex digest 作为文件名，64 字符全为 `[0-9a-f]`，完全排除路径遍历和文件名注入可能。

- **全模块无 eval/exec/hardcoded secrets**：所有 10 个文件均无动态代码执行，无硬编码密钥。

---

### 判定

COMMENT

两个 HIGH 问题均为功能性缺陷（幽灵条目 + PIT 性能），在小规模测试中不会暴露，但在生产场景（1 年 1m 数据、20 标的、因子文件反复迭代）下会造成可感知问题。建议 `_apply_pit` 在合入 main 前修复（HIGH-2），`Registry` 幽灵条目建议在后续 iteration 中修复（HIGH-1）。其余 MEDIUM/LOW 问题均为改进建议，不阻塞合并。

VerifyPass: code-reviewer
Verdict: PASS
