"use client";

import { useState, useEffect } from "react";
import { apiGet } from "@/lib/api";
import { useWsLastKnown, useWsEvent } from "@/providers/WebSocketProvider";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { FadeIn } from "@/components/motion/FadeIn";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from "recharts";
import { TrendingUp, Activity, BarChart3, Wallet, Server } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DashboardSummary {
  total_equity: number;
  daily_pnl: number;
  open_positions: number;
  active_strategy_count: number;
  sharpe_ratio: number;
  equity_curve?: { date: string; value: number }[];
  strategies?: { name: string; status: string }[];
}

interface BacktestRun {
  id: string;
  strategy_name: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  created_at: string;
  symbol?: string;
}

interface NodeInfo {
  alive: boolean;
  strategy_count?: number;
  position_count?: number;
  trading_state?: string;
  lifecycle_state?: string;
}

interface HealthData {
  status: string;
  postgres: string;
  redis: string;
  nodes?: {
    sandbox?: NodeInfo;
    live?: NodeInfo;
  };
}

import { useCountUp } from "@/hooks/useCountUp";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------


interface KpiCardProps {
  label: string;
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  change?: string;
  changePositive?: boolean;
  icon: React.ReactNode;
}

function KpiCard({ label, value, prefix = "", suffix = "", decimals = 0, change, changePositive, icon }: KpiCardProps) {
  const animated = useCountUp(value);
  const formatted = decimals > 0
    ? animated.toFixed(decimals)
    : Math.round(animated).toLocaleString();

  return (
    <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
          {label}
        </span>
        <div className="w-7 h-7 rounded-lg bg-[var(--bg-elevated)] flex items-center justify-center text-[var(--text-secondary)]">
          {icon}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="font-heading text-2xl font-bold tracking-tight text-[var(--text-primary)]">
          {prefix}{formatted}{suffix}
        </span>
        {change !== undefined && (
          <span
            className={`text-[11px] font-mono font-semibold ${
              changePositive === true
                ? "text-[var(--accent-green)]"
                : changePositive === false
                ? "text-[var(--accent-red)]"
                : "text-[var(--text-muted)]"
            }`}
          >
            {change}
          </span>
        )}
      </div>
    </div>
  );
}

function KpiCardSkeleton() {
  return (
    <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-7 w-7 rounded-lg" />
      </div>
      <Skeleton className="h-8 w-32" />
      <Skeleton className="h-3 w-16" />
    </div>
  );
}

import { StatusBadge } from "@/components/StatusBadge";

interface NodeCardProps {
  label: string;
  nodeType: "sandbox" | "live";
  info: NodeInfo | undefined;
}

function NodeCard({ label, nodeType, info }: NodeCardProps) {
  // Also subscribe to live websocket heartbeats
  const hbEvent = useWsEvent("node.heartbeat");
  const lastHb = useWsLastKnown("node.heartbeat");

  // Determine alive: either from passed info, or from WS heartbeat
  const hbData = (hbEvent ?? lastHb) as { node_type?: string; lifecycle_state?: string } | null;
  const wsAlive = hbData?.node_type === nodeType;
  const alive = info?.alive ?? wsAlive;

  const lcState = info?.lifecycle_state ?? (wsAlive ? hbData?.lifecycle_state : undefined);

  return (
    <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex flex-col gap-4 flex-1">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Server className="w-4 h-4 text-[var(--text-secondary)]" />
          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            {label}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${alive ? "bg-[var(--accent-green)] shadow-[0_0_6px_var(--accent-green)]" : "bg-[var(--text-muted)]"}`}
            style={alive ? { animation: "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite" } : undefined}
          />
          <span className={`text-[10px] font-mono font-semibold ${alive ? "text-[var(--accent-green)]" : "text-[var(--text-muted)]"}`}>
            {alive ? "ONLINE" : "OFFLINE"}
          </span>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">策略数</span>
          <span className="text-[14px] font-bold font-mono text-[var(--text-primary)]">
            {info?.strategy_count ?? "—"}
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">持仓数</span>
          <span className="text-[14px] font-bold font-mono text-[var(--text-primary)]">
            {info?.position_count ?? "—"}
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">状态</span>
          <span className={`text-[11px] font-bold font-mono uppercase ${
            lcState === "running" ? "text-[var(--accent-green)]" :
            lcState === "paused" ? "text-[var(--accent-amber)]" :
            lcState === "flattening" ? "text-[var(--accent-red)]" :
            "text-[var(--text-muted)]"
          }`}>
            {lcState ?? "—"}
          </span>
        </div>
      </div>
      {info?.trading_state && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--bg-elevated)]">
          <span className="text-[9px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">交易状态</span>
          <span className="text-[10px] font-mono font-semibold text-[var(--accent-blue)] ml-auto">
            {info.trading_state}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const { t } = useI18n();

  // Data states
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [backtestRuns, setBacktestRuns] = useState<BacktestRun[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    async function fetchAll() {
      try {
        const [summaryData, runsData, healthData] = await Promise.allSettled([
          apiGet<DashboardSummary>("/api/dashboard/summary"),
          apiGet<{ runs: BacktestRun[]; total: number }>("/api/backtest/runs", { limit: "10" }),
          apiGet<HealthData>("/api/health"),
        ]);

        if (summaryData.status === "fulfilled" && summaryData.value) {
          setSummary(summaryData.value);
        }
        if (runsData.status === "fulfilled" && runsData.value) {
          setBacktestRuns(runsData.value.runs ?? []);
        }
        if (healthData.status === "fulfilled" && healthData.value) {
          setHealth(healthData.value);
        }
      } catch {
        setError(t("dashboard.loadFailed"));
      } finally {
        setLoading(false);
      }
    }
    fetchAll();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // Derived KPI values (safe defaults for 0-state display)
  const totalEquity = summary?.total_equity ?? 0;
  const dailyPnl = summary?.daily_pnl ?? 0;
  const openPositions = summary?.open_positions ?? 0;
  const activeStrategies = summary?.active_strategy_count ?? 0;
  const equityData = summary?.equity_curve ?? [];
  const isDailyPnlPositive = dailyPnl >= 0;

  // Node info from health
  const sandboxNode = health?.nodes?.sandbox;
  const liveNode = health?.nodes?.live;

  // ---------------------------------------------------------------------------
  // Loading skeleton
  // ---------------------------------------------------------------------------
  if (loading) {
    return (
      <div className="flex flex-col gap-6 p-8">
        {/* Title skeleton */}
        <div className="flex flex-col gap-2">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-72" />
        </div>

        {/* KPI skeletons */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => <KpiCardSkeleton key={i} />)}
        </div>

        {/* Mid row skeletons */}
        <div className="flex gap-4">
          <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex-1">
            <Skeleton className="h-4 w-24 mb-4" />
            <Skeleton className="h-[280px] w-full" />
          </div>
          <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 w-[340px]">
            <Skeleton className="h-4 w-24 mb-4" />
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-10 w-full mb-2" />
            ))}
          </div>
        </div>

        {/* Node row skeletons */}
        <div className="flex gap-4">
          <Skeleton className="h-32 flex-1 rounded-xl" />
          <Skeleton className="h-32 flex-1 rounded-xl" />
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Error state
  // ---------------------------------------------------------------------------
  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <div className="flex flex-col items-center gap-3">
          <span className="font-mono text-[12px] text-[var(--accent-red)]">{error}</span>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="px-4 py-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] text-[11px] font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Main render
  // ---------------------------------------------------------------------------
  return (
    <div className="flex flex-col gap-6 p-8">
      {/* Page header */}
      <FadeIn direction="down" duration={0.25}>
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-[-1px] text-[var(--text-primary)]">
            {t("dashboard.title")}
          </h1>
          <p className="font-mono text-[12px] text-[var(--text-muted)]">
            {t("dashboard.subtitle")}
          </p>
        </div>
      </FadeIn>

      {/* KPI Cards Row */}
      <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" staggerDelay={0.1}>
        <StaggerItem>
          <KpiCard
            label={t("dashboard.totalEquity")}
            value={totalEquity}
            prefix="$"
            decimals={0}
            change="总权益"
            icon={<Wallet className="w-3.5 h-3.5" />}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label={t("dashboard.dailyPnl")}
            value={Math.abs(dailyPnl)}
            prefix={isDailyPnlPositive ? "+$" : "-$"}
            decimals={0}
            change={isDailyPnlPositive ? "今日盈利" : "今日亏损"}
            changePositive={isDailyPnlPositive}
            icon={<TrendingUp className="w-3.5 h-3.5" />}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label={t("dashboard.activePositions")}
            value={openPositions}
            decimals={0}
            change={`${activeStrategies} 个策略运行中`}
            icon={<Activity className="w-3.5 h-3.5" />}
          />
        </StaggerItem>
        <StaggerItem>
          <KpiCard
            label={t("dashboard.sharpeRatio")}
            value={summary?.sharpe_ratio ?? 0}
            decimals={2}
            change="夏普比率"
            changePositive={(summary?.sharpe_ratio ?? 0) >= 1}
            icon={<BarChart3 className="w-3.5 h-3.5" />}
          />
        </StaggerItem>
      </StaggerContainer>

      {/* Main Content Row — chart + recent backtests */}
      <FadeIn direction="up" delay={0.15} duration={0.35}>
        <div className="flex gap-4">
          {/* Equity Curve Chart */}
          <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] flex-1 flex flex-col">
            <div className="flex items-center justify-between px-5 pt-5 pb-3">
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                {t("dashboard.equityCurve")}
              </span>
              <span className="text-[10px] font-mono text-[var(--text-muted)]">ALL</span>
            </div>
            <div className="h-px bg-[var(--border-gray)]" />
            <div className="flex-1 px-3 py-4" style={{ minHeight: 280 }}>
              {equityData.length === 0 ? (
                <div className="flex items-center justify-center h-full" style={{ minHeight: 280 }}>
                  <span className="font-mono text-[11px] text-[var(--text-muted)]">
                    {t("dashboard.noEquityData")}
                  </span>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart data={equityData} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                    <defs>
                      <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#4C9EEB" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#4C9EEB" stopOpacity={0} />
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
                      tickFormatter={(v: number) =>
                        v >= 1_000_000
                          ? `$${(v / 1_000_000).toFixed(1)}M`
                          : v >= 1_000
                          ? `$${(v / 1_000).toFixed(0)}K`
                          : `$${v}`
                      }
                      domain={["dataMin - 1000", "dataMax + 1000"]}
                      width={64}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: "var(--bg-elevated)",
                        border: "1px solid var(--border-gray)",
                        borderRadius: 8,
                        fontSize: 11,
                        fontFamily: "JetBrains Mono",
                        color: "var(--text-primary)",
                      }}
                      formatter={(value: number | undefined) => [`$${Number(value ?? 0).toLocaleString()}`, "权益"]}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke="#4C9EEB"
                      strokeWidth={2}
                      fill="url(#equityGradient)"
                      dot={false}
                      activeDot={{ r: 4, fill: "#4C9EEB", stroke: "var(--bg-card)", strokeWidth: 2 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Recent Backtests */}
          <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] w-[340px] flex flex-col">
            <div className="flex items-center justify-between px-5 pt-5 pb-3">
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                最近回测
              </span>
              <a
                href="/backtest"
                className="text-[10px] font-semibold tracking-[0.5px] text-[var(--accent-blue)] hover:underline"
              >
                {t("dashboard.viewAll")}
              </a>
            </div>
            <div className="h-px bg-[var(--border-gray)]" />
            <div className="flex flex-col overflow-y-auto" style={{ maxHeight: 320 }}>
              {backtestRuns.length === 0 ? (
                <div className="flex items-center justify-center px-5 py-8">
                  <span className="font-mono text-[11px] text-[var(--text-muted)]">暂无回测记录</span>
                </div>
              ) : (
                backtestRuns.map((run, i) => (
                  <div
                    key={run.id}
                    className={`flex items-center justify-between px-5 py-3 ${
                      i < backtestRuns.length - 1 ? "border-b border-[var(--border-gray)]" : ""
                    }`}
                  >
                    <div className="flex flex-col gap-1 min-w-0">
                      <span className="text-[11px] font-semibold text-[var(--text-primary)] truncate max-w-[180px]">
                        {run.strategy_name}
                      </span>
                      {run.symbol && (
                        <span className="text-[10px] font-mono text-[var(--text-muted)]">
                          {run.symbol}
                        </span>
                      )}
                    </div>
                    <StatusBadge status={run.status} />
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </FadeIn>

      {/* Node Status Row */}
      <FadeIn direction="up" delay={0.25} duration={0.35}>
        <div className="flex gap-4">
          <NodeCard
            label="沙盒节点"
            nodeType="sandbox"
            info={sandboxNode}
          />
          <NodeCard
            label="实盘节点"
            nodeType="live"
            info={liveNode}
          />
        </div>
      </FadeIn>
    </div>
  );
}
