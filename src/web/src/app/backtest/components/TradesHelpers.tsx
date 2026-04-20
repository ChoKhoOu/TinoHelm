"use client";

import { HelpCircle } from "lucide-react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { CHART_COLORS } from "@/lib/chartTheme";

/* ------------------------------------------------------------------ */
/*  Shared Constants                                                   */
/* ------------------------------------------------------------------ */

export const CARD_CLS =
  "bg-card border border-border rounded-[10px] overflow-hidden";
export const CARD_BODY_CLS = "p-3.5";
export const STAT_LABEL_CLS =
  "inline-flex items-center font-mono text-[0.55rem] tracking-widest uppercase text-primary";

export const ACCENT_GREEN = CHART_COLORS.success;
export const ACCENT_RED = CHART_COLORS.danger;
export const ACCENT_LONG = CHART_COLORS.info;
export const ACCENT_SHORT = CHART_COLORS.accent;

/* ------------------------------------------------------------------ */
/*  HelpTip                                                            */
/* ------------------------------------------------------------------ */

export function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="inline-flex items-center justify-center ml-1 cursor-help">
          <HelpCircle className="w-3 h-3 text-qds-t3 hover:text-muted-foreground transition-colors" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[220px] text-[11px] leading-relaxed">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------ */
/*  EmptyPlaceholder                                                   */
/* ------------------------------------------------------------------ */

export function EmptyPlaceholder({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center h-full min-h-[120px]">
      <span className="text-xs text-qds-t3">{label ?? "暂无数据"}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Chart Section Title                                                */
/* ------------------------------------------------------------------ */

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <span className={STAT_LABEL_CLS}>{children}</span>;
}

/* ------------------------------------------------------------------ */
/*  Box-plot Statistics                                                */
/* ------------------------------------------------------------------ */

/** Compute Q1, median, Q3 from an array of numbers */
export function boxStats(
  values: number[],
): { min: number; q1: number; median: number; q3: number; max: number } | null {
  if (!values || values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const q = (p: number) => {
    const idx = p * (sorted.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  };
  return {
    min: sorted[0],
    q1: q(0.25),
    median: q(0.5),
    q3: q(0.75),
    max: sorted[sorted.length - 1],
  };
}
