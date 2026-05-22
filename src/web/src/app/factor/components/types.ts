/**
 * Factor framework shared types and helpers.
 *
 * Schema source of truth:
 *   - GET  /api/factor/list       → ``FactorSpec[]``
 *   - GET  /api/factor/universes  → ``string[]``
 *   - GET  /api/factor/symbols    → ``string[]``
 *   - POST /api/factor/explore    → ``ExploreResult``
 *
 * Backend code:
 *   - ``src/tinohelm/api/routes/factor.py`` (route handlers + response shape)
 *   - ``src/tinohelm/factor/types.py``      (``FactorSpec`` / ``EvalResult``)
 */

/* ------------------------------------------------------------------ */
/*  Factor metadata (GET /api/factor/list)                             */
/* ------------------------------------------------------------------ */

/**
 * Parameter schema is a flat ``{key: default}`` dict.  The factor framework
 * intentionally uses loose typing — numeric params are the norm, but booleans
 * and strings are allowed by design.
 */
export type FactorParams = Record<string, number | string | boolean>;

export interface FactorSpec {
  name: string;
  category: string;
  description: string;
  lookback: number;
  version: string;
  input_fields: string[];
  params_schema: FactorParams;
}

/* ------------------------------------------------------------------ */
/*  Explore request / response (POST /api/factor/explore)              */
/* ------------------------------------------------------------------ */

export interface ExploreRequest {
  factor_name: string;
  config: {
    universe: string[];
    start: string;
    end: string;
    forward_period?: number;
    quantiles?: number;
    cost_bps?: number;
    ic_freq?: string;
    log_ret?: boolean;
  };
  params?: FactorParams;
}

/**
 * Shape returned by the explore endpoint — see factor.py:explore_factor for the
 * exact field list. ``quantile_pnl`` keys are ``"Q0"``..``"Q{n-1}"`` labels.
 */
export interface ExploreResult {
  factor_name: string;

  /* Summary / rating */
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_tstat: number;
  ic_positive_pct: number;
  rating: number;
  is_monotonic: boolean;
  half_life: number | null;

  /* Turnover */
  turnover: number;
  turnover_annualized: number;
  fee_drag_monthly: number;

  /* Chart data */
  ic_series: { date: string; ic: number }[];
  ic_decay: { lag: number; ic: number }[];
  quantile_pnl: Record<string, number>;
  quantile_cum_returns: Record<string, { date: string; value: number }[]>;
  distribution_histogram: { bin_start: number; bin_end: number; count: number }[];
  distribution_stats: Record<string, number>;
}

/* ------------------------------------------------------------------ */
/*  UI helpers                                                         */
/* ------------------------------------------------------------------ */

export const MAX_SELECTED_FACTORS = 5;

/** Category → Tailwind dim background color for the pill tab. */
export const CATEGORY_ORDER = [
  "全部",
  "动量",
  "波动",
  "量价",
  "成交量",
  "微观结构",
  "资金费率",
  "链上数据",
  "自定义",
];

/**
 * Legacy Jesse-style IR rating bands:
 *   - IR ≥ 1.0 → strong
 *   - IR ≥ 0.5 → usable
 *   - else     → weak
 */
export function ratingLabel(rating: number): "strong" | "usable" | "weak" {
  if (rating >= 3) return "strong";
  if (rating >= 2) return "usable";
  return "weak";
}

export function irBand(ir: number | null | undefined): string {
  if (ir == null) return "text-muted-foreground";
  if (ir >= 1) return "text-qds-success";
  if (ir >= 0.5) return "text-foreground";
  return "text-destructive";
}

/** Stable semantic palette for line series (per Charts Spec). */
export const SERIES_COLORS = {
  ic: "var(--acc)",
  q1: "var(--suc)",
  q2: "var(--info)",
  q3: "var(--warn)",
  q4: "rgba(254,129,129,0.6)",
  q5: "var(--dan)",
  dist: "var(--info)",
  turnover: "var(--warn)",
} as const;
