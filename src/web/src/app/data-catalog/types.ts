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
  source_type?: string;
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
  source_type?: string;
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

/** Filter groups for data catalog (design: qds-data-catalog.html) */
export interface FilterGroup {
  label: string;
  dot: string; // CSS color value
  types: string[] | null; // null = all
}
export const FILTER_GROUPS: Record<string, FilterGroup> = {
  all:         { label: "全部",    dot: "var(--t1)",   types: null },
  klines:      { label: "Klines",  dot: "var(--info)",  types: ["bar"] },
  trades:      { label: "Trades",  dot: "var(--acc)",   types: ["trade_tick"] },
  fundingRate: { label: "Funding", dot: "var(--suc)",   types: ["funding_rate"] },
};

/** Type badge CSS class mapping (vision data_type → dc-type-* class) */
export const TYPE_BADGE_CLS: Record<string, string> = {
  klines: "dc-type-kl",
  indexPriceKlines: "dc-type-ipk",
  markPriceKlines: "dc-type-mpk",
  premiumIndexKlines: "dc-type-pik",
  aggTrades: "dc-type-at",
  trades: "dc-type-tr",
  fundingRate: "dc-type-fr",
  // DB categories (from catalog)
  bar: "dc-type-kl",
  trade_tick: "dc-type-at",
  quote_tick: "dc-type-ipk",
  funding_rate: "dc-type-fr",
};

/** Source type label (what to show in type badge for DB categories) */
export const SOURCE_TYPE_LABELS: Record<string, string> = {
  klines: "klines",
  indexPriceKlines: "indexPriceKlines",
  markPriceKlines: "markPriceKlines",
  premiumIndexKlines: "premiumIndexKlines",
  aggTrades: "aggTrades",
  trades: "trades",
  fundingRate: "fundingRate",
  bar: "bar",
  trade_tick: "trade_tick",
  quote_tick: "quote_tick",
  funding_rate: "funding_rate",
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
