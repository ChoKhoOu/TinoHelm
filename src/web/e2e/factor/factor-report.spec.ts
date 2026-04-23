/**
 * E2E: /factor/report/[id] (declarative factor diagnostic report).
 *
 * Verification goals (task s19 acceptance criteria):
 *   1. Page loads with HTTP 200 (mocked ``/api/factor/report/{id}``).
 *   2. Meta row renders factor name + status badge + timestamps.
 *   3. All 4 tab triggers render with verdict pills.
 *   4. Clicking each tab surfaces the expected ``data-testid`` panel
 *      and at least one chart.
 *   5. In-progress / failed status paths render the hint / error block.
 */
import { test, expect } from "@playwright/test";

/* ------------------------------------------------------------------ */
/*  Mock EvalResult — covers all tabs with a realistic payload.        */
/* ------------------------------------------------------------------ */

const MOCK_EVAL_RESULT = {
  ic_mean: 0.042,
  ic_std: 0.13,
  ir: 0.75,
  ic_tstat: 3.21,
  ic_positive_pct: 58.4,
  ic_max_abs: 0.31,
  half_life: 7,

  quantile_pnl: {
    Q0: -0.002,
    Q1: -0.001,
    Q2: 0.0005,
    Q3: 0.0015,
    Q4: 0.003,
  },
  is_monotonic: true,
  quantile_cum_returns: {},

  turnover: 0.18,
  turnover_annualized: 45.4,
  fee_drag_monthly: 0.0021,

  rating: 2,

  ic_series: Array.from({ length: 40 }, (_, i) => ({
    date: `2025-01-${String((i % 28) + 1).padStart(2, "0")}`,
    ic: Math.sin(i / 5) * 0.08,
  })),
  ic_decay: Array.from({ length: 10 }, (_, i) => ({
    lag: i + 1,
    ic: 0.05 * Math.exp(-i / 4),
  })),

  distribution_stats: { mean: 0.002, std: 0.04, skew: 0.12, kurt: 3.1 },
  distribution_histogram: Array.from({ length: 20 }, (_, i) => ({
    bin_start: -0.1 + i * 0.01,
    bin_end: -0.1 + (i + 1) * 0.01,
    count: Math.round(100 * Math.exp(-Math.pow((i - 10) / 3, 2))),
  })),

  robustness: {
    shuffle: {
      real_ic: 0.042,
      p_value: 0.012,
      significant: true,
      shuffle_distribution: Array.from({ length: 30 }, (_, i) => ({
        bin_start: -0.06 + i * 0.004,
        bin_end: -0.06 + (i + 1) * 0.004,
        count: Math.round(30 * Math.exp(-Math.pow((i - 15) / 4, 2))),
      })),
    },
    subsample: [
      { period: "2024-10", ic: 0.03 },
      { period: "2024-11", ic: 0.06 },
      { period: "2024-12", ic: -0.01 },
      { period: "2025-01", ic: 0.04 },
      { period: "2025-02", ic: 0.05 },
      { period: "2025-03", ic: 0.02 },
    ],
    cross_symbol: [
      { symbol: "BTCUSDT-PERP", ic: 0.05, n_obs: 500 },
      { symbol: "ETHUSDT-PERP", ic: 0.038, n_obs: 500 },
      { symbol: "SOLUSDT-PERP", ic: -0.012, n_obs: 420 },
      { symbol: "AVAXUSDT-PERP", ic: 0.022, n_obs: 400 },
    ],
  },

  cost: {
    gross_edge_bps: 75.6,
    fee_cost_bps: 14.4,
    slippage_bps: 36.0,
    net_edge_bps: 25.2,
  },
};

/**
 * Static-export route: ``/factor/report/[id]/page.tsx`` generates
 * ``/factor/report/_`` as a placeholder. We navigate to that URL and let
 * ``useParams().id`` resolve to ``"_"`` — the API mock below intercepts
 * ``/api/factor/report/_`` so the client fetches cleanly.
 */
const RUN_ID_PLACEHOLDER = "_";

const MOCK_REPORT_RESPONSE = {
  run_id: "11111111-2222-3333-4444-555555555555",
  factor_name: "ret_N",
  status: "completed",
  progress: 100,
  error: null,
  result: MOCK_EVAL_RESULT,
  config: {
    universe: ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP"],
    start: "2025-01-01",
    end: "2025-04-01",
    forward_period: 5,
    quantiles: 5,
    cost_bps: 4.0,
    ic_freq: "D",
    log_ret: false,
    params: { lookback: 20 },
  },
  created_at: "2025-04-01T10:00:00Z",
  finished_at: "2025-04-01T10:12:34Z",
};

const REPORT_URL = `/factor/report/${RUN_ID_PLACEHOLDER}`;

/* ------------------------------------------------------------------ */
/*  Fixture setup                                                      */
/* ------------------------------------------------------------------ */

test.beforeEach(async ({ page }) => {
  // Default route — completed report.  Match any factor report request so
  // the test doesn't depend on the exact run_id segment.
  await page.route(/\/api\/factor\/report\/[^?]+(\?.*)?$/, (route) =>
    route.fulfill({ json: MOCK_REPORT_RESPONSE }),
  );
});

/* ------------------------------------------------------------------ */
/*  1. HTTP 200 + meta row renders                                     */
/* ------------------------------------------------------------------ */

test("factor report page loads with 200 and meta renders", async ({ page }) => {
  const resp = await page.goto(REPORT_URL);
  expect(resp?.status()).toBeLessThan(400);

  await expect(page.locator("[data-testid='factor-report-meta']")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.locator("text=ret_N").first()).toBeVisible();
  await expect(page.locator("text=已完成").first()).toBeVisible();
});

/* ------------------------------------------------------------------ */
/*  2. All 4 tab triggers render                                       */
/* ------------------------------------------------------------------ */

test("all 4 tab triggers render with verdict pills", async ({ page }) => {
  await page.goto(REPORT_URL);

  await expect(
    page.locator("[data-testid='factor-report-tabs']"),
  ).toBeVisible({ timeout: 15000 });

  const tabs = ["profile", "predict", "robust", "cost"];
  for (const key of tabs) {
    await expect(
      page.locator(`[data-testid='factor-report-tab-trigger-${key}']`),
    ).toBeVisible();
  }
});

/* ------------------------------------------------------------------ */
/*  3. Switching tabs reveals each panel                               */
/* ------------------------------------------------------------------ */

test("clicking Predictive Power tab renders the panel and IC chart", async ({
  page,
}) => {
  await page.goto(REPORT_URL);

  await page.locator("[data-testid='factor-report-tab-trigger-predict']").click();

  await expect(
    page.locator("[data-testid='factor-report-tab-predict']"),
  ).toBeVisible();
  // IC 时序 chart panel
  await expect(
    page.locator("[data-testid='factor-report-chart-ic-series']"),
  ).toBeVisible();
  // IC decay chart panel
  await expect(
    page.locator("[data-testid='factor-report-chart-ic-decay']"),
  ).toBeVisible();
});

test("Signal Profile tab renders distribution + quantile charts", async ({
  page,
}) => {
  await page.goto(REPORT_URL);

  // Profile is the default tab — no click needed, but exercise the trigger
  // to ensure click-switch still works.
  await page.locator("[data-testid='factor-report-tab-trigger-profile']").click();

  await expect(
    page.locator("[data-testid='factor-report-tab-profile']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-distribution']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-quantile']"),
  ).toBeVisible();
});

test("Robustness tab renders all three stress-test panels", async ({ page }) => {
  await page.goto(REPORT_URL);

  await page.locator("[data-testid='factor-report-tab-trigger-robust']").click();

  await expect(
    page.locator("[data-testid='factor-report-tab-robust']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-shuffle']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-subsample']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-cross-symbol']"),
  ).toBeVisible();
});

test("Cost & Params tab renders waterfall + summary + params", async ({
  page,
}) => {
  await page.goto(REPORT_URL);

  await page.locator("[data-testid='factor-report-tab-trigger-cost']").click();

  await expect(
    page.locator("[data-testid='factor-report-tab-cost']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-waterfall']"),
  ).toBeVisible();
  await expect(
    page.locator("[data-testid='factor-report-chart-cost-summary']"),
  ).toBeVisible();
  // Params section label
  await expect(page.locator("text=换手与成本")).toBeVisible();
  await expect(page.locator("text=配置回显")).toBeVisible();
});

/* ------------------------------------------------------------------ */
/*  4. In-progress / failed paths                                      */
/* ------------------------------------------------------------------ */

test("running status shows progress hint, not tabs", async ({ page }) => {
  // Override the default route registered in beforeEach — Playwright routes
  // are LIFO, so the specific handler registered here wins.
  await page.route(/\/api\/factor\/report\/[^?]+(\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        run_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        factor_name: "mom_ratio",
        status: "running",
        progress: 42,
        error: null,
      },
    }),
  );

  await page.goto(REPORT_URL);

  await expect(page.locator("text=诊断运行中")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("text=42%").first()).toBeVisible();
  // Tabs must NOT render
  await expect(
    page.locator("[data-testid='factor-report-tabs']"),
  ).toHaveCount(0);
});

test("failed status surfaces the error message", async ({ page }) => {
  await page.route(/\/api\/factor\/report\/[^?]+(\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        run_id: "99999999-8888-7777-6666-555555555555",
        factor_name: "vol_ratio",
        status: "failed",
        progress: 0,
        error: "ValueError: insufficient bars",
      },
    }),
  );

  await page.goto(REPORT_URL);

  await expect(
    page.locator("text=ValueError: insufficient bars"),
  ).toBeVisible({ timeout: 15000 });
});
