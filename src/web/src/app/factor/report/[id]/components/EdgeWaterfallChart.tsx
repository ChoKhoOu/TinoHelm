"use client";

import { cn } from "@/lib/utils";
import type { CostPayload } from "./types";
import { formatBps } from "./types";

interface EdgeWaterfallChartProps {
  cost: CostPayload;
}

/**
 * Edge waterfall — gross → net edge breakdown in basis points.
 *
 * Backend emits 4 keys via ``edge_waterfall``:
 *   gross_edge_bps → fee_cost_bps → slippage_bps → net_edge_bps
 *
 * Presentation: horizontal bars sized to ``max(abs(value))``; colour is
 * semantic (``--suc`` for positive, ``--dan`` for negative).  Kept as a
 * pure Tailwind/CSS render — no Recharts — because it's a 4-row fixed
 * chart and Recharts adds ~30 KB of runtime for zero visual gain here.
 */
const ROW_DEFS: {
  key: keyof CostPayload;
  label: string;
  type: "positive" | "negative" | "net";
}[] = [
  { key: "gross_edge_bps", label: "毛收益", type: "positive" },
  { key: "fee_cost_bps", label: "手续费", type: "negative" },
  { key: "slippage_bps", label: "滑点", type: "negative" },
  { key: "net_edge_bps", label: "净收益", type: "net" },
];

export function EdgeWaterfallChart({ cost }: EdgeWaterfallChartProps) {
  if (
    !cost ||
    Object.values(cost).every((v) => v == null || v === 0)
  ) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        成本瀑布需 ``/run`` 深度诊断（非 explore 快照）
      </div>
    );
  }

  const values = ROW_DEFS.map((r) => ({
    ...r,
    value: cost[r.key] ?? 0,
  }));
  const maxAbs = Math.max(...values.map((v) => Math.abs(v.value)), 0.001);

  return (
    <div className="flex flex-col gap-1.5 py-2">
      {values.map((row) => {
        const widthPct = (Math.abs(row.value) / maxAbs) * 100;
        const isPositive =
          row.type === "positive" ||
          (row.type === "net" && row.value >= 0);
        return (
          <div
            key={row.key}
            className="flex items-center gap-2 font-mono text-[0.72rem]"
          >
            <span className="w-16 text-right text-muted-foreground text-[0.68rem] shrink-0">
              {row.label}
            </span>
            <div className="flex-1 h-5 relative bg-secondary rounded-sm overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-sm transition-all duration-700",
                  isPositive ? "bg-qds-success" : "bg-destructive",
                )}
                style={{ width: `${widthPct}%`, opacity: 0.75 }}
              />
            </div>
            <span
              className={cn(
                "font-medium min-w-[90px] text-right",
                isPositive ? "text-qds-success" : "text-destructive",
              )}
            >
              {formatBps(row.value, 2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
