"use client";

import { StatCard } from "@/components/qds";
import { SectionLabel } from "@/components/qds";
import { HelpTip } from "@/components/qds";

interface TurnoverChartProps {
  turnover: number;
  turnoverAnnualized: number;
  feeDragMonthly: number;
  costBps?: number;
}

/**
 * Turnover aggregate panel.
 *
 * The backend does not ship per-bar turnover series yet — only daily, annual,
 * and monthly-fee-drag scalars from ``compute_turnover``.  Rendering as a KPI
 * row (StatCard × 3) matches the backtest stat-row pattern and Web UI Kit
 * grid-4 KPI reference while keeping it visually a "chart panel".
 */
export function TurnoverChart({
  turnover,
  turnoverAnnualized,
  feeDragMonthly,
  costBps,
}: TurnoverChartProps) {
  const dailyPct = (turnover * 100).toFixed(2) + "%";
  const annualPct = (turnoverAnnualized * 100).toFixed(1) + "%";
  const feeDragPct = (feeDragMonthly * 100).toFixed(3) + "%";

  return (
    <section className="mt-5">
      <SectionLabel>Turnover · 换手与成本</SectionLabel>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="日均换手"
          value={dailyPct}
          sub="Daily turnover"
        />
        <StatCard
          label="年化换手"
          value={annualPct}
          sub="Annualized"
        />
        <StatCard
          label="月度费损"
          value={feeDragPct}
          sub={costBps != null ? `假设 ${costBps} bps` : "按默认费率"}
          trend={feeDragMonthly > 0 ? "down" : undefined}
        />
        <div className="rounded-lg border bg-card p-4">
          <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
            说明
            <HelpTip text="换手率越高，实盘交易成本越大；月度费损 = 年化换手 × 单边费率 ÷ 12" />
          </div>
          <div className="font-mono text-[0.72rem] mt-2 leading-relaxed text-qds-t1">
            换手 / 年化换手 衡量每日平均
            <br />
            分层组成员变化；月度费损
            <br />
            按 ``cost_bps`` 折算扣减。
          </div>
        </div>
      </div>
    </section>
  );
}
