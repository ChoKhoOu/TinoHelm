import { test, expect } from "@playwright/test";

const MOCK_RUNS = Array.from({ length: 10 }, (_, i) => ({
  run_id: `run-${i.toString().padStart(4, "0")}`,
  strategy_name: `ema_cross_${i}`,
  symbol: "BTCUSDT-PERP",
  interval: "5m",
  start_date: "2025-01-01",
  end_date: "2025-03-31",
  status: i === 0 ? "running" : i === 1 ? "failed" : "completed",
  created_at: new Date(Date.now() - i * 86400000).toISOString(),
  progress_pct: i === 0 ? 42 : null,
  result_summary:
    i > 1
      ? {
          total_pnl: (i % 2 === 0 ? 1 : -1) * i * 100,
          total_return_pct: (i % 2 === 0 ? 1 : -1) * i,
          sharpe_ratio: 1.2,
          win_rate: 0.55,
          total_trades: 50 + i,
        }
      : null,
}));

test.beforeEach(async ({ page }) => {
  // Intercept all API calls (the static build fetches from http://localhost:8000)
  // API returns { runs: [...], total: N } shape
  await page.route(/\/api\/backtest\/runs/, (route) => {
    route.fulfill({ json: { runs: MOCK_RUNS, total: MOCK_RUNS.length } });
  });
  await page.route(/\/api\/strategies/, (route) => {
    route.fulfill({ json: [] });
  });
});

test("list view renders rows for all 10 mock runs", async ({ page }) => {
  await page.goto("/backtest");

  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // All 10 strategies should appear somewhere on the page
  for (let i = 0; i < 10; i++) {
    await expect(page.locator(`text=ema_cross_${i}`).first()).toBeVisible({ timeout: 5000 });
  }
});

test("running row shows RingProgress svg after expand", async ({ page }) => {
  await page.goto("/backtest");

  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // The running row (ema_cross_0) is in the active zone and can be clicked to expand
  const runningRow = page.locator("text=ema_cross_0").first();
  await runningRow.click();

  // After expanding, RingProgress svg with data-ring-progress should appear
  const ringProgress = page.locator("[data-ring-progress]");
  await expect(ringProgress.first()).toBeVisible({ timeout: 5000 });
});

test("failed row shows retry button after expand", async ({ page }) => {
  await page.goto("/backtest");

  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // The failed row (ema_cross_1) is in the history zone
  // Click to expand it
  const failedRow = page.locator("text=ema_cross_1").first();
  await failedRow.click();

  // After expanding, retry button (重试) should appear
  const retryBtn = page.locator("button:has-text('重试')");
  await expect(retryBtn.first()).toBeVisible({ timeout: 5000 });
});
