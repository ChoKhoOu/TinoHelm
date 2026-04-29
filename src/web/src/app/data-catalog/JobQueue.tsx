"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { SectionLabel, StatusBadge } from "@/components/qds";

interface DataFetchJob {
  job_id: string;
  symbol: string;
  data_type: string;
  interval: string | null;
  start_date: string;
  end_date: string;
  status: string;
  progress: number;
  message: string | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

interface DataFetchProgressPayload {
  data?: {
    job_id?: string;
    progress?: number;
    message?: string | null;
  };
  job_id?: string;
  progress?: number;
  message?: string | null;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}小时前`;
  return `${Math.floor(hrs / 24)}天前`;
}

function accentColor(status: string): string {
  if (status === "running") return "var(--info)";
  if (status === "completed") return "var(--suc)";
  if (status === "failed") return "var(--dan)";
  return "var(--t3)";
}

interface JobQueueProps {
  refreshTrigger?: number;
  onJobComplete?: () => void;
}

export function JobQueue({ refreshTrigger, onJobComplete }: JobQueueProps) {
  const [jobs, setJobs] = useState<DataFetchJob[]>([]);
  const prevActiveRef = useRef<Set<string>>(new Set());
  const onJobCompleteRef = useRef(onJobComplete);
  onJobCompleteRef.current = onJobComplete;

  const load = useCallback(async () => {
    const data = await apiGet<DataFetchJob[]>("/api/data/jobs");
    if (data) setJobs(data);
  }, []);

  useEffect(() => { load(); }, [load, refreshTrigger]);

  // Poll: 3s when active jobs, 15s otherwise
  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === "running" || j.status === "queued");
    const id = setInterval(load, hasActive ? 3000 : 15000);
    return () => clearInterval(id);
  }, [jobs, load]);

  // Real-time progress via WebSocket
  const progressMsg = useWsEvent("data.fetch.progress");
  useEffect(() => {
    if (!progressMsg) return;
    const payload = progressMsg as DataFetchProgressPayload;
    const p = payload.data ?? payload;
    const jobId = p.job_id;
    const pct = p.progress;
    if (!jobId || pct == null) return;
    setJobs((prev) =>
      prev.map((j) =>
        j.job_id === jobId
          ? { ...j, progress: pct, message: p.message ?? j.message, status: "running" }
          : j,
      ),
    );
  }, [progressMsg]);

  // Detect job completions and notify parent
  useEffect(() => {
    const currentActive = new Set(
      jobs.filter((j) => j.status === "running" || j.status === "queued").map((j) => j.job_id),
    );
    const prev = prevActiveRef.current;
    // If a previously active job is no longer active, it completed/failed
    if (prev.size > 0 && currentActive.size < prev.size) {
      const finished = [...prev].some((id) => !currentActive.has(id));
      if (finished) onJobCompleteRef.current?.();
    }
    prevActiveRef.current = currentActive;
  }, [jobs]);

  const active = jobs.filter((j) => j.status === "running" || j.status === "queued");
  const recent = jobs.filter((j) => j.status !== "running" && j.status !== "queued").slice(0, 10);
  const sorted = [...active, ...recent];

  return (
    <div style={{ marginBottom: "1.5rem" }}>
      {/* Section label */}
      <SectionLabel>
        拉取队列
        <span
          className="inline-flex items-center font-mono text-[.6rem] px-1.5 py-0.5 rounded-full"
          style={{ background: "var(--acc-d)", color: "var(--acc)" }}
        >
          {sorted.length}
        </span>
      </SectionLabel>

      {/* List */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        {sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-8 text-center">
            <div className="text-[2rem] mb-4 text-qds-t3">⧖</div>
            <div className="text-[.75rem] text-muted-foreground">暂无拉取任务</div>
          </div>
        ) : (
          sorted.map((job) => (
            <JobRow key={job.job_id} job={job} />
          ))
        )}
      </div>
    </div>
  );
}

function JobRow({ job }: { job: DataFetchJob }) {
  const isRunning = job.status === "running";
  const intervalLabel =
    job.interval && job.interval !== "tick" && job.interval !== "—"
      ? ` · ${job.interval}`
      : "";
  const timeStr =
    job.status === "completed" || job.status === "failed"
      ? timeAgo(job.completed_at)
      : timeAgo(job.created_at);

  return (
    <div
      className="grid border-b border-border last:border-b-0 hover:bg-secondary transition-colors duration-150"
      style={{ gridTemplateColumns: "3px 1fr 14rem 4.5rem" }}
    >
      {/* 3px accent bar */}
      <div
        className="w-[3px] self-stretch rounded-l-sm"
        style={{ background: accentColor(job.status) }}
      />

      {/* Symbol + meta */}
      <div className="py-[.65rem] px-[.85rem]">
        <div className="font-mono text-[.78rem] font-semibold">{job.symbol}</div>
        <div className="text-[.68rem] text-muted-foreground mt-[.1rem]">
          {job.data_type}{intervalLabel} · {job.start_date} → {job.end_date}
        </div>
      </div>

      {/* Status */}
      <div className="py-[.65rem] px-[.5rem] text-right">
        {isRunning ? (
          <>
            <div className="flex items-center gap-2 font-mono text-[.68rem]">
              <div className="w-20 h-1 bg-secondary rounded-sm overflow-hidden relative">
                <div
                  className="h-full rounded-sm transition-[width] duration-1000 ease-out"
                  style={{ width: `${job.progress}%`, background: "var(--acc)" }}
                />
              </div>
              <span style={{ color: "var(--acc)" }}>{job.progress}%</span>
            </div>
            <div className="text-[.62rem] text-muted-foreground max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{job.message || "处理中..."}</div>
          </>
        ) : job.status === "queued" ? (
          <StatusBadge status="queued" />
        ) : job.status === "completed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <StatusBadge status="done">✓ 完成</StatusBadge>
            {job.message && <div className="text-[.62rem] text-muted-foreground max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{job.message}</div>}
          </div>
        ) : job.status === "failed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <StatusBadge status="failed">✕ 失败</StatusBadge>
            {(job.error || job.message) && <div className="text-[.62rem] text-destructive max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{job.error || job.message}</div>}
          </div>
        ) : (
          <StatusBadge status="cancelled" />
        )}
      </div>

      {/* Time */}
      <div className="py-[.65rem] pl-[.25rem] pr-[.85rem] font-mono text-[.65rem] text-qds-t3 text-right whitespace-nowrap">{timeStr}</div>
    </div>
  );
}
