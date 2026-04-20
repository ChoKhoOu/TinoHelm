export interface VerdictStatus {
  status: "pass" | "warn" | "fail";
  reason?: string;
}

export interface SignalProfileData {
  verdict: VerdictStatus;
  mean: number;
  std: number;
  skewness: number;
  lag1_acf: number;
  rv_corr: number;
  zero_pct: number;
  distribution: { bin: string; count: number }[];
  acf: { lag: number; value: number; ci_upper: number; ci_lower: number }[];
}

export interface PredictivePowerData {
  verdict: VerdictStatus;
  ic_mean_h5: number;
  ic_tstat: number;
  icir: number;
  ic_positive_pct: number;
  ic_mean_h15: number;
  rolling_ic: { date: string; ic: number }[];
  quantile_returns: { quantile: string; return_pct: number }[];
  cumulative_returns: {
    dates: string[];
    series: Record<string, number[]>;
  };
}

export interface RobustnessData {
  verdict: VerdictStatus;
  shuffle_test: {
    p_value: number;
    distribution: { bin: number; count: number }[];
    real_ic: number;
  };
  sub_period_ic: { period: string; ic: number }[];
  cross_symbol_ic: { symbol: string; ic: number }[];
}

export interface CostParamsData {
  verdict: VerdictStatus;
  waterfall: { label: string; value: number; type: "positive" | "negative" | "net" }[];
  heatmap: {
    x_labels: string[];
    y_labels: string[];
    values: number[][];
  };
  param_sweep: { param_value: number; ic: number }[];
}

export interface ReportData {
  id: string;
  factor_name: string;
  symbol: string;
  forward_period: number;
  created_at: string;
  params?: Record<string, unknown>;
  signal_profile: SignalProfileData;
  predictive_power: PredictivePowerData;
  robustness: RobustnessData;
  cost_params: CostParamsData;
}
