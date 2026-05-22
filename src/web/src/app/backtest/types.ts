export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";

// Trades tab chart data interfaces
export interface TradePnlDistributionBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface CumulativeTradePnl {
  trade_num: number;
  cumulative_pnl: number;
}

export interface TradePnlScatterPoint {
  timestamp: string;
  pnl: number;
  side: string;
  instrument: string;
}

export interface MaeMfePoint {
  pnl: number;
  mae: number;
  mfe: number;
  side: string;
}

export interface HoldingTimeBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface StreakEntry {
  streak_num: number;
  type: "win" | "loss";
  count: number;
  total_pnl: number;
}

export interface LongShortSideStats {
  trades: number;
  total_pnl: number;
  avg_pnl: number;
  win_rate: number;
}

export interface LongVsShort {
  long: LongShortSideStats;
  short: LongShortSideStats;
}

export interface ReturnByGroup {
  dow?: number;
  dow_name?: string;
  hour?: number;
  values: number[];
}

export interface TradeLogEntry {
  opened_at: string;
  closed_at?: string;
  instrument: string;
  side: string;
  quantity: number | string;
  avg_open: number | string;
  avg_close: number | string;
  realized_pnl: number | string;
  duration: string;
}

export interface EquityCurvePoint {
  date?: string;
  timestamp?: string;
  equity: number;
  returns_pct?: number;
  drawdown_pct?: number;
}

export interface BacktestStatistics {
  total_pnl: number;
  total_return_pct: number;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  max_drawdown: number | null;
  annual_return: number | null;
  returns_volatility: number | null;
  win_rate: number;
  profit_factor: number | null;
  expectancy: number | null;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  largest_win: number | null;
  largest_loss: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  avg_win_loss_ratio: number | null;
  winning_streak: number;
  losing_streak: number;
  long_pct: number | null;
  short_pct: number | null;
  avg_holding_time: string | null;
  avg_winning_holding_time: string | null;
  avg_losing_holding_time: string | null;
  total_fees: number;
  gross_profit: number;
  gross_loss: number;
  open_positions: number;
  total_orders: number;
  filled_orders: number;
  final_balance: string | null;
  // Extended statistics for returns analytics
  best_day?: number | null;
  worst_day?: number | null;
  best_month?: number | null;
  worst_month?: number | null;
  positive_days_pct?: number | null;
  skewness?: number | null;
  kurtosis?: number | null;
  tail_ratio?: number | null;
  stability?: number | null;
  // Performance tab extended metrics
  omega_ratio?: number | null;
  var_95?: number | null;
  var_99?: number | null;
  cvar_95?: number | null;
  downside_deviation?: number | null;
  ulcer_index?: number | null;
  max_daily_loss?: number | null;
  positive_months_pct?: number | null;
  normal_dist_mean?: number | null;
  normal_dist_std?: number | null;
  // Benchmark-relative metrics
  alpha?: number | null;
  beta?: number | null;
  r_squared?: number | null;
  information_ratio?: number | null;
  // Trades tab metrics
  median_trade_pnl?: number | null;
  std_trade_pnl?: number | null;
  fill_rate?: number | null;
  avg_trades_per_day?: number | null;
  recovery_factor?: number | null;
  sqn?: number | null;
  kelly_criterion?: number | null;
  k_ratio?: number | null;
  expectancy_r?: number | null;
}

export interface PerInstrumentEntry {
  instrument: string;
  total_trades: number;
  winning_trades?: number;
  losing_trades?: number;
  win_rate: number;
  total_pnl: number;
  gross_profit?: number;
  gross_loss?: number;
  profit_factor?: number | null;
  largest_win?: number | null;
  largest_loss?: number | null;
  avg_pnl?: number | null;
  return_pct?: number;
  sharpe_ratio: number | null;
  sortino_ratio?: number | null;
  max_drawdown: number | null;
  recovery_factor?: number | null;
}

export interface MonthlyReturn {
  period: string; // "YYYY-MM"
  return_pct: number;
}

export interface DrawdownPeriod {
  start: string;
  trough_date: string;
  recovery_date: string | null;
  max_drawdown_pct: number;
  duration_days: number;
  recovery_days: number | null;
}

export interface PortfolioAnalytics {
  diversification_ratio?: number;
  diversification_benefit_pct?: number;
}

export interface AnnualReturn {
  year: number;
  return_pct: number;
}

export interface RollingReturnPoint {
  timestamp: string;
  rolling_3m: number | null;
  rolling_6m: number | null;
  rolling_12m: number | null;
}

export interface DistributionBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface QQPlotPoint {
  theoretical: number;
  empirical: number;
}

export interface BenchmarkPoint {
  timestamp: string;
  equity: number;
}

export interface RollingSharpePoint {
  timestamp: string;
  rolling_3m: number | null;
  rolling_6m: number | null;
  rolling_12m: number | null;
}

export interface RollingSortinoPoint {
  timestamp: string;
  rolling_6m: number | null;
  rolling_12m: number | null;
}

export interface RollingVolatilityPoint {
  timestamp: string;
  rolling_6m: number | null;
  rolling_12m: number | null;
}

export interface RollingBetaPoint {
  timestamp: string;
  rolling_6m: number | null;
  rolling_12m: number | null;
}

// --- Robustness types (Layer 1/2/3) ---

export interface McEquityCone {
  percentiles: number[];
  curves: Record<string, number[]>; // p5, p25, p50, p75, p95
  original: number[];
  x_labels: number[];
}

export interface RobustnessMetrics {
  psr: number | null;
  min_backtest_length_days: number | null;
  actual_backtest_length_days: number | null;
  backtest_length_sufficient: boolean | null;
  mc_equity_cone?: McEquityCone | null;
  mc_probability_of_loss?: number | null;
  mc_5th_percentile_return?: number | null;
  mc_median_max_drawdown?: number | null;
  mc_median_final_return?: number | null;
  mc_num_simulations?: number | null;
}

export interface BacktestResult {
  statistics: BacktestStatistics;
  equity_curve: EquityCurvePoint[];
  trade_log: TradeLogEntry[];
  per_instrument?: Record<string, PerInstrumentEntry>;
  monthly_returns?: MonthlyReturn[];
  weekly_returns?: MonthlyReturn[];
  drawdown_periods?: DrawdownPeriod[];
  instrument_correlation?: Record<string, Record<string, number>>;
  portfolio_analytics?: PortfolioAnalytics;
  annual_returns?: AnnualReturn[];
  rolling_returns?: RollingReturnPoint[];
  returns_distribution?: DistributionBin[];
  qq_plot_data?: QQPlotPoint[];
  benchmark_equity_curve?: BenchmarkPoint[];
  daily_returns?: number[];
  // Performance tab rolling analytics
  rolling_sharpe?: RollingSharpePoint[];
  rolling_sortino?: RollingSortinoPoint[];
  rolling_volatility?: RollingVolatilityPoint[];
  rolling_beta?: RollingBetaPoint[];
  benchmark_type?: "single_bh" | "basket_bh" | "zero_line";
  // Trades tab chart data
  trade_pnl_distribution?: TradePnlDistributionBin[];
  cumulative_trade_pnl?: CumulativeTradePnl[];
  trade_pnl_scatter?: TradePnlScatterPoint[];
  mae_mfe?: MaeMfePoint[];
  holding_time_distribution?: HoldingTimeBin[];
  streak_sequence?: StreakEntry[];
  long_vs_short?: LongVsShort;
  return_by_dow?: ReturnByGroup[];
  return_by_hour?: ReturnByGroup[];
  // Robustness metrics (Layer 1 — any backtest)
  robustness?: RobustnessMetrics;
}
