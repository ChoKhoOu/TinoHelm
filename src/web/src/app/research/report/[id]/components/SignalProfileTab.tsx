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
import { StatCard } from "@/components/qds";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { ChartCard } from "./ChartCard";
import type { SignalProfileData } from "./types";

export function SignalProfileTab({ data }: { data: SignalProfileData }) {
  const zeroPctTrend =
    data.zero_pct < 10 ? "up" : data.zero_pct > 30 ? "down" : "neutral";

  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-6 gap-3 mb-5">
        <StatCard label="均值" value={data.mean.toFixed(3)} help="因子值的算术平均，偏离 0 太远可能有偏" />
        <StatCard label="标准差" value={data.std.toFixed(3)} help="因子值的离散程度" />
        <StatCard label="偏度" value={data.skewness.toFixed(2)} help="|skew|>3 说明分布严重偏斜" />
        <StatCard label="lag-1 ACF" value={data.lag1_acf.toFixed(2)} help="滞后一期自相关" />
        <StatCard label="与RV相关" value={data.rv_corr.toFixed(2)} help="和已实现波动率的相关性" />
        <StatCard
          label="零值占比"
          value={`${data.zero_pct.toFixed(1)}%`}
          trend={zeroPctTrend}
          help=">30% 说明信号太稀疏"
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <ChartCard title="信号分布" sub="Histogram">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.distribution}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="bin" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <Bar
                  dataKey="count"
                  fill="var(--acc)"
                  radius={[2, 2, 0, 0]}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="自相关函数" sub="95% 置信区间">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.acf}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="lag" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} domain={[-1, 1]} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" />
                {data.acf.length > 0 && (
                  <>
                    <ReferenceLine
                      y={data.acf[0]?.ci_upper ?? 0.1}
                      stroke="var(--warn)"
                      strokeDasharray="4 4"
                      strokeOpacity={0.5}
                    />
                    <ReferenceLine
                      y={data.acf[0]?.ci_lower ?? -0.1}
                      stroke="var(--warn)"
                      strokeDasharray="4 4"
                      strokeOpacity={0.5}
                    />
                  </>
                )}
                <Bar
                  dataKey="value"
                  animationDuration={CHART_ANIMATION.duration}
                  radius={[2, 2, 0, 0]}
                >
                  {data.acf.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={entry.value >= 0 ? "var(--info)" : "var(--dan)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
    </div>
  );
}
