export interface CatalogEntry {
  id: number;
  symbol: string;
  data_type: string;
  interval: string;
  record_count?: number | null;
  start_date: string;
  end_date: string;
  file_path?: string;
  size_bytes: number;
}

export interface DataTypeInfo {
  data_type: string;
  db_category: string;
  has_daily: boolean;
  has_monthly: boolean;
  implemented: boolean;
}

export interface CoverageItem {
  data_type: string;
  interval: string;
  start_date: string;
  end_date: string;
  size_bytes: number;
}

export type SortKey = "symbol" | "data_type" | "interval" | "record_count" | "start_date" | "size_bytes";
export type SortDir = "asc" | "desc";

export interface DataProgressPayload {
  type: string;
  symbol: string;
  interval: string;
  progress: number;
  message: string;
  task_id?: string;
}

/** Vision types that need an interval selector in the fetch dialog. */
export const BAR_VISION_TYPES = new Set([
  "klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines",
]);

/** Human-readable labels for DB category tabs. */
export const CATEGORY_LABELS: Record<string, string> = {
  bar: "K线",
  trade_tick: "成交",
  quote_tick: "盘口",
  funding_rate: "资金费率",
};

export const INTERVAL_OPTIONS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"];

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === undefined || n === null) return "—";
  return n.toLocaleString();
}
