"use client";

import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_GRID_STYLE, CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { BacktestResult, BenchmarkPoint } from "../types";
import {
  CARD_BODY_CLS,
  CARD_CLS,
  CARD_HEADER_CLS,
  ChartPlaceholder,
  TogglePill,
  clamp,
  downsample,
  fmtDate,
} from "./PerformanceHelpers";

export function PerformanceEquityChart({
  equityCurve,
  benchmarkCurve,
  startingBalance,
}: {
  equityCurve: BacktestResult["equity_curve"];
  benchmarkCurve?: BenchmarkPoint[];
  startingBalance: number;
}) {
  const [mode, setMode] = useState<"$" | "%">("$");
  const [scaleMode, setScaleMode] = useState<"linear" | "log">("linear");
  const [showBM, setShowBM] = useState(true);

  const hasBenchmarkData = (benchmarkCurve?.length ?? 0) > 0;
  const bmStart = benchmarkCurve?.[0]?.equity ?? startingBalance;

  const chartData = useMemo(() => {
    const sampled = downsample(equityCurve, 500);
    const bmMap = new Map(
      benchmarkCurve?.map((b) => [b.timestamp, b.equity]) ?? []
    );

    return sampled.map((p) => {
      const ts = p.timestamp ?? p.date ?? "";
      const bmEquity = bmMap.get(ts) ?? null;

      if (mode === "%") {
        return {
          t: fmtDate(ts),
          value: parseFloat(((p.equity / startingBalance - 1) * 100).toFixed(4)),
          benchmark:
            bmEquity != null && bmStart > 0
              ? parseFloat(((bmEquity / bmStart - 1) * 100).toFixed(4))
              : null,
        };
      }
      return {
        t: fmtDate(ts),
        value: p.equity,
        benchmark: bmEquity,
      };
    });
  }, [equityCurve, benchmarkCurve, startingBalance, bmStart, mode]);

  if (chartData.length < 2) return <ChartPlaceholder />;

  const values = chartData.map((d) => d.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;
  const refValue = mode === "%" ? 0 : startingBalance;
  const balanceStop = clamp((maxVal - refValue) / range, 0.01, 0.99);

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>权益曲线</span>
        <div className="flex items-center gap-1">
          <TogglePill active={mode === "$"} onClick={() => setMode("$")}>
            $
          </TogglePill>
          <TogglePill active={mode === "%"} onClick={() => setMode("%")}>
            %
          </TogglePill>
          <div className="w-px h-3 bg-secondary mx-1" />
          <TogglePill
            active={scaleMode === "linear"}
            onClick={() => setScaleMode("linear")}
          >
            Linear
          </TogglePill>
          <TogglePill
            active={scaleMode === "log"}
            onClick={() => setScaleMode("log")}
          >
            Log
          </TogglePill>
          {hasBenchmarkData && (
            <>
              <div className="w-px h-3 bg-secondary mx-1" />
              <TogglePill active={showBM} onClick={() => setShowBM(!showBM)}>
                BM
              </TogglePill>
            </>
          )}
        </div>
      </div>
      <div className={CARD_BODY_CLS}>
        {/* Legend */}
        <div className="flex items-center gap-4 text-[9px] text-muted-foreground px-1">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-4 h-[2px] rounded"
              style={{
                background:
                  "linear-gradient(90deg, var(--info), var(--info))",
              }}
            />
            策略 (盈利)
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-4 h-[2px] rounded"
              style={{ background: "var(--dan)" }}
            />
            策略 (亏损)
          </span>
          {hasBenchmarkData && showBM && (
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block w-4 h-[2px] rounded border-t border-dashed"
                style={{ borderColor: "var(--t1)" }}
              />
              Benchmark (B&H)
            </span>
          )}
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-4 h-[2px] rounded"
              style={{ background: "var(--warn)", opacity: 0.4 }}
            />
            {mode === "%" ? "零线" : "初始资金"}
          </span>
        </div>
        <div
          key={`${mode}-${scaleMode}`}
          style={{ width: "100%", height: 260 }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
            >
              <defs>
                <linearGradient id="perfEqStroke" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--info)" />
                  <stop offset={balanceStop * 0.6} stopColor="var(--info)" />
                  <stop offset={balanceStop} stopColor="var(--info)" />
                  <stop offset={balanceStop} stopColor="var(--dan)" />
                  <stop offset="100%" stopColor="var(--dan)" />
                </linearGradient>
                <linearGradient id="perfEqFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--info)" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="var(--info)" stopOpacity={0} />
                </linearGradient>
                <filter
                  id="perfEqGlow"
                  x="-20%"
                  y="-20%"
                  width="140%"
                  height="140%"
                >
                  <feGaussianBlur in="SourceGraphic" stdDeviation="4" />
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
                scale={scaleMode}
                tick={{ fill: "var(--t2)", fontSize: 9 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) =>
                  mode === "%"
                    ? `${Number(v).toFixed(0)}%`
                    : `$${(Number(v) / 1000).toFixed(Number(v) >= 10000 ? 0 : 1)}k`
                }
                width={48}
                domain={scaleMode === "log" ? ["auto", "auto"] : undefined}
              />
              <RechartsTooltip
                {...CHART_TOOLTIP_PROPS}
                formatter={(value: unknown, name: unknown) => {
                  const v = Number(value);
                  if (name === "benchmark") {
                    return [
                      <span key="v" className="text-qds-t1">
                        {mode === "%"
                          ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
                          : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                      </span>,
                      "基准 (B&H)",
                    ];
                  }
                  const color =
                    mode === "%"
                      ? v >= 0
                        ? "var(--info)"
                        : "var(--dan)"
                      : v >= startingBalance
                        ? "var(--info)"
                        : "var(--dan)";
                  return [
                    <span key="v" style={{ color }}>
                      {mode === "%"
                        ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
                        : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                    </span>,
                    "策略",
                  ];
                }}
              />
              <ReferenceLine
                y={refValue}
                stroke="var(--warn)"
                strokeDasharray="6 4"
                strokeWidth={1}
                strokeOpacity={0.4}
              />
              {/* Glow layer */}
              <Area
                type="monotone"
                dataKey="value"
                stroke="url(#perfEqStroke)"
                strokeWidth={5}
                fill="none"
                dot={false}
                filter="url(#perfEqGlow)"
                opacity={0.25}
                isAnimationActive={false}
                tooltipType="none"
              />
              {/* Main strategy line */}
              <Area
                type="monotone"
                dataKey="value"
                stroke="url(#perfEqStroke)"
                strokeWidth={1.5}
                fill="url(#perfEqFill)"
                dot={false}
                activeDot={{
                  r: 4,
                  fill: "var(--info)",
                  stroke: "rgba(76,158,235,0.3)",
                  strokeWidth: 6,
                }}
                isAnimationActive
                animationDuration={1600}
                animationEasing="ease-in-out"
              />
              {/* Benchmark line */}
              {hasBenchmarkData && showBM && (
                <Line
                  type="monotone"
                  dataKey="benchmark"
                  stroke="var(--t1)"
                  strokeWidth={1}
                  strokeDasharray="6 4"
                  dot={false}
                  connectNulls={true}
                  isAnimationActive
                  animationDuration={1600}
                  animationEasing="ease-in-out"
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
