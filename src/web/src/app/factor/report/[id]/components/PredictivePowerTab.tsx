"use client";

import { StatCard } from "@/components/qds";
import { ICLineChart } from "@/app/factor/components/charts/ICLineChart";
import { ChartPanel } from "./ChartPanel";
import { ICDecayChart } from "./ICDecayChart";
import type { EvalResultPayload } from "./types";
import { trendFromValue } from "./types";

interface PredictivePowerTabProps {
  result: EvalResultPayload;
}

/**
 * Tab 2 — Predictive Power.
 *
 * Core IC diagnostics: mean IC, volatility, IR, significance, decay.
 *
 * Data mapping (EvalResult fields):
 *   - ic_mean / ic_std / ir / ic_tstat / ic_positive_pct / ic_max_abs
 *   - half_life — IC decay half-life in bars (null when undefined)
 *   - ic_series[] — {date, ic} per period
 *   - ic_decay[]  — {lag, ic} for horizon sweep
 *
 * Reuses ICLineChart from s18 for the time series; ICDecayChart is new
 * (bar chart with semantic green/red per sign, similar to QuantilePnlChart).
 */
export function PredictivePowerTab({ result }: PredictivePowerTabProps) {
  const icTrend = trendFromValue(result.ic_mean);
  const tstatTrend = Math.abs(result.ic_tstat) >= 2 ? "up" : undefined;
  const irTrend =
    result.ir >= 0.5 ? "up" : result.ir < 0 ? "down" : undefined;

  return (
    <div
      className="animate-qds-fade-up"
      data-testid="factor-report-tab-predict"
    >
      {/* KPI row — 6 tiles */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-5">
        <StatCard
          label="IC̄"
          value={result.ic_mean.toFixed(4)}
          sub={`std ${result.ic_std.toFixed(4)}`}
          trend={icTrend}
          help="IC 均值（Spearman 秩相关），越远离 0 越有预测力"
        />
        <StatCard
          label="IR"
          value={result.ir.toFixed(2)}
          sub={`|IC| max ${result.ic_max_abs.toFixed(3)}`}
          trend={irTrend}
          help="信息比率 IC̄ / IC_std，>=0.5 属可用"
        />
        <StatCard
          label="t-stat"
          value={result.ic_tstat.toFixed(2)}
          trend={tstatTrend}
          help="|t-stat| >= 2.0 表示 95% 置信度下显著"
        />
        <StatCard
          label="IC > 0"
          value={`${result.ic_positive_pct.toFixed(1)}%`}
          trend={result.ic_positive_pct >= 55 ? "up" : undefined}
          help="IC 为正期数占比，反映方向稳定性"
        />
        <StatCard
          label="半衰期"
          value={result.half_life != null ? `${result.half_life} bars` : "—"}
          help="IC 随 lag 衰减到一半所需的 bar 数"
        />
        <StatCard
          label="评级"
          value={
            result.rating >= 3
              ? "强"
              : result.rating >= 2
                ? "可用"
                : result.rating >= 1
                  ? "弱"
                  : "无"
          }
          trend={
            result.rating >= 3
              ? "up"
              : result.rating >= 2
                ? undefined
                : "down"
          }
          help="综合 IR / 单调性 / 换手的 0-3 分评级"
        />
      </div>

      {/* Row 1: IC time series (full width) */}
      <div className="mb-4">
        <ChartPanel
          title="IC 时序"
          tip="每期的 Spearman 秩相关系数；正负交替越频繁信号越弱。"
          sub="Spearman Rank IC"
          testId="factor-report-chart-ic-series"
        >
          <ICLineChart data={result.ic_series} height={260} />
        </ChartPanel>
      </div>

      {/* Row 2: IC decay + IC summary table */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartPanel
          title="IC 衰减"
          tip="因子对不同 lag 的预测力；衰减越慢持续性越好。"
          sub="IC Decay"
          testId="factor-report-chart-ic-decay"
        >
          <ICDecayChart data={result.ic_decay} />
        </ChartPanel>

        <ChartPanel
          title="IC 概览"
          sub="Summary"
          testId="factor-report-chart-ic-summary"
        >
          <div className="pt-2">
            <table className="w-full font-mono text-[0.72rem]">
              <tbody>
                <ICRow label="IC̄" value={result.ic_mean.toFixed(4)} />
                <ICRow label="IC Std" value={result.ic_std.toFixed(4)} />
                <ICRow
                  label="IR"
                  value={result.ir.toFixed(3)}
                  colored={result.ir >= 0.5 ? "pos" : "neg"}
                />
                <ICRow
                  label="t-stat"
                  value={result.ic_tstat.toFixed(3)}
                  colored={Math.abs(result.ic_tstat) >= 2 ? "pos" : "neg"}
                />
                <ICRow
                  label="|IC| max"
                  value={result.ic_max_abs.toFixed(4)}
                />
                <ICRow
                  label="IC > 0 占比"
                  value={`${result.ic_positive_pct.toFixed(1)}%`}
                />
                <ICRow
                  label="半衰期"
                  value={
                    result.half_life != null
                      ? `${result.half_life} bars`
                      : "—"
                  }
                />
              </tbody>
            </table>
          </div>
        </ChartPanel>
      </div>
    </div>
  );
}

/**
 * Single row in the IC summary table.  Uses Tailwind semantic colour classes
 * only — green for strongly positive indicators, red when below threshold.
 */
function ICRow({
  label,
  value,
  colored,
}: {
  label: string;
  value: string;
  colored?: "pos" | "neg";
}) {
  return (
    <tr className="border-b last:border-b-0 hover:bg-secondary transition-colors duration-150">
      <td className="px-4 py-2 text-muted-foreground">{label}</td>
      <td
        className={
          colored === "pos"
            ? "px-4 py-2 text-right font-semibold text-qds-success"
            : colored === "neg"
              ? "px-4 py-2 text-right font-semibold text-destructive"
              : "px-4 py-2 text-right font-medium"
        }
      >
        {value}
      </td>
    </tr>
  );
}
