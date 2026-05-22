"use client";

import { Fragment } from "react";
import type { MonthlyReturn } from "../types";
import { InlineError } from "@/components/qds";

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function MonthlyHeatmap({ data }: { data: MonthlyReturn[] }) {
  const map: Record<number, Record<number, number>> = {};
  for (const item of data) {
    const [yearStr, monthStr] = item.period.split("-");
    const year = Number(yearStr);
    const month = Number(monthStr) - 1;
    if (!map[year]) map[year] = {};
    map[year][month] = item.return_pct;
  }

  const years = Object.keys(map).map(Number).sort((a, b) => a - b);
  if (years.length === 0) return <InlineError variant="hint">暂无月度收益数据</InlineError>;

  let maxAbs = 0;
  for (const yr of years) {
    for (let m = 0; m < 12; m++) {
      const v = Math.abs(map[yr]?.[m] ?? 0);
      if (v > maxAbs) maxAbs = v;
    }
  }

  const cellBg = (val: number | undefined) => {
    if (val === undefined) return "transparent";
    if (val === 0) return "var(--bg-t)";
    const ratio = Math.min(Math.abs(val) / (maxAbs || 1), 1);
    const pct = Math.round((0.12 + ratio * 0.45) * 100);
    return val > 0
      ? `color-mix(in srgb, var(--suc) ${pct}%, transparent)`
      : `color-mix(in srgb, var(--dan) ${pct}%, transparent)`;
  };

  const cellText = (val: number | undefined) => {
    if (val === undefined) return "var(--t3)";
    if (val === 0) return "var(--t2)";
    return "var(--t0)";
  };

  const hmLabelCls = "flex items-center justify-center font-mono text-[0.62rem] text-muted-foreground";
  const hmCellCls = "flex items-center justify-center font-mono text-[0.7rem] rounded-md h-7";

  return (
    <div className="grid gap-1" style={{ gridTemplateColumns: "auto repeat(12, 1fr)" }}>
      {/* Corner */}
      <div className={hmLabelCls} />
      {/* Month headers */}
      {MONTH_LABELS.map((m) => (
        <div key={m} className={hmLabelCls}>{m}</div>
      ))}
      {/* Year rows */}
      {years.map((yr) => (
        <Fragment key={yr}>
          <div className={`${hmLabelCls} justify-end pr-2.5`}>{yr}</div>
          {Array.from({ length: 12 }, (_, m) => {
            const val = map[yr]?.[m];
            return (
              <div
                key={m}
                className={hmCellCls}
                style={{ background: cellBg(val), color: cellText(val) }}
              >
                {val !== undefined ? `${val >= 0 ? "+" : ""}${val.toFixed(1)}%` : ""}
              </div>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}
