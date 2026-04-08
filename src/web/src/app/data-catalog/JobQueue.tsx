"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";

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
    const p = (progressMsg as any).data ?? progressMsg;
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
      <div className="dc-sl">
        拉取队列
        <span
          className="badge"
          style={{ background: "var(--acc-d)", color: "var(--acc)" }}
        >
          {sorted.length}
        </span>
      </div>

      {/* List */}
      <div className="list">
        {sorted.length === 0 ? (
          <div className="empty">
            <div className="empty-icon">⧖</div>
            <div className="empty-text">暂无拉取任务</div>
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
    <div className="dc-qrow">
      {/* 3px accent bar */}
      <div
        className="dc-qrow-acc"
        style={{ background: accentColor(job.status) }}
      />

      {/* Symbol + meta */}
      <div className="dc-qrow-info">
        <div className="dc-qrow-sym">{job.symbol}</div>
        <div className="dc-qrow-meta">
          {job.data_type}{intervalLabel} · {job.start_date} → {job.end_date}
        </div>
      </div>

      {/* Status */}
      <div className="dc-qrow-status">
        {isRunning ? (
          <>
            <div className="dc-qprog">
              <div className="dc-qprog-bar">
                <div
                  className="dc-qprog-fill"
                  style={{ width: `${job.progress}%` }}
                />
              </div>
              <span style={{ color: "var(--acc)" }}>{job.progress}%</span>
            </div>
            <div className="dc-qprog-msg">{job.message || "处理中..."}</div>
          </>
        ) : job.status === "queued" ? (
          <span className="bt-status bt-status-queue">排队中</span>
        ) : job.status === "completed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <span className="bt-status bt-status-done">✓ 完成</span>
            {job.message && <div className="dc-qprog-msg">{job.message}</div>}
          </div>
        ) : job.status === "failed" ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <span className="bt-status bt-status-fail">✕ 失败</span>
            {(job.error || job.message) && <div className="dc-qprog-msg" style={{ color: "var(--dan)" }}>{job.error || job.message}</div>}
          </div>
        ) : (
          <span className="bt-status bt-status-queue">已取消</span>
        )}
      </div>

      {/* Time */}
      <div className="dc-qrow-time">{timeStr}</div>
    </div>
  );
}
