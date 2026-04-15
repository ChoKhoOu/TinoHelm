"use client";

import { useState, useEffect, useMemo } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { TrendingUp, Grid3x3, Activity, BarChart3 } from "lucide-react";
import { apiGet } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { EmptyState } from "@/components/EmptyState";
import { Pagination } from "@/components/Pagination";
import {
  CHART_AXIS_STYLE as AXIS_STYLE,
  CHART_TOOLTIP_STYLE as TOOLTIP_STYLE,
  CHART_GRID_STYLE,
} from "@/lib/chartTheme";

/* ── Types ──────────────────────────────────────────────── */

interface AttributionStats {
  alpha: number;
  beta_return: number;
  idiosyncratic: number;
  total: number;
}

interface StrategyReturn {
  date: string;
  [strategy: string]: string | number;
}

interface CorrelationEntry {
  pair: [string, string];
  value: number;
}

interface FactorExposure {
  factor: string;
  value: number;
}

interface DistributionBin {
  range: string;
  count: number;
}

interface RiskRow {
  strategy: string;
  allocation: number;
  return_pct: number;
  contribution: number;
  risk_share: number;
  sharpe: number;
}

/* ── Constants ──────────────────────────────────────────── */

const TIME_RANGES = ["7d", "30d", "90d", "YTD", "All"] as const;
type TimeRange = (typeof TIME_RANGES)[number];

const STRATEGY_COLORS = [
  "var(--suc)",
  "var(--info)",
  "var(--acc)",
  "var(--warn)",
  "var(--dan)",
  "#A78BFA",
  "#F472B6",
  "#34D399",
];

/* ── Mock data (used when API returns nothing) ────────── */

function mockAttributionStats(): AttributionStats {
  return { alpha: 8.4, beta_return: 3.2, idiosyncratic: -1.1, total: 10.5 };
}

function mockCumulativeReturns(): { data: StrategyReturn[]; strategies: string[] } {
  const strategies = ["MM-perp", "Stat-arb", "Funding", "Basis", "Momentum"];
  const data: StrategyReturn[] = [];
  const accum = strategies.map(() => 0);
  for (let i = 0; i < 30; i++) {
    const d = new Date();
    d.setDate(d.getDate() - 29 + i);
    const entry: StrategyReturn = { date: `${d.getMonth() + 1}/${d.getDate()}` };
    strategies.forEach((s, si) => {
      accum[si] += (Math.random() - 0.3) * 1.2;
      entry[s] = Number(accum[si].toFixed(2));
    });
    data.push(entry);
  }
  return { data, strategies };
}

function mockCorrelation(): { strategies: string[]; matrix: number[][] } {
  const strategies = ["MM-perp", "Stat-arb", "Funding", "Basis", "Momentum"];
  const matrix = strategies.map((_, i) =>
    strategies.map((_, j) =>
      i === j ? 1 : Number(((Math.random() - 0.3) * 1.0).toFixed(2)),
    ),
  );
  return { strategies, matrix };
}

function mockFactorExposure(): FactorExposure[] {
  return [
    { factor: "Market", value: 0.82 },
    { factor: "Volatility", value: 0.45 },
    { factor: "Momentum", value: 0.68 },
    { factor: "Value", value: 0.31 },
    { factor: "Liquidity", value: 0.57 },
  ];
}

function mockDistribution(): DistributionBin[] {
  return [
    { range: "<-3%", count: 2 },
    { range: "-3~-2%", count: 5 },
    { range: "-2~-1%", count: 12 },
    { range: "-1~0%", count: 18 },
    { range: "0~1%", count: 22 },
    { range: "1~2%", count: 15 },
    { range: "2~3%", count: 8 },
    { range: ">3%", count: 3 },
  ];
}

function mockRiskRows(): RiskRow[] {
  return [
    { strategy: "MM-perp v3.2", allocation: 35, return_pct: 12.4, contribution: 4.3, risk_share: 42, sharpe: 2.14 },
    { strategy: "Stat-arb v1.8", allocation: 25, return_pct: 8.2, contribution: 2.1, risk_share: 28, sharpe: 1.87 },
    { strategy: "Funding arb v2.0", allocation: 20, return_pct: 6.8, contribution: 1.4, risk_share: 12, sharpe: 3.12 },
    { strategy: "Basis v1.0", allocation: 15, return_pct: 5.4, contribution: 0.8, risk_share: 10, sharpe: 1.76 },
    { strategy: "Momentum v4.1", allocation: 5, return_pct: -4.2, contribution: -0.2, risk_share: 8, sharpe: -0.38 },
  ];
}

/* ── Helpers ─────────────────────────────────────────────── */

function corrBg(v: number): string {
  const abs = Math.abs(v);
  if (v >= 0) return `rgba(54,136,75,${0.1 + abs * 0.55})`;
  return `rgba(254,129,129,${0.1 + abs * 0.55})`;
}

function valColor(v: number): string {
  if (v > 0) return "var(--suc)";
  if (v < 0) return "var(--dan)";
  return "var(--t2)";
}

function fmtPct(v: number, showSign = true): string {
  const s = showSign && v > 0 ? "+" : "";
  return `${s}${v.toFixed(1)}%`;
}

/* ── Page ────────────────────────────────────────────────── */

export default function AnalyticsPage() {
  const [range, setRange] = useState<TimeRange>("30d");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [stats, setStats] = useState<AttributionStats | null>(null);
  const [cumReturns, setCumReturns] = useState<{ data: StrategyReturn[]; strategies: string[] }>({ data: [], strategies: [] });
  const [correlation, setCorrelation] = useState<{ strategies: string[]; matrix: number[][] }>({ strategies: [], matrix: [] });
  const [factors, setFactors] = useState<FactorExposure[]>([]);
  const [distribution, setDistribution] = useState<DistributionBin[]>([]);
  const [riskRows, setRiskRows] = useState<RiskRow[]>([]);
  const [riskPage, setRiskPage] = useState(1);
  const [riskPageSize, setRiskPageSize] = useState(20);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [attrRes, cumRes, corrRes, factRes, distRes, riskRes] = await Promise.all([
          apiGet<AttributionStats>(`/api/analytics/attribution?range=${range}`).catch(() => null),
          apiGet<{ data: StrategyReturn[]; strategies: string[] }>(`/api/analytics/cumulative-returns?range=${range}`).catch(() => null),
          apiGet<{ strategies: string[]; matrix: number[][] }>(`/api/analytics/correlation?range=${range}`).catch(() => null),
          apiGet<FactorExposure[]>(`/api/analytics/factor-exposure?range=${range}`).catch(() => null),
          apiGet<DistributionBin[]>(`/api/analytics/distribution?range=${range}`).catch(() => null),
          apiGet<RiskRow[]>(`/api/analytics/risk-decomposition?range=${range}`).catch(() => null),
        ]);
        if (cancelled) return;

        setStats(attrRes ?? mockAttributionStats());
        setCumReturns(cumRes ?? mockCumulativeReturns());
        setCorrelation(corrRes ?? mockCorrelation());
        setFactors(factRes ?? mockFactorExposure());
        setDistribution(distRes ?? mockDistribution());
        setRiskRows(riskRes ?? mockRiskRows());
      } catch {
        if (!cancelled) setError("Failed to load analytics data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [range]);

  const pagedRiskRows = useMemo(() => {
    const start = (riskPage - 1) * riskPageSize;
    return riskRows.slice(start, start + riskPageSize);
  }, [riskRows, riskPage, riskPageSize]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState variant="error" title="Analytics load failed" description={error} action={{ label: "Retry", onClick: () => window.location.reload() }} />
      </div>
    );
  }

  const statCards: { label: string; sublabel: string; value: number }[] = stats
    ? [
        { label: "Alpha", sublabel: "skill return", value: stats.alpha },
        { label: "Beta Return", sublabel: "market exposure", value: stats.beta_return },
        { label: "Idiosyncratic", sublabel: "unexplained", value: stats.idiosyncratic },
        { label: "Total Return", sublabel: "net of fees", value: stats.total },
      ]
    : [];

  return (
    <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
      {/* Header + time range */}
      <FadeIn direction="down" duration={0.25}>
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-[1.3rem] font-bold tracking-tight text-foreground">
              Analytics
            </h1>
            <span className="text-[0.68rem] font-mono text-muted-foreground">
              Performance attribution & risk decomposition
            </span>
          </div>

          {/* Time range pills */}
          <div className="flex items-center gap-[2px] rounded-sm bg-input p-[3px]">
            {TIME_RANGES.map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`rounded px-3 py-1.5 text-[0.72rem] font-mono font-medium transition-all duration-150 ${
                  range === r
                    ? "bg-secondary text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-qds-t1"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
      </FadeIn>

      {/* Attribution stat cards */}
      <StaggerContainer className="grid grid-cols-4 gap-4" staggerDelay={0.06}>
        {loading
          ? Array.from({ length: 4 }).map((_, i) => (
              <StaggerItem key={i}>
                <div className="rounded-xl bg-card border p-4 animate-pulse">
                  <div className="h-3 w-16 rounded bg-secondary mb-3" />
                  <div className="h-7 w-20 rounded bg-secondary mb-1" />
                  <div className="h-3 w-14 rounded bg-secondary" />
                </div>
              </StaggerItem>
            ))
          : statCards.map((c) => (
              <StaggerItem key={c.label}>
                <div className="rounded-xl bg-card border p-4 hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
                  <div className="qds-stat-label mb-1">
                    {c.label}
                  </div>
                  <div
                    className="font-mono text-[1.35rem] font-semibold tracking-tight"
                    style={{ color: valColor(c.value) }}
                  >
                    {fmtPct(c.value)}
                  </div>
                  <div className="font-mono text-[0.68rem] text-muted-foreground mt-0.5">
                    {c.sublabel}
                  </div>
                </div>
              </StaggerItem>
            ))}
      </StaggerContainer>

      {/* Charts 2x2 */}
      <div className="grid grid-cols-2 gap-4 min-h-0">
        {/* Stacked area: cumulative return by strategy */}
        <FadeIn direction="up" duration={0.3} delay={0.1}>
          <div className="rounded-xl bg-card border overflow-hidden hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-[0.8rem] font-semibold text-foreground">Cumulative Return by Strategy</span>
            </div>
            <div className="p-4 h-[260px]">
              {loading ? (
                <div className="w-full h-full rounded bg-secondary animate-pulse" />
              ) : cumReturns.data.length === 0 ? (
                <EmptyState variant="first-use" title="No return data" className="h-full py-4" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={cumReturns.data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <CartesianGrid {...CHART_GRID_STYLE} vertical={false} />
                    <XAxis dataKey="date" tick={AXIS_STYLE} tickLine={false} axisLine={false} />
                    <YAxis
                      tick={AXIS_STYLE}
                      tickLine={false}
                      axisLine={false}
                      width={42}
                      tickFormatter={(v: number) => `${v.toFixed(1)}%`}
                    />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Legend
                      iconType="circle"
                      iconSize={8}
                      wrapperStyle={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}
                    />
                    {cumReturns.strategies.map((s, i) => (
                      <Area
                        key={s}
                        type="monotone"
                        dataKey={s}
                        stackId="1"
                        stroke={STRATEGY_COLORS[i % STRATEGY_COLORS.length]}
                        fill={STRATEGY_COLORS[i % STRATEGY_COLORS.length]}
                        fillOpacity={0.15}
                        strokeWidth={1.5}
                        dot={false}
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </FadeIn>

        {/* Correlation matrix heatmap */}
        <FadeIn direction="up" duration={0.3} delay={0.15}>
          <div className="rounded-xl bg-card border overflow-hidden hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-[0.8rem] font-semibold text-foreground">Correlation Matrix</span>
            </div>
            <div className="p-4">
              {loading ? (
                <div className="w-full h-[220px] rounded bg-secondary animate-pulse" />
              ) : correlation.strategies.length === 0 ? (
                <EmptyState variant="first-use" title="No correlation data" className="py-4" />
              ) : (
                <div
                  className="grid gap-[2px] font-mono text-[0.62rem]"
                  style={{
                    gridTemplateColumns: `auto repeat(${correlation.strategies.length}, 1fr)`,
                  }}
                >
                  {/* Header row */}
                  <div />
                  {correlation.strategies.map((s) => (
                    <div key={s} className="px-1 py-1 text-center text-muted-foreground text-[0.6rem] truncate">
                      {s.length > 6 ? s.slice(0, 6) : s}
                    </div>
                  ))}
                  {/* Data rows */}
                  {correlation.strategies.map((row, ri) => (
                    <Fragment key={`row-${ri}`}>
                      <div className="px-1 py-1 text-muted-foreground text-[0.65rem] flex items-center">
                        {row.length > 8 ? row.slice(0, 8) : row}
                      </div>
                      {correlation.matrix[ri].map((v, ci) => (
                        <div
                          key={`${ri}-${ci}`}
                          className="rounded-[3px] px-1 py-1.5 text-center font-medium transition-transform duration-150 hover:scale-110 hover:z-10"
                          style={{
                            background: corrBg(v),
                            color: Math.abs(v) > 0.5 ? "#fff" : "var(--t1)",
                          }}
                        >
                          {v.toFixed(2)}
                        </div>
                      ))}
                    </Fragment>
                  ))}
                </div>
              )}
            </div>
          </div>
        </FadeIn>

        {/* Radar: factor exposure */}
        <FadeIn direction="up" duration={0.3} delay={0.2}>
          <div className="rounded-xl bg-card border overflow-hidden hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-[0.8rem] font-semibold text-foreground">Factor Exposure</span>
            </div>
            <div className="p-4 h-[260px]">
              {loading ? (
                <div className="w-full h-full rounded bg-secondary animate-pulse" />
              ) : factors.length === 0 ? (
                <EmptyState variant="first-use" title="No factor data" className="h-full py-4" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={factors} outerRadius="70%">
                    <PolarGrid stroke="var(--bd)" />
                    <PolarAngleAxis
                      dataKey="factor"
                      tick={{ fontSize: 10, fill: "var(--t2)", fontFamily: "IBM Plex Mono" }}
                    />
                    <PolarRadiusAxis
                      angle={90}
                      tick={{ fontSize: 9, fill: "var(--t3)" }}
                      axisLine={false}
                    />
                    <Radar
                      dataKey="value"
                      stroke="var(--info)"
                      fill="var(--info)"
                      fillOpacity={0.2}
                      strokeWidth={2}
                    />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                  </RadarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </FadeIn>

        {/* Distribution bar chart */}
        <FadeIn direction="up" duration={0.3} delay={0.25}>
          <div className="rounded-xl bg-card border overflow-hidden hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-[0.8rem] font-semibold text-foreground">Rolling Return Distribution</span>
            </div>
            <div className="p-4 h-[260px]">
              {loading ? (
                <div className="w-full h-full rounded bg-secondary animate-pulse" />
              ) : distribution.length === 0 ? (
                <EmptyState variant="first-use" title="No distribution data" className="h-full py-4" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={distribution} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <CartesianGrid {...CHART_GRID_STYLE} vertical={false} />
                    <XAxis dataKey="range" tick={AXIS_STYLE} tickLine={false} axisLine={false} />
                    <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={32} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Bar dataKey="count" fill="var(--info)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </FadeIn>
      </div>

      {/* Risk decomposition table */}
      <FadeIn direction="up" duration={0.3} delay={0.3}>
        <div className="rounded-xl bg-card border overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b">
            <span className="text-[0.8rem] font-semibold text-foreground">Risk Decomposition</span>
          </div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Strategy</TableHead>
                  <TableHead className="text-right">Allocation</TableHead>
                  <TableHead className="text-right">Return</TableHead>
                  <TableHead className="text-right">Contribution</TableHead>
                  <TableHead className="text-right">Risk Share</TableHead>
                  <TableHead className="text-right">Sharpe</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading
                  ? Array.from({ length: 5 }).map((_, i) => (
                      <TableRow key={i}>
                        <TableCell><div className="h-3 w-24 rounded bg-secondary animate-pulse" /></TableCell>
                        <TableCell><div className="h-3 w-10 rounded bg-secondary animate-pulse ml-auto" /></TableCell>
                        <TableCell><div className="h-3 w-12 rounded bg-secondary animate-pulse ml-auto" /></TableCell>
                        <TableCell><div className="h-3 w-12 rounded bg-secondary animate-pulse ml-auto" /></TableCell>
                        <TableCell><div className="h-3 w-10 rounded bg-secondary animate-pulse ml-auto" /></TableCell>
                        <TableCell><div className="h-3 w-10 rounded bg-secondary animate-pulse ml-auto" /></TableCell>
                      </TableRow>
                    ))
                  : pagedRiskRows.map((r) => (
                      <TableRow key={r.strategy}>
                        <TableCell>{r.strategy}</TableCell>
                        <TableCell className="text-right">{r.allocation}%</TableCell>
                        <TableCell className="text-right" style={{ color: valColor(r.return_pct) }}>
                          {fmtPct(r.return_pct)}
                        </TableCell>
                        <TableCell className="text-right" style={{ color: valColor(r.contribution) }}>
                          {fmtPct(r.contribution)}
                        </TableCell>
                        <TableCell className="text-right">{r.risk_share}%</TableCell>
                        <TableCell className="text-right" style={{ color: valColor(r.sharpe) }}>
                          {r.sharpe.toFixed(2)}
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </div>
          {!loading && riskRows.length > 0 && (
            <div className="px-4 py-3 border-t">
              <Pagination
                total={riskRows.length}
                page={riskPage}
                pageSize={riskPageSize}
                onPageChange={setRiskPage}
                onPageSizeChange={(s) => { setRiskPageSize(s); setRiskPage(1); }}
              />
            </div>
          )}
        </div>
      </FadeIn>
    </div>
  );
}
