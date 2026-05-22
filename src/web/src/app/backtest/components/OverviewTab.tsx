"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";

import { API_BASE } from "@/lib/api";
import { OverviewEquitySvg } from "./OverviewEquitySvg";
import type { BacktestResult } from "../types";
import {
  CARD_CLS,
  CARD_BODY_CLS,
  SEC_CLS,
  SectionLabel,
  StatRow,
  fmt,
  fmtCurrency,
} from "./OverviewHelpers";
import { VIEW_BTN_CLS } from "./backtestStyles";
import { OverviewKpiGrid } from "./OverviewKpiGrid";
import { MonthlyHeatmap } from "./OverviewMonthlyHeatmap";
import { WinLossBar, LongShortBar } from "./OverviewDistributionBars";
import {
  TopTrades,
  DrawdownTable,
  InstrumentBreakdown,
  CorrelationMatrix,
} from "./OverviewTradeTables";

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

interface OverviewTabProps {
  runId: string;
  onViewAllTrades?: () => void;
}

export function OverviewTab({ runId, onViewAllTrades }: OverviewTabProps) {
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
        if (!cancelled) setResult(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
        <div className="grid grid-cols-6 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-5">
          <Skeleton className="h-64 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
        <Skeleton className="h-48 rounded-lg" />
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-destructive">
          {error ?? "加载结果失败"}
        </span>
      </div>
    );
  }

  const { statistics: s, equity_curve, trade_log, per_instrument, monthly_returns, drawdown_periods, instrument_correlation, portfolio_analytics } = result;

  const hasMultiInst = per_instrument && Object.keys(per_instrument).length > 1;

  const totalPnl = s.total_pnl;
  const totalRetPct = s.total_return_pct;
  const isPnlPositive = totalPnl >= 0;

  const maxDdPct = s.max_drawdown !== null ? Math.abs(s.max_drawdown) * 100 : null;

  return (
    <div className="flex flex-col gap-5 p-4">

      {/* 1. Core + secondary KPI metrics */}
      <OverviewKpiGrid
        s={s}
        totalPnl={totalPnl}
        totalRetPct={totalRetPct}
        isPnlPositive={isPnlPositive}
        maxDdPct={maxDdPct}
      />

      {/* 3. Equity & Drawdown — self-drawn SVG */}
      <div className="mt-5">
        <SectionLabel>Equity &amp; Drawdown</SectionLabel>
        <div className={CARD_CLS}>
          <div className={CARD_BODY_CLS}>
            <OverviewEquitySvg data={equity_curve} />
          </div>
        </div>
      </div>

      {/* 4. Monthly Returns Heatmap + Drawdown — side by side */}
      {(monthly_returns && monthly_returns.length > 0 || drawdown_periods && drawdown_periods.length > 0) && (
        <div className="grid grid-cols-[1.4fr_1fr] gap-5 mt-5">
          {/* Left: Monthly Heatmap */}
          {monthly_returns && monthly_returns.length > 0 ? (
            <div className={SEC_CLS}>
              <SectionLabel>Monthly Returns</SectionLabel>
              <div className={CARD_CLS}>
                <div className={CARD_BODY_CLS}>
                  <MonthlyHeatmap data={monthly_returns} />
                </div>
              </div>
            </div>
          ) : <div />}
          {/* Right: Drawdown top 4 */}
          {drawdown_periods && drawdown_periods.length > 0 && (
            <DrawdownTable periods={drawdown_periods} topN={4} />
          )}
        </div>
      )}

      {/* 5. Win/Loss + Long/Short Bars */}
      <div className="grid grid-cols-2 gap-3">
        <WinLossBar s={s} />
        <LongShortBar s={s} />
      </div>

      {/* 6. Expanded Trade & PnL Statistics */}
      <div className="grid grid-cols-3 gap-3">
        {/* Trade Stats */}
        <div className={CARD_CLS}>
          <div className="px-4 py-2 border-b border-border text-xs font-medium text-muted-foreground">
            交易统计
          </div>
          <div className={`${CARD_BODY_CLS} flex flex-col gap-0.5`}>
            <StatRow label="总交易" value={String(s.total_trades)} />
            <StatRow label="盈利笔数" value={String(s.winning_trades)} color="text-qds-success" />
            <StatRow label="亏损笔数" value={String(s.losing_trades)} color="text-destructive" />
            <StatRow label="总订单" value={String(s.total_orders)} />
            <StatRow label="已成交" value={String(s.filled_orders)} />
            <StatRow label="未平仓" value={String(s.open_positions)} />
            <StatRow label="胜率" value={`${fmt(s.win_rate * 100, 1)}%`}
              color={s.win_rate >= 0.5 ? "text-qds-success" : "text-destructive"} />
            <StatRow label="盈亏比" value={fmt(s.profit_factor, 2)}
              color={s.profit_factor !== null && s.profit_factor >= 1 ? "text-qds-success" : "text-destructive"} />
          </div>
        </div>

        {/* PnL Stats */}
        <div className={CARD_CLS}>
          <div className="px-4 py-2 border-b border-border text-xs font-medium text-muted-foreground">
            收益统计
          </div>
          <div className={`${CARD_BODY_CLS} flex flex-col gap-0.5`}>
            <StatRow label="总盈利" value={fmtCurrency(s.gross_profit)} color="text-qds-success" />
            <StatRow label="总亏损" value={fmtCurrency(s.gross_loss)} color="text-destructive" />
            <StatRow label="总手续费" value={fmtCurrency(s.total_fees)} />
            <StatRow label="最大单笔盈利" value={fmtCurrency(s.largest_win)} color="text-qds-success" />
            <StatRow label="最大单笔亏损" value={fmtCurrency(s.largest_loss)} color="text-destructive" />
            <StatRow label="平均盈利" value={fmtCurrency(s.avg_win)} color="text-qds-success" />
            <StatRow label="平均亏损" value={fmtCurrency(s.avg_loss)} color="text-destructive" />
            <StatRow label="期望值" value={fmtCurrency(s.expectancy)} />
          </div>
        </div>

        {/* Holding & Streaks */}
        <div className={CARD_CLS}>
          <div className="px-4 py-2 border-b border-border text-xs font-medium text-muted-foreground">
            持仓与连续
          </div>
          <div className={`${CARD_BODY_CLS} flex flex-col gap-0.5`}>
            <StatRow label="平均持仓" value={s.avg_holding_time ?? "—"} />
            <StatRow label="盈利持仓" value={s.avg_winning_holding_time ?? "—"} />
            <StatRow label="亏损持仓" value={s.avg_losing_holding_time ?? "—"} />
            <StatRow label="盈亏比率" value={fmt(s.avg_win_loss_ratio, 2)} />
            <StatRow label="最长连胜" value={`${s.winning_streak} 笔`} color="text-qds-success" />
            <StatRow label="最长连负" value={`${s.losing_streak} 笔`} color="text-destructive" />
            {hasMultiInst && portfolio_analytics?.diversification_ratio && (
              <>
                <StatRow label="分散化比率" value={fmt(portfolio_analytics.diversification_ratio, 2)} color="text-primary" />
                <StatRow label="分散化收益" value={`${fmt(portfolio_analytics.diversification_benefit_pct, 1)}%`} color="text-primary" />
              </>
            )}
          </div>
        </div>
      </div>

      {/* 7. View all trades button */}
      <div className="flex justify-end">
        <button type="button" className={VIEW_BTN_CLS} onClick={() => onViewAllTrades?.()}>
          查看所有交易 →
        </button>
      </div>

      {/* 8. Top Trades */}
      <TopTrades tradeLog={trade_log} />

      {/* 9. Instrument Breakdown */}
      {per_instrument && Object.keys(per_instrument).length > 0 && (
        <InstrumentBreakdown data={per_instrument} />
      )}

      {/* 10. Correlation Matrix */}
      {hasMultiInst && instrument_correlation && Object.keys(instrument_correlation).length > 0 && (
        <CorrelationMatrix data={instrument_correlation} />
      )}
    </div>
  );
}
