"use client";

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_GRID_STYLE, CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { DistributionBin, QQPlotPoint } from "../types";
import {
  CARD_BODY_CLS,
  CARD_CLS,
  CARD_HEADER_CLS,
  ChartCard,
  ChartPlaceholder,
} from "./PerformanceHelpers";

export function PerformanceDistributionHistogram({
  distribution,
  normalMean,
  normalStd,
}: {
  distribution?: DistributionBin[];
  normalMean?: number | null;
  normalStd?: number | null;
}) {
  const { barData, normalCurve } = useMemo(() => {
    if (!distribution?.length) return { barData: [], normalCurve: [] };

    const barData = distribution.map((b) => ({
      mid: parseFloat(((b.bin_start + b.bin_end) / 2).toFixed(4)),
      count: b.count,
    }));

    // Generate normal distribution overlay
    let normalCurve: Array<{ mid: number; normal: number }> = [];
    if (normalMean != null && normalStd != null && normalStd > 0) {
      const totalCount = distribution.reduce((sum, b) => sum + b.count, 0);
      const binWidth =
        distribution.length > 1
          ? distribution[1].bin_start - distribution[0].bin_start
          : 1;
      const minX = distribution[0].bin_start;
      const maxX = distribution[distribution.length - 1].bin_end;
      const numPoints = 100;
      const step = (maxX - minX) / numPoints;
      const sigma = normalStd;
      const mu = normalMean;
      const coeff =
        (1 / (sigma * Math.sqrt(2 * Math.PI))) * totalCount * binWidth;

      normalCurve = Array.from({ length: numPoints + 1 }, (_, i) => {
        const x = minX + i * step;
        const exponent = -0.5 * Math.pow((x - mu) / sigma, 2);
        return {
          mid: parseFloat(x.toFixed(4)),
          normal: coeff * Math.exp(exponent),
        };
      });
    }

    return { barData, normalCurve };
  }, [distribution, normalMean, normalStd]);

  // Merge bar data and normal curve for ComposedChart
  // Must be before conditional return to satisfy React hooks rules
  const mergedData = useMemo(() => {
    if (!barData.length) return [];
    const normalMap = new Map(normalCurve.map((n) => [n.mid, n.normal]));
    // Add bar data points
    const result = barData.map((b) => ({
      mid: b.mid,
      count: b.count,
      normal: normalMap.get(b.mid) ?? null,
    }));
    // Add normal-only points between bars for smooth curve
    for (const n of normalCurve) {
      if (!barData.some((b) => Math.abs(b.mid - n.mid) < 0.0001)) {
        result.push({ mid: n.mid, count: 0, normal: n.normal });
      }
    }
    result.sort((a, b) => a.mid - b.mid);
    return result;
  }, [barData, normalCurve]);

  if (!barData.length) {
    return (
      <ChartCard title="日收益分布">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>日收益分布</span>
        {normalCurve.length > 0 && (
          <div className="flex items-center gap-3 text-[9px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-[2px] rounded bg-qds-info" />
              实际
            </span>
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-3 h-[1px] rounded"
                style={{ borderTop: "1px dashed var(--info)" }}
              />
              正态拟合
            </span>
          </div>
        )}
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart
            data={mergedData}
            margin={{ top: 8, right: 8, left: 8, bottom: 4 }}
          >
            <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
            <XAxis
              dataKey="mid"
              tick={{ fill: "var(--t2)", fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => `${Number(v).toFixed(1)}%`}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: "var(--t2)", fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                if (name === "normal") {
                  return [
                    <span key="v" style={{ color: "var(--info)" }}>
                      {v.toFixed(1)}
                    </span>,
                    "正态拟合",
                  ];
                }
                return [
                  <span key="v" style={{ color: "var(--info)" }}>
                    {v} 天
                  </span>,
                  "实际频次",
                ];
              }}
            />
            <ReferenceLine x={0} stroke="var(--t3)" strokeWidth={1} />
            <Bar dataKey="count" radius={[2, 2, 0, 0]}>
              {mergedData.map((entry, index) => (
                <Cell
                  key={`dist-${index}`}
                  fill={entry.mid >= 0 ? "var(--suc)" : "var(--dan)"}
                  fillOpacity={0.75}
                />
              ))}
            </Bar>
            {normalCurve.length > 0 && (
              <Line
                type="monotone"
                dataKey="normal"
                stroke="var(--info)"
                strokeWidth={1.5}
                strokeDasharray="4 2"
                dot={false}
                connectNulls
                isAnimationActive
                animationDuration={1600}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PerformanceQQPlotChart({
  qqData,
}: {
  qqData?: QQPlotPoint[];
}) {
  const { data, refMin, refMax } = useMemo(() => {
    if (!qqData?.length) return { data: [], refMin: -3, refMax: 3 };
    const theoreticals = qqData.map((p) => p.theoretical);
    const empiricals = qqData.map((p) => p.empirical);
    const refMin = Math.min(...theoreticals, ...empiricals);
    const refMax = Math.max(...theoreticals, ...empiricals);
    return {
      data: qqData.map((p) => ({
        theoretical: p.theoretical,
        empirical: p.empirical,
      })),
      refMin,
      refMax,
    };
  }, [qqData]);

  if (!data.length) {
    return (
      <ChartCard title="Q-Q 正态图">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>Q-Q 正态图</span>
        <span className="text-[9px] text-qds-t3">偏离对角线 = 非正态</span>
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={220}>
          <ScatterChart margin={{ top: 8, right: 16, left: 8, bottom: 4 }}>
            <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="theoretical"
              name="理论分位数"
              tick={{ fill: "var(--t2)", fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => Number(v).toFixed(1)}
              domain={[refMin * 1.1, refMax * 1.1]}
              label={{
                value: "理论分位数 (正态)",
                position: "insideBottom",
                offset: -2,
                fill: "var(--t3)",
                fontSize: 8,
              }}
            />
            <YAxis
              type="number"
              dataKey="empirical"
              name="实际分位数"
              tick={{ fill: "var(--t2)", fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => Number(v).toFixed(3)}
              width={52}
              label={{
                value: "实际日收益",
                angle: -90,
                position: "insideLeft",
                fill: "var(--t3)",
                fontSize: 8,
              }}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: string | undefined) => [
                Number(value).toFixed(4),
                name ?? "",
              ]}
            />
            <ReferenceLine
              segment={[
                { x: refMin, y: refMin },
                { x: refMax, y: refMax },
              ]}
              stroke="var(--warn)"
              strokeDasharray="5 3"
              strokeWidth={1}
              strokeOpacity={0.5}
            />
            <Scatter data={data} fill="var(--info)" fillOpacity={0.6} r={2} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
