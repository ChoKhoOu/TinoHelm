## Context

TinoHelm MVP 已完成后端 API 和前端 UI 的独立开发，但前后端数据集成未完成。当前状态：
- 7/11 页面完全使用硬编码 mock 数据
- 3/11 页面有 API 调用但仍有 mock 残留（riskMetrics、strategyOptions）
- 后端 dashboard/analytics 端点返回 placeholder 数据（空数组或零值）
- 后端已有 `/api/portfolio/allocation`、`/api/orders`、`/api/strategies`、`/api/data/catalog`、`/api/settings/*` 端点
- 后端缺少 watchlist CRUD 端点

## Goals / Non-Goals

**Goals:**
- 所有前端页面从后端 API 获取真实数据，移除 mock 数据常量
- 后端 dashboard/analytics 端点返回从数据库计算的真实统计数据
- Settings 页面读写真实配置
- Watchlist 支持持久化 CRUD
- 每个页面有 loading 和 error 状态

**Non-Goals:**
- 不改变 UI 布局或视觉设计
- 不新增实时行情 WebSocket（Watchlist 暂用轮询，实时行情留给后续）
- 不实现 Analytics 页面的完整量化统计引擎（基于已有回测结果聚合即可）
- 不修改认证/授权机制

## Decisions

### D1: 前端数据获取模式 — useEffect + apiGet + fallback 空状态

每个页面使用统一模式：
```tsx
const [data, setData] = useState<T[]>([]);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);

useEffect(() => {
  apiGet<T[]>("/api/xxx")
    .then(d => d && setData(d))
    .catch(() => setError("Failed to load"))
    .finally(() => setLoading(false));
}, []);
```

**不使用 mock 作为 fallback**，API 失败时显示空状态 + 错误提示，避免用户分不清真实数据和假数据。

**替代方案**: SWR/React Query — 过度设计，MVP 阶段手动 fetch 更轻量。

### D2: Analytics 数据源 — 聚合已有回测结果

Analytics 4 个图表的数据从 `backtest_runs` 表聚合：
- 月收益热力图：按月分组回测结果 `result_summary_json.total_return`
- 回撤图：从回测结果的 `max_drawdown` 字段提取
- 收益分布：对所有回测的收益率做直方图分桶
- 滚动 Sharpe：按时间窗口滚动计算

不需要额外的数据收集，直接复用回测结果数据。

### D3: Watchlist 存储 — 数据库表

新增 `watchlist_items` 表（id, instrument_id, source, created_at）。API 端点：
- `GET /api/watchlist` — 列出所有关注项
- `POST /api/watchlist` — 添加关注项
- `DELETE /api/watchlist/{id}` — 删除关注项

价格数据通过前端轮询 `/api/node/status` 或 WebSocket ticker channel 获取。

### D4: Settings 系统信息 — 从运行时读取

系统信息从以下来源获取：
- NautilusTrader 版本：`nautilus_trader.__version__`
- Python 版本：`sys.version`
- Redis 版本：`redis.info("server")["redis_version"]`
- Uptime：API 启动时记录 `start_time`，运行时计算差值
- Platform 版本：从 `pyproject.toml` 的 `version` 字段读取

### D5: Dashboard summary 对齐 — 后端返回前端期望的字段结构

当前后端返回 `{total_equity, daily_pnl, open_positions, ...}` 数字类型，前端期望 `{totalEquity: "$1,284,567", ...}` 格式化字符串。

决策：**后端返回原始数字，前端负责格式化显示**。修改前端 DashboardSummary 接口对齐后端。

## Risks / Trade-offs

- **[数据库为空时 Analytics 无数据]** → 显示空状态提示"运行回测后查看分析"，不使用 mock
- **[Watchlist 无实时价格]** → MVP 阶段暂用页面加载时获取一次，后续添加 WebSocket ticker
- **[Dashboard equity/PnL 依赖交易引擎运行]** → 引擎未运行时显示 0 或 "N/A"
