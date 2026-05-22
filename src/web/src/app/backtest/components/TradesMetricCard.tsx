"use client";

import { useCountUp } from "@/hooks/useCountUp";
import { HelpTip, STAT_LABEL_CLS } from "./TradesHelpers";

/* ------------------------------------------------------------------ */
/*  KPI Metric Card                                                    */
/* ------------------------------------------------------------------ */

export interface MetricCardProps {
  label: string;
  tooltip?: string;
  value: number | null | undefined;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  showSign?: boolean;
  positive?: boolean | null;
  index: number;
}

export function MetricCard({
  label,
  tooltip,
  value,
  decimals = 2,
  prefix = "",
  suffix = "",
  showSign = false,
  positive,
  index,
}: MetricCardProps) {
  const numeric = value ?? 0;
  const animated = useCountUp(numeric, 800 + index * 80, value != null);

  const colorClass =
    positive == null
      ? "text-foreground"
      : positive
        ? "text-qds-success"
        : "text-destructive";

  const accentColor =
    positive == null
      ? "var(--info)"
      : positive
        ? "var(--suc)"
        : "var(--dan)";

  const formatted =
    value == null
      ? "N/A"
      : decimals === 0
        ? `${prefix}${Math.round(animated).toLocaleString()}${suffix}`
        : showSign
          ? `${prefix}${animated >= 0 ? "+" : ""}${animated.toFixed(decimals)}${suffix}`
          : `${prefix}${animated.toFixed(decimals)}${suffix}`;

  return (
    <div
      className="group relative flex flex-col gap-2.5 rounded-xl border bg-card p-4 hover:bg-secondary transition-all duration-300 overflow-hidden"
    >
      <div
        className="absolute bottom-0 left-0 right-0 h-[2px] opacity-30 group-hover:opacity-70 transition-opacity duration-500"
        style={{ background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)` }}
      />
      <span className={STAT_LABEL_CLS}>
        {label}
        {tooltip && <HelpTip text={tooltip} />}
      </span>
      <span className={`text-2xl font-bold font-mono tracking-tight leading-none ${colorClass}`}>
        {formatted}
      </span>
    </div>
  );
}
