/**
 * /signal shared types — match :mod:`tinohelm.api.routes.signal`.
 *
 * Backend reference:
 *   - ``src/tinohelm/api/routes/signal.py``           — 7 endpoints
 *   - ``src/tinohelm/signal/types.py:SignalSpec``     — registry catalogue
 *   - ``src/tinohelm/signal/evaluator.py:SignalEvalResult`` — result fields
 *
 * All field names below are the EXACT snake_case keys the backend returns —
 * agent-generated code historically mismatched these so we curl-verified each
 * route prior to authoring this file.
 */

/* ------------------------------------------------------------------ */
/*  GET /api/signal/list                                                */
/* ------------------------------------------------------------------ */

export interface SignalListItem {
  name: string;
  version: string;
  /** One of 5 kernel slugs. */
  method: string;
  /** Weight scaling: equal | ic_weighted | ir_weighted | risk_parity. */
  weighting: string;
  /** Reference into the factor registry: "factor_name" or "factor_name@version". */
  factor_ref: string;
  universe_ref: string;
  rebalance_freq: string;
  gross_exposure: number;
  net_exposure: number;
  max_position: number;
  extra_warmup_bars: number;
  description: string;
  deprecated: boolean;
}

/* ------------------------------------------------------------------ */
/*  GET /api/signal/runs                                                */
/* ------------------------------------------------------------------ */

export interface SignalRunInfo {
  run_id: string;
  signal_name: string;
  factor_ref: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | string;
  progress: number | null;
  /** Worker-emitted stage label: "loading" | "kernel" | "evaluator" | "persisting" | null. */
  progress_stage: string | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface SignalRunsPage {
  runs: SignalRunInfo[];
  page: number;
  page_size: number;
}

/* ------------------------------------------------------------------ */
/*  GET /api/signal/report/{run_id}                                     */
/* ------------------------------------------------------------------ */

/**
 * Full SignalEvalResult (only present when status === "completed").
 *
 * Mirrors the dataclass in ``tinohelm.signal.evaluator.SignalEvalResult``
 * field-for-field.  Backend stores it as JSON in ``signal_runs.result``
 * and surfaces verbatim through ``GET /api/signal/report``.
 */
export interface SignalEvalResult {
  sharpe: number;
  /** Maximum drawdown as a positive fraction (e.g. 0.15 = 15%). */
  mdd: number;
  /** Mean single-sided turnover × periods_per_year. */
  turnover_annualized: number;
  /** Concentration proxy in [0, 1]; 0 = single asset, ~0.75 = 4-asset equal weight. */
  capacity_score: number;
  /** 1st-percentile period return (usually negative). */
  tail_loss_p99: number;
  net_pnl_curve: number[];
  gross_pnl_curve: number[];
  total_return: number;
  n_periods: number;
  cost_drag: number;
}

export interface SignalReportResponse {
  run_id: string;
  signal_name: string;
  factor_ref: string;
  status: SignalRunInfo["status"];
  progress: number | null;
  progress_stage: string | null;
  error: string | null;
  /** Only populated when status === "completed". */
  result?: SignalEvalResult;
}

/* ------------------------------------------------------------------ */
/*  GET /api/signal/export/{run_id}                                     */
/* ------------------------------------------------------------------ */

export interface SignalExportConfig {
  signal_name: string;
  instrument_ids: string[];
  bar_type_template: string;
  warmup_bars: number;
  rebalance_freq_ns: number;
  signal_spec_json: Record<string, unknown> & {
    cost_model?: {
      name: string;
      fee_bps_per_side: number;
      slippage_bps_per_side: number;
      rebate_bps_per_side: number;
    };
    gross_exposure?: number;
    net_exposure?: number;
    max_position?: number;
    turnover_budget?: number | null;
    method?: string;
    weighting?: string;
    rebalance_freq?: string;
    universe_ref?: string;
  };
  factor_lookback: number;
}

export interface SignalExportResponse {
  strategy_class: string;
  config: SignalExportConfig;
  metadata: {
    exported_from_run_id: string;
    factor_ref: string;
    factor_lookback: number;
    extra_warmup_bars: number;
    warmup_bars_derived: number;
    code_hash: string | null;
    started_at: string | null;
    finished_at: string | null;
  };
}

/* ------------------------------------------------------------------ */
/*  Local UI helper types                                               */
/* ------------------------------------------------------------------ */

export type SignalRunStatusFilter =
  | "all"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export const STATUS_FILTER_OPTIONS: { id: SignalRunStatusFilter; label: string }[] = [
  { id: "all",        label: "全部" },
  { id: "running",    label: "运行中" },
  { id: "queued",     label: "排队中" },
  { id: "completed",  label: "已完成" },
  { id: "failed",     label: "失败" },
  { id: "cancelled",  label: "已取消" },
];
