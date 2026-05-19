"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { SectionLabel, StatusBadge } from "@/components/qds";

interface DataFetchBatchCounts {
  jobs: number;
  queued: number;
  running: number;
  completed: number;
  partial_completed: number;
  failed: number;
  cancelled: number;
}

interface DataFetchBatch {
  batch_id: string;
  data_type: string;
  asset_class: string;
  symbols: string[];
  intervals: string[];
  start_date: string;
  end_date: string;
  status: string;
  progress: number;
  counts: DataFetchBatchCounts;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

interface DataFetchBatchListResponse {
  batches: DataFetchBatch[];
  total: number;
  page: number;
  page_size: number;
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
  if (status === "partial_completed") return "var(--warn)";
  if (status === "failed") return "var(--dan)";
  return "var(--t3)";
}

interface JobQueueProps {
  refreshTrigger?: number;
  onJobComplete?: () => void;
}

export function JobQueue({ refreshTrigger, onJobComplete }: JobQueueProps) {
  const [batches, setBatches] = useState<DataFetchBatch[]>([]);
  const prevActiveRef = useRef<Set<string>>(new Set());
  const onJobCompleteRef = useRef(onJobComplete);
  const progressRefreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  onJobCompleteRef.current = onJobComplete;

  const load = useCallback(async () => {
    const data = await apiGet<DataFetchBatchListResponse>("/api/data/batches", {
      page: "1",
      page_size: "100",
    });
    if (data) setBatches(data.batches);
  }, []);

  useEffect(() => { load(); }, [load, refreshTrigger]);

  // Poll: 3s when active jobs, 15s otherwise
  useEffect(() => {
    const hasActive = batches.some((b: DataFetchBatch) => b.status === "running" || b.status === "queued");
    const id = setInterval(load, hasActive ? 3000 : 15000);
    return () => clearInterval(id);
  }, [batches, load]);

  // Real-time progress via WebSocket
  const progressMsg = useWsEvent("data.fetch.progress");
  useEffect(() => {
    if (!progressMsg) return;
    const payload = progressMsg as DataFetchProgressPayload;
    const p = payload.data ?? payload;
    const jobId = p.job_id;
    const pct = p.progress;
    if (!jobId || pct == null) return;
    if (progressRefreshTimeoutRef.current) {
      clearTimeout(progressRefreshTimeoutRef.current);
    }
    progressRefreshTimeoutRef.current = setTimeout(() => {
      void load();
      progressRefreshTimeoutRef.current = null;
    }, 300);
  }, [load, progressMsg]);

  // Detect job completions and notify parent
  useEffect(() => {
    const currentActive = new Set(
      batches.filter((b: DataFetchBatch) => b.status === "running" || b.status === "queued").map((b: DataFetchBatch) => b.batch_id),
    );
    const prev = prevActiveRef.current;
    if (prev.size > 0 && currentActive.size < prev.size) {
      const finished = [...prev].some((id) => !currentActive.has(id));
      if (finished) onJobCompleteRef.current?.();
    }
    prevActiveRef.current = currentActive;
    return () => {
      if (progressRefreshTimeoutRef.current) {
        clearTimeout(progressRefreshTimeoutRef.current);
        progressRefreshTimeoutRef.current = null;
      }
    };
  }, [batches]);

  const isActive = (s: string) => s === "running" || s === "queued";
  const active = batches.filter((b: DataFetchBatch) => isActive(b.status));
  const recent = batches.filter((b: DataFetchBatch) => !isActive(b.status)).slice(0, 10);
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
          sorted.map((batch) => (
            <JobRow key={batch.batch_id} batch={batch} />
          ))
        )}
      </div>
    </div>
  );
}

function JobRow({ batch }: { batch: DataFetchBatch }) {
  const isRunning = batch.status === "running";
  const intervalLabel = batch.intervals.length > 0 ? ` · ${batch.intervals.join(", ")}` : "";
  const isTerminal = batch.status === "completed" || batch.status === "partial_completed" || batch.status === "failed";
  const timeStr = isTerminal ? timeAgo(batch.completed_at) : timeAgo(batch.created_at);
  const symbolLabel = batch.symbols.length <= 2 ? batch.symbols.join(", ") : `${batch.symbols[0]} +${batch.symbols.length - 1}`;
  const message = `${batch.counts.jobs} job${batch.counts.jobs > 1 ? "s" : ""}`;

  return (
    <div
      className="grid border-b border-border last:border-b-0 hover:bg-secondary transition-colors duration-150"
      style={{ gridTemplateColumns: "3px 1fr 14rem 4.5rem" }}
    >
      <div
        className="w-[3px] self-stretch rounded-l-sm"
        style={{ background: accentColor(batch.status) }}
      />

      <div className="py-[.65rem] px-[.85rem]">
        <div className="font-mono text-[.78rem] font-semibold">{symbolLabel}</div>
        <div className="text-[.68rem] text-muted-foreground mt-[.1rem]">
          {batch.data_type}{intervalLabel} · {batch.start_date} → {batch.end_date}
        </div>
      </div>

      <div className="py-[.65rem] px-[.5rem] text-right">
        {isRunning ? (
          <>
            <div className="flex items-center gap-2 font-mono text-[.68rem]">
              <div className="w-20 h-1 bg-secondary rounded-sm overflow-hidden relative">
                <div
                  className="h-full rounded-sm transition-[width] duration-1000 ease-out"
                  style={{ width: `${batch.progress}%`, background: "var(--acc)" }}
                />
              </div>
              <span style={{ color: "var(--acc)" }}>{batch.progress}%</span>
            </div>
            <div className="text-[.62rem] text-muted-foreground max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{message}</div>
          </>
        ) : batch.status === "queued" ? (
          <StatusBadge status="queued" />
        ) : batch.status === "completed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <StatusBadge status="done">✓ 完成</StatusBadge>
            <div className="text-[.62rem] text-muted-foreground max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{message}</div>
          </div>
        ) : batch.status === "partial_completed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <StatusBadge status="warning">◐ 部分完成</StatusBadge>
            <div className="text-[.62rem] text-qds-warning max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{message}</div>
          </div>
        ) : batch.status === "failed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <StatusBadge status="failed">✕ 失败</StatusBadge>
            <div className="text-[.62rem] text-destructive max-w-[160px] overflow-hidden text-ellipsis whitespace-nowrap">{message}</div>
          </div>
        ) : (
          <StatusBadge status="cancelled" />
        )}
      </div>

      <div className="py-[.65rem] pl-[.25rem] pr-[.85rem] font-mono text-[.65rem] text-qds-t3 text-right whitespace-nowrap">{timeStr}</div>
    </div>
  );
}
