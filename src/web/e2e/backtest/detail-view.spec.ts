import { test, expect } from "@playwright/test";

const COMPLETED_RUN = {
  run_id: "run-completed-001",
  strategy_name: "ema_cross_demo",
  symbol: "BTCUSDT-PERP",
  interval: "5m",
  start_date: "2025-01-01",
  end_date: "2025-03-31",
  status: "completed",
  created_at: new Date().toISOString(),
  progress_pct: 100,
  result_summary: {
    total_pnl: 12345.67,
    total_return_pct: 12.3,
    sharpe_ratio: 1.85,
    calmar_ratio: 2.1,
    win_rate: 0.62,
    total_trades: 87,
  },
};


test.beforeEach(async ({ page }) => {
  // Register catch-all first (lowest priority in Playwright's LIFO order)
  await page.route(/\/api\/backtest\//, (route) => {
    route.fulfill({ json: {} });
  });
  // More specific routes registered after override the catch-all
  await page.route(new RegExp(`/api/backtest/${COMPLETED_RUN.run_id}/result`), (route) => {
    route.fulfill({
      json: {
        statistics: {
          total_pnl: 12345.67, total_return_pct: 12.3, sharpe_ratio: 1.85,
          win_rate: 0.62, total_trades: 87, winning_trades: 54, losing_trades: 33,
        },
        equity_curve: [
          { date: "2025-01-01", equity: 100000 },
          { date: "2025-03-31", equity: 112345 },
        ],
        trade_log: [],
      },
    });
  });
  await page.route(/\/api\/backtest\/runs/, (route) => {
    route.fulfill({ json: { runs: [COMPLETED_RUN], total: 1 } });
  });
  await page.route(/\/api\/strategies/, (route) => {
    route.fulfill({ json: [] });
  });
});

async function navigateToDetail(page: import("@playwright/test").Page) {
  await page.goto("/backtest");
  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // Click the history row to expand it (BacktestHistoryRow always expands on click)
  await page.locator("text=ema_cross_demo").first().click();
  // Click "查看完整报告" button that appears in the expanded completed row
  const viewBtn = page.locator("button:has-text('查看完整报告')").first();
  await expect(viewBtn).toBeVisible({ timeout: 5000 });
  await viewBtn.click();
}

test("detail view renders 6 KPI cells", async ({ page }) => {
  await navigateToDetail(page);

  // Wait for detail view to load
  await expect(page.locator("[data-kpi-grid]")).toBeVisible({ timeout: 10000 });

  // Should have exactly 6 KPI cells
  const kpiCells = page.locator("[data-kpi-cell]");
  await expect(kpiCells).toHaveCount(6, { timeout: 5000 });
});

test("switching to Overview tab shows equity SVG component", async ({ page }) => {
  await navigateToDetail(page);

  await expect(page.locator("[data-kpi-grid]")).toBeVisible({ timeout: 10000 });

  // Overview tab should be active by default — click it to ensure
  await page.locator("button:has-text('Overview')").click();

  // OverviewEquitySvg renders an SVG inside the overview tab
  // The equity chart is a RechartsChart or SVG — check it appears
  await expect(page.locator("svg").first()).toBeVisible({ timeout: 10000 });
});
