import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { ChartCard } from "./ChartCard";
import { Heatmap } from "./Heatmap";
import { Waterfall } from "./Waterfall";
import type { CostParamsData } from "./types";

export function CostParamsTab({ data }: { data: CostParamsData }) {
  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard title="Edge Waterfall">
          <Waterfall items={data.waterfall} />
        </ChartCard>
        <ChartCard title="参数热力图" sub="lookback x forward_period">
          <Heatmap
            xLabels={data.heatmap.x_labels}
            yLabels={data.heatmap.y_labels}
            values={data.heatmap.values}
          />
        </ChartCard>
      </div>
      <ChartCard title="单参数扫描" sub="平滑度">
        <div style={{ height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.param_sweep}>
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis dataKey="param_value" tick={CHART_AXIS_STYLE} />
              <YAxis tick={CHART_AXIS_STYLE} />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <Line
                type="monotone"
                dataKey="ic"
                stroke="var(--acc)"
                strokeWidth={2}
                dot={{ r: 3, fill: "var(--acc)" }}
                animationDuration={CHART_ANIMATION.duration}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}
