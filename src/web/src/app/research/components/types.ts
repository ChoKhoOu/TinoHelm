export interface SymbolOption {
  symbol: string;
  label?: string;
}

export interface FactorDef {
  name: string;
  params?: { key: string; label: string; default: number; unit?: string; tip?: string }[];
}

export interface FactorGroup {
  group: string;
  factors: FactorDef[];
}

export interface HistoryJob {
  id: string;
  factor: string;
  symbol: string;
  interval: string;
  status: "running" | "completed" | "failed";
  ir: number | null;
  profile: string | null;
  predict: string | null;
  robust: string | null;
  cost: string | null;
  progress: number | null;
  error_msg: string | null;
  created_at: string;
}

export interface ExploreFactor {
  name: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_pct: number;
  rating: string; // "strong" | "usable" | "weak"
}

export interface CatalogEntry {
  symbol: string;
  data_type: string;
  interval: string;
  record_count: number | null;
  start_date: string;
  end_date: string;
}

export interface ExploreResult {
  factors: ExploreFactor[];
  ic_timeseries: { date: string; [factor: string]: number | string }[];
  ic_decay: { lag: number; ic: number }[];
  quantile_returns: { date: string; Q1: number; Q2: number; Q3: number; Q4: number; Q5: number }[];
  distribution: { bin: string; count: number }[];
  turnover: {
    daily_avg: string;
    annual: string;
    fee_drag: string;
    fee_rate: string;
  };
}

export const MAX_FACTORS = 5;
export const DOT_COLORS = ["var(--suc)", "var(--info)", "var(--acc)", "var(--warn)", "#A882DC"];

export const FALLBACK_SYMBOLS: SymbolOption[] = [
  { symbol: "BTCUSDT-PERP" },
  { symbol: "ETHUSDT-PERP" },
  { symbol: "SOLUSDT-PERP" },
];

export const FALLBACK_FACTOR_GROUPS: FactorGroup[] = [
  {
    group: "动量",
    factors: [
      { name: "ret_N", params: [{ key: "lookback", label: "回看周期", default: 20, tip: "计算过去 N 根 bar 的收益率作为因子值" }] },
      { name: "mom_ratio", params: [{ key: "fast", label: "快窗口", default: 5 }, { key: "slow", label: "慢窗口", default: 20 }] },
      { name: "roc", params: [{ key: "period", label: "周期", default: 12 }] },
      { name: "rsi_signal", params: [{ key: "period", label: "RSI 周期", default: 14 }] },
    ],
  },
  {
    group: "波动",
    factors: [
      { name: "vol_ratio", params: [{ key: "fast", label: "快窗口", default: 5, tip: "短期波动率的滚动窗口长度" }, { key: "slow", label: "慢窗口", default: 20, tip: "长期波动率的滚动窗口长度，快/慢比值衡量波动率变化" }] },
      { name: "realized_vol", params: [{ key: "window", label: "窗口", default: 20 }] },
      { name: "atr_norm", params: [{ key: "period", label: "ATR 周期", default: 14 }] },
      { name: "parkinson_vol", params: [{ key: "window", label: "窗口", default: 20 }] },
    ],
  },
  {
    group: "量价",
    factors: [
      { name: "vwap_dev", params: [{ key: "period", label: "周期", default: 20 }] },
      { name: "volume_surge", params: [{ key: "lookback", label: "回看周期", default: 20, tip: "当前成交量 / 过去 N 根 bar 平均成交量" }] },
      { name: "obv_slope", params: [{ key: "period", label: "OBV 斜率周期", default: 10 }] },
    ],
  },
  {
    group: "微观结构",
    factors: [
      { name: "trade_imbalance", params: [{ key: "window", label: "窗口", default: 50 }] },
      { name: "kyle_lambda", params: [{ key: "window", label: "窗口", default: 100 }] },
      { name: "amihud_illiq", params: [{ key: "window", label: "窗口", default: 20 }] },
    ],
  },
  {
    group: "自定义",
    factors: [{ name: "my_momentum", params: [{ key: "lookback", label: "回看周期", default: 10 }] }],
  },
];

export function irColor(ir: number | null): string {
  if (ir == null) return "";
  if (ir >= 1) return "text-qds-success";
  if (ir >= 0.5) return "";
  return "text-destructive";
}

export function timeAgo(dateStr: string): string {
  const d = new Date(dateStr);
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return "昨天";
  return `${days} 天前`;
}
