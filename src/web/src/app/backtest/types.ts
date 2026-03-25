export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";

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
}
