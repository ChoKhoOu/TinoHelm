"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CHART_GRID_STYLE, CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { BacktestResult, DrawdownPeriod } from "../types";
import {
  ChartCard,
  ChartPlaceholder,
  downsample,
  fmtDate,
  fmtDateFull,
} from "./PerformanceHelpers";

export function PerformanceDrawdownChart({
  equityCurve,
}: {
  equityCurve: BacktestResult["equity_curve"];
}) {
  const chartData = useMemo(() => {
    const sampled = downsample(equityCurve, 500);
    return sampled.map((p) => ({
      t: fmtDate(p.timestamp ?? p.date ?? ""),
      drawdown: p.drawdown_pct ?? 0,
    }));
  }, [equityCurve]);

  if (chartData.length < 2) return <ChartPlaceholder />;

  return (
    <ChartCard title="水下回撤曲线">
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart
          data={chartData}
          margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
        >
          <defs>
            <linearGradient id="perfDdGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--dan)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--dan)" stopOpacity={0.02} />
            </linearGradient>
            <filter
              id="perfDdGlow"
              x="-20%"
              y="-20%"
              width="140%"
              height="140%"
            >
              <feGaussianBlur in="SourceGraphic" stdDeviation="3" />
            </filter>
          </defs>
          <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={42}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            formatter={(value: unknown) => [
              <span key="v" className="text-destructive">
                {Number(value).toFixed(2)}%
              </span>,
              "回撤",
            ]}
          />
          {/* Glow layer */}
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="var(--dan)"
            strokeWidth={4}
            fill="none"
            dot={false}
            filter="url(#perfDdGlow)"
            opacity={0.25}
            isAnimationActive={false}
            tooltipType="none"
          />
          {/* Main line */}
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="var(--dan)"
            strokeWidth={1.2}
            fill="url(#perfDdGrad)"
            dot={false}
            isAnimationActive={true}
            animationDuration={1600}
            animationEasing="ease-in-out"
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PerformanceTop5DrawdownsTable({
  drawdownPeriods,
}: {
  drawdownPeriods?: DrawdownPeriod[];
}) {
  const top5 = useMemo(
    () => (drawdownPeriods ?? []).slice(0, 5),
    [drawdownPeriods]
  );

  if (!top5.length) {
    return (
      <ChartCard title="Top 5 最大回撤">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  return (
    <ChartCard title="Top 5 最大回撤">
      <Table>
        <TableHeader>
          <TableRow className="border hover:bg-transparent">
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8">
              #
            </TableHead>
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8">
              开始日期
            </TableHead>
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8">
              谷底日期
            </TableHead>
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8 text-right">
              深度
            </TableHead>
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8 text-right">
              持续 (天)
            </TableHead>
            <TableHead className="text-[9px] text-muted-foreground font-semibold h-8 text-right">
              恢复 (天)
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top5.map((dd, i) => (
            <TableRow key={i} className="border hover:bg-secondary">
              <TableCell className="text-[10px] text-muted-foreground py-2">
                {i + 1}
              </TableCell>
              <TableCell className="text-[10px] text-qds-t1 py-2">
                {fmtDateFull(dd.start)}
              </TableCell>
              <TableCell className="text-[10px] text-qds-t1 py-2">
                {fmtDateFull(dd.trough_date)}
              </TableCell>
              <TableCell className="text-[10px] text-destructive font-medium py-2 text-right">
                {dd.max_drawdown_pct.toFixed(2)}%
              </TableCell>
              <TableCell className="text-[10px] text-qds-t1 py-2 text-right">
                {dd.duration_days}
              </TableCell>
              <TableCell className="text-[10px] text-qds-t1 py-2 text-right">
                {dd.recovery_days != null ? dd.recovery_days : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ChartCard>
  );
}
