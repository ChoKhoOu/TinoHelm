import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/backtest\/runs/, (route) => {
    route.fulfill({ json: { runs: [], total: 0 } });
  });
  await page.route(/\/api\/strategies\/[^/]+\/defaults/, (route) => {
    route.fulfill({
      json: {
        subscriptions: [
          { symbol: "BTCUSDT-PERP", timeframe: "5m", data_type: "bar" },
        ],
      },
    });
  });
  await page.route(/\/api\/strategies\/[^/]+\/params/, (route) => {
    route.fulfill({ json: [] });
  });
  await page.route(/\/api\/strategies(?:\?|$)/, (route) => {
    route.fulfill({
      json: [
        { name: "ema_cross_demo" },
        { name: "btc_momentum" },
      ],
    });
  });
  await page.route(/\/api\/data\/symbols/, (route) => {
    route.fulfill({ json: [] });
  });
});

test("clicking + 创建回测 opens the create sheet", async ({ page }) => {
  await page.goto("/backtest");

  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });

  // Click the header create button (not the empty state button)
  await page.locator("button:has-text('创建回测')").first().click();

  // Sheet should slide out — wait for the sheet title
  await expect(page.locator("text=创建回测").nth(1)).toBeVisible({ timeout: 5000 });
});

test("stepper renders 3 states and step navigation works", async ({ page }) => {
  await page.goto("/backtest");

  await expect(page.locator("text=回测管理")).toBeVisible({ timeout: 15000 });
  await page.locator("button:has-text('创建回测')").first().click();

  // Sheet should open
  await expect(page.locator("text=创建回测").nth(1)).toBeVisible({ timeout: 5000 });

  // Stepper labels are visible
  await expect(page.locator("text=策略与标的")).toBeVisible({ timeout: 3000 });
  await expect(page.locator("text=时间区间")).toBeVisible({ timeout: 3000 });
  await expect(page.locator("text=资金与成本")).toBeVisible({ timeout: 3000 });

  // Guard (FR-044): Next is disabled until strategy selected. Open the
  // strategy search dropdown (input[placeholder="搜索策略..."]) and pick one;
  // selecting triggers /api/strategies/:name/defaults which autofills subs.
  await page.locator('input[placeholder="搜索策略..."]').focus();
  await page.locator("text=ema_cross_demo").first().click();

  // Click 下一步 to go to step 2
  await page.locator("button:has-text('下一步')").click();
  await expect(page.locator("text=开始日期")).toBeVisible({ timeout: 3000 });

  // Fill date range so FR-044 step-2 guard passes.
  await page.locator('input[type="date"]').first().fill("2025-01-01");
  await page.locator('input[type="date"]').nth(1).fill("2025-02-01");

  // Click 下一步 again to go to step 3
  await page.locator("button:has-text('下一步')").click();
  // Step 3 shows initial capital label (uses <SectionLabel> component now).
  await expect(page.locator("text=初始资金").first()).toBeVisible({ timeout: 3000 });
});
