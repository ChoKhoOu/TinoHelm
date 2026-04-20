import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { SectionLabel } from "@/components/qds";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_LABEL_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { ChartCard } from "./ChartCard";
import type { RobustnessData } from "./types";

export function RobustnessTab({ data }: { data: RobustnessData }) {
  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard
          title="Shuffle Test"
          badge={
            <span className="font-mono text-[0.6rem] px-2 py-0.5 rounded-full bg-qds-success-dim text-qds-success">
              p={data.shuffle_test.p_value.toFixed(3)}
            </span>
          }
        >
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.shuffle_test.distribution}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="bin" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine
                  x={data.shuffle_test.real_ic}
                  stroke="var(--dan)"
                  strokeWidth={2}
                  label={{ ...CHART_LABEL_STYLE, fill: "var(--dan)", value: "Real IC" }}
                />
                <Bar
                  dataKey="count"
                  fill="var(--t3)"
                  radius={[2, 2, 0, 0]}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard
          title="分段 IC"
          sub={`正段: ${Math.round(
            (data.sub_period_ic.filter((d) => d.ic > 0).length /
              Math.max(data.sub_period_ic.length, 1)) *
              100,
          )}%`}
        >
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.sub_period_ic}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="period" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" />
                <Bar
                  dataKey="ic"
                  animationDuration={CHART_ANIMATION.duration}
                  radius={[2, 2, 0, 0]}
                >
                  {data.sub_period_ic.map((entry, idx) => (
                    <Cell key={idx} fill={entry.ic >= 0 ? "var(--suc)" : "var(--dan)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
      <SectionLabel>跨品种 IC</SectionLabel>
      <ChartCard title="跨品种 IC 水平">
        <div style={{ height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.cross_symbol_ic} layout="vertical">
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis type="number" tick={CHART_AXIS_STYLE} />
              <YAxis
                type="category"
                dataKey="symbol"
                tick={CHART_AXIS_STYLE}
                width={80}
              />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <ReferenceLine x={0} stroke="var(--t3)" />
              <Bar
                dataKey="ic"
                animationDuration={CHART_ANIMATION.duration}
                radius={[0, 3, 3, 0]}
              >
                {data.cross_symbol_ic.map((entry, idx) => (
                  <Cell key={idx} fill={entry.ic >= 0 ? "var(--suc)" : "var(--dan)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}
