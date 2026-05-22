"use client";

import { StatCard } from "@/components/qds";
import { DistributionChart } from "@/app/factor/components/charts/DistributionChart";
import { QuantilePnlChart } from "@/app/factor/components/charts/QuantilePnlChart";
import { ChartPanel } from "./ChartPanel";
import type { EvalResultPayload } from "./types";

interface SignalProfileTabProps {
  result: EvalResultPayload;
}

/**
 * Tab 1 — Signal Profile.
 *
 * Surfaces the factor's raw behaviour before asking predictive-power
 * questions: distribution shape, quantile-group returns, monotonicity.
 *
 * Data mapping (EvalResult fields):
 *   - distribution_stats.{mean, std, skew, kurt}
 *   - distribution_histogram[]
 *   - quantile_pnl (Record<Qk, avg_return>)
 *   - quantile_cum_returns (currently rendered implicitly via quantile_pnl)
 *   - is_monotonic (boolean flag)
 *
 * Reuses DistributionChart + QuantilePnlChart from s18 (``/factor/components/charts``)
 * so the explore → report transition is visually continuous.
 */
export function SignalProfileTab({ result }: SignalProfileTabProps) {
  const stats = result.distribution_stats ?? {};
  const mean = stats.mean ?? 0;
  const std = stats.std ?? 0;
  const skew = stats.skew ?? 0;
  const kurt = stats.kurt ?? 0;

  return (
    <div
      className="animate-qds-fade-up"
      data-testid="factor-report-tab-profile"
    >
      {/* KPI row — 5 tiles covering distribution + monotonicity */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
        <StatCard
          label="均值"
          value={Number.isFinite(mean) ? mean.toFixed(3) : "—"}
          help="因子值的算术平均；偏离 0 太远可能有偏"
        />
        <StatCard
          label="标准差"
          value={Number.isFinite(std) ? std.toFixed(3) : "—"}
          help="因子值的离散程度"
        />
        <StatCard
          label="偏度"
          value={Number.isFinite(skew) ? skew.toFixed(2) : "—"}
          trend={
            Math.abs(skew) > 3
              ? "down"
              : Math.abs(skew) < 0.5
                ? "up"
                : undefined
          }
          help="|skew| > 3 说明分布严重偏斜"
        />
        <StatCard
          label="峰度"
          value={Number.isFinite(kurt) ? kurt.toFixed(2) : "—"}
          help="峰度 > 3 表示相对正态分布更尖"
        />
        <StatCard
          label="单调性"
          value={result.is_monotonic ? "单调" : "非单调"}
          trend={result.is_monotonic ? "up" : "down"}
          help="分位平均收益是否随分位递增或递减"
        />
      </div>

      {/* Row 1: distribution + quantile PnL */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-3">
        <ChartPanel
          title="信号分布"
          tip="因子值的频率分布。理想因子接近正态且无极端尖峰，分布越健康信号越稳定。"
          sub={
            stats.mean != null
              ? `μ=${stats.mean.toFixed(3)}`
              : "Histogram"
          }
          testId="factor-report-chart-distribution"
        >
          <DistributionChart
            histogram={result.distribution_histogram}
            stats={result.distribution_stats}
          />
        </ChartPanel>

        <ChartPanel
          title="分位平均收益"
          tip="按因子值分位后各组的平均 forward return；理想情况单调递增或递减。"
          sub={result.is_monotonic ? "单调 ✓" : "非单调"}
          testId="factor-report-chart-quantile"
        >
          <QuantilePnlChart quantilePnl={result.quantile_pnl} />
        </ChartPanel>
      </div>
    </div>
  );
}
