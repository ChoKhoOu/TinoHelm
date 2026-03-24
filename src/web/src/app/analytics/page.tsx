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
import { Grid3x3, TrendingDown, BarChart3, Activity } from "lucide-react";
import { apiGet } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";

/* ── Constants ──────────────────────────────────────────────── */

const MONTHS_ZH = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

import { CHART_AXIS_STYLE as AXIS_STYLE, CHART_TOOLTIP_STYLE as TOOLTIP_STYLE } from "@/lib/chartTheme";

/* ── Helpers ────────────────────────────────────────────────── */

function heatBg(val: number): string {
  if (val >= 5) return "color-mix(in srgb, var(--accent-green) 30%, var(--bg-card))";
  if (val >= 3) return "color-mix(in srgb, var(--accent-green) 22%, var(--bg-card))";
  if (val > 0) return "color-mix(in srgb, var(--accent-green) 14%, var(--bg-card))";
  if (val === 0) return "var(--bg-elevated)";
  if (val > -3) return "color-mix(in srgb, var(--accent-red) 14%, var(--bg-card))";
  return "color-mix(in srgb, var(--accent-red) 30%, var(--bg-card))";
}

function heatFg(val: number): string {
  if (val > 0) return "#26D97F";
  if (val < 0) return "#EF5350";
  return "var(--text-muted)";
}

/* ── Chart Card ─────────────────────────────────────────────── */

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
    <div className="flex flex-col rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden min-h-0">
      <div className="flex items-center gap-2 px-5 py-[13px] shrink-0">
        <span className="text-[var(--text-muted)]">{icon}</span>
        <span className="text-[11px] font-semibold tracking-[0.5px] uppercase text-[var(--text-secondary)]">
          {title}
        </span>
      </div>
      <div className="h-px bg-[var(--border-gray)] shrink-0" />
      <div className="flex-1 p-5 min-h-0">{children}</div>
    </div>
  );
}

/* ── Skeleton ───────────────────────────────────────────────── */

function ChartSkeleton() {
  return (
    <div className="w-full h-full flex flex-col gap-3 animate-pulse">
      <div className="h-4 w-1/3 rounded bg-[var(--border-gray)]" />
      <div className="flex-1 rounded bg-[var(--border-gray)]" />
    </div>
  );
}

/* ── Empty State ────────────────────────────────────────────── */

function EmptyChart({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2">
      <span className="text-[11px] font-mono text-[var(--text-muted)]">{label}</span>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */

export default function AnalyticsPage() {
  const [heatmapData, setHeatmapData] = useState<{ years: string[]; map: Record<string, number[]> }>({ years: [], map: {} });
  const [drawdownData, setDrawdownData] = useState<{ date: string; drawdown: number }[]>([]);
  const [distributionData, setDistributionData] = useState<{ range: string; count: number }[]>([]);
  const [rollingSharpeData, setRollingSharpeData] = useState<{ date: string; sharpe: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadAll() {
      try {
        const [heatmap, dd, dist, sharpe] = await Promise.all([
          apiGet<{ data: { year: string; month: number; return_pct: number }[] }>("/api/analytics/returns-heatmap"),
          apiGet<{ data: { date: string; drawdown: number }[] }>("/api/analytics/drawdown"),
          apiGet<{ data: { range: string; count: number }[] }>("/api/analytics/distribution"),
          apiGet<{ data: { date: string; sharpe: number }[] }>("/api/analytics/rolling-sharpe"),
        ]);
        if (cancelled) return;

        if (heatmap?.data?.length) {
          const map: Record<string, number[]> = {};
          const yearsSet = new Set<string>();
          for (const item of heatmap.data) {
            if (!map[item.year]) map[item.year] = new Array(12).fill(0);
            map[item.year][item.month - 1] = item.return_pct;
            yearsSet.add(item.year);
          }
          setHeatmapData({ years: Array.from(yearsSet).sort(), map });
        }
        if (dd?.data) setDrawdownData(dd.data);
        if (dist?.data) setDistributionData(dist.data);
        if (sharpe?.data) setRollingSharpeData(sharpe.data);
      } catch {
        if (!cancelled) setError("加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadAll();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Header */}
      <FadeIn direction="down" duration={0.25}>
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-[26px] font-bold tracking-tight text-[var(--text-primary)]">
              数据分析
            </h1>
            <span className="text-[11px] font-mono text-[var(--text-muted)]">
              // 深度绩效分析 — 全部策略
            </span>
          </div>
        </div>
      </FadeIn>

      {/* Error */}
      {error ? (
        <div className="flex items-center justify-center flex-1">
          <span className="font-mono text-[12px] text-[#EF5350]">{error}</span>
        </div>
      ) : (
        /* 2×2 grid */
        <StaggerContainer className="flex-1 grid grid-cols-2 grid-rows-2 gap-4 min-h-0" staggerDelay={0.08}>
          {/* 月度收益热力图 */}
          <StaggerItem className="min-h-0 flex flex-col">
            <ChartCard icon={<Grid3x3 className="w-4 h-4" />} title="月度收益热力图">
              {loading ? <ChartSkeleton /> : heatmapData.years.length === 0 ? (
                <EmptyChart label="暂无收益数据，运行回测后查看" />
              ) : (
                <div className="flex flex-col gap-1.5 overflow-auto h-full">
                  {/* Month headers */}
                  <div className="grid grid-cols-[44px_repeat(12,1fr)] gap-1">
                    <div />
                    {MONTHS_ZH.map((m) => (
                      <div key={m} className="text-[9px] font-medium text-[var(--text-muted)] text-center font-mono">
                        {m}
                      </div>
                    ))}
                  </div>
                  {/* Year rows */}
                  {heatmapData.years.map((year) => (
                    <div key={year} className="grid grid-cols-[44px_repeat(12,1fr)] gap-1">
                      <div className="text-[10px] font-semibold font-mono text-[var(--text-secondary)] flex items-center">
                        {year}
                      </div>
                      {(heatmapData.map[year] ?? new Array(12).fill(0)).map((v, i) => (
                        <div
                          key={i}
                          className="flex items-center justify-center rounded h-7 text-[9px] font-bold font-mono"
                          style={{ backgroundColor: heatBg(v), color: heatFg(v) }}
                        >
                          {v !== 0 ? `${v > 0 ? "+" : ""}${v.toFixed(1)}%` : "—"}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </ChartCard>
          </StaggerItem>

          {/* 回撤曲线 */}
          <StaggerItem className="min-h-0 flex flex-col">
            <ChartCard icon={<TrendingDown className="w-4 h-4" />} title="回撤曲线">
              {loading ? <ChartSkeleton /> : drawdownData.length === 0 ? (
                <EmptyChart label="暂无回撤数据" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={drawdownData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#EF5350" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#EF5350" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" vertical={false} />
                    <XAxis dataKey="date" tick={AXIS_STYLE} tickLine={false} axisLine={{ stroke: "var(--border-gray)" }} interval="preserveStartEnd" />
                    <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} tickFormatter={(v: number) => `${v.toFixed(1)}%`} width={48} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number | undefined) => [v != null ? `${v.toFixed(2)}%` : "—", "回撤"]} />
                    <ReferenceLine y={0} stroke="var(--border-gray)" strokeDasharray="4 4" />
                    <Area type="monotone" dataKey="drawdown" stroke="#EF5350" fill="url(#ddGrad)" strokeWidth={1.5} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </StaggerItem>

          {/* PnL 分布 */}
          <StaggerItem className="min-h-0 flex flex-col">
            <ChartCard icon={<BarChart3 className="w-4 h-4" />} title="PnL 分布">
              {loading ? <ChartSkeleton /> : distributionData.length === 0 ? (
                <EmptyChart label="暂无分布数据" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={distributionData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" vertical={false} />
                    <XAxis dataKey="range" tick={AXIS_STYLE} tickLine={false} axisLine={{ stroke: "var(--border-gray)" }} />
                    <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={36} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number | undefined) => [v ?? 0, "次数"]} />
                    <Bar dataKey="count" fill="#4C9EEB" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </StaggerItem>

          {/* 滚动夏普 */}
          <StaggerItem className="min-h-0 flex flex-col">
            <ChartCard icon={<Activity className="w-4 h-4" />} title="滚动夏普比率">
              {loading ? <ChartSkeleton /> : rollingSharpeData.length === 0 ? (
                <EmptyChart label="暂无夏普数据" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={rollingSharpeData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" vertical={false} />
                    <XAxis dataKey="date" tick={AXIS_STYLE} tickLine={false} axisLine={{ stroke: "var(--border-gray)" }} interval="preserveStartEnd" />
                    <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={36} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number | undefined) => [v != null ? v.toFixed(2) : "—", "夏普比率"]} />
                    <ReferenceLine y={1} stroke="#4C9EEB" strokeDasharray="4 4" strokeOpacity={0.5} />
                    <ReferenceLine y={0} stroke="var(--border-gray)" />
                    <Line type="monotone" dataKey="sharpe" stroke="#4C9EEB" strokeWidth={2} dot={false} activeDot={{ r: 3, fill: "#4C9EEB" }} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </StaggerItem>
        </StaggerContainer>
      )}
    </div>
  );
}
