import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3001",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // webServer: self-managed. Start with `npx serve out -l 3001` before running tests.
  // IMPORTANT: do NOT use the `-s` flag. `out/backtest/` is a Next.js RSC data
  // directory, which collides with `-s` SPA fallback and causes `/backtest` to
  // serve `index.html` (Dashboard HTML) — the browser then tries to hydrate
  // with the BacktestPage component, producing a React #418 text-content
  // mismatch and a blank page. Plain `serve` replicates nginx `try_files $uri
  // $uri.html` behavior, which correctly maps `/backtest` → `backtest.html`.
});
