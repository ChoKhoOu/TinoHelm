"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineError, StatusBadge } from "@/components/qds";
import { useCancelFactorRun } from "../../hooks/useCancelFactorRun";
import { cn } from "@/lib/utils";
import { SignalProfileTab } from "./components/SignalProfileTab";
import { PredictivePowerTab } from "./components/PredictivePowerTab";
import { RobustnessTab } from "./components/RobustnessTab";
import { CostParamsTab } from "./components/CostParamsTab";
import { useReport } from "./hooks/useReport";
import {
  TABS,
  tabVerdict,
  type TabKey,
  type TabVerdict,
} from "./components/types";

/**
 * /factor/report/[id] main client shell.
 *
 * Layout (mirrors the Web UI Kit main-content frame):
 *   - Back button
 *   - Meta row (factor name, status badge, created/finished timestamps)
 *   - Tab bar (4 tabs with per-tab verdict pill)
 *   - Tab content (4 panels, switched by ``activeTab`` state)
 *
 * Error strategy:
 *   - Network / API transport errors → ``<InlineError />`` under back button
 *     (Layer-2 contract — NO toast).
 *   - ``status === "failed"``  → show ``run.error`` via ``<InlineError />``.
 *   - ``status === "queued" | "running"`` → progress skeleton with hint.
 */
export function ReportClient() {
  const params = useParams();
  const router = useRouter();
  const runId = (params?.id as string) ?? "";

  const { report, loading, error, reload } = useReport(runId);

  const [activeTab, setActiveTab] = useState<TabKey>("profile");

  const cancelAction = useCancelFactorRun();

  const result = report?.result;

  const handleBack = () => router.push("/factor");

  /* ------------------------------------------------------------------ */
  /*  Auto-refresh while queued / running (5s interval)                  */
  /* ------------------------------------------------------------------ */

  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  useEffect(() => {
    const status = report?.status;
    const shouldPoll = status === "queued" || status === "running";

    if (!shouldPoll) return;

    const id = setInterval(() => {
      reloadRef.current();
    }, 5_000);

    return () => clearInterval(id);
  }, [report?.status]);

  /* ------------------------------------------------------------------ */
  /*  Loading skeleton                                                   */
  /* ------------------------------------------------------------------ */

  if (loading) {
    return <ReportSkeleton />;
  }

  /* ------------------------------------------------------------------ */
  /*  Transport / 404 / API error                                        */
  /* ------------------------------------------------------------------ */

  if (error) {
    return (
      <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1200px]">
        <div className="mb-4">
          <Button variant="outline" size="sm" onClick={handleBack}>
            <ArrowLeft className="w-3.5 h-3.5" />
            返回探索
          </Button>
        </div>
        <InlineError>{`加载报告失败: ${error}`}</InlineError>
        <div className="mt-3">
          <Button variant="outline" size="sm" onClick={reload}>
            重试
          </Button>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1200px]">
        <div className="mb-4">
          <Button variant="outline" size="sm" onClick={handleBack}>
            <ArrowLeft className="w-3.5 h-3.5" />
            返回探索
          </Button>
        </div>
        <InlineError>报告不存在或尚未提交</InlineError>
      </div>
    );
  }

  /* ------------------------------------------------------------------ */
  /*  In-progress / failed — show meta + hint, no tabs                   */
  /* ------------------------------------------------------------------ */

  if (report.status !== "completed" || !result) {
    return (
      <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1200px]">
        <div className="mb-4">
          <Button variant="outline" size="sm" onClick={handleBack}>
            <ArrowLeft className="w-3.5 h-3.5" />
            返回探索
          </Button>
        </div>

        <ReportMeta
          factorName={report.factor_name}
          runId={report.run_id}
          status={report.status}
          createdAt={report.created_at}
          finishedAt={report.finished_at}
          progress={report.progress}
        />

        {report.status === "failed" && report.error && (
          <InlineError>{report.error}</InlineError>
        )}
        {report.status !== "failed" && report.status !== "cancelled" && (
          <div className="mt-4 rounded-lg border bg-card p-8 text-center">
            <div className="text-[0.8rem] font-semibold mb-2">
              诊断运行中 · {report.status}
            </div>
            <div className="font-mono text-[0.68rem] text-muted-foreground leading-relaxed max-w-md mx-auto">
              当前进度 {report.progress ?? 0}%，完成后自动刷新。
            </div>
            {(report.status === "queued" || report.status === "running") && (
              <div className="mt-4 flex flex-col items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cancelAction.execute(runId).then(() => reload())}
                  disabled={cancelAction.state === "loading" || cancelAction.state === "success"}
                >
                  {cancelAction.state === "loading"
                    ? "取消中..."
                    : cancelAction.state === "success"
                    ? "已发送取消"
                    : "取消运行"}
                </Button>
                {cancelAction.state === "error" && cancelAction.error && (
                  <InlineError>{cancelAction.error}</InlineError>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  /* ------------------------------------------------------------------ */
  /*  Completed — full 4-tab layout                                      */
  /* ------------------------------------------------------------------ */

  return (
    <div className="flex-1 overflow-y-auto px-8 py-5 max-w-[1200px]">
      {/* Back button */}
      <div className="mb-4">
        <Button variant="outline" size="sm" onClick={handleBack}>
          <ArrowLeft className="w-3.5 h-3.5" />
          返回探索
        </Button>
      </div>

      {/* Meta */}
      <ReportMeta
        factorName={report.factor_name}
        runId={report.run_id}
        status={report.status}
        createdAt={report.created_at}
        finishedAt={report.finished_at}
        progress={report.progress}
      />

      {/* Tab bar — matches preview/component-tabs.html ``.tabs`` pattern */}
      <div
        className="inline-flex gap-0.5 bg-input rounded-md p-[3px] mb-5"
        role="tablist"
        data-testid="factor-report-tabs"
      >
        {TABS.map((tab) => (
          <TabButton
            key={tab.key}
            tab={tab.key}
            label={tab.label}
            isActive={activeTab === tab.key}
            verdict={tabVerdict(tab.key, result)}
            onClick={() => setActiveTab(tab.key)}
          />
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "profile" && <SignalProfileTab result={result} />}
      {activeTab === "predict" && <PredictivePowerTab result={result} />}
      {activeTab === "robust" && <RobustnessTab result={result} />}
      {activeTab === "cost" && (
        <CostParamsTab result={result} config={report.config} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Meta strip                                                          */
/* ------------------------------------------------------------------ */

interface ReportMetaProps {
  factorName: string;
  runId: string;
  status: string;
  createdAt?: string;
  finishedAt?: string;
  progress?: number;
}

function ReportMeta({
  factorName,
  runId,
  status,
  createdAt,
  finishedAt,
  progress,
}: ReportMetaProps) {
  return (
    <div
      className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-5"
      data-testid="factor-report-meta"
    >
      <div className="text-[1.05rem] font-bold leading-tight">
        {factorName}
      </div>
      <StatusBadge status={status} />
      <span className="flex items-center gap-1 font-mono text-[0.68rem] text-muted-foreground">
        run_id:
        <code className="text-foreground">{runId.slice(0, 8)}</code>
      </span>
      {createdAt && (
        <span className="flex items-center gap-1 font-mono text-[0.68rem] text-muted-foreground">
          创建: <span className="text-foreground">{formatTs(createdAt)}</span>
        </span>
      )}
      {finishedAt && (
        <span className="flex items-center gap-1 font-mono text-[0.68rem] text-muted-foreground">
          完成: <span className="text-foreground">{formatTs(finishedAt)}</span>
        </span>
      )}
      {status !== "completed" && status !== "failed" && progress != null && (
        <span className="flex items-center gap-1 font-mono text-[0.68rem] text-muted-foreground">
          进度:
          <span className="text-primary font-semibold">
            {progress}%
          </span>
        </span>
      )}
    </div>
  );
}

function formatTs(iso: string): string {
  // Accept both with/without trailing Z — server emits ISO 8601.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/* ------------------------------------------------------------------ */
/*  Tab button                                                         */
/* ------------------------------------------------------------------ */

interface TabButtonProps {
  tab: TabKey;
  label: string;
  isActive: boolean;
  verdict: TabVerdict;
  onClick: () => void;
}

function TabButton({ tab, label, isActive, verdict, onClick }: TabButtonProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      onClick={onClick}
      data-testid={`factor-report-tab-trigger-${tab}`}
      className={cn(
        "font-mono text-[0.68rem] px-3 py-1.5 rounded flex items-center gap-1.5 border-0 cursor-pointer whitespace-nowrap transition-colors duration-150 ease-qds",
        isActive
          ? "bg-secondary text-foreground shadow-sm"
          : "bg-transparent text-muted-foreground hover:text-qds-t1",
      )}
    >
      {label}
      {verdict !== "none" && <VerdictPill verdict={verdict} />}
    </button>
  );
}

function VerdictPill({ verdict }: { verdict: TabVerdict }) {
  const cls =
    verdict === "pass"
      ? "bg-qds-success-dim text-qds-success"
      : verdict === "warn"
        ? "bg-qds-warning-dim text-qds-warning"
        : verdict === "fail"
          ? "bg-qds-danger-dim text-destructive"
          : "";
  const glyph =
    verdict === "pass" ? "✓" : verdict === "warn" ? "!" : "✕";
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center font-mono text-[0.6rem] font-medium px-1.5 py-0.5 rounded-full min-w-[18px]",
        cls,
      )}
    >
      {glyph}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                    */
/* ------------------------------------------------------------------ */

function ReportSkeleton() {
  return (
    <div
      className="flex-1 overflow-y-auto px-8 py-5 max-w-[1200px]"
      data-testid="factor-report-skeleton"
    >
      <Skeleton className="h-8 w-20 mb-4" />
      <div className="flex gap-4 mb-5">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-32" />
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

