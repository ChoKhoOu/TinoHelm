"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { HelpTip, SectionLabel } from "@/components/qds";
import { cn } from "@/lib/utils";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_COLORS,
  CHART_GRID_STYLE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { DOT_COLORS, irColor } from "./types";
import type { ExploreResult } from "./types";
import { StrengthBadge } from "./VerdictBadge";

interface ResearchExploreResultProps {
  result: ExploreResult;
}

/** Small card header pattern: title span (optional HelpTip) on the left, sub text on the right. */
function ChartHeader({
  title,
  tip,
  sub,
}: {
  title: string;
  tip?: string;
  sub?: string;
}) {
  return (
    <CardHeader className="flex flex-row justify-between items-center px-3 py-2.5 border-b text-[0.72rem] font-semibold">
      <span className="flex items-center">
        {title}
        {tip && <HelpTip text={tip} />}
      </span>
      {sub && (
        <span className="font-mono text-[0.58rem] font-normal text-muted-foreground">
          {sub}
        </span>
      )}
    </CardHeader>
  );
}

export function ResearchExploreResult({ result }: ResearchExploreResultProps) {
  return (
    <>
      {/* ===== Summary table ===== */}
      <SectionLabel>探索结果 · {result.factors.length} 个因子</SectionLabel>
      <div className="rounded-lg border bg-card overflow-hidden mb-5">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>因子</TableHead>
                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    IC̄
                    <HelpTip text="IC 均值，因子预测力的核心指标，>0.03 可用，>0.05 优秀" />
                  </span>
                </TableHead>
                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    IC Std
                    <HelpTip text="IC 的标准差，越小说明预测力越稳定" />
                  </span>
                </TableHead>
                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    IR
                    <HelpTip text="信息比率 = IC̄ / IC Std，综合衡量预测力和稳定性，>0.5 可用，>1.0 优秀" />
                  </span>
                </TableHead>
                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    IC&gt;0%
                    <HelpTip text="IC 为正的期数占比，>55% 说明因子在大多数时间都有效" />
                  </span>
                </TableHead>
                <TableHead className="text-right">强度</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.factors.map((f, i) => (
                <TableRow key={f.name}>
                  <TableCell>
                    <span
                      className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
                      style={{ background: DOT_COLORS[i % DOT_COLORS.length] }}
                    />
                    {f.name}
                  </TableCell>
                  <TableCell className="text-right font-semibold">
                    {f.ic_mean.toFixed(3)}
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {f.ic_std.toFixed(3)}
                  </TableCell>
                  <TableCell
                    className={cn("text-right font-semibold", irColor(f.ir))}
                  >
                    {f.ir.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right">{f.ic_positive_pct}%</TableCell>
                  <TableCell className="text-right">
                    <StrengthBadge ir={f.ir} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* ===== IC timeseries + IC decay ===== */}
      <div className="grid grid-cols-2 gap-4 mb-5">
        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="IC 时序"
            tip="每期的 Spearman 秩相关系数，衡量因子排序和未来收益排序的一致性"
            sub="Spearman Rank IC"
          />
          <CardContent className="p-3">
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.ic_timeseries}>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis dataKey="date" {...CHART_AXIS_STYLE} tick={CHART_AXIS_STYLE} />
                  <YAxis
                    {...CHART_AXIS_STYLE}
                    tick={CHART_AXIS_STYLE}
                    tickFormatter={(v: number) => v.toFixed(2)}
                  />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                  <ReferenceLine
                    y={0}
                    stroke="var(--t3)"
                    strokeDasharray="3 3"
                    strokeOpacity={0.4}
                  />
                  <Legend iconSize={8} wrapperStyle={CHART_LEGEND_STYLE} />
                  {result.factors.map((f, i) => (
                    <Line
                      key={f.name}
                      type="monotone"
                      dataKey={f.name}
                      stroke={DOT_COLORS[i % DOT_COLORS.length]}
                      strokeWidth={1.5}
                      dot={false}
                      animationDuration={CHART_ANIMATION.duration}
                      animationEasing={CHART_ANIMATION.easing}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="IC 衰减"
            tip="因子对不同 lag 的预测力，衰减越慢说明信号持续性越好"
            sub="IC Decay"
          />
          <CardContent className="p-3">
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.ic_decay}>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis
                    dataKey="lag"
                    {...CHART_AXIS_STYLE}
                    tick={CHART_AXIS_STYLE}
                    tickFormatter={(v: number) => `lag ${v}`}
                  />
                  <YAxis
                    {...CHART_AXIS_STYLE}
                    tick={CHART_AXIS_STYLE}
                    tickFormatter={(v: number) => v.toFixed(3)}
                  />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                  <Line
                    type="monotone"
                    dataKey="ic"
                    stroke={CHART_COLORS.success}
                    fill={CHART_COLORS.success}
                    strokeWidth={1.5}
                    dot={{ fill: CHART_COLORS.success, r: 3 }}
                    animationDuration={CHART_ANIMATION.duration}
                    animationEasing={CHART_ANIMATION.easing}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ===== Quantile returns + distribution ===== */}
      <div className="grid grid-cols-2 gap-4 mb-5">
        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="分层累计收益"
            tip="按因子值分组后各组的累计收益，Q1(高因子值)和Q5(低因子值)分得越开越好"
            sub="Q1 (高) → Q5 (低)"
          />
          <CardContent className="p-3">
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.quantile_returns}>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis dataKey="date" {...CHART_AXIS_STYLE} tick={CHART_AXIS_STYLE} />
                  <YAxis
                    {...CHART_AXIS_STYLE}
                    tick={CHART_AXIS_STYLE}
                    tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                  <Legend iconSize={8} wrapperStyle={CHART_LEGEND_STYLE} />
                  <Line
                    type="monotone"
                    dataKey="Q1"
                    name="Q1 (高)"
                    stroke={CHART_COLORS.success}
                    strokeWidth={1.5}
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="Q2"
                    stroke="rgba(54,136,75,0.4)"
                    strokeWidth={1}
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="Q3"
                    stroke="var(--t2)"
                    strokeWidth={1}
                    dot={false}
                    strokeDasharray="3 3"
                  />
                  <Line
                    type="monotone"
                    dataKey="Q4"
                    stroke="rgba(254,129,129,0.4)"
                    strokeWidth={1}
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="Q5"
                    name="Q5 (低)"
                    stroke={CHART_COLORS.danger}
                    strokeWidth={1.5}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="因子分布"
            tip="因子值的频率分布，理想的因子应该接近正态分布，没有极端尖峰或偏斜"
            sub={result.factors[0]?.name ?? ""}
          />
          <CardContent className="p-3">
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={result.distribution}>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis dataKey="bin" {...CHART_AXIS_STYLE} tick={CHART_AXIS_STYLE} />
                  <YAxis {...CHART_AXIS_STYLE} tick={CHART_AXIS_STYLE} hide />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                  <Bar
                    dataKey="count"
                    fill="var(--info)"
                    fillOpacity={0.4}
                    stroke="var(--info)"
                    strokeWidth={1}
                    radius={[2, 2, 0, 0]}
                    animationDuration={CHART_ANIMATION.duration}
                    animationEasing={CHART_ANIMATION.easing}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ===== Turnover stats row ===== */}
      <div className="flex flex-wrap gap-6 font-mono text-[0.72rem] bg-input px-3 py-2 rounded-md">
        <div className="flex flex-col">
          <div className="flex items-center text-[0.6rem] text-muted-foreground uppercase tracking-wider">
            平均日换手
            <HelpTip text="每日分层组成员变化比例，换手越高交易成本越大" />
          </div>
          <div className="font-semibold text-foreground mt-0.5">
            {result.turnover.daily_avg}
          </div>
        </div>
        <div className="flex flex-col">
          <div className="flex items-center text-[0.6rem] text-muted-foreground uppercase tracking-wider">
            年化换手
            <HelpTip text="全年的累计换手次数，= 日均换手 × 252" />
          </div>
          <div className="font-semibold text-foreground mt-0.5">
            {result.turnover.annual}
          </div>
        </div>
        <div className="flex flex-col">
          <div className="flex items-center text-[0.6rem] text-muted-foreground uppercase tracking-wider">
            隐含手续费损耗
            <HelpTip text="按当前换手率和费率估算每月被手续费吃掉的收益" />
          </div>
          <div className="font-semibold text-destructive mt-0.5">
            {result.turnover.fee_drag}
          </div>
        </div>
        <div className="flex flex-col">
          <div className="text-[0.6rem] text-muted-foreground uppercase tracking-wider">
            按 {result.turnover.fee_rate} 单边
          </div>
          <div className="font-semibold text-muted-foreground mt-0.5">taker fee</div>
        </div>
      </div>
    </>
  );
}
