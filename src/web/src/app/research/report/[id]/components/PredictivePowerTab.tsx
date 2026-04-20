import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { StatCard } from "@/components/qds";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { ChartCard } from "./ChartCard";
import type { PredictivePowerData } from "./types";

export function PredictivePowerTab({ data }: { data: PredictivePowerData }) {
  const icTrend = data.ic_mean_h5 > 0.03 ? "up" : data.ic_mean_h5 < 0 ? "down" : "neutral";
  const tstatTrend = data.ic_tstat >= 2.0 ? "up" : "neutral";
  const icirTrend = data.icir >= 0.5 ? "up" : "neutral";

  // Build cumulative return series for LineChart
  const cumDates = data.cumulative_returns?.dates ?? [];
  const cumSeries = data.cumulative_returns?.series ?? {};
  const cumData = cumDates.map((d, i) => {
    const point: Record<string, string | number> = { date: d };
    for (const [key, vals] of Object.entries(cumSeries)) {
      point[key] = vals[i] ?? 0;
    }
    return point;
  });
  const cumKeys = Object.keys(cumSeries);
  const cumColors = [
    "var(--suc)",
    "var(--info)",
    "var(--warn)",
    "var(--dan)",
    "var(--t2)",
    "var(--acc)",
  ];

  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-5 gap-3 mb-5">
        <StatCard
          label={`IC\u0304 (h=5)`}
          value={data.ic_mean_h5.toFixed(3)}
          trend={icTrend}
          help="预测周期=5时的 IC 均值"
        />
        <StatCard
          label="IC t-stat"
          value={data.ic_tstat.toFixed(2)}
          trend={tstatTrend}
          help=">2.0 表示 95% 置信度下显著"
        />
        <StatCard
          label="ICIR"
          value={data.icir.toFixed(2)}
          trend={icirTrend}
          help={`IC\u0304 / IC Std`}
        />
        <StatCard
          label="IC>0%"
          value={`${data.ic_positive_pct}%`}
          help="IC 为正的时间占比"
        />
        <StatCard
          label={`IC\u0304 (h=15)`}
          value={data.ic_mean_h15.toFixed(3)}
          help="更长预测周期的 IC"
        />
      </div>
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard title="Rolling IC" sub="window=60">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.rolling_ic}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="date" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="4 4" />
                <Line
                  type="monotone"
                  dataKey="ic"
                  stroke="var(--acc)"
                  strokeWidth={1.5}
                  dot={false}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="分位数平均收益">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.quantile_returns} layout="vertical">
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis type="number" tick={CHART_AXIS_STYLE} />
                <YAxis
                  type="category"
                  dataKey="quantile"
                  tick={CHART_AXIS_STYLE}
                  width={36}
                />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine x={0} stroke="var(--t3)" />
                <Bar
                  dataKey="return_pct"
                  animationDuration={CHART_ANIMATION.duration}
                  radius={[0, 3, 3, 0]}
                >
                  {data.quantile_returns.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={entry.return_pct >= 0 ? "var(--suc)" : "var(--dan)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
      <ChartCard title="分层累计收益" sub="Q1~Q5 + L/S">
        <div style={{ height: 240 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={cumData}>
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis dataKey="date" tick={CHART_AXIS_STYLE} />
              <YAxis tick={CHART_AXIS_STYLE} />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="4 4" />
              {cumKeys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={cumColors[i % cumColors.length]}
                  strokeWidth={key === "L/S" ? 2 : 1.2}
                  dot={false}
                  strokeDasharray={key === "L/S" ? "6 3" : undefined}
                  animationDuration={CHART_ANIMATION.duration}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}
