import { test, expect } from "@playwright/test";
import type { TradeLogEntry } from "../../src/app/backtest/types";

const COMPLETED_RUN = {
  run_id: "run-trades-001",
  strategy_name: "trades_test_strategy",
  symbol: "BTCUSDT-PERP",
  interval: "5m",
  start_date: "2025-01-01",
  end_date: "2025-03-31",
  status: "completed",
  created_at: new Date().toISOString(),
  progress_pct: 100,
  result_summary: {
    total_pnl: 5000,
    total_return_pct: 5.0,
    sharpe_ratio: 1.4,
    calmar_ratio: 1.8,
    win_rate: 0.58,
    total_trades: 20,
  },
};

// 20 mock trade entries
const MOCK_TRADES: TradeLogEntry[] = Array.from({ length: 20 }, (_, i) => ({
  opened_at: new Date(Date.now() - (20 - i) * 86400000).toISOString(),
  closed_at: new Date(Date.now() - (20 - i) * 86400000 + 3600000).toISOString(),
  instrument: "BTCUSDT-PERP.BINANCE",
  side: i % 2 === 0 ? "BUY" : "SELL",
  quantity: "0.1",
  avg_open: (50000 + i * 100).toString(),
  avg_close: (50000 + i * 100 + (i % 3 === 0 ? 500 : -200)).toString(),
  realized_pnl: i % 3 === 0 ? (i + 1) * 50 : -(i + 1) * 20,
  duration: "1h",
}));


test.beforeEach(async ({ page }) => {
  // Register catch-all first (lowest priority in Playwright's LIFO order)
  await page.route(/\/api\/backtest\//, (route) => {
    route.fulfill({ json: {} });
  });
  // Specific result endpoint (higher priority — registered after)
  await page.route(new RegExp(`/api/backtest/${COMPLETED_RUN.run_id}/result`), (route) => {
    route.fulfill({
      json: {
        statistics: {
          total_pnl: 5000, total_return_pct: 5.0, sharpe_ratio: 1.4,
          win_rate: 0.58, total_trades: 20, winning_trades: 12, losing_trades: 8,
        },
        equity_curve: [],
        trade_log: MOCK_TRADES,
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

async function navigateToTradesView(page: import("@playwright/test").Page) {
  await page.goto("/backtest");
  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // Expand history row and click View
  await page.locator("text=trades_test_strategy").first().click();
  const viewBtn = page.locator("button:has-text('查看完整报告')").first();
  await expect(viewBtn).toBeVisible({ timeout: 5000 });
  await viewBtn.click();

  // Wait for detail view KPI grid
  await expect(page.locator("[data-kpi-grid]")).toBeVisible({ timeout: 10000 });

  // Overview tab is active by default — "查看所有交易" is in OverviewTab
  // Click "Overview" tab to ensure it's active
  await page.locator("button:has-text('Overview')").click();

  // Click 查看所有交易 button
  const viewAllBtn = page.locator("button:has-text('查看所有交易')");
  await expect(viewAllBtn).toBeVisible({ timeout: 10000 });
  await viewAllBtn.click();

  // Wait for trades view header
  await expect(page.locator("text=所有交易")).toBeVisible({ timeout: 5000 });
}

test("from Overview tab click 查看所有交易 navigates to trades view", async ({ page }) => {
  await page.goto("/backtest");
  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  await page.locator("text=trades_test_strategy").first().click();
  // "查看完整报告" appears in the expanded completed row
  const viewBtn = page.locator("button:has-text('查看完整报告')").first();
  await expect(viewBtn).toBeVisible({ timeout: 5000 });
  await viewBtn.click();

  await expect(page.locator("[data-kpi-grid]")).toBeVisible({ timeout: 10000 });

  // Overview tab is default — find 查看所有交易
  const viewAllBtn = page.locator("button:has-text('查看所有交易')");
  await expect(viewAllBtn).toBeVisible({ timeout: 15000 });
  await viewAllBtn.click();

  await expect(page.locator("text=所有交易")).toBeVisible({ timeout: 5000 });
});

test("trades view shows 6 summary cells and 8-column table", async ({ page }) => {
  await navigateToTradesView(page);

  // 6 summary cells
  const summaryCells = page.locator("[data-summary-cell]");
  await expect(summaryCells).toHaveCount(6, { timeout: 5000 });

  // 8 table columns (header row)
  const headerCells = page.locator("thead th");
  await expect(headerCells).toHaveCount(8, { timeout: 5000 });
});

test("search input is focused by Cmd+K keyboard shortcut", async ({ page }) => {
  await navigateToTradesView(page);

  // Press Cmd+K (or Ctrl+K)
  await page.keyboard.press("Meta+k");

  // The search input should be focused
  const searchInput = page.locator("input[placeholder='搜索...']");
  await expect(searchInput).toBeFocused({ timeout: 3000 });
});
