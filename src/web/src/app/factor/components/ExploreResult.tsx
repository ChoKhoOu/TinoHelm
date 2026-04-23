"use client";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { HelpTip, SectionLabel, StatCard } from "@/components/qds";
import { cn } from "@/lib/utils";
import { ICLineChart } from "./charts/ICLineChart";
import { QuantilePnlChart } from "./charts/QuantilePnlChart";
import { DistributionChart } from "./charts/DistributionChart";
import { TurnoverChart } from "./charts/TurnoverChart";
import { irBand, ratingLabel } from "./types";
import type { ExploreResult as ExploreResultType } from "./types";

interface ExploreResultProps {
  result: ExploreResultType;
  costBps: number;
}

/**
 * Small card header (title + optional help + right-aligned sub) — matches
 * the backtest tabs' in-page card header density.
 */
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

/**
 * Rating star rendering — matches the Factor Research pattern: 0..3 filled
 * stars out of a fixed 3-star max. Colored ``--acc`` throughout.
 */
function RatingStars({ rating }: { rating: number }) {
  const clamped = Math.max(0, Math.min(3, Math.round(rating)));
  return (
    <span className="inline-flex items-center gap-[2px] font-mono text-primary">
      {Array.from({ length: 3 }).map((_, i) => (
        <span
          key={i}
          className={i < clamped ? "text-primary" : "text-qds-t3"}
          aria-hidden
        >
          ★
        </span>
      ))}
    </span>
  );
}

export function ExploreResult({ result, costBps }: ExploreResultProps) {
  const label = ratingLabel(result.rating);

  /* ------------------------------------------------------------------ */
  /*  Summary KPI row — 4 tiles                                          */
  /* ------------------------------------------------------------------ */
  return (
    <>
      <SectionLabel>
        探索结果 · {result.factor_name}
      </SectionLabel>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <StatCard
          label="IC̄"
          value={result.ic_mean.toFixed(4)}
          sub={`std ${result.ic_std.toFixed(4)}`}
          trend={result.ic_mean >= 0 ? "up" : "down"}
          help="IC 均值，Spearman 秩相关"
        />
        <StatCard
          label="IR"
          value={result.ir.toFixed(2)}
          sub={`t = ${result.ic_tstat.toFixed(2)}`}
          trend={result.ir >= 0.5 ? "up" : result.ir < 0 ? "down" : undefined}
          help="信息比率 = IC̄ / IC Std"
        />
        <StatCard
          label="IC > 0"
          value={`${result.ic_positive_pct.toFixed(1)}%`}
          sub={
            result.half_life != null
              ? `半衰期 ${result.half_life} bars`
              : "半衰期 —"
          }
          help="IC 为正期数占比"
        />
        <div className="rounded-lg border bg-card p-4 flex flex-col gap-1">
          <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
            评级 · Rating
            <HelpTip text="综合 IR / 单调性 / 换手 的 0-3 分评级" />
          </div>
          <div className="flex items-center gap-2 mt-1">
            <RatingStars rating={result.rating} />
            <span
              className={cn(
                "font-mono text-[0.72rem] font-semibold",
                label === "strong" && "text-qds-success",
                label === "usable" && "text-qds-warning",
                label === "weak" && "text-destructive",
              )}
            >
              {label === "strong" ? "强" : label === "usable" ? "可用" : "弱"}
            </span>
          </div>
          <span className="font-mono text-[0.62rem] text-muted-foreground mt-0.5">
            {result.is_monotonic ? "分位单调 ✓" : "分位不单调"}
          </span>
        </div>
      </div>

      {/* Summary table — row per factor (we only have 1 in explore, but the
          tabular layout keeps parity with the multi-factor research list) */}
      <div className="rounded-lg border bg-card overflow-hidden mb-5">
        <table className="w-full font-mono text-[0.72rem]">
          <thead className="bg-background border-b">
            <tr className="text-muted-foreground">
              <th className="text-left px-3 py-2 font-medium">因子</th>
              <th className="text-right px-3 py-2 font-medium">IC̄</th>
              <th className="text-right px-3 py-2 font-medium">IC Std</th>
              <th className="text-right px-3 py-2 font-medium">IR</th>
              <th className="text-right px-3 py-2 font-medium">IC&gt;0</th>
              <th className="text-right px-3 py-2 font-medium">评级</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="px-3 py-2 text-foreground">{result.factor_name}</td>
              <td className="px-3 py-2 text-right font-semibold">
                {result.ic_mean.toFixed(4)}
              </td>
              <td className="px-3 py-2 text-right text-muted-foreground">
                {result.ic_std.toFixed(4)}
              </td>
              <td
                className={cn(
                  "px-3 py-2 text-right font-semibold",
                  irBand(result.ir),
                )}
              >
                {result.ir.toFixed(2)}
              </td>
              <td className="px-3 py-2 text-right">
                {result.ic_positive_pct.toFixed(1)}%
              </td>
              <td className="px-3 py-2 text-right">
                <RatingStars rating={result.rating} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Chart grid: IC timeseries + Quantile PnL on row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="IC 时序"
            tip="每期的 Spearman 秩相关系数，衡量因子排序和未来收益排序的一致性"
            sub="Spearman Rank IC"
          />
          <CardContent className="p-3">
            <ICLineChart data={result.ic_series} />
          </CardContent>
        </Card>

        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="分位平均收益"
            tip="按因子值分位后各组的平均 forward return，理想情况单调递增或递减"
            sub="Quantile Avg Return"
          />
          <CardContent className="p-3">
            <QuantilePnlChart quantilePnl={result.quantile_pnl} />
          </CardContent>
        </Card>
      </div>

      {/* Chart grid: Distribution on row 2 (full width when ic_decay is empty) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-3">
        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="因子分布"
            tip="因子值的频率分布，理想因子接近正态且无极端尖峰"
            sub={
              result.distribution_stats.mean != null
                ? `μ=${result.distribution_stats.mean.toFixed(3)}`
                : "Histogram"
            }
          />
          <CardContent className="p-3">
            <DistributionChart
              histogram={result.distribution_histogram}
              stats={result.distribution_stats}
            />
          </CardContent>
        </Card>

        <Card padding={false} className="overflow-hidden">
          <ChartHeader
            title="IC 衰减"
            tip="因子对不同 lag 的预测力，衰减越慢说明信号持续性越好"
            sub="IC Decay"
          />
          <CardContent className="p-3">
            {result.ic_decay && result.ic_decay.length > 0 ? (
              <ICLineChart
                data={result.ic_decay.map((d) => ({
                  date: `lag ${d.lag}`,
                  ic: d.ic,
                }))}
              />
            ) : (
              <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
                本次 explore 未产出 IC 衰减
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Turnover aggregate panel */}
      <TurnoverChart
        turnover={result.turnover}
        turnoverAnnualized={result.turnover_annualized}
        feeDragMonthly={result.fee_drag_monthly}
        costBps={costBps}
      />
    </>
  );
}
