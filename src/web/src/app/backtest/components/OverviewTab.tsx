"use client";

import { useEffect, useState } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { Skeleton } from "@/components/ui/skeleton";

import { CHART_TOOLTIP_PROPS, CHART_GRID_STYLE, CHART_LABEL_STYLE } from "@/lib/chartTheme";
import { API_BASE } from "@/lib/api";
import type { BacktestResult } from "../types";
import {
  CARD_CLS,
  CARD_HEADER_CLS,
  CARD_BODY_CLS,
  BADGE_BASE_CLS,
  BADGE_G_CLS,
  BADGE_R_CLS,
  SEC_CLS,
  SectionLabel,
  StatRow,
  fmt,
  fmtSigned,
  fmtCurrency,
} from "./OverviewHelpers";
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
}

export function OverviewTab({ runId }: OverviewTabProps) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);

    fetch(`${API_BASE}/api/backtest/${runId}/result`, {
      headers: { ...(process.env.NEXT_PUBLIC_API_KEY ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY } : {}) },
    })
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

  // Equity + drawdown chart data — sample to ~200 points
  const maxPoints = 200;
  const step = equity_curve.length > maxPoints ? Math.ceil(equity_curve.length / maxPoints) : 1;
  const chartData = equity_curve
    .filter((_, i) => i % step === 0)
    .map((p) => ({
      t: new Date(p.timestamp ?? p.date ?? "").toLocaleDateString("zh-CN", { month: "short", day: "numeric" }),
      equity: p.equity,
      drawdown: p.drawdown_pct ?? 0,
    }));

  const totalPnl = s.total_pnl;
  const totalRetPct = s.total_return_pct;
  const isPnlPositive = totalPnl >= 0;

  const maxDdPct = s.max_drawdown !== null ? Math.abs(s.max_drawdown) * 100 : null;
  const chartMaxDd = chartData.length > 0
    ? Math.min(...chartData.map((d) => d.drawdown))
    : null;

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

      {/* 3. Equity & Drawdown — side by side */}
      {chartData.length > 0 && (
        <div className={SEC_CLS}>
          <SectionLabel>Equity &amp; Drawdown</SectionLabel>
          <div className="grid grid-cols-2 gap-5">
            {/* Equity Curve */}
            <div className={CARD_CLS}>
              <div className={CARD_HEADER_CLS}>
                <span>Equity Curve</span>
                <span className={`${BADGE_BASE_CLS} ${isPnlPositive ? BADGE_G_CLS : BADGE_R_CLS}`}>
                  {fmtSigned(totalRetPct, 1)}%
                </span>
              </div>
              <div className={CARD_BODY_CLS}>
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="var(--info)" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="var(--info)" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
                    <XAxis dataKey="t" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                    <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={false} axisLine={false}
                      tickFormatter={(v) => `$${fmt(Number(v) / 1000, 0, "0")}k`} width={48} />
                    <RechartsTooltip
                      {...CHART_TOOLTIP_PROPS}
                      formatter={(value: unknown) => [`$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`, "权益"]}
                    />
                    <ReferenceLine
                      y={chartData[0]?.equity ?? 0}
                      stroke="var(--warn)"
                      strokeDasharray="4 4"
                      strokeWidth={1}
                      label={{ ...CHART_LABEL_STYLE, value: `本金 $${fmt((chartData[0]?.equity ?? 0) / 1000, 0, "0")}k`, fill: "var(--warn)", position: "insideTopLeft" }}
                    />
                    <Area type="monotone" dataKey="equity" stroke="var(--info)" strokeWidth={1.5} fill="url(#eqGrad)" dot={false} activeDot={{ r: 3, fill: "var(--info)" }} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Drawdown */}
            <div className={CARD_CLS}>
              <div className={CARD_HEADER_CLS}>
                <span>Drawdown</span>
                <span className={`${BADGE_BASE_CLS} ${BADGE_R_CLS}`}>
                  Max {chartMaxDd !== null ? fmt(chartMaxDd, 1) : (maxDdPct !== null ? `-${fmt(maxDdPct, 1)}` : "—")}%
                </span>
              </div>
              <div className={CARD_BODY_CLS}>
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
                    <defs>
                      <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#E5534B" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#E5534B" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
                    <XAxis dataKey="t" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                    <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={false} axisLine={false}
                      tickFormatter={(v) => `${fmt(v, 1)}%`} width={48} />
                    <RechartsTooltip
                      {...CHART_TOOLTIP_PROPS}
                      formatter={(value: unknown) => [`${fmt(value, 2)}%`, "回撤"]}
                    />
                    <Area type="monotone" dataKey="drawdown" stroke="#E5534B" strokeWidth={1.5} fill="url(#ddGrad)" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 4. Monthly Returns Heatmap */}
      {monthly_returns && monthly_returns.length > 0 && (
        <div className={SEC_CLS}>
          <SectionLabel>Monthly Returns</SectionLabel>
          <div className={CARD_CLS}>
            <div className={CARD_BODY_CLS}>
              <MonthlyHeatmap data={monthly_returns} />
            </div>
          </div>
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
          <div className={CARD_HEADER_CLS}>交易统计</div>
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
          <div className={CARD_HEADER_CLS}>收益统计</div>
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
          <div className={CARD_HEADER_CLS}>持仓与连续</div>
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

      {/* 7. Top Trades */}
      <TopTrades tradeLog={trade_log} />

      {/* 8+9. Drawdowns + Instrument Breakdown — side by side */}
      <div className="grid grid-cols-2 gap-5">
        {drawdown_periods && drawdown_periods.length > 0 && (
          <DrawdownTable periods={drawdown_periods} />
        )}
        {per_instrument && Object.keys(per_instrument).length > 0 && (
          <InstrumentBreakdown data={per_instrument} />
        )}
      </div>

      {/* 10. Correlation Matrix */}
      {hasMultiInst && instrument_correlation && Object.keys(instrument_correlation).length > 0 && (
        <CorrelationMatrix data={instrument_correlation} />
      )}
    </div>
  );
}
