"use client";

import { useState, useEffect } from "react";
import { Search, Bell, TrendingUp } from "lucide-react";
import { Card, MetricCard } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const timeRanges = ["1D", "1W", "1M", "3M", "1Y", "ALL"];

type Strategy = { name: string; status: "success" | "warning" | "error"; pnl: string; pnlType: "positive" | "negative" };

export default function DashboardPage() {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [equityData, setEquityData] = useState<{date: string; value: number}[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [metrics, setMetrics] = useState({
    totalEquity: "$0", totalEquityChange: "0%",
    dailyPnl: "$0", dailyPnlChange: "0%",
    activePositions: "0", activePositionsChange: "",
    sharpeRatio: "0", sharpeChange: "",
  });
  const [selectedRange, setSelectedRange] = useState("1Y");

  useEffect(() => {
    let cancelled = false;
    async function fetchDashboard() {
      try {
        const data = await apiGet<{
          total_equity: number;
          daily_pnl: number;
          open_positions: number;
          active_strategy_count: number;
          sharpe_ratio: number;
          equity_curve?: {date: string; value: number}[];
          strategies?: Strategy[];
        }>("/api/dashboard/summary");
        if (cancelled || !data) return;
        setMetrics({
          totalEquity: `$${data.total_equity.toLocaleString()}`,
          totalEquityChange: "—",
          dailyPnl: `$${data.daily_pnl.toLocaleString()}`,
          dailyPnlChange: "—",
          activePositions: String(data.open_positions),
          activePositionsChange: `${data.active_strategy_count} strategies`,
          sharpeRatio: data.sharpe_ratio.toFixed(2),
          sharpeChange: "—",
        });
        if (data.equity_curve) setEquityData(data.equity_curve);
        if (data.strategies) setStrategies(data.strategies);
      } catch {
        setError("dashboard.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchDashboard();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--text-muted)] animate-pulse">
          {t("dashboard.connecting")}
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("dashboard.loadFailed")}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-8">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-[-1px] text-[var(--text-primary)]">
            {t("dashboard.title")}
          </h1>
          <p className="font-mono text-[12px] text-[var(--text-muted)]">
            {t("dashboard.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-3 py-2">
            <Search className="w-3.5 h-3.5 text-[var(--text-muted)]" />
            <input
              type="text"
              placeholder={t("common.search")}
              className="w-40 bg-transparent text-[11px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none font-mono"
            />
          </div>
          <button aria-label="Notifications" className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
            <Bell className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label={t("dashboard.totalEquity")}
          value={metrics.totalEquity}
          change={metrics.totalEquityChange}
          changeType={parseFloat(metrics.totalEquityChange.replace(/[^0-9.-]/g, "")) >= 0 ? "positive" : "negative"}
        />
        <MetricCard
          label={t("dashboard.dailyPnl")}
          value={metrics.dailyPnl}
          change={metrics.dailyPnlChange}
          changeType={parseFloat(metrics.dailyPnl.replace(/[^0-9.-]/g, "")) >= 0 ? "positive" : "negative"}
        />
        <MetricCard
          label={t("dashboard.activePositions")}
          value={metrics.activePositions}
          change={metrics.activePositionsChange}
          changeType="neutral"
        />
        <MetricCard
          label={t("dashboard.sharpeRatio")}
          value={metrics.sharpeRatio}
          change={metrics.sharpeChange}
          changeType={parseFloat(metrics.sharpeChange.replace(/[^0-9.-]/g, "")) >= 0 ? "positive" : "negative"}
        />
      </div>

      {/* Mid row */}
      <div className="flex gap-4">
        {/* Equity Chart */}
        <Card padding={false} className="flex-1 flex flex-col">
          <div className="flex items-center justify-between px-5 pt-5 pb-3">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              {t("dashboard.equityCurve")}
            </span>
            <div className="flex items-center gap-1">
              {timeRanges.map((range) => (
                <button
                  key={range}
                  onClick={() => setSelectedRange(range)}
                  className={`px-2.5 py-1 rounded text-[10px] font-semibold tracking-[0.5px] transition-colors ${
                    range === selectedRange
                      ? "bg-[var(--accent-green-20)] text-[var(--accent-green)]"
                      : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                  }`}
                >
                  {range}
                </button>
              ))}
            </div>
          </div>
          <div className="h-px bg-[var(--border-gray)]" />
          <div className="flex-1 px-3 py-4" style={{ minHeight: 280 }}>
            {equityData.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <span className="font-mono text-[11px] text-[var(--text-muted)]">{t("dashboard.noEquityData")}</span>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityData}>
                  <defs>
                    <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent-green)" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="var(--accent-green)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="date"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                  />
                  <YAxis
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                    tickFormatter={(v) => `$${(v / 1000000).toFixed(1)}M`}
                    domain={["dataMin - 50000", "dataMax + 50000"]}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--bg-elevated)",
                      border: "1px solid var(--border-gray)",
                      borderRadius: 8,
                      fontSize: 11,
                      fontFamily: "JetBrains Mono",
                      color: "var(--text-primary)",
                    }}
                    formatter={(value) => [`$${Number(value).toLocaleString()}`, "Equity"]}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="var(--accent-green)"
                    strokeWidth={2}
                    fill="url(#equityGradient)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        {/* Active Strategies */}
        <Card padding={false} className="w-[340px] flex flex-col">
          <div className="flex items-center justify-between px-5 pt-5 pb-3">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              {t("dashboard.activeStrategies")}
            </span>
            <button className="text-[10px] font-semibold tracking-[0.5px] text-[var(--accent-green)] hover:underline">
              {t("dashboard.viewAll")}
            </button>
          </div>
          <div className="h-px bg-[var(--border-gray)]" />
          <div className="flex flex-col">
            {strategies.length === 0 ? (
              <div className="flex items-center justify-center px-5 py-8">
                <span className="font-mono text-[11px] text-[var(--text-muted)]">{t("dashboard.noStrategies")}</span>
              </div>
            ) : (
              strategies.map((s, i) => (
                <div
                  key={s.name}
                  className={`flex items-center justify-between px-5 py-3 ${
                    i < strategies.length - 1 ? "border-b border-[var(--border-gray)]" : ""
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-lg bg-[var(--bg-elevated)] flex items-center justify-center">
                      <TrendingUp className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="text-[11px] font-semibold text-[var(--text-primary)]">
                        {s.name}
                      </span>
                      <Badge variant={s.status}>{s.status === "success" ? "RUNNING" : s.status === "warning" ? "WARMING" : "STOPPED"}</Badge>
                    </div>
                  </div>
                  <span
                    className={`text-[12px] font-bold font-mono ${
                      s.pnlType === "positive"
                        ? "text-[var(--accent-green)]"
                        : "text-[var(--accent-red)]"
                    }`}
                  >
                    {s.pnl}
                  </span>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
