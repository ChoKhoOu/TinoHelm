"use client";

import type React from "react";
import { HelpCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { useCountUp } from "@/hooks/useCountUp";

/* ------------------------------------------------------------------ */
/*  Pure helpers                                                       */
/* ------------------------------------------------------------------ */

export const clamp = (v: number, min: number, max: number) =>
  Math.max(min, Math.min(max, v));

export function downsample<T>(arr: T[], maxPoints: number): T[] {
  if (arr.length <= maxPoints) return arr;
  const step = arr.length / maxPoints;
  return Array.from({ length: maxPoints }, (_, i) => arr[Math.round(i * step)]);
}

export function fmtDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return ts.slice(5, 10);
  }
}

export function fmtDateFull(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return ts.slice(0, 10);
  }
}

export function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "N/A";
  return v.toFixed(decimals);
}

/* ------------------------------------------------------------------ */
/*  Shared card class tokens                                            */
/* ------------------------------------------------------------------ */

export const CARD_CLS =
  "bg-card border border-border rounded-[10px] overflow-hidden";
export const CARD_HEADER_CLS =
  "flex items-center justify-between px-4 py-2.5 border-b border-border text-[0.75rem] font-semibold text-qds-t1";
export const CARD_BODY_CLS = "p-3.5";

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
        <TooltipContent
          side="top"
          className="max-w-[240px] text-[11px] leading-relaxed"
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------ */
/*  ChartCard — bg-card + border + Tailwind                            */
/* ------------------------------------------------------------------ */

export function ChartCard({
  title,
  children,
  headerRight,
}: {
  title?: string;
  children: React.ReactNode;
  headerRight?: React.ReactNode;
}) {
  return (
    <div className={CARD_CLS}>
      {title != null && (
        <div className={CARD_HEADER_CLS}>
          <span>{title}</span>
          {headerRight}
        </div>
      )}
      <div className={CARD_BODY_CLS}>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SectionHeader                                                      */
/* ------------------------------------------------------------------ */

export function SectionHeader({
  title,
}: {
  title: string;
  index?: number;
}) {
  return <span className="qds-section-label">{title}</span>;
}

/* ------------------------------------------------------------------ */
/*  MetricCard                                                         */
/* ------------------------------------------------------------------ */

interface MetricCardProps {
  label: string;
  sublabel?: string;
  tooltip: string;
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
  sublabel,
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
    positive === null || positive === undefined
      ? "text-foreground"
      : positive
        ? "text-qds-success"
        : "text-destructive";

  const accentColor =
    positive === null || positive === undefined
      ? "rgba(76, 158, 235, 0.5)"
      : positive
        ? "rgba(38, 217, 127, 0.5)"
        : "rgba(239, 83, 80, 0.5)";

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
        style={{
          background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`,
        }}
      />
      <span className="inline-flex items-center font-mono text-[0.55rem] tracking-widest uppercase text-primary">
        {label}
        <HelpTip text={tooltip} />
      </span>
      <span
        className={`text-2xl font-bold font-mono tracking-tight leading-none ${colorClass}`}
      >
        {formatted}
      </span>
      {sublabel && (
        <span className="text-[9px] text-qds-t3 leading-tight">
          {sublabel}
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ChartPlaceholder                                                   */
/* ------------------------------------------------------------------ */

export function ChartPlaceholder({ message = "暂无数据" }: { message?: string }) {
  return (
    <div className="flex items-center justify-center h-full min-h-[120px]">
      <span className="text-xs text-qds-t3">{message}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Toggle pill button (inline, no toggle-group dep)                   */
/* ------------------------------------------------------------------ */

export function TogglePill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-[9px] px-2 py-0.5 rounded border transition-all duration-200 ${
        active
          ? "border-qds-info text-qds-info bg-qds-info/10"
          : "border text-qds-t3 hover:text-qds-t1"
      }`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Rolling chart legend                                               */
/* ------------------------------------------------------------------ */

export function RollingLegend({
  items,
}: {
  items: { color: string; label: string }[];
}) {
  return (
    <div className="flex items-center gap-3 text-[9px] text-muted-foreground">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-[2px] rounded"
            style={{ background: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading Skeleton                                                   */
/* ------------------------------------------------------------------ */

export function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-6 p-5">
      <Skeleton className="h-4 w-32 rounded" />
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-72 rounded-xl" />
        <Skeleton className="h-72 rounded-xl" />
      </div>
      <Skeleton className="h-44 rounded-xl" />
      <div className="grid grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-4 w-32 rounded" />
      <Skeleton className="h-48 rounded-xl" />
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-48 rounded-xl" />
        <Skeleton className="h-48 rounded-xl" />
      </div>
    </div>
  );
}
