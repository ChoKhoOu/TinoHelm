"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_GRID_STYLE, CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type {
  RollingBetaPoint,
  RollingReturnPoint,
  RollingSharpePoint,
  RollingSortinoPoint,
  RollingVolatilityPoint,
} from "../types";
import {
  CARD_BODY_CLS,
  CARD_CLS,
  CARD_HEADER_CLS,
  ChartCard,
  ChartPlaceholder,
  RollingLegend,
  downsample,
  fmtDate,
} from "./PerformanceHelpers";

export function PerformanceRollingSharpeChart({
  data: rawData,
}: {
  data?: RollingSharpePoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_3m: r.rolling_3m,
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some(
    (d) => d.rolling_3m != null || d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <ChartCard title="滚动 Sharpe 比率">
        <ChartPlaceholder message="数据不足（需要 3 个月以上）" />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>滚动 Sharpe 比率</span>
        <RollingLegend
          items={[
            { color: "var(--info)", label: "3m" },
            { color: "var(--info)", label: "6m" },
            { color: "var(--warn)", label: "12m" },
          ]}
        />
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart
            data={data}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
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
              width={36}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                const colorMap: Record<string, string> = {
                  rolling_3m: "var(--info)",
                  rolling_6m: "var(--info)",
                  rolling_12m: "var(--warn)",
                };
                const labelMap: Record<string, string> = {
                  rolling_3m: "3m Sharpe",
                  rolling_6m: "6m Sharpe",
                  rolling_12m: "12m Sharpe",
                };
                const key = String(name);
                return [
                  <span
                    key="v"
                    style={{ color: colorMap[key] ?? "var(--t0)" }}
                  >
                    {v.toFixed(3)}
                  </span>,
                  labelMap[key] ?? key,
                ];
              }}
            />
            <ReferenceLine
              y={0}
              stroke="rgba(255,255,255,0.15)"
              strokeWidth={1}
            />
            <Line
              type="monotone"
              dataKey="rolling_3m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_6m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_12m"
              stroke="var(--warn)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PerformanceRollingSortinoChart({
  data: rawData,
}: {
  data?: RollingSortinoPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some(
    (d) => d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <ChartCard title="滚动 Sortino 比率">
        <ChartPlaceholder message="数据不足（需要 6 个月以上）" />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>滚动 Sortino 比率</span>
        <RollingLegend
          items={[
            { color: "var(--info)", label: "6m" },
            { color: "var(--warn)", label: "12m" },
          ]}
        />
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart
            data={data}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
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
              width={36}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                const colorMap: Record<string, string> = {
                  rolling_6m: "var(--info)",
                  rolling_12m: "var(--warn)",
                };
                const labelMap: Record<string, string> = {
                  rolling_6m: "6m Sortino",
                  rolling_12m: "12m Sortino",
                };
                const key = String(name);
                return [
                  <span
                    key="v"
                    style={{ color: colorMap[key] ?? "var(--t0)" }}
                  >
                    {v.toFixed(3)}
                  </span>,
                  labelMap[key] ?? key,
                ];
              }}
            />
            <ReferenceLine
              y={0}
              stroke="rgba(255,255,255,0.15)"
              strokeWidth={1}
            />
            <Line
              type="monotone"
              dataKey="rolling_6m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_12m"
              stroke="var(--warn)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PerformanceRollingVolatilityChart({
  data: rawData,
}: {
  data?: RollingVolatilityPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some(
    (d) => d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <ChartCard title="滚动波动率">
        <ChartPlaceholder message="数据不足（需要 6 个月以上）" />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>滚动波动率</span>
        <RollingLegend
          items={[
            { color: "var(--info)", label: "6m" },
            { color: "var(--warn)", label: "12m" },
          ]}
        />
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart
            data={data}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
            <defs>
              <linearGradient id="perfVol6mFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--info)" stopOpacity={0.15} />
                <stop offset="100%" stopColor="var(--info)" stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="perfVol12mFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--warn)" stopOpacity={0.12} />
                <stop offset="100%" stopColor="var(--warn)" stopOpacity={0.02} />
              </linearGradient>
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
              tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
              width={42}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                const colorMap: Record<string, string> = {
                  rolling_6m: "var(--info)",
                  rolling_12m: "var(--warn)",
                };
                const labelMap: Record<string, string> = {
                  rolling_6m: "6m 波动率",
                  rolling_12m: "12m 波动率",
                };
                const key = String(name);
                return [
                  <span
                    key="v"
                    style={{ color: colorMap[key] ?? "var(--t0)" }}
                  >
                    {(v * 100).toFixed(2)}%
                  </span>,
                  labelMap[key] ?? key,
                ];
              }}
            />
            <Area
              type="monotone"
              dataKey="rolling_6m"
              stroke="var(--info)"
              strokeWidth={1.5}
              fill="url(#perfVol6mFill)"
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Area
              type="monotone"
              dataKey="rolling_12m"
              stroke="var(--warn)"
              strokeWidth={1.5}
              fill="url(#perfVol12mFill)"
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PerformanceRollingBetaChart({
  data: rawData,
}: {
  data?: RollingBetaPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some(
    (d) => d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <ChartCard title="滚动 Beta">
        <ChartPlaceholder message="数据不足或无基准" />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>滚动 Beta</span>
        <RollingLegend
          items={[
            { color: "var(--info)", label: "6m" },
            { color: "var(--warn)", label: "12m" },
          ]}
        />
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart
            data={data}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
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
              width={36}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                const colorMap: Record<string, string> = {
                  rolling_6m: "var(--info)",
                  rolling_12m: "var(--warn)",
                };
                const labelMap: Record<string, string> = {
                  rolling_6m: "6m Beta",
                  rolling_12m: "12m Beta",
                };
                const key = String(name);
                return [
                  <span
                    key="v"
                    style={{ color: colorMap[key] ?? "var(--t0)" }}
                  >
                    {v.toFixed(3)}
                  </span>,
                  labelMap[key] ?? key,
                ];
              }}
            />
            <ReferenceLine
              y={1}
              stroke="var(--warn)"
              strokeDasharray="4 3"
              strokeWidth={1}
              strokeOpacity={0.4}
            />
            <Line
              type="monotone"
              dataKey="rolling_6m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_12m"
              stroke="var(--warn)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PerformanceRollingReturnsChart({
  rollingReturns,
}: {
  rollingReturns?: RollingReturnPoint[];
}) {
  const data = useMemo(() => {
    if (!rollingReturns?.length) return [];
    return downsample(rollingReturns, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_3m: r.rolling_3m,
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rollingReturns]);

  const hasData = data.some(
    (d) => d.rolling_3m != null || d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <ChartCard title="滚动收益 (3m / 6m / 12m)">
        <ChartPlaceholder message="数据不足（需要 3 个月以上）" />
      </ChartCard>
    );
  }

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>
        <span>滚动收益</span>
        <RollingLegend
          items={[
            { color: "var(--info)", label: "3m" },
            { color: "var(--info)", label: "6m" },
            { color: "var(--warn)", label: "12m" },
          ]}
        />
      </div>
      <div className={CARD_BODY_CLS}>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart
            data={data}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
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
              formatter={(value: unknown, name: unknown) => {
                const v = Number(value);
                const colorMap: Record<string, string> = {
                  rolling_3m: "var(--info)",
                  rolling_6m: "var(--info)",
                  rolling_12m: "var(--warn)",
                };
                const labelMap: Record<string, string> = {
                  rolling_3m: "3m 滚动",
                  rolling_6m: "6m 滚动",
                  rolling_12m: "12m 滚动",
                };
                const key = String(name);
                return [
                  <span
                    key="v"
                    style={{ color: colorMap[key] ?? "var(--t0)" }}
                  >
                    {v >= 0 ? "+" : ""}
                    {v.toFixed(2)}%
                  </span>,
                  labelMap[key] ?? key,
                ];
              }}
            />
            <ReferenceLine
              y={0}
              stroke="rgba(255,255,255,0.15)"
              strokeWidth={1}
            />
            <Line
              type="monotone"
              dataKey="rolling_3m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_6m"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
            <Line
              type="monotone"
              dataKey="rolling_12m"
              stroke="var(--warn)"
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
              isAnimationActive
              animationDuration={1400}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
