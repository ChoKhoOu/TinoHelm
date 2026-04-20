/* ------------------------------------------------------------------ */
/*  Shared helpers & class constants for Overview subtree              */
/* ------------------------------------------------------------------ */

export const CARD_CLS = "bg-card border border-border rounded-[10px] overflow-hidden";
export const CARD_HEADER_CLS = "flex items-center justify-between px-4 py-2.5 border-b border-border text-[0.75rem] font-semibold text-qds-t1";
export const CARD_BODY_CLS = "p-3.5";
export const STAT_CARD_CLS = "bg-card border border-border rounded-[10px] p-3.5 flex flex-col gap-1.5";
export const STAT_LABEL_CLS = "flex items-center font-mono text-[0.56rem] tracking-widest uppercase text-muted-foreground";
export const STAT_VALUE_CLS = "font-mono text-[1.2rem] font-semibold leading-[1.2] text-foreground";
export const STAT_SUB_CLS = "font-mono text-[0.7rem] mt-0.5 text-muted-foreground";
export const BADGE_BASE_CLS = "font-mono text-[0.62rem] font-semibold px-2 py-0.5 rounded-full inline-flex items-center";
export const BADGE_G_CLS = "bg-qds-success-dim text-qds-success";
export const BADGE_R_CLS = "bg-qds-danger-dim text-destructive";
export const SEC_CLS = "animate-qds-fade-up";

export function fmt(value: unknown, decimals: number, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return num.toFixed(decimals);
}

export function fmtSigned(value: unknown, decimals: number, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return `${num >= 0 ? "+" : ""}${num.toFixed(decimals)}`;
}

export function fmtCurrency(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return `$${num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function stripVenue(s: string): string {
  return s.replace(/\.BINANCE$/i, "");
}

export const pnlColor = (v: number) => (v >= 0 ? "text-qds-success" : "text-destructive");

/* ------------------------------------------------------------------ */
/*  Section label                                                      */
/* ------------------------------------------------------------------ */

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="qds-section-label">
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Stat row (label + value)                                           */
/* ------------------------------------------------------------------ */

export function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`text-xs font-medium ${color ?? "text-muted-foreground"}`}>{value}</span>
    </div>
  );
}
