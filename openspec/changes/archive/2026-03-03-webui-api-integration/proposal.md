## Why

Web UI 的 11 个页面中有 7 个完全使用硬编码 mock 数据（Portfolio、Orders、Analytics、Watchlist、Settings、Strategies、Data Catalog），另外 3 个（Dashboard、Live Trading、Backtest）虽有 API 调用但仍有大量 mock 残留。后端 API 端点大部分已存在但返回 placeholder 数据或前端未对接。需要完成前后端数据集成，使所有页面显示真实数据。

## What Changes

- 前端所有页面替换 mock 数据为 API 调用，使用 `useEffect` + `apiGet()` 获取后端数据
- 后端 dashboard/analytics 端点从 placeholder 升级为查询真实数据库数据
- 后端新增缺失的 watchlist CRUD 端点
- 后端 settings 端点补充系统信息查询（版本、uptime）
- 前端 Backtest 页面策略列表从 `/api/strategies` 动态获取
- Live Trading 页面 riskMetrics 从 Redis 心跳数据获取
- Dashboard summary 返回结构对齐前端 DashboardSummary 接口

## Capabilities

### New Capabilities
- `watchlist-api`: Watchlist CRUD 端点 — 用户可添加/删除/列出关注的交易对，支持 WebSocket 实时行情推送

### Modified Capabilities
- `trading-api`: 补全 dashboard summary 真实数据计算、analytics 端点实现、settings 系统信息、risk metrics 查询
- `web-ui`: 所有页面替换 mock 数据为 API 调用，添加 loading/error 状态处理

## Impact

- **后端修改**: `dashboard.py`（summary/analytics 实现）、`settings.py`（系统信息）、`node.py`（risk metrics）、新增 `watchlist.py`
- **前端修改**: 全部 10 个页面文件（除 `_not-found`），`api.ts` 类型定义
- **数据库**: 新增 `watchlist` 表（instrument_id, source, created_at）
- **依赖**: 无新依赖
