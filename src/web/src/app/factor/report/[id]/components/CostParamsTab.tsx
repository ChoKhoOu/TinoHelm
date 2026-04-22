"use client";

import { SectionLabel, StatCard } from "@/components/qds";
import { ChartPanel } from "./ChartPanel";
import { EdgeWaterfallChart } from "./EdgeWaterfallChart";
import { ParamsEcho } from "./ParamsEcho";
import type { EvalResultPayload } from "./types";
import { formatBps, formatPct } from "./types";

interface CostParamsTabProps {
  result: EvalResultPayload;
  config?: Record<string, unknown>;
}

/**
 * Tab 4 — Cost & Params.
 *
 * Two concerns in one tab (deliberately — both answer "will this factor
 * survive trading costs?"):
 *   1. **Edge waterfall** — gross IC edge → fees → slippage → net (bps).
 *   2. **Turnover stats** — daily/annual turnover + monthly fee drag.
 *   3. **Parameter echo** — the ``EvalConfig`` snapshot + factor params.
 *
 * The waterfall only renders when ``result.cost`` is populated
 * (i.e. ``evaluate_full()`` path).  Turnover stats are always available.
 */
export function CostParamsTab({ result, config }: CostParamsTabProps) {
  const cost = result.cost ?? {};
  const net = cost.net_edge_bps;
  const hasCost = Object.keys(cost).length > 0;

  const feeDragTrend =
    result.fee_drag_monthly > 0.02
      ? "down"
      : result.fee_drag_monthly < 0.005
        ? "up"
        : undefined;

  return (
    <div
      className="animate-qds-fade-up"
      data-testid="factor-report-tab-cost"
    >
      {/* Row 1: Edge waterfall + turnover KPI grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
        <ChartPanel
          title="Edge 瀑布"
          tip="毛收益 IC edge → 手续费 → 滑点 → 净收益（单位 bps）"
          sub={
            net != null ? (net > 0 ? "Net 为正" : "Net 为负") : "Waterfall"
          }
          testId="factor-report-chart-waterfall"
        >
          <EdgeWaterfallChart cost={cost} />
        </ChartPanel>

        <ChartPanel
          title="成本摘要"
          sub="Cost Summary"
          testId="factor-report-chart-cost-summary"
        >
          <div className="grid grid-cols-2 gap-3 pt-2">
            <StatCard
              label="毛收益"
              value={formatBps(cost.gross_edge_bps)}
              help="|IC| × 10000，粗略估算的毛 edge"
            />
            <StatCard
              label="手续费"
              value={formatBps(cost.fee_cost_bps)}
              trend="down"
              help="fee_rate × 2 × 10000 × 日换手"
            />
            <StatCard
              label="滑点"
              value={formatBps(cost.slippage_bps)}
              trend="down"
              help="slippage_bps × 日换手"
            />
            <StatCard
              label="净收益"
              value={formatBps(cost.net_edge_bps)}
              trend={
                net != null ? (net > 0 ? "up" : "down") : undefined
              }
              help="毛收益 − 手续费 − 滑点"
            />
          </div>
          {!hasCost && (
            <div className="mt-3 font-mono text-[0.62rem] text-muted-foreground leading-relaxed">
              成本瀑布需通过 ``POST /api/factor/run``
              以 ``full=true`` 运行，当前 run 仅含 explore 快照。
            </div>
          )}
        </ChartPanel>
      </div>

      {/* Row 2: Turnover KPI row */}
      <SectionLabel>Turnover · 换手与成本</SectionLabel>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <StatCard
          label="日均换手"
          value={formatPct(result.turnover)}
          sub="Daily turnover"
          help="每日分层组成员变化比例"
        />
        <StatCard
          label="年化换手"
          value={formatPct(result.turnover_annualized, 1)}
          sub="Annualized"
          help="日均换手 × 252"
        />
        <StatCard
          label="月度费损"
          value={formatPct(result.fee_drag_monthly, 3)}
          sub="Fee drag"
          trend={feeDragTrend}
          help="年化换手 × 单边费率 ÷ 12"
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
          sub={
            result.is_monotonic ? "分位单调 ✓" : "分位不单调"
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

      {/* Row 3: Params echo */}
      <SectionLabel>Parameters · 配置回显</SectionLabel>
      <ParamsEcho config={config} />
    </div>
  );
}
