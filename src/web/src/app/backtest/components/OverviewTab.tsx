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
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { API_BASE } from "@/lib/api";
import { useCountUp } from "@/hooks/useCountUp";
import type { BacktestResult, MonthlyReturn, DrawdownPeriod, PerInstrumentEntry } from "../types";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function fmt(value: unknown, decimals: number, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return num.toFixed(decimals);
}

function fmtSigned(value: unknown, decimals: number, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return `${num >= 0 ? "+" : ""}${num.toFixed(decimals)}`;
}

function fmtCurrency(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const num = typeof value === "number" ? value : Number(value);
  if (isNaN(num)) return fallback;
  return `$${num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function stripVenue(s: string): string {
  return s.replace(/\.BINANCE$/i, "");
}

const pnlColor = (v: number) => (v >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]");

/* ------------------------------------------------------------------ */
/*  Section label                                                      */
/* ------------------------------------------------------------------ */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Card wrapper                                                       */
/* ------------------------------------------------------------------ */

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4 ${className}`}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Stat row (label + value)                                           */
/* ------------------------------------------------------------------ */

function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-xs text-[var(--text-muted)]">{label}</span>
      <span className={`text-xs font-medium ${color ?? "text-[var(--text-secondary)]"}`}>{value}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  KPI Card                                                           */
/* ------------------------------------------------------------------ */

interface KpiCardProps {
  label: string;
  value: number | null;
  format: "pct" | "number" | "ratio" | "currency";
  positive?: boolean | null;
  prefix?: string;
  suffix?: string;
  showSign?: boolean;
  small?: boolean;
}

function KpiCard({ label, value, format, positive, prefix = "", suffix = "", showSign = true, small }: KpiCardProps) {
  const numeric = value ?? 0;
  const animated = useCountUp(numeric, 700, value !== null);

  const formatted = (() => {
    if (value === null) return "N/A";
    switch (format) {
      case "pct":
        return showSign ? `${fmtSigned(animated, 2)}%` : `${fmt(animated, 2)}%`;
      case "ratio":
        return fmt(animated, 2);
      case "number":
        return Math.round(animated).toLocaleString();
      case "currency":
        return `$${Number(animated).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
  })();

  const colorClass =
    positive === null || positive === undefined
      ? "text-[var(--text-primary)]"
      : positive
      ? "text-[var(--accent-green)]"
      : "text-[var(--accent-red)]";

  return (
    <div className="flex flex-col gap-1.5 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
      <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
        {label}
      </span>
      <span className={`${small ? "text-lg" : "text-2xl"} font-bold font-heading tracking-tight ${colorClass}`}>
        {prefix}{formatted}{suffix}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Hero PnL Banner                                                    */
/* ------------------------------------------------------------------ */

function HeroBanner({ s }: { s: BacktestResult["statistics"] }) {
  const pnl = s.total_pnl;
  const retPct = s.total_return_pct;
  const isPositive = pnl >= 0;

  // Parse final balance (e.g. "10114.60 USDT" → 10114.60)
  const finalBal = s.final_balance
    ? parseFloat(s.final_balance.split(" ")[0])
    : null;

  return (
    <Card className="flex items-center justify-between">
      <div className="flex flex-col gap-1">
        <SectionLabel>总盈亏</SectionLabel>
        <div className="flex items-baseline gap-3">
          <span className={`text-3xl font-bold font-heading tracking-tight ${isPositive ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>
            {fmtSigned(pnl, 2)} USDT
          </span>
          <span className={`text-sm font-semibold ${isPositive ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>
            ({fmtSigned(retPct, 2)}%)
          </span>
        </div>
      </div>
      {finalBal !== null && (
        <div className="flex flex-col items-end gap-1">
          <SectionLabel>最终余额</SectionLabel>
          <span className="text-xl font-bold font-heading text-[var(--text-primary)]">
            {fmtCurrency(finalBal)}
          </span>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Win/Loss Distribution Bar                                          */
/* ------------------------------------------------------------------ */

function WinLossBar({ s }: { s: BacktestResult["statistics"] }) {
  const total = s.winning_trades + s.losing_trades;
  if (total === 0) return null;
  const winPct = (s.winning_trades / total) * 100;

  return (
    <Card className="flex flex-col gap-3">
      <SectionLabel>胜负分布</SectionLabel>
      <div className="flex items-center gap-3">
        <span className="text-xs font-medium text-[var(--accent-green)]">{s.winning_trades}W</span>
        <div className="flex-1 h-3 rounded-full overflow-hidden bg-[var(--accent-red)]/30 flex">
          <div
            className="h-full rounded-l-full bg-[var(--accent-green)]"
            style={{ width: `${winPct}%` }}
          />
          <div className="h-full flex-1 bg-[var(--accent-red)]" />
        </div>
        <span className="text-xs font-medium text-[var(--accent-red)]">{s.losing_trades}L</span>
      </div>
      <div className="flex items-center justify-between text-[10px] text-[var(--text-muted)]">
        <span>总计 {total} 笔 · 胜率 {fmt(winPct, 1)}%</span>
        <span>连胜 {s.winning_streak} / 连负 {s.losing_streak}</span>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Long/Short Distribution Bar                                        */
/* ------------------------------------------------------------------ */

function LongShortBar({ s }: { s: BacktestResult["statistics"] }) {
  const longPct = s.long_pct !== null ? s.long_pct * 100 : null;
  const shortPct = s.short_pct !== null ? s.short_pct * 100 : null;
  if (longPct === null || shortPct === null) return null;

  return (
    <Card className="flex flex-col gap-3">
      <SectionLabel>多空分布</SectionLabel>
      <div className="flex items-center gap-3">
        <span className="text-xs font-medium text-[var(--accent-blue)]">做多</span>
        <div className="flex-1 h-3 rounded-full overflow-hidden flex">
          <div
            className="h-full rounded-l-full bg-[var(--accent-blue)]"
            style={{ width: `${longPct}%` }}
          />
          <div className="h-full flex-1 bg-[#D4A843]" />
        </div>
        <span className="text-xs font-medium text-[#D4A843]">做空</span>
      </div>
      <div className="flex items-center justify-between text-[10px] text-[var(--text-muted)]">
        <span>做多 {fmt(longPct, 1)}%</span>
        <span>做空 {fmt(shortPct, 1)}%</span>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Monthly Returns Heatmap (from backend data)                        */
/* ------------------------------------------------------------------ */

const MONTHS = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

function MonthlyHeatmap({ data }: { data: MonthlyReturn[] }) {
  // Group by year → month
  const map: Record<number, Record<number, number>> = {};
  for (const item of data) {
    const [yearStr, monthStr] = item.period.split("-");
    const year = Number(yearStr);
    const month = Number(monthStr) - 1; // 0-based
    if (!map[year]) map[year] = {};
    map[year][month] = item.return_pct;
  }

  const years = Object.keys(map).map(Number).sort((a, b) => a - b);
  if (years.length === 0) return null;

  let maxAbs = 0;
  for (const yr of years) {
    for (let m = 0; m < 12; m++) {
      const v = Math.abs(map[yr]?.[m] ?? 0);
      if (v > maxAbs) maxAbs = v;
    }
  }

  const cellColor = (val: number | undefined) => {
    if (val === undefined || val === 0) return "var(--bg-subtle)";
    const ratio = Math.min(Math.abs(val) / (maxAbs || 1), 1);
    if (val > 0) {
      const g = Math.round(80 + ratio * 137);
      return `rgba(38, ${g}, 127, 0.7)`;
    } else {
      const r = Math.round(180 + ratio * 55);
      return `rgba(${r}, 83, 80, 0.7)`;
    }
  };

  return (
    <TooltipProvider>
      <div className="flex flex-col gap-2">
        <SectionLabel>月度收益热力图</SectionLabel>
        <div className="overflow-x-auto">
          <table className="text-[10px] border-separate border-spacing-0.5">
            <thead>
              <tr>
                <th className="w-12 text-left text-[var(--text-muted)] font-medium pr-2">年份</th>
                {MONTHS.map((m) => (
                  <th key={m} className="w-10 text-center text-[var(--text-muted)] font-medium">{m}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {years.map((yr) => (
                <tr key={yr}>
                  <td className="text-[var(--text-secondary)] pr-2">{yr}</td>
                  {Array.from({ length: 12 }, (_, m) => {
                    const val = map[yr]?.[m];
                    return (
                      <td
                        key={m}
                        style={{ backgroundColor: cellColor(val) }}
                        className="rounded text-center h-6 cursor-default"
                      >
                        {val !== undefined ? (
                          <Tooltip>
                            <TooltipTrigger className="w-full h-full flex items-center justify-center">
                              <span className={val >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                                {val >= 0 ? "+" : ""}{Math.abs(val) > 99 ? `${fmt(val, 0)}` : fmt(val, 1)}%
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              <span className="font-medium">{yr}年{MONTHS[m]}</span>
                              <span className={val >= 0 ? "text-green-400" : "text-red-400"}>
                                {" "}{fmtSigned(val, 2)}%
                              </span>
                            </TooltipContent>
                          </Tooltip>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------ */
/*  Top Trades                                                         */
/* ------------------------------------------------------------------ */

function TopTrades({ tradeLog }: { tradeLog: BacktestResult["trade_log"] }) {
  if (!tradeLog || tradeLog.length < 2) return null;

  const sorted = [...tradeLog].sort((a, b) => Number(b.realized_pnl) - Number(a.realized_pnl));
  const best = sorted.slice(0, 3).filter((t) => Number(t.realized_pnl) > 0);
  const worst = sorted.slice(-3).reverse().filter((t) => Number(t.realized_pnl) < 0);

  if (best.length === 0 && worst.length === 0) return null;

  const renderTrade = (t: (typeof tradeLog)[0], idx: number) => {
    const pnl = Number(t.realized_pnl);
    return (
      <div key={idx} className="flex items-center justify-between py-1 border-b border-[var(--border-gray)]/30 last:border-0">
        <div className="flex items-center gap-2">
          <span className="text-xs text-[var(--accent-blue)] font-medium w-28 truncate">{stripVenue(t.instrument)}</span>
          <span className={`text-[10px] font-medium ${t.side === "BUY" ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>
            {t.side}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-xs font-semibold ${pnlColor(pnl)}`}>{fmtSigned(pnl, 2)}</span>
          {t.duration && <span className="text-[10px] text-[var(--text-muted)]">{t.duration}</span>}
        </div>
      </div>
    );
  };

  return (
    <Card className="flex flex-col gap-3">
      <SectionLabel>标志性交易</SectionLabel>
      <div className="grid grid-cols-2 gap-4">
        {best.length > 0 && (
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-medium text-[var(--accent-green)] mb-1">最佳交易</span>
            {best.map(renderTrade)}
          </div>
        )}
        {worst.length > 0 && (
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-medium text-[var(--accent-red)] mb-1">最差交易</span>
            {worst.map(renderTrade)}
          </div>
        )}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Notable Drawdowns                                                  */
/* ------------------------------------------------------------------ */

function DrawdownTable({ periods }: { periods: DrawdownPeriod[] }) {
  if (!periods || periods.length === 0) return null;
  const top5 = periods.slice(0, 5);

  return (
    <Card className="flex flex-col gap-2">
      <SectionLabel>显著回撤</SectionLabel>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--border-gray)] text-[var(--text-muted)]">
              <th className="text-left py-1.5 pr-3 font-medium">开始日期</th>
              <th className="text-right py-1.5 pr-3 font-medium">最大回撤</th>
              <th className="text-right py-1.5 pr-3 font-medium">持续天数</th>
              <th className="text-right py-1.5 font-medium">恢复</th>
            </tr>
          </thead>
          <tbody>
            {top5.map((dd, i) => (
              <tr key={i} className="border-b border-[var(--border-gray)]/30">
                <td className="py-1.5 pr-3 text-[var(--text-secondary)]">{dd.start.slice(0, 10)}</td>
                <td className="py-1.5 pr-3 text-right text-[var(--accent-red)] font-medium">
                  {fmt(dd.max_drawdown_pct, 2)}%
                </td>
                <td className="py-1.5 pr-3 text-right text-[var(--text-secondary)]">{dd.duration_days}天</td>
                <td className="py-1.5 text-right">
                  {dd.recovery_days !== null ? (
                    <span className="text-[var(--accent-green)]">{dd.recovery_days}天</span>
                  ) : (
                    <span className="text-[var(--text-muted)]">未恢复</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Per-Instrument Breakdown                                           */
/* ------------------------------------------------------------------ */

function InstrumentBreakdown({ data }: { data: Record<string, PerInstrumentEntry> }) {
  const entries = Object.entries(data)
    .map(([key, val]) => ({ ...val, instrument: stripVenue(key) }))
    .sort((a, b) => b.total_pnl - a.total_pnl);

  if (entries.length === 0) return null;

  // Max |PnL| for bar scaling
  const maxAbsPnl = Math.max(...entries.map((e) => Math.abs(e.total_pnl)), 1);

  return (
    <Card className="flex flex-col gap-2">
      <SectionLabel>品种分解</SectionLabel>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--border-gray)] text-[var(--text-muted)]">
              <th className="text-left py-2 pr-3 font-medium">品种</th>
              <th className="text-right py-2 pr-3 font-medium">交易数</th>
              <th className="text-right py-2 pr-3 font-medium">胜率</th>
              <th className="text-right py-2 pr-3 font-medium">盈亏</th>
              <th className="text-left py-2 pr-3 pl-2 font-medium w-24"></th>
              <th className="text-right py-2 pr-3 font-medium">夏普</th>
              <th className="text-right py-2 font-medium">最大回撤</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((row) => {
              const barWidth = Math.round((Math.abs(row.total_pnl) / maxAbsPnl) * 100);
              return (
                <tr key={row.instrument} className="border-b border-[var(--border-gray)]/30 hover:bg-[var(--bg-subtle)]/50">
                  <td className="py-1.5 pr-3 text-[var(--accent-blue)] font-medium">{row.instrument}</td>
                  <td className="py-1.5 pr-3 text-right text-[var(--text-secondary)]">{row.total_trades}</td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className={(row.win_rate ?? 0) >= 0.5 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                      {fmt((row.win_rate ?? 0) * 100, 1)}%
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className={pnlColor(row.total_pnl)}>
                      {fmtSigned(row.total_pnl, 2)}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 pl-2">
                    <div className="h-2 rounded-full overflow-hidden bg-[var(--bg-subtle)]">
                      <div
                        className={`h-full rounded-full ${row.total_pnl >= 0 ? "bg-[var(--accent-green)]" : "bg-[var(--accent-red)]"}`}
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </td>
                  <td className="py-1.5 pr-3 text-right text-[var(--text-secondary)]">{fmt(row.sharpe_ratio, 2)}</td>
                  <td className="py-1.5 text-right">
                    {row.max_drawdown !== null && Math.abs(row.max_drawdown) > 0.0001 ? (
                      <span className="text-[var(--accent-red)]">{fmt(Math.abs(row.max_drawdown) * 100, 1)}%</span>
                    ) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Correlation Matrix                                                 */
/* ------------------------------------------------------------------ */

function CorrelationMatrix({ data }: { data: Record<string, Record<string, number>> }) {
  const instruments = Object.keys(data).map(stripVenue);
  const rawKeys = Object.keys(data);
  if (instruments.length < 2) return null;

  // Collect all pairs
  const pairs: { a: string; b: string; corr: number }[] = [];
  for (let i = 0; i < rawKeys.length; i++) {
    for (let j = i + 1; j < rawKeys.length; j++) {
      const corr = data[rawKeys[i]]?.[rawKeys[j]];
      if (corr !== undefined) {
        pairs.push({ a: stripVenue(rawKeys[i]), b: stripVenue(rawKeys[j]), corr });
      }
    }
  }

  const sorted = [...pairs].sort((a, b) => b.corr - a.corr);
  const top = sorted.slice(0, 3);
  const bottom = [...pairs].sort((a, b) => a.corr - b.corr).slice(0, 3);

  const corrColor = (v: number) => {
    if (v > 0.7) return "text-[var(--accent-red)]";
    if (v > 0.3) return "text-[var(--text-secondary)]";
    if (v < 0) return "text-[var(--accent-green)]";
    return "text-[var(--accent-blue)]";
  };

  return (
    <Card className="flex flex-col gap-3">
      <SectionLabel>品种相关性</SectionLabel>
      <div className="grid grid-cols-2 gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-medium text-[var(--accent-red)] mb-1">高相关 (风险集中)</span>
          {top.map((p, i) => (
            <div key={i} className="flex items-center justify-between py-0.5">
              <span className="text-xs text-[var(--text-secondary)]">{p.a} / {p.b}</span>
              <span className={`text-xs font-medium ${corrColor(p.corr)}`}>{fmtSigned(p.corr, 3)}</span>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-medium text-[var(--accent-green)] mb-1">低相关 (分散化)</span>
          {bottom.map((p, i) => (
            <div key={i} className="flex items-center justify-between py-0.5">
              <span className="text-xs text-[var(--text-secondary)]">{p.a} / {p.b}</span>
              <span className={`text-xs font-medium ${corrColor(p.corr)}`}>{fmtSigned(p.corr, 3)}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

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
        <Skeleton className="h-24 rounded-lg" />
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-48 rounded-lg" />
        <Skeleton className="h-32 rounded-lg" />
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-[var(--accent-red)]">
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

  return (
    <div className="flex flex-col gap-4 p-4">

      {/* 1. Hero PnL Banner */}
      <HeroBanner s={s} />

      {/* 2. KPI Grid */}
      <StaggerContainer className="grid grid-cols-3 gap-3" staggerDelay={0.05}>
        <StaggerItem>
          <KpiCard label="夏普比率" value={s.sharpe_ratio} format="ratio"
            positive={s.sharpe_ratio !== null ? s.sharpe_ratio >= 1 : null} />
        </StaggerItem>
        <StaggerItem>
          <KpiCard label="索提诺比率" value={s.sortino_ratio} format="ratio"
            positive={s.sortino_ratio !== null ? s.sortino_ratio >= 1 : null} />
        </StaggerItem>
        <StaggerItem>
          <KpiCard label="最大回撤" value={s.max_drawdown !== null ? Math.abs(s.max_drawdown) * 100 : null}
            format="pct" showSign={false}
            positive={s.max_drawdown !== null ? s.max_drawdown > -0.2 : null} />
        </StaggerItem>
        <StaggerItem>
          <KpiCard label="卡尔马比率" value={s.calmar_ratio} format="ratio"
            positive={s.calmar_ratio !== null ? s.calmar_ratio >= 1 : null} />
        </StaggerItem>
        <StaggerItem>
          <KpiCard label="年化收益" value={s.annual_return !== null ? s.annual_return * 100 : null}
            format="pct" showSign={true}
            positive={s.annual_return !== null ? s.annual_return >= 0 : null} />
        </StaggerItem>
        <StaggerItem>
          <KpiCard label="波动率" value={s.returns_volatility !== null ? s.returns_volatility * 100 : null}
            format="pct" showSign={false} positive={null} />
        </StaggerItem>
      </StaggerContainer>

      {/* 3. Equity Curve + Drawdown */}
      {chartData.length > 0 && (
        <Card className="flex flex-col gap-2">
          <SectionLabel>权益曲线</SectionLabel>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4C9EEB" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4C9EEB" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" />
              <XAxis dataKey="t" tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickLine={false} axisLine={false}
                tickFormatter={(v) => `$${fmt(Number(v) / 1000, 0, "0")}k`} width={48} />
              <RechartsTooltip
                contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-gray)", borderRadius: 8, fontSize: 11, color: "var(--text-primary)" }}
                formatter={(value: unknown) => [`$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`, "权益"]}
              />
              <ReferenceLine
                y={chartData[0]?.equity ?? 0}
                stroke="#D4A843"
                strokeDasharray="4 4"
                strokeWidth={1}
                label={{ value: `本金 $${fmt((chartData[0]?.equity ?? 0) / 1000, 0, "0")}k`, fill: "#D4A843", fontSize: 10, position: "insideTopLeft" }}
              />
              <Area type="monotone" dataKey="equity" stroke="#4C9EEB" strokeWidth={1.5} fill="url(#eqGrad)" dot={false} activeDot={{ r: 3, fill: "#4C9EEB" }} />
            </AreaChart>
          </ResponsiveContainer>

          {/* Drawdown chart */}
          <SectionLabel>回撤曲线</SectionLabel>
          <ResponsiveContainer width="100%" height={100}>
            <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
              <defs>
                <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#E5534B" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#E5534B" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" />
              <XAxis dataKey="t" tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" hide />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickLine={false} axisLine={false}
                tickFormatter={(v) => `${fmt(v, 0)}%`} width={48} />
              <RechartsTooltip
                contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-gray)", borderRadius: 8, fontSize: 11, color: "var(--text-primary)" }}
                formatter={(value: unknown) => [`${fmt(value, 2)}%`, "回撤"]}
              />
              <Area type="monotone" dataKey="drawdown" stroke="#E5534B" strokeWidth={1} fill="url(#ddGrad)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* 4. Win/Loss + Long/Short Bars */}
      <div className="grid grid-cols-2 gap-3">
        <WinLossBar s={s} />
        <LongShortBar s={s} />
      </div>

      {/* 5. Expanded Trade & PnL Statistics */}
      <div className="grid grid-cols-3 gap-3">
        {/* Trade Stats */}
        <Card className="flex flex-col gap-2">
          <SectionLabel>交易统计</SectionLabel>
          <div className="flex flex-col gap-0.5">
            <StatRow label="总交易" value={String(s.total_trades)} />
            <StatRow label="盈利笔数" value={String(s.winning_trades)} color="text-[var(--accent-green)]" />
            <StatRow label="亏损笔数" value={String(s.losing_trades)} color="text-[var(--accent-red)]" />
            <StatRow label="总订单" value={String(s.total_orders)} />
            <StatRow label="已成交" value={String(s.filled_orders)} />
            <StatRow label="未平仓" value={String(s.open_positions)} />
            <StatRow label="胜率" value={`${fmt(s.win_rate * 100, 1)}%`}
              color={s.win_rate >= 0.5 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"} />
            <StatRow label="盈亏比" value={fmt(s.profit_factor, 2)}
              color={s.profit_factor !== null && s.profit_factor >= 1 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"} />
          </div>
        </Card>

        {/* PnL Stats */}
        <Card className="flex flex-col gap-2">
          <SectionLabel>收益统计</SectionLabel>
          <div className="flex flex-col gap-0.5">
            <StatRow label="总盈利" value={fmtCurrency(s.gross_profit)} color="text-[var(--accent-green)]" />
            <StatRow label="总亏损" value={fmtCurrency(s.gross_loss)} color="text-[var(--accent-red)]" />
            <StatRow label="总手续费" value={fmtCurrency(s.total_fees)} />
            <StatRow label="最大单笔盈利" value={fmtCurrency(s.largest_win)} color="text-[var(--accent-green)]" />
            <StatRow label="最大单笔亏损" value={fmtCurrency(s.largest_loss)} color="text-[var(--accent-red)]" />
            <StatRow label="平均盈利" value={fmtCurrency(s.avg_win)} color="text-[var(--accent-green)]" />
            <StatRow label="平均亏损" value={fmtCurrency(s.avg_loss)} color="text-[var(--accent-red)]" />
            <StatRow label="期望值" value={fmtCurrency(s.expectancy)} />
          </div>
        </Card>

        {/* Holding & Streaks */}
        <Card className="flex flex-col gap-2">
          <SectionLabel>持仓与连续</SectionLabel>
          <div className="flex flex-col gap-0.5">
            <StatRow label="平均持仓" value={s.avg_holding_time ?? "—"} />
            <StatRow label="盈利持仓" value={s.avg_winning_holding_time ?? "—"} />
            <StatRow label="亏损持仓" value={s.avg_losing_holding_time ?? "—"} />
            <StatRow label="盈亏比率" value={fmt(s.avg_win_loss_ratio, 2)} />
            <StatRow label="最长连胜" value={`${s.winning_streak} 笔`} color="text-[var(--accent-green)]" />
            <StatRow label="最长连负" value={`${s.losing_streak} 笔`} color="text-[var(--accent-red)]" />
            {hasMultiInst && portfolio_analytics?.diversification_ratio && (
              <>
                <StatRow label="分散化比率" value={fmt(portfolio_analytics.diversification_ratio, 2)} color="text-[var(--accent-blue)]" />
                <StatRow label="分散化收益" value={`${fmt(portfolio_analytics.diversification_benefit_pct, 1)}%`} color="text-[var(--accent-blue)]" />
              </>
            )}
          </div>
        </Card>
      </div>

      {/* 6. Top Trades */}
      <TopTrades tradeLog={trade_log} />

      {/* 7. Monthly Returns Heatmap */}
      {monthly_returns && monthly_returns.length > 0 && (
        <Card>
          <MonthlyHeatmap data={monthly_returns} />
        </Card>
      )}

      {/* 8. Notable Drawdowns */}
      {drawdown_periods && drawdown_periods.length > 0 && (
        <DrawdownTable periods={drawdown_periods} />
      )}

      {/* 9. Per-Instrument Breakdown */}
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
