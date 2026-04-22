# 声明式因子研究框架 -- 调研文档

## 1. Panel (T, N) 面板数据模型选型

### 方案对比

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| **A: pandas DataFrame** | T=index, N=columns, 多字段用 dict[str, DataFrame] | 生态成熟、与现有 loader.py 无缝衔接、shift/rolling/rank 原生支持 | 单 DataFrame 内存布局非列式、大规模 N (>500) 时 pivot 开销 | **v1 选定** |
| B: numpy ndarray | shape=(T, N), 多字段用 dict[str, ndarray] | 最快、内存紧凑 | 无 index 管理、时间对齐需手动、缺少 rolling/groupby | v2 Numba backend 时考虑 |
| C: polars LazyFrame | 列式存储、惰性执行、自动并行 | 大数据集快、内存省 | 生态较新、与 scipy.stats 不兼容（IC 计算需 numpy 中转）、shift 语义不同 | v1 PolarsBackend 接口预留 |

### 决策

**v1 使用 pandas DataFrame 作为 Panel 底层**。理由:
1. 现有 `research/loader.py` 返回 `pd.DataFrame`，迁移成本最低
2. IC 计算依赖 `scipy.stats.spearmanr`，天然与 numpy/pandas 兼容
3. shift/rolling/rank 是 kernel 高频操作，pandas 原生支持
4. v1 的 universe 规模 < 50 symbols，pandas 性能足够

Panel 类型定义:
```python
Panel = pd.DataFrame  # index=DatetimeIndex (T), columns=symbols (N)
```

多字段通过 `dict[alias, Panel]` 传递给 kernel:
```python
def kernel(close: Panel, volume: Panel) -> Panel:
    ...
```

---

## 2. @factor 装饰器 + AST 静态检查

### 装饰器设计

```python
@factor(category="动量", lookback=20)
def ret_N(close: Panel) -> Panel:
    return close.pct_change(20)
```

装饰器职责:
1. **元数据注入**: category, lookback, version, params（从函数默认参数提取）
2. **InputSpec 推断**: 从函数签名的参数名查字段别名表，生成 `InputSpec(alias="close", table="bar", field="close")`
3. **OutputSpec 推断**: 固定为 `OutputSpec(kind="cross_section", dtype="float64", shape="(T, N)")`
4. **code_hash 计算**: 取函数源码的 SHA-256（用 `inspect.getsource`）

### AST 静态检查方案

**目标**: 检测 kernel 中的 `shift(-n)` / `.shift(n)` 调用，自动累加 lookback。

**实现**: 使用 `ast.NodeVisitor` 遍历函数 AST:
1. 查找 `ast.Call` 节点，target 为 `Attribute(attr="shift")`
2. 提取第一个参数的值（常量整数）
3. 取绝对值累加到 lookback

```python
class ShiftDetector(ast.NodeVisitor):
    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "shift":
            if node.args and isinstance(node.args[0], ast.Constant):
                self.max_shift = max(self.max_shift, abs(node.args[0].value))
        self.generic_visit(node)
```

**局限**: 动态 shift（`shift(n)` 中 n 是变量）无法静态检测。v1 要求 shift 参数必须是常量，否则开发者需在 `@factor(lookback=N)` 中显式声明。

### 与现有 Registry 的对比

| 方面 | 旧 Registry | 新 Registry |
|------|-------------|-------------|
| 因子发现 | 扫描 `.py` 文件，查 `BUILTIN_FACTORS` dict | 扫描 `.py` 文件，查 `@factor` 装饰器 |
| 元数据 | 手写 dict（容易遗漏/不一致） | 从装饰器参数 + 函数签名自动推断 |
| 数据依赖 | `data_type: "bar"` 粗粒度 | 字段级 InputSpec（`close`, `volume` 各自独立） |
| 缓存 key | 无 | code_hash + data_snapshot_hash |
| 静态检查 | 无 | AST shift 检测 |

---

## 3. DAG 依赖求解算法

### v1 场景分析

v1 的因子之间**无直接依赖**（因子 A 不引用因子 B 的输出），只有共享数据依赖。因此 DAG 是一个浅层图:

```
data_source_1 → factor_A
data_source_1 → factor_B    (共享 data_source_1)
data_source_2 → factor_C
```

### 算法选择

| 方案 | 复杂度 | 适用场景 |
|------|--------|---------|
| **Kahn's algorithm (BFS 拓扑排序)** | O(V+E) | 通用 DAG，v1 足够 |
| DFS 拓扑排序 | O(V+E) | 同上但代码更紧凑 |
| graphlib.TopologicalSorter (Python 3.9+) | O(V+E) | Python 标准库，API 直接支持 `static_order()` 和分组并行 |

**决策**: 使用 Python 标准库 `graphlib.TopologicalSorter`。理由:
1. 零外部依赖
2. `get_ready()` / `done()` API 天然支持分组并行调度
3. 自动检测环依赖 (`CycleError`)

### 数据合并优化

Planner 在拓扑排序前先合并 DataRequest:
1. 收集所有因子的 InputSpec
2. 按 `(table, field)` 分组去重
3. 计算最大 lookback (max over all factors)
4. 生成 `DataRequest(table_groups: dict[str, list[field]], lookback: int, symbols: list[str], time_range)`

---

## 4. 缓存 Key 设计 + 部分命中

### Key 组成

```
cache_key = hash(
    factor_name,           # 因子名
    code_hash,             # 因子源码 SHA-256
    data_snapshot_hash,    # 数据文件的 mtime + size 哈希
    params_hash,           # 因子参数 JSON 的 SHA-256
    universe_hash,         # universe CSV 的 SHA-256
    time_range,            # (start, end, interval)
)
```

### 缓存目录布局

```
~/.tino/research/cache/
├── manifest.json              # 所有缓存条目的元信息索引
├── {cache_key}/
│   ├── factor_values.parquet  # 因子值 Panel (T, N)
│   ├── eval_result.json       # 评估结果
│   └── meta.json              # code_hash, params, timestamp
```

### 部分命中策略

| 场景 | 命中情况 | 行为 |
|------|---------|------|
| 完全匹配 | factor_values + eval_result 都存在 | 直接返回 |
| 因子值命中，评估参数变化 | factor_values 存在，forward_period 等变化 | 跳过 kernel，重新运行 evaluator |
| code_hash 变化 | 缓存全失效 | 重新运行全流程 |
| 数据变化 | data_snapshot_hash 不同 | 重新运行全流程 |

### DAG 失效传播

v1 因子之间无依赖，传播逻辑简单: 只需按因子独立检查缓存。v2 若引入因子依赖因子（组合因子），需要在 DAG 上做逆拓扑遍历传播失效。

---

## 5. PIT Universe CSV 格式

### 格式设计

```csv
date,symbol
2025-01-01,BTCUSDT-PERP
2025-01-01,ETHUSDT-PERP
2025-01-01,BNBUSDT-PERP
...
2025-02-01,BTCUSDT-PERP
2025-02-01,ETHUSDT-PERP
2025-02-01,SOLUSDT-PERP
...
```

- `date` 列: 月初日期（YYYY-MM-DD），表示"从该日期开始的月份"
- `symbol` 列: TinoHelm 格式 symbol（含 `-PERP` 后缀）
- 每月一组行，按 date 排序
- 查询 `2025-01-15` 时返回 `2025-01-01` 的 symbol 列表

### 查询逻辑

```python
def get_symbols_at(universe_df, date):
    # 找 <= date 的最大月初日期
    valid_dates = universe_df[universe_df["date"] <= date]["date"].unique()
    latest = max(valid_dates)
    return universe_df[universe_df["date"] == latest]["symbol"].tolist()
```

### 新币隔离

PIT 过滤器在 DataLayer 层面实现:
- 新币首次出现在 universe CSV 的日期 + 7 天 = 可用日期
- 在隔离期内，该币的 Panel 列设为 NaN

---

## 6. 字段别名表设计

### 默认别名表

| 别名 (参数名) | table | field | 说明 |
|---------------|-------|-------|------|
| `open` | bar | open | OHLCV |
| `high` | bar | high | OHLCV |
| `low` | bar | low | OHLCV |
| `close` | bar | close | OHLCV |
| `volume` | bar | volume | OHLCV |
> **注意**: `vwap` 不作为别名条目。bar Parquet 中无 `vwap` 列（仅 open/high/low/close/volume），vwap 是派生值。需要 vwap 的因子（如 `vwap_dev`）应直接声明依赖 `close, high, low, volume` 四个别名，在 kernel 内部计算 `(high+low+close)/3 * volume` 的 rolling sum。
| `funding_rate` | funding_rate | funding_rate | Funding Rate |
| `mark_price` | funding_rate | mark_price | Mark Price |
| `bid_price` | bookTicker | best_bid_price | L1 Bid |
| `bid_qty` | bookTicker | best_bid_qty | L1 Bid Size |
| `ask_price` | bookTicker | best_ask_price | L1 Ask |
| `ask_qty` | bookTicker | best_ask_qty | L1 Ask Size |
| `trade_price` | trade_tick | price | Trade Price |
| `trade_qty` | trade_tick | quantity | Trade Size |
| `trade_side` | trade_tick | side | Taker Side |
| `sum_open_interest` | metrics | sum_open_interest | OI |
| `open_interest_value` | metrics | sum_open_interest_value | OI (USDT) |

### 数据存储格式

别名表以 Python dict 常量存储在 `src/tinohelm/factor/alias.py` 中。v1 不需要动态加载或 DB 存储。

### 冲突检测

注册新别名时检查: 如果同一个 alias 映射到不同的 `(table, field)`，抛出 `AmbiguousAliasError`。自定义别名（`@factor(aliases={...})`）在因子级别 override，不影响全局表。

---

## 7. 与现有 BinanceVisionPipeline 数据类型的映射

### 数据管道映射

| Pipeline `data_type` | `WRITE_CATEGORY` | 存储路径 | DataLayer 读取方式 |
|---------------------|------------------|---------|-------------------|
| klines | bar | `catalog/data/bar/{bar_type}/` | `pd.read_parquet` |
| aggTrades / trades | trade_tick | `catalog/data/trade_tick/{inst_id}/` | `pd.read_parquet` |
| fundingRate | funding_rate | `catalog/data/funding_rate/{inst_id}/` (v1 升级后) | `pd.read_parquet` |
| bookTicker | quote_tick | `catalog/data/quote_tick/{inst_id}/` | `pd.read_parquet` |
| metrics | metrics | `catalog/data/metrics/{inst_id}/` | `pd.read_parquet` |

### DataLayer 与现有 loader.py 的对比

| 方面 | 旧 loader.py | 新 DataLayer |
|------|-------------|-------------|
| 单/多币种 | 单币种 | 原生多币种并行加载 |
| 数据源 | bar / trade_tick / funding_rate | bar / trade_tick / funding_rate / bookTicker / metrics |
| 时间对齐 | 无 | 不同频率数据 forward-fill 到目标 interval |
| PIT 过滤 | 无 | 新币隔离 + funding_rate as-of 延迟 |
| 输出格式 | pd.DataFrame (单币种) | dict[alias, Panel] (多币种面板) |
| funding_rate 格式 | JSON | Parquet (升级后) |

### funding_rate 存储升级路径

1. 修改 `data/converters/funding_rate.py`，让 Pipeline 将 funding_rate 同时写入 Parquet
2. 编写迁移脚本 `scripts/migrate_funding_json_to_parquet.py`，遍历 `~/.tino/data/funding_rates/*.json`，转换为 Parquet 写入 catalog
3. DataLayer 优先读 Parquet，fallback 到 JSON
4. `data/funding_cache.py` 保留但标记 deprecated

---

## 8. DB Schema 设计 (factor_runs)

### 与现有 research_jobs 的对比

| 字段 | research_jobs | factor_runs | 变更理由 |
|------|-------------|-------------|---------|
| job_id | varchar(36) | varchar(36) | 不变，改名 run_id |
| factor_name | varchar(100) | varchar(100) | 不变 |
| symbol | varchar(50) | - | **删除**: 多币种由 universe 决定 |
| universe | - | varchar(100) | **新增**: universe 名称 |
| factors_json | - | JSON | **新增**: 多因子批量运行时的因子列表 |
| data_type | varchar(30) | - | **删除**: 由因子声明自动推断 |
| interval | varchar(10) | varchar(10) | 保留 |
| config_json | - | JSON | **新增**: 替代 parameters_json，更结构化 |
| result_summary_json | - | JSON | **新增**: 评估摘要（多因子版） |
| cache_hit | - | boolean | **新增**: 是否缓存命中 |

---

## 9. Redis Key 设计

### 新 Key 映射

| 旧 Key | 新 Key | 用途 |
|--------|--------|------|
| `tino:research:queue` | `tino:factor:queue` | 异步任务队列 |
| `tino:research:progress:{job_id}` | `tino:factor:progress:{run_id}` | 进度 PubSub |
| `tino:research:events` | `tino:factor:events` | 完成/失败事件 PubSub |

### EventBridge 映射

```python
# bridge.py
"tino:factor:": "factor.",

# notification-router.ts
"factor.progress":   { channel: "silent" },
"factor.completed":  { channel: "toast", type: "success", dedupeKey: (e) => e.run_id },
"factor.failed":     { channel: "toast", type: "error",   dedupeKey: (e) => e.run_id },
```
