/**
 * Response types for the factor diagnostic report page.
 *
 * Schema source of truth:
 *   - Route: ``GET /api/factor/report/{run_id}`` in
 *     ``src/tinohelm/api/routes/factor.py``
 *   - Full result: ``EvalResult`` dataclass in
 *     ``src/tinohelm/factor/types.py``
 *   - Robustness: ``src/tinohelm/factor/evaluation/robustness.py``
 *   - Cost: ``src/tinohelm/factor/evaluation/cost.py``
 *
 * The API always includes ``{run_id, factor_name, status}``.  When
 * ``status === "completed"`` the ``result`` field contains a serialised
 * ``EvalResult``; otherwise the progress/error payload is returned.
 */

/* ------------------------------------------------------------------ */
/*  EvalResult — the full diagnostic payload                           */
/* ------------------------------------------------------------------ */

export interface ShuffleResult {
  real_ic: number;
  shuffle_distribution: {
    bin_start: number;
    bin_end: number;
    count: number;
  }[];
  p_value: number;
  significant: boolean;
  /** Present only on shuffle-test errors. */
  error?: string;
}

export interface SubsampleICEntry {
  period: string;
  ic: number;
}

export interface CrossSymbolICEntry {
  symbol: string;
  ic: number;
  n_obs: number;
  error?: string;
}

export interface RobustnessPayload {
  shuffle?: ShuffleResult;
  subsample?: SubsampleICEntry[];
  cross_symbol?: CrossSymbolICEntry[];
}

/**
 * ``edge_waterfall`` emits four keys, all in basis points.  When the
 * backend runs ``evaluate()`` (not ``evaluate_full()``) this dict is
 * empty — the cost tab falls back to a "no cost waterfall" hint.
 */
export interface CostPayload {
  gross_edge_bps?: number;
  fee_cost_bps?: number;
  slippage_bps?: number;
  net_edge_bps?: number;
}

export interface EvalResultPayload {
  /* IC stats */
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_tstat: number;
  ic_positive_pct: number;
  ic_max_abs: number;

  /* Decay / half-life */
  half_life: number | null;

  /* Quantile analysis */
  quantile_pnl: Record<string, number>;
  is_monotonic: boolean;
  quantile_cum_returns: Record<string, { date: string; value: number }[]>;

  /* Turnover / cost */
  turnover: number;
  turnover_annualized: number;
  fee_drag_monthly: number;

  /* Rating */
  rating: number;

  /* Time series */
  ic_series: { date: string; ic: number }[];
  ic_decay: { lag: number; ic: number }[];

  /* Distribution */
  distribution_stats: Record<string, number>;
  distribution_histogram: {
    bin_start: number;
    bin_end: number;
    count: number;
  }[];

  /* Full-only extensions */
  robustness?: RobustnessPayload;
  cost?: CostPayload;
}

/* ------------------------------------------------------------------ */
/*  API envelope                                                        */
/* ------------------------------------------------------------------ */

/**
 * Shape returned by ``GET /api/factor/report/{run_id}``.
 *
 * - ``status === "completed"``  → ``result`` populated, render all 4 tabs.
 * - ``status === "queued" | "running"`` → show progress hint.
 * - ``status === "failed"``     → show ``error`` in ``<InlineError />``.
 */
export interface FactorReportResponse {
  run_id: string;
  factor_name: string;
  status: "queued" | "running" | "completed" | "failed";
  progress?: number;
  error?: string | null;
  result?: EvalResultPayload;
  /** FactorRun.config echoed back — contains the EvalConfig snapshot + params. */
  config?: Record<string, unknown>;
  created_at?: string;
  finished_at?: string;
}

/* ------------------------------------------------------------------ */
/*  UI helpers                                                          */
/* ------------------------------------------------------------------ */

export type TabKey = "profile" | "predict" | "robust" | "cost";

export interface TabDef {
  key: TabKey;
  label: string;
}

export const TABS: readonly TabDef[] = [
  { key: "profile", label: "Signal Profile" },
  { key: "predict", label: "Predictive Power" },
  { key: "robust", label: "Robustness" },
  { key: "cost", label: "Cost & Params" },
] as const;

/**
 * Stable semantic palette for line series (mirrors ``factor/components/types.ts``).
 * Keeping the palette local avoids cross-tab imports while preserving the
 * Charts Spec contract (``--acc`` for primary, semantic ``--suc``/``--dan``
 * for profit/loss).
 */
export const SERIES_COLORS = {
  ic: "var(--acc)",
  positive: "var(--suc)",
  negative: "var(--dan)",
  info: "var(--info)",
  warning: "var(--warn)",
  neutral: "var(--t3)",
} as const;

/**
 * Derive a tab verdict colour from the EvalResult.  Rules mirror the legacy
 * ``research`` tab-bar badges but tie to concrete EvalResult fields:
 *
 * - **profile** — verdict based on monotonic flag + abs(mean).
 * - **predict** — based on ``abs(ic_tstat)`` threshold (2.0 = 95% confidence).
 * - **robust** — based on ``robustness.shuffle.significant``.
 * - **cost** — based on ``net_edge_bps`` sign.
 */
export type TabVerdict = "pass" | "warn" | "fail" | "none";

export function tabVerdict(
  key: TabKey,
  result: EvalResultPayload | undefined,
): TabVerdict {
  if (!result) return "none";
  switch (key) {
    case "profile":
      return result.is_monotonic ? "pass" : "warn";
    case "predict":
      if (Math.abs(result.ic_tstat) >= 2.0) return "pass";
      if (Math.abs(result.ic_tstat) >= 1.0) return "warn";
      return "fail";
    case "robust": {
      const shuffle = result.robustness?.shuffle;
      if (!shuffle) return "none";
      return shuffle.significant ? "pass" : "fail";
    }
    case "cost": {
      const net = result.cost?.net_edge_bps;
      if (net == null) return "none";
      if (net > 0) return "pass";
      if (net > -5) return "warn";
      return "fail";
    }
  }
}

/**
 * Shared trend helper for KPI StatCards — mirrors the explore page's
 * ``IR >= 0.5 up``, ``IR < 0 down`` convention.
 */
export function trendFromValue(
  value: number,
  positiveThreshold: number = 0,
): "up" | "down" | undefined {
  if (!Number.isFinite(value)) return undefined;
  if (value > positiveThreshold) return "up";
  if (value < 0) return "down";
  return undefined;
}

/**
 * Format a decimal proportion as a percentage string — used for IC / PnL /
 * turnover etc.  ``0.0421 → "4.21%"``.
 */
export function formatPct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/**
 * Format a basis-point value — the cost waterfall emits raw bps numbers.
 * ``4.21 → "+4.21 bps"``.
 */
export function formatBps(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)} bps`;
}
