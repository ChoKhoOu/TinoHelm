"use client";

import { useState, useEffect } from "react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import {
  Download,
  Grid3x3,
  TrendingDown,
  BarChart3,
  Activity,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";

/* ── Constants ─────────────────────────────────────────────── */

const months = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/* ── Helpers ────────────────────────────────────────────────── */

function heatColor(val: number): string {
  if (val >= 3) return "var(--accent-green)";
  if (val > 0) return "var(--accent-green-20)";
  if (val > -2) return "var(--accent-red-20)";
  return "var(--accent-red)";
}

function heatText(val: number): string {
  if (val >= 3) return "var(--text-on-accent)";
  if (val > 0) return "var(--accent-green)";
  if (val > -2) return "var(--accent-red)";
  return "white";
}

/* ── Chart Card Wrapper ─────────────────────────────────────── */

function ChartCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-[14px]">
        <span className="text-[var(--text-muted)]">{icon}</span>
        <span className="text-[11px] font-semibold tracking-[0.5px] text-[var(--text-secondary)]">
          {title}
        </span>
      </div>
      <div className="h-px bg-[var(--border-gray)]" />
      <div className="flex-1 p-5">{children}</div>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */

export default function AnalyticsPage() {
  const { t } = useI18n();
  const [heatmapData, setHeatmapData] = useState<Record<string, number[]>>({});
  const [drawdownData, setDrawdownData] = useState<{date: string; drawdown: number}[]>([]);
  const [distributionData, setDistributionData] = useState<{range: string; count: number}[]>([]);
  const [rollingSharpeData, setRollingSharpeData] = useState<{month: string; sharpe: number}[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadAnalytics() {
      try {
        const [heatmap, dd, dist, sharpe] = await Promise.all([
          apiGet<{data: {year: string; month: number; return_pct: number}[]}>("/api/analytics/returns-heatmap"),
          apiGet<{data: {date: string; drawdown: number}[]}>("/api/analytics/drawdown"),
          apiGet<{data: {range: string; count: number}[]}>("/api/analytics/distribution"),
          apiGet<{data: {date: string; sharpe: number}[]}>("/api/analytics/rolling-sharpe"),
        ]);
        if (cancelled) return;
        // Transform heatmap data into Record<string, number[]> format
        if (heatmap?.data?.length) {
          const map: Record<string, number[]> = {};
          for (const item of heatmap.data) {
            if (!map[item.year]) map[item.year] = new Array(12).fill(0);
            map[item.year][item.month - 1] = item.return_pct;
          }
          setHeatmapData(map);
        }
        if (dd?.data) setDrawdownData(dd.data);
        if (dist?.data) setDistributionData(dist.data);
        if (sharpe?.data) setRollingSharpeData(sharpe.data.map(s => ({ month: s.date, sharpe: s.sharpe })));
      } catch {
        if (!cancelled) setError("common.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadAnalytics();
    return () => { cancelled = true; };
  }, []);

  const emptyState = (
    <div className="flex items-center justify-center h-full text-[11px] text-[var(--text-muted)]">
      {t("analytics.noData")}
    </div>
  );

  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("analytics.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("analytics.subtitle")}
          </span>
        </div>
        <Button variant="secondary" icon={<Download className="w-3 h-3" />}>
          {t("analytics.exportCsv")}
        </Button>
      </div>

      {error ? (
        <div className="flex items-center justify-center h-full p-8">
          <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("common.loadFailed")}</span>
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center flex-1 text-[11px] text-[var(--text-muted)]">
          {t("analytics.loading")}
        </div>
      ) : (
        /* 2x2 Grid */
        <div className="flex-1 grid grid-cols-2 grid-rows-2 gap-4 min-h-0">
          {/* Monthly Returns Heatmap */}
          <ChartCard
            icon={<Grid3x3 className="w-4 h-4" />}
            title={t("analytics.monthlyReturns")}
          >
            {Object.keys(heatmapData).length === 0 ? emptyState : (
              <div className="flex flex-col gap-2">
                {/* Month headers */}
                <div className="grid grid-cols-[48px_repeat(12,1fr)] gap-1">
                  <div />
                  {months.map((m) => (
                    <div
                      key={m}
                      className="text-[9px] font-medium text-[var(--text-muted)] text-center"
                    >
                      {m}
                    </div>
                  ))}
                </div>
                {/* Year rows */}
                {Object.entries(heatmapData).map(([year, vals]) => (
                  <div
                    key={year}
                    className="grid grid-cols-[48px_repeat(12,1fr)] gap-1"
                  >
                    <div className="text-[10px] font-semibold text-[var(--text-secondary)] flex items-center">
                      {year}
                    </div>
                    {vals.map((v, i) => (
                      <div
                        key={months[i]}
                        className="flex items-center justify-center rounded h-8 text-[9px] font-bold"
                        style={{
                          backgroundColor: heatColor(v),
                          color: heatText(v),
                        }}
                      >
                        {v > 0 ? "+" : ""}
                        {v.toFixed(1)}%
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </ChartCard>

          {/* Drawdown Chart */}
          <ChartCard
            icon={<TrendingDown className="w-4 h-4" />}
            title={t("analytics.drawdown")}
          >
            {drawdownData.length === 0 ? emptyState : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={drawdownData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border-gray)"
                  />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                    interval={14}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                    tickFormatter={(v: number) => `${v}%`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--bg-elevated)",
                      border: "1px solid var(--border-gray)",
                      borderRadius: 8,
                      fontSize: 11,
                      color: "var(--text-primary)",
                    }}
                  />
                  <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="3 3" />
                  <Area
                    type="monotone"
                    dataKey="drawdown"
                    stroke="var(--accent-red)"
                    fill="var(--accent-red-20)"
                    strokeWidth={1.5}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </ChartCard>

          {/* Returns Distribution */}
          <ChartCard
            icon={<BarChart3 className="w-4 h-4" />}
            title={t("analytics.distribution")}
          >
            {distributionData.length === 0 ? emptyState : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={distributionData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border-gray)"
                  />
                  <XAxis
                    dataKey="range"
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--bg-elevated)",
                      border: "1px solid var(--border-gray)",
                      borderRadius: 8,
                      fontSize: 11,
                      color: "var(--text-primary)",
                    }}
                  />
                  <Bar
                    dataKey="count"
                    fill="var(--accent-green)"
                    radius={[4, 4, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            )}
          </ChartCard>

          {/* Rolling Sharpe */}
          <ChartCard
            icon={<Activity className="w-4 h-4" />}
            title={t("analytics.rollingSharpe")}
          >
            {rollingSharpeData.length === 0 ? emptyState : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={rollingSharpeData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border-gray)"
                  />
                  <XAxis
                    dataKey="month"
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                    interval={5}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: "var(--text-muted)" }}
                    tickLine={false}
                    axisLine={{ stroke: "var(--border-gray)" }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--bg-elevated)",
                      border: "1px solid var(--border-gray)",
                      borderRadius: 8,
                      fontSize: 11,
                      color: "var(--text-primary)",
                    }}
                  />
                  <ReferenceLine y={1} stroke="var(--text-muted)" strokeDasharray="3 3" label="" />
                  <Line
                    type="monotone"
                    dataKey="sharpe"
                    stroke="var(--accent-green)"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </ChartCard>
        </div>
      )}
    </div>
  );
}
