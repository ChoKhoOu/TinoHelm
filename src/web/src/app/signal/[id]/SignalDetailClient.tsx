"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Download, RotateCcw } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  HelpTip,
  InlineError,
  PageHeader,
  SectionLabel,
  ShimmerBar,
  StatCard,
  StatusBadge,
} from "@/components/qds";
import { apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import type { ShimmerStage } from "@/components/qds";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRADIENT_OPACITY,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import { cn } from "@/lib/utils";
import { useSignalCatalogue } from "../hooks/useSignalList";
import { useSignalDetail } from "../hooks/useSignalDetail";
import type {
  SignalEvalResult,
  SignalListItem,
  SignalReportResponse,
} from "../types";

type DetailTab = "overview" | "performance" | "constraints" | "cost";

const TABS: { id: DetailTab; label: string; help: string }[] = [
  {
    id: "overview",
    label: "Overview",
    help: "信号 spec：method、factor_ref、rebalance_freq、constraints。",
  },
  {
    id: "performance",
    label: "Performance",
    help: "Sharpe / MDD / Capacity / Total return + cumulative PnL 曲线。",
  },
  {
    id: "constraints",
    label: "Constraints",
    help: "Gross / Net / Max position 等约束的实际生效值。",
  },
  {
    id: "cost",
    label: "Cost",
    help: "Cost model 预设与 cost_drag 实际值。",
  },
];

/**
 * /signal/[id] detail page client shell.
 *
 * Reads ``run_id`` from the URL via ``useParams`` (static-export compatible).
 * Polling is delegated to ``useSignalDetail`` which stops once status is
 * terminal.  Layer-2: errors render via ``<InlineError />``; never toast.
 */
export function SignalDetailClient() {
  const params = useParams();
  const router = useRouter();
  const runId = (params?.id as string) ?? "";

  const { report, loading, error, reload } = useSignalDetail(runId);
  const { items: catalogue } = useSignalCatalogue();

  const [activeTab, setActiveTab] = useState<DetailTab>("overview");

  const cancelAction = useAction(
    async () => {
      await apiPost<{ run_id: string; cancel_set: boolean }>(
        `/api/signal/cancel/${runId}`,
      );
      reload();
    },
    { successDuration: 2000 },
  );

  /** Look up the registry entry by signal name — used for spec defaults. */
  const spec = useMemo<SignalListItem | undefined>(() => {
    if (!report) return undefined;
    return catalogue.find((s) => s.name === report.signal_name);
  }, [catalogue, report]);

  if (loading && !report) return <DetailSkeleton />;

  if (error) {
    return (
      <div className="flex-1 overflow-y-auto px-6 py-5 max-w-[1280px]">
        <BackBar onBack={() => router.push("/signal")} />
        <InlineError>{`加载失败: ${error}`}</InlineError>
        <div className="mt-3">
          <Button variant="outline" size="sm" onClick={reload}>
            <RotateCcw className="w-3.5 h-3.5" />
            重试
          </Button>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex-1 overflow-y-auto px-6 py-5 max-w-[1280px]">
        <BackBar onBack={() => router.push("/signal")} />
        <InlineError>SignalRun 不存在或尚未提交</InlineError>
      </div>
    );
  }

  const isCompleted = report.status === "completed" && !!report.result;
  const isInflight = report.status === "queued" || report.status === "running";

  const handleExport = () => {
    /* Static export: no apiGet streaming, so we direct-link to the JSON
       payload via a same-origin <a> anchor.  The user's browser will
       download / open the JSON. */
    if (typeof window !== "undefined") {
      window.open(`/api/signal/export/${runId}`, "_blank");
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5 pb-16 max-w-[1280px]">
      <BackBar onBack={() => router.push("/signal")} />

      <PageHeader
        title={report.signal_name}
        subtitle={`run_id ${report.run_id.slice(0, 8)} · factor ${report.factor_ref}`}
        actions={
          <>
            {isInflight && (
              <Button
                variant="destructive"
                size="sm"
                onClick={cancelAction.execute}
                disabled={cancelAction.state === "loading"}
              >
                {cancelAction.state === "loading" ? "取消中..." : "取消运行"}
              </Button>
            )}
            {isCompleted && (
              <Button variant="outline" size="sm" onClick={handleExport}>
                <Download className="w-3.5 h-3.5" />
                导出 portfolio.yaml
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={reload}>
              <RotateCcw className="w-3.5 h-3.5" />
              刷新
            </Button>
          </>
        }
      />

      {/* Meta strip */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-5">
        <StatusBadge status={report.status} locale="en" />
        {report.progress_stage && (
          <span className="font-mono text-[0.68rem] text-muted-foreground">
            stage: <span className="text-foreground">{report.progress_stage}</span>
          </span>
        )}
        {report.progress != null && !isCompleted && (
          <span className="font-mono text-[0.68rem] text-muted-foreground">
            progress: <span className="text-primary font-semibold">{report.progress}%</span>
          </span>
        )}
      </div>

      {cancelAction.state === "error" && cancelAction.error && (
        <InlineError>{`取消失败: ${cancelAction.error}`}</InlineError>
      )}

      {/* In-flight progress card */}
      {isInflight && (
        <div className="rounded-lg border bg-card p-4 mb-5">
          <div className="flex items-center justify-between mb-3">
            <div className="font-mono text-[0.6rem] uppercase tracking-widest text-primary">
              Progress · {report.progress_stage ?? "starting"}
            </div>
            <div className="font-mono text-[0.65rem] text-primary font-semibold">
              {report.progress ?? 0}%
            </div>
          </div>
          <ShimmerBar
            progress={report.progress ?? 0}
            height="md"
            active={report.status === "running"}
            variant="accent"
            showStages
            stage={(report.progress_stage as ShimmerStage) ?? null}
          />
          <div className="font-mono text-[0.65rem] text-muted-foreground mt-2.5">
            后端每秒推送 progress；本页 4 秒轮询 /api/signal/report。
          </div>
        </div>
      )}

      {report.status === "failed" && report.error && (
        <div className="mb-5">
          <InlineError>{report.error}</InlineError>
        </div>
      )}

      {/* Tab nav (segmented pill — preview/component-tabs.html .tabs) */}
      <div
        className="inline-flex gap-0.5 bg-input rounded-md p-[3px] mb-5"
        role="tablist"
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === activeTab}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "font-mono text-[0.7rem] px-3 py-1.5 rounded inline-flex items-center gap-1.5 cursor-pointer transition-colors duration-150 ease-qds",
              tab.id === activeTab
                ? "bg-secondary text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            <HelpTip text={tab.help} />
          </button>
        ))}
      </div>

      {/* Tab body */}
      {activeTab === "overview" && (
        <OverviewTab report={report} spec={spec} />
      )}
      {activeTab === "performance" && (
        <PerformanceTab result={report.result} />
      )}
      {activeTab === "constraints" && (
        <ConstraintsTab spec={spec} />
      )}
      {activeTab === "cost" && (
        <CostTab spec={spec} result={report.result} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BackBar                                                             */
/* ------------------------------------------------------------------ */

function BackBar({ onBack }: { onBack: () => void }) {
  return (
    <div className="mb-3">
      <Button variant="outline" size="sm" onClick={onBack}>
        <ArrowLeft className="w-3.5 h-3.5" />
        返回 Signals
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  OverviewTab — spec snapshot                                        */
/* ------------------------------------------------------------------ */

interface OverviewTabProps {
  report: SignalReportResponse;
  spec: SignalListItem | undefined;
}

function OverviewTab({ report, spec }: OverviewTabProps) {
  const items: { label: string; value: string }[] = [
    { label: "Method",         value: spec?.method ?? "—" },
    { label: "Weighting",      value: spec?.weighting ?? "—" },
    { label: "Factor ref",     value: report.factor_ref || "—" },
    { label: "Rebalance freq", value: spec?.rebalance_freq ?? "—" },
    { label: "Universe ref",   value: spec?.universe_ref ?? "—" },
    { label: "Version",        value: spec?.version ?? "—" },
  ];

  return (
    <div className="flex flex-col gap-5">
      <div>
        <SectionLabel>Spec · 信号定义</SectionLabel>
        <div className="rounded-lg border bg-card overflow-hidden">
          {items.map((row, i) => (
            <div
              key={row.label}
              className={cn(
                "flex justify-between items-baseline px-4 py-2.5",
                i < items.length - 1 && "border-b border-border",
              )}
            >
              <span className="font-mono text-[0.65rem] uppercase tracking-wider text-muted-foreground">
                {row.label}
              </span>
              <span className="font-mono text-[0.78rem] text-foreground">
                {row.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {spec?.description && (
        <div>
          <SectionLabel>Description</SectionLabel>
          <div className="rounded-lg border bg-card p-4 text-sm text-foreground leading-relaxed">
            {spec.description}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  PerformanceTab — KPI grid + cumulative PnL chart                   */
/* ------------------------------------------------------------------ */

interface PerformanceTabProps {
  result: SignalEvalResult | undefined;
}

function PerformanceTab({ result }: PerformanceTabProps) {
  const chartData = useMemo(
    () =>
      result
        ? result.net_pnl_curve.map((v, i) => ({
            idx: i,
            net: v,
            gross: result.gross_pnl_curve[i] ?? null,
          }))
        : [],
    [result],
  );

  if (!result) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center font-mono text-[0.7rem] text-muted-foreground">
        运行未完成 · 暂无评估结果
      </div>
    );
  }

  const sharpeTrend = result.sharpe >= 1 ? "up" : result.sharpe < 0 ? "down" : "neutral";
  const returnTrend = result.total_return >= 0 ? "up" : "down";
  const mddTrend = "down" as const;

  return (
    <div className="flex flex-col gap-5">
      {/* KPI grid */}
      <div>
        <SectionLabel>KPIs</SectionLabel>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label="Sharpe"
            value={result.sharpe.toFixed(2)}
            sub="annualised"
            trend={sharpeTrend}
            help="Annualised Sharpe ratio of net period returns."
          />
          <StatCard
            label="Max DD"
            value={`${(result.mdd * 100).toFixed(2)}%`}
            sub="positive fraction"
            trend={mddTrend}
            help="Maximum drawdown of the net cumulative PnL curve."
          />
          <StatCard
            label="Total Return"
            value={`${result.total_return >= 0 ? "+" : ""}${(result.total_return * 100).toFixed(2)}%`}
            sub="net of cost"
            trend={returnTrend}
            help="Last value of net_pnl_curve."
          />
          <StatCard
            label="Capacity"
            value={result.capacity_score.toFixed(3)}
            sub="0 = single asset · ~0.75 = 4-leg eq weight"
            help="Portfolio diversification proxy in [0,1]."
          />
        </div>
      </div>

      {/* Cumulative PnL chart */}
      <div>
        <SectionLabel>Cumulative PnL</SectionLabel>
        <div className="rounded-lg border bg-card p-4">
          {chartData.length === 0 ? (
            <div className="h-[260px] flex items-center justify-center font-mono text-[0.72rem] text-muted-foreground">
              暂无 PnL 曲线
            </div>
          ) : (
            <div style={{ width: "100%", height: 260 }}>
              <ResponsiveContainer>
                <AreaChart
                  data={chartData}
                  margin={{ top: 12, right: 12, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="net-pnl-gradient" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="0%"
                        stopColor="var(--acc)"
                        stopOpacity={CHART_GRADIENT_OPACITY.areaFill}
                      />
                      <stop offset="100%" stopColor="var(--acc)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis
                    dataKey="idx"
                    tick={CHART_AXIS_STYLE}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={32}
                  />
                  <YAxis
                    tick={CHART_AXIS_STYLE}
                    tickLine={false}
                    axisLine={false}
                    width={48}
                    tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`}
                  />
                  <RechartsTooltip
                    {...CHART_TOOLTIP_PROPS}
                    formatter={(v: unknown, name: unknown) => [
                      typeof v === "number" ? `${(v * 100).toFixed(3)}%` : String(v ?? ""),
                      String(name ?? ""),
                    ]}
                    labelFormatter={(label: unknown) => `period ${String(label ?? "")}`}
                  />
                  <Area
                    type="monotone"
                    dataKey="net"
                    name="net"
                    stroke="var(--acc)"
                    strokeWidth={1.5}
                    fill="url(#net-pnl-gradient)"
                    animationDuration={CHART_ANIMATION.duration}
                    animationEasing={CHART_ANIMATION.easing}
                  />
                  <Area
                    type="monotone"
                    dataKey="gross"
                    name="gross"
                    stroke="var(--info)"
                    strokeWidth={1}
                    strokeDasharray="4 3"
                    fill="transparent"
                    animationDuration={CHART_ANIMATION.duration}
                    animationEasing={CHART_ANIMATION.easing}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="flex flex-wrap gap-4 mt-3 font-mono text-[0.65rem] text-muted-foreground">
            <span>
              <span className="inline-block w-3 h-0.5 align-middle mr-1.5 bg-primary" />
              net (after cost)
            </span>
            <span>
              <span className="inline-block w-3 h-0.5 align-middle mr-1.5 bg-qds-info" />
              gross (before cost)
            </span>
            <span className="ml-auto">n_periods = {result.n_periods}</span>
          </div>
        </div>
      </div>

      {/* Secondary metrics */}
      <div>
        <SectionLabel>Risk &amp; Tail</SectionLabel>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <StatCard
            label="Tail Loss p99"
            value={`${(result.tail_loss_p99 * 100).toFixed(3)}%`}
            sub="worst 1% period"
            trend={result.tail_loss_p99 < 0 ? "down" : "neutral"}
            help="1st-percentile of net period returns; usually negative."
          />
          <StatCard
            label="Turnover (ann.)"
            value={`${(result.turnover_annualized * 100).toFixed(1)}%`}
            sub="single-sided, periods_per_year"
            help="Mean per-period single-sided turnover × periods_per_year."
          />
          <StatCard
            label="Cost Drag"
            value={`${(result.cost_drag * 100).toFixed(3)}%`}
            sub="gross − net"
            trend="down"
            help="Total cost drag = sum of per-period costs."
          />
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ConstraintsTab                                                      */
/* ------------------------------------------------------------------ */

interface ConstraintsTabProps {
  spec: SignalListItem | undefined;
}

function ConstraintsTab({ spec }: ConstraintsTabProps) {
  if (!spec) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center font-mono text-[0.7rem] text-muted-foreground">
        信号未在注册表中，无 constraints 数据
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <StatCard
        label="Gross Exposure"
        value={spec.gross_exposure.toFixed(2)}
        sub="Σ|wᵢ| ≤ this"
        help="Upper bound on the sum of absolute weights per timestamp."
      />
      <StatCard
        label="Net Exposure"
        value={spec.net_exposure.toFixed(2)}
        sub="|Σwᵢ| ≤ this"
        help="Upper bound on the absolute sum of signed weights per timestamp."
      />
      <StatCard
        label="Max Position"
        value={spec.max_position.toFixed(3)}
        sub="|wᵢ| ≤ this"
        help="Per-asset weight magnitude cap."
      />
      <StatCard
        label="Extra Warmup"
        value={String(spec.extra_warmup_bars)}
        sub="bars (additive to factor lookback)"
        help="Additional warmup beyond factor.lookback used by NT adapter."
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CostTab                                                             */
/* ------------------------------------------------------------------ */

interface CostTabProps {
  spec: SignalListItem | undefined;
  result: SignalEvalResult | undefined;
}

function CostTab({ spec, result }: CostTabProps) {
  /* Cost model defaults — sourced from SignalSpec.cost_model on the
     registry endpoint; the GET /list response does not surface CostModel
     directly so we display the run's cost_drag as the ground-truth signal
     and show static taker_8bps preset hints alongside. */
  return (
    <div className="flex flex-col gap-5">
      <div>
        <SectionLabel>Cost Model · 默认 taker_8bps</SectionLabel>
        <div className="rounded-lg border bg-card p-4 grid grid-cols-1 md:grid-cols-3 gap-3">
          <KvLine label="Preset" value="taker_8bps" />
          <KvLine label="Fee per side" value="4 bps" />
          <KvLine label="Slippage per side" value="1 bps" />
          <KvLine label="Rebate per side" value="0 bps" />
          <KvLine
            label="Round-trip cost"
            value="≈ 10 bps"
            help="2 × (fee + slippage − rebate)"
          />
          <KvLine label="Source" value={spec ? "registry" : "—"} />
        </div>
      </div>

      <div>
        <SectionLabel>Realised Drag</SectionLabel>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <StatCard
            label="Cost Drag"
            value={
              result ? `${(result.cost_drag * 100).toFixed(3)}%` : "—"
            }
            sub="gross_total − net_total"
            trend={result ? "down" : undefined}
            help="Sum of all per-period costs deducted from gross PnL."
          />
          <StatCard
            label="Turnover (ann.)"
            value={
              result ? `${(result.turnover_annualized * 100).toFixed(1)}%` : "—"
            }
            sub="annualised single-sided"
            help="Higher turnover → higher cost drag for the same model."
          />
        </div>
      </div>

      <div className="rounded-md border bg-card p-3 text-xs text-muted-foreground leading-relaxed">
        <strong className="text-foreground font-medium">研究 vs Live 范围说明：</strong>
        {" "}研究侧 Cost Drag 使用完整的 fee + slippage − rebate 模型估算（上方 3 项）；
        Live commission monitor（<code className="font-mono text-[0.7rem]">signal.commission.deviation</code>）
        仅验证交易所手续费偏离，不包含 slippage 或 rebate。
        Slippage / rebate 的实测对比是 follow-up 工作。
      </div>
    </div>
  );
}

interface KvLineProps {
  label: string;
  value: string;
  help?: string;
}

function KvLine({ label, value, help }: KvLineProps) {
  return (
    <div className="flex flex-col gap-1">
      <div className="font-mono text-[0.6rem] uppercase tracking-widest text-muted-foreground inline-flex items-center gap-1">
        {label}
        {help && <HelpTip text={help} />}
      </div>
      <div className="font-mono text-[0.85rem] font-medium text-foreground">
        {value}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Skeleton                                                            */
/* ------------------------------------------------------------------ */

function DetailSkeleton() {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-5 max-w-[1280px]">
      <Skeleton className="h-8 w-32 mb-4" />
      <Skeleton className="h-7 w-72 mb-2" />
      <Skeleton className="h-4 w-56 mb-5" />
      <Skeleton className="h-10 w-96 mb-5" />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
