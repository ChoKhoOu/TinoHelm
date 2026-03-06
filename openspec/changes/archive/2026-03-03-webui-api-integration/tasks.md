## 1. Backend — Watchlist API & DB Model

- [x] 1.1 Add `WatchlistItem` SQLAlchemy model to `src/tinohelm/db/models.py`: id (PK), instrument_id (varchar, unique), source (varchar), created_at (timestamp with timezone, default=now)
- [x] 1.2 Create Alembic migration for `watchlist_items` table
- [x] 1.3 Create `src/tinohelm/api/routes/watchlist.py`: GET /api/watchlist (list all), POST /api/watchlist (add, 409 on duplicate), DELETE /api/watchlist/{id} (remove, 404 if missing)
- [x] 1.4 Register watchlist router in `src/tinohelm/api/app.py`

## 2. Backend — Dashboard & Analytics Endpoints

- [x] 2.1 Update `GET /api/dashboard/summary` in `dashboard.py`: return real counts from positions/orders/strategies tables, compute equity from positions sum, return numeric values (not formatted strings)
- [x] 2.2 Implement `GET /api/analytics/returns-heatmap`: aggregate completed backtest_runs by year/month, return `{data: [{year, month, return_pct}]}`
- [x] 2.3 Implement `GET /api/analytics/drawdown`: extract max_drawdown from completed backtest_runs ordered by completed_at, return `{data: [{date, drawdown}]}`
- [x] 2.4 Implement `GET /api/analytics/distribution`: compute histogram bins from backtest_runs total_return values, return `{data: [{range, count}]}`
- [x] 2.5 Implement `GET /api/analytics/rolling-sharpe`: compute rolling sharpe from backtest_runs ordered by date, return `{data: [{date, sharpe}]}`

## 3. Backend — Settings & Health Enhancements

- [x] 3.1 Enhance `GET /api/health` in `settings.py`: add nautilus_version (from nautilus_trader.__version__), python_version (sys.version), redis_version (from redis INFO), uptime_seconds (from startup timestamp), platform_version (from package metadata)
- [x] 3.2 Record API startup timestamp in lifespan handler (`app.py`) and expose via deps for uptime calculation

## 4. Backend — Node Status Risk Metrics

- [x] 4.1 Enhance `GET /api/node/status` in `node.py`: add risk_metrics object computed from positions table — daily_var, max_drawdown, margin_used_pct, leverage, total_exposure

## 5. Frontend — Dashboard Page Integration

- [x] 5.1 Replace `page.tsx` (dashboard): remove mockEquityData/mockStrategies/mockMetrics constants, fetch `GET /api/dashboard/summary`, format numeric values in frontend, add loading/error states, show empty state when API returns zeros

## 6. Frontend — Live Trading Page Integration

- [x] 6.1 Replace `live/page.tsx`: remove riskMetrics hardcoded constant, fetch risk_metrics from `GET /api/node/status` response, update riskMetrics state from API data

## 7. Frontend — Backtest Page Integration

- [x] 7.1 Replace `backtest/page.tsx`: remove hardcoded strategyOptions/venueOptions, fetch strategy list from `GET /api/strategies` on mount, populate Select dropdowns dynamically

## 8. Frontend — Strategies Page Integration

- [x] 8.1 Replace `strategies/page.tsx`: remove hardcoded configParams/performanceStats, fetch `GET /api/strategies` on mount, display strategy list with metadata from API, add loading/error/empty states

## 9. Frontend — Settings Page Integration

- [x] 9.1 Replace `settings/page.tsx`: remove hardcoded apiKeys/riskLimits/systemInfo, fetch `GET /api/health` for system info, fetch `GET /api/settings` for risk limits, implement PUT /api/settings/risk-limits on value change, add loading/error states

## 10. Frontend — Data Catalog Page Integration

- [x] 10.1 Replace `data-catalog/page.tsx`: remove mockDatasets/stats constants, fetch `GET /api/data/catalog` on mount, compute summary stats from response, add loading/error/empty states

## 11. Frontend — Orders Page Integration

- [x] 11.1 Replace `orders/page.tsx`: remove mockOrders constant, fetch `GET /api/orders` with activeTab and search as query params, add loading/error/empty states

## 12. Frontend — Portfolio Page Integration

- [x] 12.1 Replace `portfolio/page.tsx`: remove allocations/venues constants, fetch `GET /api/portfolio/allocation` on mount, group positions into allocation categories, add loading/error/empty states

## 13. Frontend — Analytics Page Integration

- [x] 13.1 Replace `analytics/page.tsx`: remove all mock data constants (heatmapData/drawdownData/distributionData/rollingShapeData), fetch all four analytics endpoints in parallel, render charts with API data, show "Run backtests to see analytics" empty state when no data

## 14. Frontend — Watchlist Page Integration

- [x] 14.1 Replace `watchlist/page.tsx`: remove mockWatchlist constant, fetch `GET /api/watchlist` on mount, implement "Add Instrument" form with POST /api/watchlist, implement delete with DELETE /api/watchlist/{id}, add loading/error/empty states

## 15. Docker Fix — App Startup

- [x] 15.1 Verify `app = create_app()` in `app.py` resolves the `Attribute "app" not found` Docker error
- [x] 15.2 Verify Alembic `env.py` reads `TINO_DATABASE__URL` env var for container migrations
- [x] 15.3 Rebuild and test `docker compose up --build` — all 4 services healthy

## 16. Build Verification

- [x] 16.1 Verify all Python files compile without errors (`python -c "import ast; ast.parse(...)"`)
- [x] 16.2 Verify Next.js build succeeds (`npm run build` in web/)
- [x] 16.3 Verify no remaining mock* or hardcoded data constants in page files (grep check)
