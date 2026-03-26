"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { Download, ScanLine, HardDrive, Minimize2, ChevronUp, ChevronDown, X } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";
import { useWsEvent } from "@/providers/WebSocketProvider";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/* ── Types ───────────────────────────────────────────────────────── */

interface CatalogEntry {
  symbol: string;
  data_type: string;
  interval: string;
  bar_count?: number;
  start_date: string;
  end_date: string;
  file_path?: string;
  size_bytes: number;
}

type SortKey = "symbol" | "interval" | "bar_count" | "start_date" | "size_bytes";
type SortDir = "asc" | "desc";

/* ── Helpers ─────────────────────────────────────────────────────── */

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function formatNumber(n: number | undefined): string {
  if (n === undefined) return "—";
  return n.toLocaleString();
}

const INTERVAL_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"];

/* ── Fetch Dialog ────────────────────────────────────────────────── */

interface DataProgressPayload {
  type: string;
  symbol: string;
  interval: string;
  progress: number;
  message: string;
  task_id?: string;
}

function FetchDialog({
  open,
  onClose,
  onSuccess,
}: {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [interval, setInterval] = useState("1m");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Progress tracking
  const [phase, setPhase] = useState<"form" | "progress">("form");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("已提交，等待开始...");

  const progressEvent = useWsEvent("data.progress");
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;
  const refreshedRef = useRef(false);

  // Track progress from WS events
  useEffect(() => {
    if (!progressEvent || !taskId || phase !== "progress") return;
    const evt = progressEvent as unknown as DataProgressPayload;
    if (evt.task_id !== taskId) return;

    setProgress(evt.progress);
    setProgressMsg(evt.message);

    if (evt.progress === 100 && !refreshedRef.current) {
      refreshedRef.current = true;
      onSuccessRef.current();
    }
  }, [progressEvent, taskId, phase]);

  // Reset on dialog close
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setPhase("form");
        setProgress(0);
        setProgressMsg("已提交，等待开始...");
        setError(null);
        setTaskId(null);
        setSubmitting(false);
        refreshedRef.current = false;
      }, 200);
      return () => clearTimeout(t);
    }
  }, [open]);

  async function handleSubmit() {
    if (!symbol.trim() || !startDate || !endDate) {
      setError("请填写所有必填项");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiPost<{ task_id: string }>("/api/data/fetch", {
        symbol: symbol.trim(),
        interval,
        start: startDate,
        end: endDate,
      });
      setTaskId(res?.task_id ?? null);
      setPhase("progress");
      setProgress(0);
      setProgressMsg("已提交，等待开始...");
      refreshedRef.current = false;
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  const isDone = progress === 100;
  const isError = progress === -1;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border border-border sm:max-w-[480px]">
        {phase === "form" ? (
          <>
            <DialogHeader>
              <DialogTitle>拉取数据</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-2">
              <Input
                label="品种 (如 BTCUSDT-PERP)"
                placeholder="BTCUSDT-PERP"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              />
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">周期</label>
                <Select value={interval} onValueChange={(v: string | null) => v && setInterval(v)}>
                  <SelectTrigger className="h-8 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {INTERVAL_OPTIONS.map((opt) => (
                      <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Input
                  label="开始日期"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
                <Input
                  label="结束日期"
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
              </div>
              {error && (
                <span className="text-[11px] text-destructive">{error}</span>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={onClose}
                className="inline-flex items-center justify-center h-8 rounded-lg border border-border bg-transparent px-4 text-[11px] font-semibold text-muted-foreground hover:bg-popover transition-all"
              >
                取消
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={submitting}
                className="inline-flex items-center gap-1.5 justify-center h-8 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-4 text-[11px] font-bold hover:opacity-90 transition-all disabled:opacity-50"
              >
                {submitting ? (
                  <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                ) : (
                  <Download className="w-3 h-3" />
                )}
                开始拉取
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {isDone ? (
                  <span className="text-[var(--accent-green)]">拉取完成</span>
                ) : isError ? (
                  <span className="text-destructive">拉取失败</span>
                ) : (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-[var(--accent-blue)] border-t-transparent rounded-full animate-spin" />
                    正在拉取数据
                  </>
                )}
              </DialogTitle>
            </DialogHeader>
            <div className="py-4 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground font-mono">
                  {symbol.trim()} · {interval}
                </span>
                <span className={`text-xs font-mono font-bold ${
                  isDone ? "text-[var(--accent-green)]" : isError ? "text-destructive" : "text-foreground"
                }`}>
                  {isError ? "错误" : `${Math.max(0, progress)}%`}
                </span>
              </div>
              {/* Progress bar */}
              <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ease-out ${
                    isError
                      ? "bg-destructive"
                      : isDone
                        ? "bg-[var(--accent-green)]"
                        : "bg-[var(--accent-blue)]"
                  }`}
                  style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                />
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {progressMsg}
              </p>
            </div>
            <DialogFooter>
              {isDone ? (
                <Button
                  onClick={onClose}
                  className="inline-flex items-center gap-1.5 justify-center h-8 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-4 text-[11px] font-bold hover:opacity-90 transition-all"
                >
                  完成
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={onClose}
                  className="inline-flex items-center justify-center h-8 rounded-lg border border-border bg-transparent px-4 text-[11px] font-semibold text-muted-foreground hover:bg-popover transition-all"
                >
                  {isError ? "关闭" : "后台运行"}
                </Button>
              )}
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ── Sort header cell ────────────────────────────────────────────── */

function SortCell({
  label,
  sortKey,
  current,
  dir,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: SortDir;
  onSort: (k: SortKey) => void;
  className?: string;
}) {
  const active = current === sortKey;
  return (
    <Button
      variant="ghost"
      onClick={() => onSort(sortKey)}
      className={`flex items-center gap-1 text-[10px] font-semibold tracking-[0.5px] text-muted-foreground hover:text-muted-foreground transition-colors ${className ?? ""}`}
    >
      {label}
      {active ? (
        dir === "asc" ? (
          <ChevronUp className="w-3 h-3" />
        ) : (
          <ChevronDown className="w-3 h-3" />
        )
      ) : (
        <ChevronDown className="w-3 h-3 opacity-30" />
      )}
    </Button>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function DataCatalogPage() {
  const [datasets, setDatasets] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fetchOpen, setFetchOpen] = useState(false);

  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const [actionState, setActionState] = useState<Record<string, boolean>>({});
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    loadCatalog();
  }, []);

  async function loadCatalog() {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<CatalogEntry[]>("/api/data/catalog");
      if (data) setDatasets(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  async function runAction(key: string, path: string, successMsg: string) {
    setActionState((s) => ({ ...s, [key]: true }));
    setActionMsg(null);
    try {
      await apiPost(path);
      setActionMsg(successMsg);
      await loadCatalog();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionState((s) => ({ ...s, [key]: false }));
    }
  }

  const sorted = useMemo(() => {
    const arr = [...datasets];
    arr.sort((a, b) => {
      let va: string | number;
      let vb: string | number;
      switch (sortKey) {
        case "symbol":
          va = a.symbol;
          vb = b.symbol;
          break;
        case "interval":
          va = a.interval;
          vb = b.interval;
          break;
        case "bar_count":
          va = a.bar_count ?? 0;
          vb = b.bar_count ?? 0;
          break;
        case "start_date":
          va = a.start_date;
          vb = b.start_date;
          break;
        case "size_bytes":
          va = a.size_bytes;
          vb = b.size_bytes;
          break;
        default:
          return 0;
      }
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [datasets, sortKey, sortDir]);

  const totalSize = datasets.reduce((s, d) => s + d.size_bytes, 0);
  const totalBars = datasets.reduce((s, d) => s + (d.bar_count ?? 0), 0);
  const allDates = datasets.flatMap((d) => [d.start_date, d.end_date]).filter(Boolean).sort();
  const dateRange =
    allDates.length >= 2 ? `${allDates[0]} → ${allDates[allDates.length - 1]}` : "—";

  const stats = [
    { label: "数据集", value: String(datasets.length) },
    { label: "K线总数", value: totalBars > 0 ? totalBars.toLocaleString() : "—" },
    { label: "日期跨度", value: dateRange },
    { label: "总大小", value: formatBytes(totalSize) },
  ];

  return (
    <div className="flex flex-col gap-6 p-6 h-full">
      {/* Top bar */}
      <div className="flex items-end justify-between shrink-0">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-heading text-[22px] font-bold tracking-tight text-foreground">
            数据目录
          </h1>
          <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
            // 本地 ParquetDataCatalog
          </span>
        </div>
        {/* Toolbar */}
        <div className="flex items-center gap-2">
          <Button
            onClick={() => setFetchOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-4 py-2 text-[11px] font-bold hover:opacity-90 transition-all"
          >
            <Download className="w-3 h-3" />
            拉取数据
          </Button>
          <Button
            variant="outline"
            onClick={() => runAction("compact", "/api/data/compact", "压缩完成")}
            disabled={actionState.compact}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-popover px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-[var(--accent-green)]/50 hover:text-foreground transition-all disabled:opacity-50"
          >
            {actionState.compact ? (
              <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              <Minimize2 className="w-3 h-3" />
            )}
            压缩
          </Button>
          <Button
            variant="outline"
            onClick={() => runAction("scan", "/api/data/scan", "扫描完成")}
            disabled={actionState.scan}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-popover px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-[var(--accent-green)]/50 hover:text-foreground transition-all disabled:opacity-50"
          >
            {actionState.scan ? (
              <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              <ScanLine className="w-3 h-3" />
            )}
            扫描
          </Button>
          <Button
            variant="outline"
            onClick={() => runAction("batch", "/api/data/fetch-batch", "批量拉取已提交")}
            disabled={actionState.batch}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-popover px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-[var(--accent-green)]/50 hover:text-foreground transition-all disabled:opacity-50"
          >
            {actionState.batch ? (
              <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              <HardDrive className="w-3 h-3" />
            )}
            批量拉取
          </Button>
        </div>
      </div>

      {/* Action message */}
      {actionMsg && (
        <div className="shrink-0 flex items-center justify-between rounded-lg bg-popover border border-border px-4 py-2">
          <span className="text-[11px] text-muted-foreground">{actionMsg}</span>
          <Button variant="ghost" size="icon" onClick={() => setActionMsg(null)}>
            <X className="w-3 h-3 text-muted-foreground hover:text-foreground" />
          </Button>
        </div>
      )}

      {/* Stats row */}
      <FadeIn className="grid grid-cols-4 gap-4 shrink-0">
        {stats.map((s) => (
          <div
            key={s.label}
            className="rounded-xl bg-card border border-border p-4"
          >
            <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
              {s.label}
            </span>
            <div className="font-heading text-2xl font-bold mt-2 text-foreground">
              {s.value}
            </div>
          </div>
        ))}
      </FadeIn>

      {/* Table */}
      {loading ? (
        <div className="rounded-xl bg-card border border-border p-5 flex flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full bg-popover" />
          ))}
        </div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-[11px] text-destructive">{error}</span>
        </div>
      ) : datasets.length === 0 ? (
        <div className="flex-1 rounded-xl bg-card border border-border flex items-center justify-center">
          <span className="text-[11px] text-muted-foreground">暂无数据集，请先拉取数据</span>
        </div>
      ) : (
        <FadeIn delay={0.1} className="rounded-xl bg-card border border-border overflow-hidden">
          {/* Header */}
          <div className="flex items-center px-5 py-3 border-b border-border">
            <div className="w-[180px]">
              <SortCell label="品种" sortKey="symbol" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="w-[80px]">
              <SortCell label="周期" sortKey="interval" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="w-[100px]">
              <SortCell label="K线数" sortKey="bar_count" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="flex-1">
              <SortCell label="日期范围" sortKey="start_date" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="w-[80px]">
              <SortCell label="文件大小" sortKey="size_bytes" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
          </div>
          {/* Rows */}
          {sorted.map((ds, i) => (
            <div
              key={`${ds.symbol}-${ds.data_type}-${ds.interval}-${i}`}
              className={`flex items-center px-5 py-[11px] text-[11px] font-medium hover:bg-popover/50 transition-colors ${
                i < sorted.length - 1 ? "border-b border-border" : ""
              }`}
            >
              <div className="w-[180px] flex items-center gap-2">
                <span className="text-foreground font-mono">{ds.symbol}</span>
              </div>
              <div className="w-[80px]">
                <span className="inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold bg-[var(--accent-blue-20)] text-[var(--accent-blue)]">
                  {ds.interval}
                </span>
              </div>
              <div className="w-[100px] text-muted-foreground font-mono">
                {formatNumber(ds.bar_count)}
              </div>
              <div className="flex-1 text-muted-foreground">
                {ds.start_date && ds.end_date ? `${ds.start_date} → ${ds.end_date}` : "—"}
              </div>
              <div className="w-[80px] text-muted-foreground">
                {formatBytes(ds.size_bytes)}
              </div>
            </div>
          ))}
        </FadeIn>
      )}

      {/* Fetch dialog */}
      <FetchDialog
        open={fetchOpen}
        onClose={() => setFetchOpen(false)}
        onSuccess={loadCatalog}
      />
    </div>
  );
}
