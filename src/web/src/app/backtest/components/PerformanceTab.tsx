"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import type { BacktestResult } from "../types";
import { LoadingSkeleton, MetricCard, SectionHeader } from "./PerformanceHelpers";
import { PerformanceEquityChart } from "./PerformanceEquityChart";
import {
  PerformanceDrawdownChart,
  PerformanceTop5DrawdownsTable,
} from "./PerformanceDrawdownChart";
import {
  PerformanceAnnualReturnsChart,
  PerformanceMonthlyHeatmap,
  PerformanceWeeklyReturnsChart,
} from "./PerformancePeriodChart";
import {
  PerformanceRollingBetaChart,
  PerformanceRollingReturnsChart,
  PerformanceRollingSharpeChart,
  PerformanceRollingSortinoChart,
  PerformanceRollingVolatilityChart,
} from "./PerformanceRollingChart";
import {
  PerformanceDistributionHistogram,
  PerformanceQQPlotChart,
} from "./PerformanceDistributionChart";
import { PerformanceMetricsSummary } from "./PerformanceMetricsSummary";

export function PerformanceTab({ runId }: { runId: string }) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);

    fetch(`${API_BASE}/api/backtest/${runId}/result`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setResult(data as BacktestResult);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) return <LoadingSkeleton />;

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-destructive">
          {error ?? "加载失败"}
        </span>
      </div>
    );
  }

  const {
    statistics: s,
    equity_curve,
    annual_returns,
    rolling_returns,
    returns_distribution,
    qq_plot_data,
    benchmark_equity_curve,
    monthly_returns,
    weekly_returns,
    drawdown_periods,
    rolling_sharpe,
    rolling_sortino,
    rolling_volatility,
    rolling_beta,
    benchmark_type,
  } = result;

  // Derive starting balance from first equity point
  const startingBalance =
    equity_curve?.length > 0
      ? equity_curve[0].equity
      : (() => {
          const finalEq = s.final_balance
            ? parseFloat(s.final_balance.split(" ")[0])
            : null;
          return finalEq != null ? finalEq - s.total_pnl : 10000;
        })();

  // Parse annual_return from statistics: comes as fraction (0.xx), convert to %
  const annualReturnPct =
    s.annual_return != null ? s.annual_return * 100 : null;

  const totalReturnPct = s.total_return_pct ?? 0;

  const volatilityPct =
    s.returns_volatility != null ? s.returns_volatility * 100 : null;

  const showBenchmarkRelative = benchmark_type !== "zero_line";

  return (
    <div className="flex flex-col gap-6 p-5">
      {/* ============================================================ */}
      {/* Section 2.1: 权益表现                                        */}
      {/* ============================================================ */}
      <div className="flex flex-col gap-4">
        <SectionHeader title="权益表现" index={0} />

        {/* Enhanced equity curve — full width */}
        <PerformanceEquityChart
          equityCurve={equity_curve}
          benchmarkCurve={benchmark_equity_curve}
          startingBalance={startingBalance}
        />

        {/* Drawdown chart */}
        <PerformanceDrawdownChart equityCurve={equity_curve} />

        {/* Top 5 Drawdowns table */}
        <PerformanceTop5DrawdownsTable drawdownPeriods={drawdown_periods} />

        {/* 5 metric cards */}
        <div className="grid grid-cols-5 gap-3">
          <MetricCard
            label="总收益率"
            tooltip="策略从开始到结束的累计百分比收益"
            value={totalReturnPct}
            suffix="%"
            showSign
            positive={totalReturnPct >= 0}
            index={0}
          />
          <MetricCard
            label="年化收益 CAGR"
            sublabel="复利年化增长率"
            tooltip="按复利计算的年化增长率，便于跨周期比较"
            value={annualReturnPct}
            suffix="%"
            showSign
            positive={annualReturnPct != null ? annualReturnPct >= 0 : null}
            index={1}
          />
          <MetricCard
            label="年化波动率"
            sublabel="日收益标准差 x sqrt(365)"
            tooltip="日收益率标准差 x sqrt(365)，衡量收益的波动程度"
            value={volatilityPct}
            suffix="%"
            positive={null}
            index={2}
          />
          <MetricCard
            label="最佳单日"
            tooltip="回测期间单日最高收益率"
            value={s.best_day ?? null}
            suffix="%"
            showSign
            positive={true}
            index={3}
          />
          <MetricCard
            label="最差单日"
            tooltip="回测期间单日最大亏损率"
            value={s.worst_day ?? null}
            suffix="%"
            showSign
            positive={false}
            index={4}
          />
        </div>
      </div>

      {/* ============================================================ */}
      {/* Section 2.2: 周期收益                                        */}
      {/* ============================================================ */}
      <div className="flex flex-col gap-4">
        <SectionHeader title="周期收益" index={1} />

        {/* Monthly heatmap — full width */}
        <PerformanceMonthlyHeatmap monthlyReturns={monthly_returns} />

        {/* Annual + Weekly in 2-col */}
        <div className="grid grid-cols-2 gap-3">
          <PerformanceAnnualReturnsChart annualReturns={annual_returns} />
          <PerformanceWeeklyReturnsChart weeklyReturns={weekly_returns} />
        </div>

        {/* 3 metric cards */}
        <div className="grid grid-cols-3 gap-3">
          <MetricCard
            label="最佳单月"
            tooltip="回测期间单月最高收益率"
            value={s.best_month ?? null}
            suffix="%"
            showSign
            positive={true}
            index={0}
          />
          <MetricCard
            label="最差单月"
            tooltip="回测期间单月最大亏损率"
            value={s.worst_month ?? null}
            suffix="%"
            showSign
            positive={false}
            index={1}
          />
          <MetricCard
            label="盈利月份"
            sublabel="月度收益为正的比例"
            tooltip="月度收益为正的月份占总月份的比例"
            value={s.positive_months_pct ?? null}
            suffix="%"
            decimals={1}
            positive={
              s.positive_months_pct != null
                ? s.positive_months_pct >= 50
                : null
            }
            index={2}
          />
        </div>
      </div>

      {/* ============================================================ */}
      {/* Section 2.3: 滚动分析                                        */}
      {/* ============================================================ */}
      <div className="flex flex-col gap-4">
        <SectionHeader title="滚动分析" index={2} />

        <div className="grid grid-cols-2 gap-3">
          <PerformanceRollingSharpeChart data={rolling_sharpe} />
          <PerformanceRollingSortinoChart data={rolling_sortino} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <PerformanceRollingVolatilityChart data={rolling_volatility} />
          {showBenchmarkRelative && (
            <PerformanceRollingBetaChart data={rolling_beta} />
          )}
        </div>

        {/* Rolling returns — full width */}
        <PerformanceRollingReturnsChart rollingReturns={rolling_returns} />
      </div>

      {/* ============================================================ */}
      {/* Section 2.4: 收益分布                                        */}
      {/* ============================================================ */}
      <div className="flex flex-col gap-4">
        <SectionHeader title="收益分布" index={3} />

        <div className="grid grid-cols-2 gap-3">
          <PerformanceDistributionHistogram
            distribution={returns_distribution}
            normalMean={s.normal_dist_mean}
            normalStd={s.normal_dist_std}
          />
          <PerformanceQQPlotChart qqData={qq_plot_data} />
        </div>
      </div>

      {/* ============================================================ */}
      {/* Section 2.5: Performance 指标汇总                             */}
      {/* ============================================================ */}
      <div className="flex flex-col gap-4">
        <SectionHeader title="Performance 指标汇总" index={4} />

        <PerformanceMetricsSummary
          statistics={s}
          drawdownPeriods={drawdown_periods}
          benchmarkType={benchmark_type}
        />
      </div>
    </div>
  );
}
