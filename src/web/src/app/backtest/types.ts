export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";

export interface TradeLogEntry {
  opened_at: string;
  instrument: string;
  side: string;
  quantity: number;
  avg_open: number;
  avg_close: number;
  realized_pnl: number;
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
  max_drawdown: number | null;
  win_rate: number;
  profit_factor: number | null;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  gross_profit: number;
  gross_loss: number;
  final_balance: string | null;
  annual_return: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  expectancy: number | null;
  total_fees: number;
  avg_holding_time: string | null;
}

export interface PerInstrumentEntry {
  instrument: string;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  sharpe_ratio: number | null;
  max_drawdown: number | null;
}

export interface BacktestResult {
  statistics: BacktestStatistics;
  equity_curve: EquityCurvePoint[];
  trade_log: TradeLogEntry[];
  per_instrument?: PerInstrumentEntry[];
}
