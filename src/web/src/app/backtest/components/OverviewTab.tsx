"use client";

import { useEffect, useState } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { Skeleton } from "@/components/ui/skeleton";
import { API_BASE } from "@/lib/api";
import { useCountUp } from "@/hooks/useCountUp";
import type { TradeLogEntry, BacktestResult } from "../types";


/* ------------------------------------------------------------------ */
/*  KPI Card                                                           */
/* ------------------------------------------------------------------ */

interface KpiCardProps {
  label: string;
  value: number | null;
  format: "pct" | "number" | "ratio" | "currency";
  positive?: boolean | null; // null = neutral
  prefix?: string;
  suffix?: string;
}

function KpiCard({ label, value, format, positive, prefix = "", suffix = "" }: KpiCardProps) {
  const numeric = value ?? 0;
  const animated = useCountUp(numeric, 700, value !== null);

  const formatted = (() => {
    if (value === null) return "N/A";
    switch (format) {
      case "pct":
        return `${animated >= 0 ? "+" : ""}${animated.toFixed(2)}%`;
      case "ratio":
        return animated.toFixed(2);
      case "number":
        return Math.round(animated).toLocaleString();
      case "currency":
        return `$${animated.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
      <span className={`text-2xl font-bold font-heading tracking-tight ${colorClass}`}>
        {prefix}{formatted}{suffix}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Monthly Returns Heatmap                                            */
/* ------------------------------------------------------------------ */

const MONTHS = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

function MonthlyHeatmap({ tradeLog }: { tradeLog: TradeLogEntry[] }) {
  // Build a map of year→month→total pnl
  const map: Record<number, Record<number, number>> = {};
  for (const trade of tradeLog) {
    const d = new Date(trade.opened_at);
    const year = d.getFullYear();
    const month = d.getMonth(); // 0-based
    if (!map[year]) map[year] = {};
    map[year][month] = (map[year][month] ?? 0) + trade.realized_pnl;
  }

  const years = Object.keys(map).map(Number).sort((a, b) => a - b);
  if (years.length === 0) return null;

  // find max abs for color scaling
  let maxAbs = 0;
  for (const yr of years) {
    for (let m = 0; m < 12; m++) {
      const v = Math.abs(map[yr][m] ?? 0);
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
    <div className="flex flex-col gap-2">
      <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
        月度收益热力图
      </span>
      <div className="overflow-x-auto">
        <table className="text-[10px] border-separate border-spacing-0.5">
          <thead>
            <tr>
              <th className="w-12 text-left text-[var(--text-muted)] font-medium pr-2">年份</th>
              {MONTHS.map((m) => (
                <th key={m} className="w-10 text-center text-[var(--text-muted)] font-medium">
                  {m}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {years.map((yr) => (
              <tr key={yr}>
                <td className="text-[var(--text-secondary)] pr-2">{yr}</td>
                {Array.from({ length: 12 }, (_, m) => {
                  const val = map[yr][m];
                  return (
                    <td
                      key={m}
                      title={val !== undefined ? `${val >= 0 ? "+" : ""}${val.toFixed(2)}` : "—"}
                      style={{ backgroundColor: cellColor(val) }}
                      className="rounded text-center h-6 cursor-default"
                    >
                      {val !== undefined ? (
                        <span className={val >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                          {val >= 0 ? "+" : ""}
                          {Math.abs(val) > 999 ? `${(val / 1000).toFixed(1)}k` : val.toFixed(0)}
                        </span>
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

  const { statistics: s, equity_curve, trade_log, per_instrument } = result;

  // Equity chart data — sample to ~200 points if large
  const maxPoints = 200;
  const step = equity_curve.length > maxPoints ? Math.ceil(equity_curve.length / maxPoints) : 1;
  const chartData = equity_curve
    .filter((_, i) => i % step === 0)
    .map((p) => ({
      t: new Date(p.timestamp ?? p.date ?? "").toLocaleDateString("zh-CN", { month: "short", day: "numeric" }),
      equity: p.equity,
      drawdown: p.drawdown_pct,
    }));

  return (
    <div className="flex flex-col gap-5 p-4">
      {/* KPI Grid */}
      <StaggerContainer className="grid grid-cols-3 gap-3" staggerDelay={0.07}>
        <StaggerItem>
          <KpiCard
            label="总收益率"
            value={s.total_return_pct}
            format="pct"
            positive={s.total_return_pct >= 0 ? true : false}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label="夏普比率"
            value={s.sharpe_ratio}
            format="ratio"
            positive={s.sharpe_ratio !== null ? s.sharpe_ratio >= 1 : null}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label="最大回撤"
            value={s.max_drawdown !== null ? Math.abs(s.max_drawdown) : null}
            format="pct"
            positive={s.max_drawdown !== null ? s.max_drawdown > -20 : null}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label="胜率"
            value={s.win_rate}
            format="pct"
            positive={s.win_rate >= 50 ? true : false}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label="盈亏比"
            value={s.profit_factor}
            format="ratio"
            positive={s.profit_factor !== null ? s.profit_factor >= 1 : null}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label="总交易次数"
            value={s.total_trades}
            format="number"
            positive={null}
          />
        </StaggerItem>
      </StaggerContainer>

      {/* Equity Curve */}
      {chartData.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
          <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
            权益曲线
          </span>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
              <defs>
                <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4C9EEB" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4C9EEB" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" />
              <XAxis
                dataKey="t"
                tick={{ fill: "var(--text-muted)", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: "var(--text-muted)", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                width={48}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-gray)",
                  borderRadius: 8,
                  fontSize: 11,
                  color: "var(--text-primary)",
                }}
                formatter={(value: unknown) => [`$${(value as number).toLocaleString("en-US", { maximumFractionDigits: 0 })}`, "权益"]}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="#4C9EEB"
                strokeWidth={1.5}
                fill="url(#equityGradient)"
                dot={false}
                activeDot={{ r: 3, fill: "#4C9EEB" }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Monthly Heatmap */}
      {trade_log && trade_log.length > 0 && (
        <div className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
          <MonthlyHeatmap tradeLog={trade_log} />
        </div>
      )}

      {/* Per-instrument breakdown */}
      {per_instrument && per_instrument.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
          <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
            品种分解
          </span>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--border-gray)] text-[var(--text-muted)]">
                  <th className="text-left py-2 pr-4 font-medium">品种</th>
                  <th className="text-right py-2 pr-4 font-medium">交易数</th>
                  <th className="text-right py-2 pr-4 font-medium">胜率</th>
                  <th className="text-right py-2 pr-4 font-medium">总盈亏</th>
                  <th className="text-right py-2 pr-4 font-medium">夏普</th>
                  <th className="text-right py-2 font-medium">最大回撤</th>
                </tr>
              </thead>
              <tbody>
                {per_instrument.map((row) => (
                  <tr key={row.instrument} className="border-b border-[var(--border-gray)]/40 hover:bg-[var(--bg-subtle)]/50">
                    <td className="py-1.5 pr-4 text-[var(--accent-blue)] font-medium">{row.instrument}</td>
                    <td className="py-1.5 pr-4 text-right text-[var(--text-secondary)]">{row.total_trades}</td>
                    <td className="py-1.5 pr-4 text-right">
                      <span className={row.win_rate >= 50 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                        {row.win_rate.toFixed(1)}%
                      </span>
                    </td>
                    <td className="py-1.5 pr-4 text-right">
                      <span className={row.total_pnl >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                        {row.total_pnl >= 0 ? "+" : ""}{row.total_pnl.toFixed(2)}
                      </span>
                    </td>
                    <td className="py-1.5 pr-4 text-right text-[var(--text-secondary)]">
                      {row.sharpe_ratio !== null ? row.sharpe_ratio.toFixed(2) : "—"}
                    </td>
                    <td className="py-1.5 text-right">
                      {row.max_drawdown !== null ? (
                        <span className="text-[var(--accent-red)]">
                          {row.max_drawdown.toFixed(2)}%
                        </span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Additional stats */}
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
          <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
            交易统计
          </span>
          <div className="flex flex-col gap-1">
            {[
              ["盈利笔数", s.winning_trades, null],
              ["亏损笔数", s.losing_trades, null],
              ["总手续费", `$${s.total_fees.toFixed(2)}`, null],
              ["平均持仓", s.avg_holding_time ?? "—", null],
            ].map(([label, val]) => (
              <div key={label as string} className="flex items-center justify-between py-0.5">
                <span className="text-xs text-[var(--text-muted)]">{label as string}</span>
                <span className="text-xs text-[var(--text-secondary)]">{String(val)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-4">
          <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-[var(--text-muted)]">
            收益统计
          </span>
          <div className="flex flex-col gap-1">
            {[
              ["总盈利", `$${s.gross_profit.toFixed(2)}`],
              ["总亏损", `$${s.gross_loss.toFixed(2)}`],
              ["年化收益", s.annual_return !== null ? `${s.annual_return.toFixed(2)}%` : "—"],
              ["索提诺比率", s.sortino_ratio !== null ? s.sortino_ratio.toFixed(2) : "—"],
            ].map(([label, val]) => (
              <div key={label} className="flex items-center justify-between py-0.5">
                <span className="text-xs text-[var(--text-muted)]">{label}</span>
                <span className="text-xs text-[var(--text-secondary)]">{val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
