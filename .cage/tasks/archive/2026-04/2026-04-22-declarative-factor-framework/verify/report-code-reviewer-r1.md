# Code-Reviewer Report — Round 1

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **REQUEST CHANGES**
**问题总数**: 17（3 CRITICAL / 7 HIGH / 4 MEDIUM / 3 LOW）

## Stage 1: 规格合规性

| 需求 | 状态 | 备注 |
|------|------|------|
| US-001 @factor 装饰器 | 通过 | decorator.py 实现完整 |
| US-002 别名表 | 未通过 | AC-2.1 必须别名缺失：bid_price/ask_price/trade_price/sum_open_interest/mark_price 等 |
| US-003 DataLayer | 部分通过 | 接口正确，但 3 处调用方使用了错误参数名 `catalog_path`（正确：`catalog_root`），且没传入必填的 `universe` — **运行时必然 TypeError** |
| US-004 Universe | 通过 | PIT 语义正确（7 天隔离 + delisting_date） |
| US-005 ComputeBackend | 通过 | PandasBackend + AbstractBackend 存在 |
| US-006 DAG 调度 | 通过 | Planner + Scheduler 实现正确 |
| US-007 评估管道 | 通过 | Evaluator 覆盖全部指标 |
| US-008 L2 缓存 | 部分通过 | 实现完整，但调用方使用错误构造参数 `cache_dir=` |
| US-009 Observer | 通过 | Observer span 机制完整 |
| US-010 异步任务 | 部分通过 | Worker 缺少 recover_interrupted_jobs；使用 deprecated `datetime.utcnow()` |
| US-011 前端列表页 | 未通过 | `npm run build` 失败：12 个 module-not-found 错误 |
| US-012 前端报告页 | 未通过 | `CardContent`/`CardHeader` 不存在于 card.tsx，build 失败 |
| US-013 12 个内置因子 | 部分通过 | `trade_imbalance` 未使用 trade tick 数据源（AC-13.1 违规） |
| US-014 funding_rate Parquet | 通过 | pipeline.py 双写，DataLayer 优先读 Parquet |
| US-015 旧模块清理 | 部分通过 | Python 侧清理完整；Sidebar 未添加 `/factor` 导航链接 |

## Stage 2: 问题清单（按严重度）

### CRITICAL-1: `card.tsx` 移除 6 个命名导出 → npm build fail

**File**: `src/web/src/components/ui/card.tsx`

原 shadcn 实现 export 7 组件（Card/CardHeader/CardContent/CardFooter/CardTitle/CardDescription/CardAction），本次改为只 export `Card`/`MetricCard`。受影响：
- `factor/components/ExploreResult.tsx:3` — 9 处 CardContent/CardHeader
- `factor/report/[id]/components/ChartPanel.tsx:4` — 4 处
- `factor/report/[id]/components/ParamsEcho.tsx:3` — 4 处

**Fix**:
```bash
git checkout HEAD -- src/web/src/components/ui/card.tsx
# 如需 MetricCard，放入 src/web/src/components/qds/MetricCard.tsx
```

### CRITICAL-2: Button/Input/Badge 大小写重命名 → Linux build fail

**Files**:
- `src/web/src/components/ui/Button.tsx`（大写 B）
- `src/web/src/components/ui/Input.tsx`（大写 I）
- `src/web/src/components/ui/Badge.tsx`（大写 B）

macOS 文件系统不区分大小写本地无感，但 Linux Docker 容器下所有现有小写导入 `"@/components/ui/button"` 全部 module-not-found。12 build errors 的主要根源。

**Fix**:
```bash
cd src/web/src/components/ui
git rm -f Button.tsx Input.tsx Badge.tsx Toggle.tsx
git checkout HEAD -- button.tsx input.tsx badge.tsx toggle.tsx
```

### CRITICAL-3: DataLayer 构造器调用错误 → 运行时 TypeError

**Files**:
- `src/tinohelm/factor/worker.py:270`
- `src/tinohelm/api/routes/factor.py:186`
- `src/tinohelm/factor/evaluation/robustness.py:163`

**真实签名**：`DataLayer(universe: Universe, catalog_root: Path | None = None, ...)`

3 处调用：
```python
DataLayer(catalog_path=catalog_path)  # 错：(1) 缺 universe, (2) 参数名不存在
```

触发路径：`POST /api/factor/explore`, `POST /api/factor/run`, cross-symbol IC。**所有 factor run 立即 TypeError 崩溃。**

**Fix**:
```python
from tinohelm.factor.universe import Universe
universe_obj = Universe.load_csv(universe_file_path)
data_layer = DataLayer(universe_obj, catalog_root=catalog_path)
```

### HIGH-1: Worker 缺少 recover_interrupted_jobs

**File**: `src/tinohelm/factor/worker.py` + `src/tinohelm/api/app.py:95`

同类 `data.worker`/`backtest.worker` 都实现并在 lifespan 中调用。factor worker 缺失意味着 API 重启后 `running` 状态任务永久卡死。

**Fix**: 参照 `src/tinohelm/data/worker.py` 实现 `recover_factor_jobs()`，在 `app.py lifespan` 中 `await recover_factor_jobs(redis_client)`。

### HIGH-2: datetime.utcnow() 3 处 DeprecationWarning

**File**: `src/tinohelm/factor/worker.py:133, 193, 218`

**Fix**: `from datetime import datetime, UTC; datetime.now(UTC).replace(tzinfo=None)`（保持 DB TIMESTAMP WITHOUT TIME ZONE naive 语义）。

### HIGH-3: AC-2.1 必须别名缺失

**File**: `src/tinohelm/factor/alias.py`

缺失：`bid_price`, `bid_qty`, `ask_price`, `ask_qty`（bookTicker），`trade_price`, `trade_qty`, `trade_side`（trade_tick），`sum_open_interest`, `open_interest_value`（metrics），`mark_price`（funding_rate）。

因子声明 `def orderbook_imbalance_L1(bid_price: Panel, ...)` 时 `resolve_alias` 会 pass-through，planner `_infer_source` 兜底归 `"bar"`，从错误数据源加载。

**Fix**: 在 `FIELD_ALIAS` dict 中添加上述 10 个映射。

### HIGH-4: trade_imbalance 因子使用 bar 数据而非 trade tick

**File**: `src/tinohelm/factor/builtins/microstructure.py:18-37`

AC-13.1 要求 `trade_imbalance` 依赖 `trade_price/trade_qty/trade_side`，但实现用 `high/low/close/volume`。`DataLayer._load_table` 中 trade_tick 源路径是 `raise NotImplementedError`（`data_layer.py:234`）。

**Fix**: 要么完成 trade_tick 数据加载（实现 DataLayer._load_table 的 trade_tick 分支 + 从 catalog 读 `data/trade_tick/*.parquet`），要么在 4-tasks.md 记录此因子推迟。

### HIGH-5: FactorCache 构造器参数不匹配

**Files**: `src/tinohelm/factor/worker.py:279`, `src/tinohelm/api/routes/factor.py:191`

```python
cache = FactorCache(cache_dir=str(cache_dir))  # 错
# 正确：
cache = FactorCache(cache_root=str(cache_dir))
```

因 `cache_root` 有默认值，Python 不报错，但传入路径被忽略，实际用默认 `~/.tino/factor_cache/`。

### HIGH-6: Sidebar 未添加 /factor 导航（AC-15.7 违规）

**File**: `src/web/src/components/Sidebar.tsx`

删除了 `{ href: "/research", ... }` 但未添加 `/factor`。用户无 UI 入口。

**Fix**: 在 navItems 数组中添加：
```tsx
{ href: "/factor", labelKey: "nav.factor", icon: FlaskConical },
```
并在 i18n messages 添加 `nav.factor: "因子研究"`。

### HIGH-7: _infer_source 不覆盖 sum_open_interest/open_interest_value

**File**: `src/tinohelm/factor/engine/planner.py:295`

**Fix**:
```python
_OPEN_INTEREST_FIELDS: frozenset[str] = frozenset({
    "open_interest", "sum_open_interest", "open_interest_value"
})
```

### MEDIUM-1: notification-router.ts 3 处 any 类型

**File**: `src/web/src/lib/notification-router.ts:10, 52, 74`

**Fix**: 定义 `type FactorWsEvent = { type: string; run_id?: string; factor_name?: string; progress?: number; error?: string; [key: string]: unknown }`，替换 `any`。

### MEDIUM-2: MetricCard 使用禁用 CSS 变量

**File**: `src/web/src/components/ui/card.tsx:36-41`

使用 `var(--accent-green)/var(--accent-red)/var(--text-muted)/var(--text-primary)`，已在 2026-04-19 DS 标准化删除。

**Fix**（若保留 MetricCard）：改为 `text-qds-success / text-destructive / text-muted-foreground / text-foreground`。或随 CRITICAL-1 一起 `git checkout HEAD --` 整体回滚。

### MEDIUM-3: Orchestrator.run() 硬性要求 close panel

**File**: `src/tinohelm/factor/engine/orchestrator.py:287-292`

对 `funding_rate_level / funding_rate_mom / oi_change / orderbook_imbalance_L1` 等不依赖 close 的因子，第 289 行 `raise RuntimeError("'close' Panel missing")` 崩溃。

**Fix**: 仅在因子的 `input_specs` 含 close 派生时强制 close；或自动附加 close 数据请求。

### MEDIUM-4: 缺测试覆盖 recover_interrupted_jobs

**File**: `tests/factor/test_worker.py`

函数本身缺失（HIGH-1），且现有 tests 无 API 重启恢复场景。

### LOW-1: worker.py catalog_path 获取逻辑错误

**File**: `src/tinohelm/factor/worker.py:261-264`

```python
catalog_path = getattr(settings, "catalog_path", None) or str(...)
```
`settings` 无 `catalog_path`（正确是 `settings.paths.catalog`），永远走兜底。

**Fix**: `catalog_path = str(settings.paths.catalog)`

### LOW-2: DataLayer._align_time 未对 funding_rate 做 as-of 延迟

**File**: `src/tinohelm/factor/data_layer.py:203-213`

`_load_panel` 中 `funding_rate` 分支使用通用 `_align_time`，未调用 `_align_funding_onto_bar_index`（shift(1) + ffill）。AC-3.4 要求 as-of 延迟。模块级 `load_aligned()` 实现了，但 `DataLayer.load()` 主路径没用。

### LOW-3: FactorCache.store() 的 json.dump(default=str) 掩盖错误

**File**: `src/tinohelm/factor/cache.py:338`

数据已 NaN-scrub，`default=str` 会把非预期的 Timestamp/ndarray 转字符串而非报错，掩盖数据质量问题。

## 推荐修复顺序

1. CRITICAL-1: 恢复 `card.tsx` 6 个导出（`git checkout HEAD --`）
2. CRITICAL-2: Button/Input/Badge/Toggle 文件大小写统一
3. CRITICAL-3: 修 3 处 `DataLayer` 构造器调用 + 补传 `universe`
4. HIGH-5: `FactorCache(cache_dir=...)` → `cache_root=...`
5. HIGH-1: 实现 `recover_factor_jobs` + lifespan 注册
6. HIGH-3 + HIGH-7: 补全 AC-2.1 别名 + 更新 `_infer_source`
7. HIGH-6: Sidebar 添加 /factor 导航
8. HIGH-2: 替换 `datetime.utcnow()`
9. MEDIUM-3: 修 close panel 强制要求
10. 其余 MEDIUM/LOW 按需

## Verdict: **REQUEST CHANGES**
