"use client";

import { useState, useEffect, Fragment } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { apiGet } from "@/lib/api";
import { StatCard, SectionLabel } from "@/components/qds";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  Cell,
} from "recharts";
import {
  CHART_TOOLTIP_PROPS,
  CHART_GRID_STYLE,
  CHART_AXIS_STYLE,
  CHART_ANIMATION,
} from "@/lib/chartTheme";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface VerdictStatus {
  status: "pass" | "warn" | "fail";
  reason?: string;
}

interface SignalProfileData {
  verdict: VerdictStatus;
  mean: number;
  std: number;
  skewness: number;
  lag1_acf: number;
  rv_corr: number;
  zero_pct: number;
  distribution: { bin: string; count: number }[];
  acf: { lag: number; value: number; ci_upper: number; ci_lower: number }[];
}

interface PredictivePowerData {
  verdict: VerdictStatus;
  ic_mean_h5: number;
  ic_tstat: number;
  icir: number;
  ic_positive_pct: number;
  ic_mean_h15: number;
  rolling_ic: { date: string; ic: number }[];
  quantile_returns: { quantile: string; return_pct: number }[];
  cumulative_returns: {
    dates: string[];
    series: Record<string, number[]>;
  };
}

interface RobustnessData {
  verdict: VerdictStatus;
  shuffle_test: {
    p_value: number;
    distribution: { bin: number; count: number }[];
    real_ic: number;
  };
  sub_period_ic: { period: string; ic: number }[];
  cross_symbol_ic: { symbol: string; ic: number }[];
}

interface CostParamsData {
  verdict: VerdictStatus;
  waterfall: { label: string; value: number; type: "positive" | "negative" | "net" }[];
  heatmap: {
    x_labels: string[];
    y_labels: string[];
    values: number[][];
  };
  param_sweep: { param_value: number; ic: number }[];
}

interface ReportData {
  id: string;
  factor_name: string;
  symbol: string;
  forward_period: number;
  created_at: string;
  params?: Record<string, unknown>;
  signal_profile: SignalProfileData;
  predictive_power: PredictivePowerData;
  robustness: RobustnessData;
  cost_params: CostParamsData;
}

/* ------------------------------------------------------------------ */
/*  VerdictBadge                                                       */
/* ------------------------------------------------------------------ */

function VerdictBadge({ status }: { status: string }) {
  const cls: Record<string, string> = {
    pass: "bg-qds-success-dim text-qds-success",
    warn: "bg-qds-warning-dim text-qds-warning",
    fail: "bg-qds-danger-dim text-destructive",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 font-mono text-[10px] font-medium px-2 py-0.5 rounded-full ${cls[status] || ""}`}
    >
      {status === "pass" ? "\u2713" : status === "warn" ? "\u26A0" : "\u2717"}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Chart Card wrapper                                                 */
/* ------------------------------------------------------------------ */

function ChartCard({
  title,
  sub,
  badge,
  children,
  className,
}: {
  title: string;
  sub?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border bg-card overflow-hidden transition-colors duration-150 hover:border-qds-border-hover ${className || ""}`}
    >
      <div className="flex justify-between items-center px-3 py-2.5 border-b text-[0.72rem] font-semibold">
        <span>{title}</span>
        <span className="font-mono text-[0.58rem] font-normal text-muted-foreground">
          {badge || sub || ""}
        </span>
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Heatmap Component                                                  */
/* ------------------------------------------------------------------ */

function Heatmap({
  xLabels,
  yLabels,
  values,
}: {
  xLabels: string[];
  yLabels: string[];
  values: number[][];
}) {
  const maxAbs = Math.max(
    ...values.flat().map((v) => Math.abs(v)),
    0.001
  );

  function cellColor(v: number): string {
    const opacity = Math.min(Math.abs(v) / maxAbs, 1) * 0.7 + 0.15;
    if (v > 0) return `rgba(54,136,75,${opacity.toFixed(2)})`;
    if (v < 0) return `rgba(254,129,129,${opacity.toFixed(2)})`;
    return "rgba(128,128,128,0.15)";
  }

  return (
    <div
      className="font-mono text-[0.58rem]"
      style={{
        display: "grid",
        gridTemplateColumns: `auto repeat(${xLabels.length}, 1fr)`,
        gap: 2,
      }}
    >
      {/* top-left corner */}
      <div />
      {/* x header */}
      {xLabels.map((x) => (
        <div
          key={x}
          className="text-center text-muted-foreground flex items-center justify-center"
          style={{ padding: "0.25rem 0.2rem", fontSize: "0.55rem" }}
        >
          {x}
        </div>
      ))}
      {/* rows */}
      {yLabels.map((y, ri) => (
        <Fragment key={`row-${y}`}>
          <div
            className="text-muted-foreground flex items-center justify-center"
            style={{ padding: "0.25rem 0.35rem", fontSize: "0.55rem" }}
          >
            {y}
          </div>
          {xLabels.map((x, ci) => {
            const v = values[ri]?.[ci] ?? 0;
            return (
              <div
                key={`${y}-${x}`}
                className="text-center rounded-sm font-medium cursor-default transition-transform duration-150 hover:scale-110 hover:z-10"
                style={{
                  padding: "0.3rem 0.2rem",
                  background: cellColor(v),
                  color: "var(--t0)",
                }}
                title={`${y} x ${x}: ${v.toFixed(3)}`}
              >
                {v.toFixed(2)}
              </div>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Waterfall Component                                                */
/* ------------------------------------------------------------------ */

function Waterfall({
  items,
}: {
  items: { label: string; value: number; type: "positive" | "negative" | "net" }[];
}) {
  const maxVal = Math.max(...items.map((i) => Math.abs(i.value)), 0.001);

  return (
    <div className="flex flex-col gap-1.5">
      {items.map((item) => {
        const widthPct = (Math.abs(item.value) / maxVal) * 100;
        const isPositive = item.type === "positive" || (item.type === "net" && item.value >= 0);
        return (
          <div key={item.label} className="flex items-center gap-2 font-mono text-[0.72rem]">
            <span className="w-20 text-right text-muted-foreground text-[0.68rem] shrink-0">
              {item.label}
            </span>
            <div className="flex-1 h-5 relative">
              <div
                className={`h-full rounded-sm transition-all duration-700 ${isPositive ? "bg-qds-success" : "bg-destructive"}`}
                style={{ width: `${widthPct}%`, opacity: 0.7 }}
              />
            </div>
            <span
              className={`font-medium min-w-[70px] ${isPositive ? "text-qds-success" : "text-destructive"}`}
            >
              {item.value >= 0 ? "+" : ""}
              {(item.value * 100).toFixed(1)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab definitions                                                    */
/* ------------------------------------------------------------------ */

const TABS = [
  { key: "profile", label: "Signal Profile" },
  { key: "predict", label: "Predictive Power" },
  { key: "robust", label: "Robustness" },
  { key: "cost", label: "Cost & Params" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function tabVerdict(report: ReportData | null, key: TabKey): string {
  if (!report) return "";
  const map: Record<TabKey, VerdictStatus | undefined> = {
    profile: report.signal_profile?.verdict,
    predict: report.predictive_power?.verdict,
    robust: report.robustness?.verdict,
    cost: report.cost_params?.verdict,
  };
  return map[key]?.status ?? "";
}

/* ------------------------------------------------------------------ */
/*  Tab 1: Signal Profile                                              */
/* ------------------------------------------------------------------ */

function SignalProfileTab({ data }: { data: SignalProfileData }) {
  const zeroPctTrend =
    data.zero_pct < 10 ? "up" : data.zero_pct > 30 ? "down" : "neutral";

  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-6 gap-3 mb-5">
        <StatCard label="均值" value={data.mean.toFixed(3)} help="因子值的算术平均，偏离 0 太远可能有偏" />
        <StatCard label="标准差" value={data.std.toFixed(3)} help="因子值的离散程度" />
        <StatCard label="偏度" value={data.skewness.toFixed(2)} help="|skew|>3 说明分布严重偏斜" />
        <StatCard label="lag-1 ACF" value={data.lag1_acf.toFixed(2)} help="滞后一期自相关" />
        <StatCard label="与RV相关" value={data.rv_corr.toFixed(2)} help="和已实现波动率的相关性" />
        <StatCard
          label="零值占比"
          value={`${data.zero_pct.toFixed(1)}%`}
          trend={zeroPctTrend}
          help=">30% 说明信号太稀疏"
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <ChartCard title="信号分布" sub="Histogram">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.distribution}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="bin" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <Bar
                  dataKey="count"
                  fill="var(--acc)"
                  radius={[2, 2, 0, 0]}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="自相关函数" sub="95% 置信区间">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.acf}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="lag" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} domain={[-1, 1]} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" />
                {data.acf.length > 0 && (
                  <>
                    <ReferenceLine
                      y={data.acf[0]?.ci_upper ?? 0.1}
                      stroke="var(--warn)"
                      strokeDasharray="4 4"
                      strokeOpacity={0.5}
                    />
                    <ReferenceLine
                      y={data.acf[0]?.ci_lower ?? -0.1}
                      stroke="var(--warn)"
                      strokeDasharray="4 4"
                      strokeOpacity={0.5}
                    />
                  </>
                )}
                <Bar dataKey="value" animationDuration={CHART_ANIMATION.duration} radius={[2, 2, 0, 0]}>
                  {data.acf.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={entry.value >= 0 ? "var(--info)" : "var(--dan)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 2: Predictive Power                                            */
/* ------------------------------------------------------------------ */

function PredictivePowerTab({ data }: { data: PredictivePowerData }) {
  const icTrend = data.ic_mean_h5 > 0.03 ? "up" : data.ic_mean_h5 < 0 ? "down" : "neutral";
  const tstatTrend = data.ic_tstat >= 2.0 ? "up" : "neutral";
  const icirTrend = data.icir >= 0.5 ? "up" : "neutral";

  // Build cumulative return series for LineChart
  const cumDates = data.cumulative_returns?.dates ?? [];
  const cumSeries = data.cumulative_returns?.series ?? {};
  const cumData = cumDates.map((d, i) => {
    const point: Record<string, string | number> = { date: d };
    for (const [key, vals] of Object.entries(cumSeries)) {
      point[key] = vals[i] ?? 0;
    }
    return point;
  });
  const cumKeys = Object.keys(cumSeries);
  const cumColors = ["var(--suc)", "var(--info)", "var(--warn)", "var(--dan)", "var(--t2)", "var(--acc)"];

  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-5 gap-3 mb-5">
        <StatCard label={`IC\u0304 (h=5)`} value={data.ic_mean_h5.toFixed(3)} trend={icTrend} help="预测周期=5时的 IC 均值" />
        <StatCard label="IC t-stat" value={data.ic_tstat.toFixed(2)} trend={tstatTrend} help=">2.0 表示 95% 置信度下显著" />
        <StatCard label="ICIR" value={data.icir.toFixed(2)} trend={icirTrend} help={`IC\u0304 / IC Std`} />
        <StatCard label="IC>0%" value={`${data.ic_positive_pct}%`} help="IC 为正的时间占比" />
        <StatCard label={`IC\u0304 (h=15)`} value={data.ic_mean_h15.toFixed(3)} help="更长预测周期的 IC" />
      </div>
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard title="Rolling IC" sub="window=60">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.rolling_ic}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="date" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="4 4" />
                <Line
                  type="monotone"
                  dataKey="ic"
                  stroke="var(--acc)"
                  strokeWidth={1.5}
                  dot={false}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="分位数平均收益">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.quantile_returns} layout="vertical">
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis type="number" tick={CHART_AXIS_STYLE} />
                <YAxis type="category" dataKey="quantile" tick={CHART_AXIS_STYLE} width={36} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine x={0} stroke="var(--t3)" />
                <Bar dataKey="return_pct" animationDuration={CHART_ANIMATION.duration} radius={[0, 3, 3, 0]}>
                  {data.quantile_returns.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={entry.return_pct >= 0 ? "var(--suc)" : "var(--dan)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
      <ChartCard title="分层累计收益" sub="Q1~Q5 + L/S">
        <div style={{ height: 240 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={cumData}>
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis dataKey="date" tick={CHART_AXIS_STYLE} />
              <YAxis tick={CHART_AXIS_STYLE} />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="4 4" />
              {cumKeys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={cumColors[i % cumColors.length]}
                  strokeWidth={key === "L/S" ? 2 : 1.2}
                  dot={false}
                  strokeDasharray={key === "L/S" ? "6 3" : undefined}
                  animationDuration={CHART_ANIMATION.duration}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 3: Robustness                                                  */
/* ------------------------------------------------------------------ */

function RobustnessTab({ data }: { data: RobustnessData }) {
  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard
          title="Shuffle Test"
          badge={
            <span className="font-mono text-[0.6rem] px-2 py-0.5 rounded-full bg-qds-success-dim text-qds-success">
              p={data.shuffle_test.p_value.toFixed(3)}
            </span>
          }
        >
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.shuffle_test.distribution}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="bin" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine
                  x={data.shuffle_test.real_ic}
                  stroke="var(--dan)"
                  strokeWidth={2}
                  label={{ value: "Real IC", fill: "var(--dan)", fontSize: 10 }}
                />
                <Bar
                  dataKey="count"
                  fill="var(--t3)"
                  radius={[2, 2, 0, 0]}
                  animationDuration={CHART_ANIMATION.duration}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="分段 IC" sub={`正段: ${Math.round((data.sub_period_ic.filter((d) => d.ic > 0).length / Math.max(data.sub_period_ic.length, 1)) * 100)}%`}>
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.sub_period_ic}>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="period" tick={CHART_AXIS_STYLE} />
                <YAxis tick={CHART_AXIS_STYLE} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                <ReferenceLine y={0} stroke="var(--t3)" />
                <Bar dataKey="ic" animationDuration={CHART_ANIMATION.duration} radius={[2, 2, 0, 0]}>
                  {data.sub_period_ic.map((entry, idx) => (
                    <Cell key={idx} fill={entry.ic >= 0 ? "var(--suc)" : "var(--dan)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
      <SectionLabel>跨品种 IC</SectionLabel>
      <ChartCard title="跨品种 IC 水平">
        <div style={{ height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.cross_symbol_ic} layout="vertical">
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis type="number" tick={CHART_AXIS_STYLE} />
              <YAxis type="category" dataKey="symbol" tick={CHART_AXIS_STYLE} width={80} />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <ReferenceLine x={0} stroke="var(--t3)" />
              <Bar dataKey="ic" animationDuration={CHART_ANIMATION.duration} radius={[0, 3, 3, 0]}>
                {data.cross_symbol_ic.map((entry, idx) => (
                  <Cell key={idx} fill={entry.ic >= 0 ? "var(--suc)" : "var(--dan)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 4: Cost & Params                                               */
/* ------------------------------------------------------------------ */

function CostParamsTab({ data }: { data: CostParamsData }) {
  return (
    <div className="animate-qds-fade-up">
      <div className="grid grid-cols-2 gap-4 mb-5">
        <ChartCard title="Edge Waterfall">
          <Waterfall items={data.waterfall} />
        </ChartCard>
        <ChartCard title="参数热力图" sub="lookback x forward_period">
          <Heatmap
            xLabels={data.heatmap.x_labels}
            yLabels={data.heatmap.y_labels}
            values={data.heatmap.values}
          />
        </ChartCard>
      </div>
      <ChartCard title="单参数扫描" sub="平滑度">
        <div style={{ height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.param_sweep}>
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis dataKey="param_value" tick={CHART_AXIS_STYLE} />
              <YAxis tick={CHART_AXIS_STYLE} />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              <Line
                type="monotone"
                dataKey="ic"
                stroke="var(--acc)"
                strokeWidth={2}
                dot={{ r: 3, fill: "var(--acc)" }}
                animationDuration={CHART_ANIMATION.duration}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function ReportSkeleton() {
  return (
    <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1100px]">
      <Skeleton className="h-8 w-20 mb-4" />
      <div className="flex gap-4 mb-5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-40" />
      </div>
      <Skeleton className="h-10 w-96 mb-5" />
      <div className="grid grid-cols-6 gap-3 mb-5">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Error state                                                        */
/* ------------------------------------------------------------------ */

function ReportError({ message, onBack }: { message: string; onBack: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
      <div className="text-2xl text-qds-t3 mb-4">{"\u26A0"}</div>
      <div className="text-sm font-semibold mb-1">加载报告失败</div>
      <div className="text-xs text-muted-foreground mb-4 max-w-xs">{message}</div>
      <button
        onClick={onBack}
        className="font-mono text-[0.72rem] px-3 py-1.5 rounded-md border bg-transparent text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all duration-150 cursor-pointer"
      >
        {"\u2190"} 返回
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page Component                                                */
/* ------------------------------------------------------------------ */

export default function ReportClient() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [report, setReport] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("profile");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<ReportData>(`/api/research/report/${id}`)
      .then((data) => {
        if (!cancelled && data) setReport(data);
        if (!cancelled && !data) setError("报告不存在");
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "未知错误");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleBack = () => router.push("/research");

  if (loading) return <ReportSkeleton />;
  if (error || !report) return <ReportError message={error || "报告不存在"} onBack={handleBack} />;

  return (
    <div className="flex-1 overflow-y-auto px-8 py-5" style={{ maxWidth: 1100 }}>
      {/* Back button */}
      <div className="mb-4">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-1.5 font-mono text-[0.72rem] px-3 py-1.5 rounded-md border bg-transparent text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all duration-150 cursor-pointer"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          返回探索
        </button>
      </div>

      {/* Meta info */}
      <div className="flex flex-wrap gap-6 mb-5 font-mono text-[0.68rem] text-muted-foreground">
        <span className="flex items-center gap-1">
          因子: <strong className="text-foreground">{report.factor_name}</strong>
        </span>
        <span className="flex items-center gap-1">
          品种: <strong className="text-foreground">{report.symbol}</strong>
        </span>
        <span className="flex items-center gap-1">
          预测周期: <strong className="text-foreground">{report.forward_period} bars</strong>
        </span>
        <span className="flex items-center gap-1">
          生成时间: <strong className="text-foreground">{report.created_at}</strong>
        </span>
      </div>

      {/* Tab bar with verdict badges */}
      <div className="flex gap-0.5 bg-input rounded-md p-[3px] mb-5 w-fit">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          const verdict = tabVerdict(report, tab.key);
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`font-mono text-[0.68rem] px-3 py-1.5 rounded flex items-center gap-1.5 border-0 cursor-pointer whitespace-nowrap transition-all duration-150 ${
                isActive
                  ? "bg-secondary text-foreground shadow-sm"
                  : "bg-transparent text-muted-foreground hover:text-qds-t1"
              }`}
            >
              {tab.label}
              {verdict && <VerdictBadge status={verdict} />}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === "profile" && report.signal_profile && (
        <SignalProfileTab data={report.signal_profile} />
      )}
      {activeTab === "predict" && report.predictive_power && (
        <PredictivePowerTab data={report.predictive_power} />
      )}
      {activeTab === "robust" && report.robustness && (
        <RobustnessTab data={report.robustness} />
      )}
      {activeTab === "cost" && report.cost_params && (
        <CostParamsTab data={report.cost_params} />
      )}
    </div>
  );
}
