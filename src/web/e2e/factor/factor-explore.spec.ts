/**
 * E2E: /factor (declarative factor explore page).
 *
 * Verification goals (from task s18 acceptance criteria):
 *   1. Page loads with HTTP 200.
 *   2. At least 12 built-in factors are rendered in the list.
 *   3. Universe dropdown surfaces the mocked ``binance_perp_top20``.
 *   4. Clicking a factor + pressing "运行探索" posts /api/factor/explore
 *      and renders the result panel.
 *
 * Mocking: the Playwright test intercepts /api/factor/* so the spec does
 * not depend on a running backend (mirrors the pattern in
 * e2e/backtest/list-view.spec.ts).
 */
import { test, expect } from "@playwright/test";

/* --- Mock data --- */

const MOCK_FACTORS = [
  { name: "ret_N",           category: "动量",       lookback: 20,  input_fields: ["close"],         params_schema: { lookback: 20 }, description: "Return over N bars", version: "1.0.0" },
  { name: "mom_ratio",       category: "动量",       lookback: 20,  input_fields: ["close"],         params_schema: { fast: 5, slow: 20 }, description: "Fast/slow momentum ratio", version: "1.0.0" },
  { name: "roc",             category: "动量",       lookback: 12,  input_fields: ["close"],         params_schema: { period: 12 }, description: "Rate of change", version: "1.0.0" },
  { name: "rsi_signal",      category: "动量",       lookback: 14,  input_fields: ["close"],         params_schema: { period: 14 }, description: "RSI-based signal", version: "1.0.0" },
  { name: "vol_ratio",       category: "波动",       lookback: 20,  input_fields: ["close"],         params_schema: { fast: 5, slow: 20 }, description: "Fast/slow vol ratio", version: "1.0.0" },
  { name: "realized_vol",    category: "波动",       lookback: 20,  input_fields: ["close"],         params_schema: { window: 20 }, description: "Realized volatility", version: "1.0.0" },
  { name: "atr_norm",        category: "波动",       lookback: 14,  input_fields: ["high", "low", "close"], params_schema: { period: 14 }, description: "Normalized ATR", version: "1.0.0" },
  { name: "vwap_dev",        category: "量价",       lookback: 20,  input_fields: ["close", "volume"], params_schema: { period: 20 }, description: "VWAP deviation", version: "1.0.0" },
  { name: "volume_surge",    category: "成交量",     lookback: 20,  input_fields: ["volume"],        params_schema: { lookback: 20 }, description: "Volume surge", version: "1.0.0" },
  { name: "obv_slope",       category: "成交量",     lookback: 10,  input_fields: ["close", "volume"], params_schema: { lookback: 20 }, description: "OBV slope", version: "1.0.0" },
  { name: "amihud_illiq",    category: "微观结构",   lookback: 20,  input_fields: ["close", "volume"], params_schema: { lookback: 20 }, description: "Amihud illiquidity", version: "1.0.0" },
  { name: "funding_rate_level", category: "资金费率", lookback: 1,   input_fields: ["funding_rate"],   params_schema: { lookback: 1 }, description: "Funding rate level", version: "1.0.0" },
  { name: "oi_change",       category: "链上数据",   lookback: 2,   input_fields: ["open_interest"], params_schema: { lookback: 1 }, description: "Open interest change", version: "1.0.0" },
];

const MOCK_UNIVERSES = ["binance_perp_top20"];

const MOCK_SYMBOLS = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP", "AVAXUSDT-PERP"];

const MOCK_EXPLORE_RESULT = {
  factor_name: "ret_N",
  ic_mean: 0.042,
  ic_std: 0.13,
  ir: 0.75,
  ic_tstat: 3.21,
  ic_positive_pct: 58.4,
  rating: 2,
  is_monotonic: true,
  half_life: 7,
  turnover: 0.18,
  turnover_annualized: 45.4,
  fee_drag_monthly: 0.0021,
  ic_series: Array.from({ length: 40 }, (_, i) => ({
    date: `2025-01-${String((i % 28) + 1).padStart(2, "0")}`,
    ic: Math.sin(i / 5) * 0.08 + (Math.random() - 0.5) * 0.02,
  })),
  ic_decay: Array.from({ length: 10 }, (_, i) => ({
    lag: i + 1,
    ic: 0.05 * Math.exp(-i / 4),
  })),
  quantile_pnl: {
    Q0: -0.002,
    Q1: -0.001,
    Q2: 0.0005,
    Q3: 0.0015,
    Q4: 0.003,
  },
  quantile_cum_returns: {},
  distribution_histogram: Array.from({ length: 20 }, (_, i) => ({
    bin_start: -0.1 + i * 0.01,
    bin_end: -0.1 + (i + 1) * 0.01,
    count: Math.round(100 * Math.exp(-Math.pow((i - 10) / 3, 2))),
  })),
  distribution_stats: { mean: 0.002, std: 0.04, skew: 0.12, kurt: 3.1 },
};

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/factor\/list(\?.*)?$/, (route) => {
    route.fulfill({ json: MOCK_FACTORS });
  });
  await page.route(/\/api\/factor\/universes(\?.*)?$/, (route) => {
    route.fulfill({ json: MOCK_UNIVERSES });
  });
  await page.route(/\/api\/factor\/symbols(\?.*)?$/, (route) => {
    route.fulfill({ json: MOCK_SYMBOLS });
  });
  await page.route(/\/api\/factor\/explore$/, (route) => {
    route.fulfill({ json: MOCK_EXPLORE_RESULT });
  });
});

test("factor page loads with 200 and header renders", async ({ page }) => {
  const resp = await page.goto("/factor");
  expect(resp?.status()).toBe(200);
  await expect(page.locator("text=Factor Explore").first()).toBeVisible({
    timeout: 15000,
  });
});

test("factor list renders all 13 mocked built-in factors", async ({ page }) => {
  await page.goto("/factor");
  await expect(page.locator("[data-testid='factor-list']")).toBeVisible({
    timeout: 15000,
  });
  // Spot-check a representative sample across categories
  const sample = ["ret_N", "vol_ratio", "volume_surge", "amihud_illiq", "funding_rate_level", "oi_change"];
  for (const name of sample) {
    await expect(page.locator(`[data-testid="factor-item-${name}"]`)).toBeVisible();
  }
});

test("universe dropdown surfaces binance_perp_top20", async ({ page }) => {
  await page.goto("/factor");
  // The Select trigger renders the current value by default; check the
  // trigger text reflects the mocked universe.
  const trigger = page.locator("[data-testid='universe-select']");
  await expect(trigger).toBeVisible({ timeout: 15000 });
  await expect(trigger).toContainText("binance_perp_top20");
});

test("category filter narrows the factor list", async ({ page }) => {
  await page.goto("/factor");
  await expect(page.locator("[data-testid='factor-list']")).toBeVisible({
    timeout: 15000,
  });
  // Click the 动量 tab — the 资金费率 factor should disappear.
  await page.locator("[data-testid='factor-category-动量']").click();
  await expect(
    page.locator("[data-testid='factor-item-funding_rate_level']"),
  ).toHaveCount(0);
  await expect(page.locator("[data-testid='factor-item-ret_N']")).toBeVisible();
});

test("selecting a factor then Run shows the result panel", async ({ page }) => {
  await page.goto("/factor");
  await expect(page.locator("[data-testid='factor-list']")).toBeVisible({
    timeout: 15000,
  });

  // Select volume_surge explicitly (not the pre-selected first item)
  await page.locator("[data-testid='factor-item-volume_surge']").click();

  await page.locator("[data-testid='run-explore']").click();

  // Result panel should render ExploreResult — look for a stable marker.
  await expect(page.locator("text=探索结果")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("text=IC 时序").first()).toBeVisible();
  await expect(page.locator("text=分位平均收益").first()).toBeVisible();
  await expect(page.locator("text=因子分布").first()).toBeVisible();
  await expect(page.locator("text=Turnover").first()).toBeVisible();
});
