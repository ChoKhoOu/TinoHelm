# Kickback to Execute — Round 1

**任务**: 2026-04-22-declarative-factor-framework
**来源**: `/cage:verify` r1 (verifier FAIL + code-reviewer REQUEST CHANGES)
**状态**: 验证失败，回退到执行阶段修复

## 一句话概述

后端（s1–s17 Python 代码）整体高质量，但 **3 处 DataLayer/FactorCache 构造器调用错误**导致所有 factor run 运行时崩溃；前端（s18/s19）**Turbopack build 彻底失败**（12 errors），根因是越界重写了 `card.tsx`/`button.tsx`/`input.tsx` 等 shadcn primitives 与 `globals.css`。Sidebar 未添加 `/factor` 导航，用户无入口。

## 验收失败清单

### Phase 1 Quality Gates
- ❌ `npm run build` 清空 `.next` 缓存后实测 **12 errors**（Turbopack module-not-found）
- ❌ `npx tsc --noEmit` 15+ errors（TS2305 CardContent/CardHeader 不存在 + TS1149 大小写冲突）
- ❌ `npm run lint` 新增 7 errors（set-state-in-effect × 2, JSX-comment × 1, no-explicit-any × 3, 其他 1）
- ❌ `vitest tests/fonts/tokens.test.ts` 10/15 failed（根因：globals.css 878 行 diff 删除 QDS token）
- ✅ `pytest tests/` ~450 passed（含 tests/factor/ 全部 + tests/data/ + 其他）
- ✅ Python imports OK
- ✅ `vitest tests/lib/notification-router.test.ts` 19 passed

### Phase 2 多视角验证
- Verifier: **FAIL** — 20 subtask: 12 VERIFIED / 6 PARTIAL / 2 FAIL（s18, s19）
- Code-Reviewer: **REQUEST CHANGES** — 3 CRITICAL / 7 HIGH / 4 MEDIUM / 3 LOW

## 必须修复（按优先级）

### ★★★ CRITICAL（修完才能重跑 Gates）

#### C-1: 恢复 `card.tsx`（生产运行时崩溃阻断点）

`src/web/src/components/ui/card.tsx` 被替换为只 export `Card`/`MetricCard` 的极简版，丢失 `CardHeader`/`CardContent`/`CardFooter`/`CardTitle`/`CardDescription`/`CardAction` 6 个导出。factor 前端 11 处使用这些组件 → React 运行时 `Element type is invalid: got: undefined`。

```bash
cd /Users/ouzhuohao/TinoHelm
git checkout HEAD -- src/web/src/components/ui/card.tsx
# 如确需 MetricCard，新建 src/web/src/components/qds/MetricCard.tsx
#   注意：MetricCard 原实现用了 var(--accent-green)/var(--accent-red) 等已禁用 token，
#   重写时必须使用 Tailwind 语义类 text-qds-success/text-destructive
```

#### C-2: 统一 `Button.tsx`/`Input.tsx`/`Badge.tsx`/`Toggle.tsx` 文件名大小写

被重命名为大写（`Button.tsx`/`Input.tsx`/`Badge.tsx`/`Toggle.tsx`）。macOS 不区分大小写所以本地 import 不报错，但 Linux Docker 里 `"@/components/ui/button"` → module-not-found。

```bash
cd /Users/ouzhuohao/TinoHelm/src/web/src/components/ui
# 1) 删大写副本（如果存在）
git rm -f Button.tsx Input.tsx Badge.tsx Toggle.tsx 2>/dev/null || true
# 2) 恢复原 shadcn 版本
git checkout HEAD -- button.tsx input.tsx badge.tsx toggle.tsx
```

#### C-3: 修复 `DataLayer` 构造器调用（3 处，运行时 TypeError）

**真实签名**：`DataLayer(universe: Universe, catalog_root: Path | None = None, ...)`

3 处误传 `catalog_path=` 且缺少 `universe` 必填参数：

1. `src/tinohelm/factor/worker.py:270`
2. `src/tinohelm/api/routes/factor.py:186`
3. `src/tinohelm/factor/evaluation/robustness.py:163`

```python
# 错误：
DataLayer(catalog_path=catalog_path)

# 正确：
from tinohelm.factor.universe import Universe
universe_obj = Universe.load_csv(universe_file_path)  # 从 config_dict 或 ~/.tino/research/universes/ 取
data_layer = DataLayer(universe_obj, catalog_root=catalog_path)
```

`worker._process_job` 和 `factor.explore` 端点都需要 universe 传入的路径，从 job.config 或 request.universe 字段解析 CSV 文件。

#### C-4: 修复 `FactorCache(cache_dir=...)`（silent fail，缓存走错路径）

`src/tinohelm/factor/worker.py:279`, `src/tinohelm/api/routes/factor.py:191`

```python
# 错误：
cache = FactorCache(cache_dir=str(cache_dir))  # Python 不报错但被忽略

# 正确：
cache = FactorCache(cache_root=str(cache_dir))
```

### ★★ HIGH

#### H-1: Worker 补 recover_interrupted_jobs + lifespan 注册

API 重启后 `status='running'` 的 FactorRun 永久卡死。参照 `src/tinohelm/data/worker.py` 实现：

```python
# src/tinohelm/factor/worker.py 新增
async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """重置 running → queued 并重入队。"""
    from tinohelm.core.async_queue_worker import requeue_running_jobs
    return await requeue_running_jobs(
        get_session_factory(), FactorRun, rds, QUEUE_KEY, reset_queue=True
    )
```

```python
# src/tinohelm/api/app.py lifespan 内紧跟 start_factor_worker 之前：
from tinohelm.factor.worker import recover_interrupted_jobs as recover_factor_jobs
await recover_factor_jobs(redis_client)
start_factor_worker(redis_url=cfg.redis.url)
```

#### H-2: 替换 3 处 `datetime.utcnow()`

`src/tinohelm/factor/worker.py:133, 193, 218`

```python
# 文件头：
from datetime import datetime, UTC

# 3 处替换：
datetime.utcnow()
# →
datetime.now(UTC).replace(tzinfo=None)  # 保持 naive 兼容 TIMESTAMP WITHOUT TIME ZONE
```

#### H-3: 补全 AC-2.1 必须别名

`src/tinohelm/factor/alias.py` 的 `FIELD_ALIAS` dict 缺失以下（来源字段名 → 标准别名组）：

```python
FIELD_ALIAS.update({
    # bookTicker 源
    "bid_price": ("quote_tick", "bid_price"),
    "bid_qty": ("quote_tick", "bid_qty"),
    "ask_price": ("quote_tick", "ask_price"),
    "ask_qty": ("quote_tick", "ask_qty"),
    # trade_tick 源
    "trade_price": ("trade_tick", "price"),
    "trade_qty": ("trade_tick", "quantity"),
    "trade_side": ("trade_tick", "side"),
    # metrics（OI）源
    "sum_open_interest": ("metrics", "sum_open_interest"),
    "open_interest_value": ("metrics", "open_interest_value"),
    # funding_rate 源
    "mark_price": ("funding_rate", "mark_price"),
})
```
确认具体 tuple 结构按 `alias.py` 现有风格（可能是 dict 或 tuple），保持一致。

#### H-4: Planner `_infer_source` 覆盖 open interest 变体

`src/tinohelm/factor/engine/planner.py:295`

```python
_OPEN_INTEREST_FIELDS: frozenset[str] = frozenset({
    "open_interest", "sum_open_interest", "open_interest_value"
})
```

#### H-5: Sidebar 添加 `/factor` 导航

`src/web/src/components/Sidebar.tsx` — 在 navItems 数组中（紧跟 `/backtest` 之后 / `/live` 之前）添加：

```tsx
{ href: "/factor", labelKey: "nav.factor", icon: FlaskConical },
```

并确认 `TopBar.tsx` 的路由标题映射已更新为 `"/factor": "Factor Research"`（若未）。

在 i18n 翻译字典添加 `nav.factor: "因子研究"` 中英对照（具体文件取决于项目 i18n 约定）。

#### H-6: trade_imbalance 因子实现对齐 AC-13.1

`src/tinohelm/factor/builtins/microstructure.py:18-37`

当前用 `high/low/close/volume` 替代 trade_tick 数据。两个可选方案：

**方案 A**（推荐，保留因子）：完成 `data_layer.py:234` 的 `trade_tick` 分支：
```python
elif field.source == "trade_tick":
    df = pd.read_parquet(catalog / "data" / "trade_tick" / f"{instrument_id}.parquet")
    # 聚合到 bar 频率：
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").resample(interval_rule).agg({
        "price": "last", "quantity": "sum", "side": "last"
    })
    ...
```

**方案 B**（推迟因子）：删除 `trade_imbalance`（或打 `@factor(status="experimental")` 标记），在 4-tasks.md s12 清单记录推迟原因。

#### H-7: notification-router.ts 去 any

`src/web/src/lib/notification-router.ts:10, 52, 74`

```ts
type FactorWsEvent = {
  type: string;
  run_id?: string;
  factor_name?: string;
  progress?: number;
  rating?: number;
  error?: string;
  [key: string]: unknown;
};

// 3 处 any → FactorWsEvent（或更通用的 Record<string, unknown>）
```

#### H-8: FactorExploreClient/useReport 消除 setState-in-effect

- `src/web/src/app/factor/FactorExploreClient.tsx:69`
- `src/web/src/app/factor/report/[id]/hooks/useReport.ts:39`

重写策略：将 effect 内的 `setState` 重置改为 `useState` 初始值派生，或使用 `key` prop 重置组件。

- `src/web/src/app/factor/components/DatasetPanel.tsx:151` JSX 注释 `//...` → `{/* ... */}`

### ★ MEDIUM

#### M-1: Orchestrator 不依赖 close 的因子 RuntimeError

`src/tinohelm/factor/engine/orchestrator.py:287-292`

对 `funding_rate_level/mom`、`oi_change`、`orderbook_imbalance_L1` 等不依赖 close 的因子：

```python
# 改为条件性：
if factor_spec.input_specs and any(inp.field == "close" for inp in factor_spec.input_specs):
    close_panel = _extract_close_panel(data)
    if close_panel is None:
        raise RuntimeError("'close' Panel missing for factor depending on close")
else:
    close_panel = None  # 评估阶段按需要再 raise
```

#### M-2: worker.py catalog_path

`src/tinohelm/factor/worker.py:261-264`

```python
# 错：
catalog_path = getattr(settings, "catalog_path", None) or str(default)

# 正确：
catalog_path = str(settings.paths.catalog)
```

#### M-3: DataLayer funding_rate 走 as-of 延迟

`src/tinohelm/factor/data_layer.py:203-213`

当 `field.source == "funding_rate"` 时，使用模块级的 `_align_funding_onto_bar_index`（shift(1) + ffill），而非通用 `_align_time`。

#### M-4: 回滚越界修改

15+ 文件不在 20 subtask 范围内但被修改。逐一审查：

```bash
cd /Users/ouzhuohao/TinoHelm
for f in \
  src/web/src/app/analytics/page.tsx \
  src/web/src/app/backtest/page.tsx \
  src/web/src/app/data-catalog/page.tsx \
  src/web/src/app/orders/page.tsx \
  src/web/src/app/settings/page.tsx \
  src/web/src/app/strategies/page.tsx \
  "src/web/src/app/strategies/[name]/EditorClient.tsx" \
  src/web/src/app/watchlist/page.tsx \
  src/web/src/app/layout.tsx \
  src/web/src/app/page.tsx \
  src/web/src/components/Providers.tsx \
  src/web/src/hooks/useWebSocket.ts \
  src/web/.gitignore \
  src/web/package.json \
  src/web/package-lock.json \
  src/web/src/app/globals.css \
; do
  echo "=== $f ==="
  git diff HEAD -- "$f" | head -30
done
# 对与 factor 任务无关的修改执行 git checkout HEAD -- <file>
# globals.css 必须回滚（CRITICAL-3 级：tokens.test.ts 10 failed 的根因）
```

注：`src/web/src/components/TopBar.tsx` 的 `/research → /factor` 路由映射属于 s20 AC 允许范围，可保留。

### LOW（可合并到后续修复）

- L-1: `factor/cache.py:338` `json.dump(default=str)` 去掉，让序列化失败直接抛错。
- L-2: DB migration 011 schema 按需补列（`run_id UNIQUE` / `universe` / `interval` / `rating` 用于索引查询）。
- L-3: `funding_rate.py` 与 4-tasks.md s13 清单偏差（功能达成，仅记录偏离）。

## 修复完成后的验证清单

Executor 完成以上修复后，跑以下命令确认：

```bash
cd /Users/ouzhuohao/TinoHelm

# 1. Python 测试仍全过
.venv/bin/python -m pytest tests/ -x -q
# 期望：all pass

# 2. 前端 build 清缓存后必须过
cd src/web && rm -rf .next && npm run build
# 期望：Generating static pages using 9 workers (N/N) + 无 Turbopack errors

# 3. TypeScript 严格
npx tsc --noEmit
# 期望：无输出（0 errors）

# 4. ESLint 不新增 error（允许历史遗留）
npm run lint 2>&1 | grep "factor/\|lib/notification-router\|hooks/use-tick-flash\|useCountUp"
# 期望：factor/* 和 notification-router.ts 无 error

# 5. vitest 目标测试
npx vitest run tests/lib/notification-router.test.ts
npx vitest run src/lib/__tests__/notification-router.test.ts
# 期望：all pass

# 6. globals.css 回滚后 fonts 测试恢复
npx vitest run tests/fonts/tokens.test.ts
# 期望：all 15 pass

# 7. DataLayer 构造器调用修复验证（用 grep）
grep -rn "DataLayer(catalog_path" src/tinohelm/ && echo "still broken" || echo "OK"
grep -rn "FactorCache(cache_dir" src/tinohelm/ && echo "still broken" || echo "OK"
# 期望：两个都输出 "OK"

# 8. Sidebar 导航
grep -E '"/factor"|href="/factor"' src/web/src/components/Sidebar.tsx
# 期望：有匹配
```

## 超限提醒

当前是 round 1，`max_kickback_rounds = 10`。本轮问题集中，修复点明确，预计 1 轮 executor 工作可以全部修复。修复完后重新 `/cage:verify`。

## 相关报告

- Verifier 详细报告：`.cage/tasks/2026-04-22-declarative-factor-framework/verify/report-verifier-r1.md`
- Code-Reviewer 详细报告：`.cage/tasks/2026-04-22-declarative-factor-framework/verify/report-code-reviewer-r1.md`
