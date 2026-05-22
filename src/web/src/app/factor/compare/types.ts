/**
 * /factor/compare shared types — match POST /api/factor/compare/multi.
 *
 * Backend reference:
 *   - ``src/tinohelm/api/routes/factor.py`` (CompareMultiRequest, compare_multi_endpoint)
 *   - ``src/tinohelm/factor/evaluation/compare.py:compare_multi``
 *
 * Also reads runs from GET /api/factor/runs (paginated FactorRun list).
 */

/* ------------------------------------------------------------------ */
/*  GET /api/factor/runs                                               */
/* ------------------------------------------------------------------ */

export interface FactorRunSummary {
  run_id: string;
  factor_name: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | string;
  progress: number | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/* ------------------------------------------------------------------ */
/*  POST /api/factor/compare/multi  →  CompareMultiResult              */
/* ------------------------------------------------------------------ */

export interface RankingHeatmap {
  factors: string[];
  metrics: string[];
  /** F × M raw metric values (``null`` when missing / NaN). */
  values: (number | null)[][];
  /** F × M 1-based ranks per metric column.  Higher value → rank 1. */
  rankings: number[][];
}

export interface RollingICSmallMultiples {
  factors: string[];
  rolling_ic_window: number;
  /** Per-factor rolling-mean IC arrays (``null`` for NaN). */
  series: Record<string, (number | null)[]>;
}

export interface Dendrogram {
  /**
   * scipy linkage matrix rows: ``[child_a, child_b, distance, n_obs]``.
   * Empty when fewer than 2 factors carry usable IC series.
   */
  linkage_matrix: number[][];
  /** Leaf labels in input order.  ``len(labels)`` always = #factors. */
  labels: string[];
}

export interface ICTimeSeriesCorr {
  factors: string[];
  /** F × F correlation matrix (``null`` cells when undefined). */
  matrix: (number | null)[][];
}

export interface AgentSummaryTopPerformer {
  name: string;
  ir: number;
  why: string;
}

export interface AgentSummaryWarning {
  factor: string;
  type: string;
  message: string;
}

export interface AgentSummary {
  top_performers: AgentSummaryTopPerformer[];
  warnings: AgentSummaryWarning[];
  regime_sensitivity: Record<string, { name: string; segment: string; ir: number }[]>;
}

export interface CompareMultiResult {
  ranking_heatmap: RankingHeatmap;
  rolling_ic_small_multiples: RollingICSmallMultiples;
  dendrogram: Dendrogram;
  ic_time_series_corr: ICTimeSeriesCorr;
  agent_summary: AgentSummary;
}

/* ------------------------------------------------------------------ */
/*  Request shape                                                       */
/* ------------------------------------------------------------------ */

export interface CompareMultiRequest {
  /** At least 2 FactorRun ids; 1-element input is rejected with 422. */
  eval_run_ids: string[];
  n_bootstrap?: number;
}
