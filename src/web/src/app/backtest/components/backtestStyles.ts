/**
 * Shared class-name constants for the backtest page sub-modules.
 *
 * These replace the legacy bt-form-* / bt-po-* / bt-* classes. They are
 * exported from a single location so that the list view, detail view and
 * create form stay in visual lock-step.
 */

// Form section / row / group classes (used by BacktestCreateView).
export const FORM_SECTION_CLS =
  "mb-7 opacity-0 translate-y-4 transition-[opacity,transform] duration-[450ms] ease-qds data-[visible=true]:opacity-100 data-[visible=true]:translate-y-0";
export const FORM_ROW_CLS = "grid grid-cols-2 gap-4 mb-4";
export const FORM_ROW_3_CLS = "grid grid-cols-3 gap-4 mb-4";
export const FORM_GROUP_CLS = "flex flex-col gap-1.5";
export const FORM_LABEL_CLS =
  "flex items-center gap-1 font-mono text-[0.65rem] text-muted-foreground";
export const FORM_HINT_CLS = "text-[0.65rem] text-qds-t3 mt-0.5";

// Action / view button base classes (used by list rows + detail top bar).
export const ACT_BTN_CLS =
  "inline-flex items-center gap-1 font-mono text-[0.68rem] px-2.5 py-1 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-qds-border-hover hover:text-foreground hover:bg-secondary";
export const VIEW_BTN_CLS =
  "inline-flex items-center gap-1 font-mono text-[0.7rem] px-2.5 py-1 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-primary hover:text-primary hover:bg-primary/10";

// Status accent stripe + pill background/text maps (used by both row types).
export const ACCENT_BG_MAP: Record<string, string> = {
  run: "bg-qds-info",
  done: "bg-qds-success",
  fail: "bg-destructive",
  queue: "bg-qds-t3",
};

export const STATUS_PILL_MAP: Record<string, string> = {
  run: "bg-primary/15 text-primary",
  done: "bg-qds-success-dim text-qds-success",
  fail: "bg-qds-danger-dim text-destructive",
  queue: "bg-secondary text-muted-foreground",
};
