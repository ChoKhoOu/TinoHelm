"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { apiGet } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CostParamsTab,
  PredictivePowerTab,
  RobustnessTab,
  SignalProfileTab,
  VerdictBadge,
  type ReportData,
  type VerdictStatus,
} from "./components";

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
      <Button variant="outline" size="sm" onClick={onBack}>
        <ArrowLeft className="w-3.5 h-3.5" />
        返回
      </Button>
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
  if (error || !report) {
    return <ReportError message={error || "报告不存在"} onBack={handleBack} />;
  }

  return (
    <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1100px]">
      {/* Back button */}
      <div className="mb-4">
        <Button variant="outline" size="sm" onClick={handleBack}>
          <ArrowLeft className="w-3.5 h-3.5" />
          返回探索
        </Button>
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
          预测周期:{" "}
          <strong className="text-foreground">{report.forward_period} bars</strong>
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
