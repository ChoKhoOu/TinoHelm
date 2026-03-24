"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Search, Plus, Play, RefreshCw } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Input, Select } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { OverviewTab } from "./components/OverviewTab";
import { TearsheetTab } from "./components/TearsheetTab";
import { TradeLogTab } from "./components/TradeLogTab";
import { ReportsTab } from "./components/ReportsTab";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";

interface BacktestRunSummary {
  run_id: string;
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  status: RunStatus;
  created_at: string;
  error?: string;
}

interface TradeLogEntry {
  opened_at: string;
  instrument: string;
  side: string;
  quantity: number;
  avg_open: number;
  avg_close: number;
  realized_pnl: number;
  duration: string;
}

interface BacktestResult {
  statistics: Record<string, unknown>;
  equity_curve: unknown[];
  trade_log: TradeLogEntry[];
  per_instrument?: unknown[];
}

interface StrategyInfo {
  name: string;
}

interface NewRunForm {
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
}

/* ------------------------------------------------------------------ */
/*  Status badge                                                       */
/* ------------------------------------------------------------------ */

const STATUS_LABELS: Record<RunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelling: "取消中",
  cancelled: "已取消",
};

const STATUS_CLASSES: Record<RunStatus, string> = {
  queued: "bg-[var(--accent-amber-20)] text-[var(--accent-amber)]",
  running: "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]",
  completed: "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  failed: "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  cancelling: "bg-[var(--bg-subtle)] text-[var(--text-muted)]",
  cancelled: "bg-[var(--bg-subtle)] text-[var(--text-muted)]",
};

function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold ${STATUS_CLASSES[status] ?? STATUS_CLASSES.cancelled}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Run Row                                                            */
/* ------------------------------------------------------------------ */

interface RunRowProps {
  run: BacktestRunSummary;
  selected: boolean;
  progress: number | null;
  onClick: () => void;
}

function RunRow({ run, selected, progress, onClick }: RunRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-[var(--border-gray)] transition-colors ${
        selected
          ? "bg-[var(--accent-blue-20)] border-l-2 border-l-[var(--accent-blue)]"
          : "hover:bg-[var(--bg-subtle)]/60"
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-xs font-medium text-[var(--text-primary)] truncate flex-1">
          {run.strategy_name}
        </span>
        <StatusBadge status={run.status} />
      </div>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] text-[var(--accent-blue)] font-medium">{run.symbol}</span>
        <span className="text-[10px] text-[var(--text-muted)]">{run.interval}</span>
      </div>
      <div className="text-[10px] text-[var(--text-muted)]">{dateRange}</div>
      {run.status === "running" && progress !== null && (
        <div className="mt-1.5 h-1 rounded-full bg-[var(--bg-subtle)] overflow-hidden">
          <div
            className="h-full rounded-full bg-[var(--accent-blue)] transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  New Run Dialog                                                     */
/* ------------------------------------------------------------------ */

const INTERVAL_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"];

interface NewRunDialogProps {
  open: boolean;
  onClose: () => void;
  strategies: StrategyInfo[];
  onSubmit: (form: NewRunForm) => Promise<void>;
}

function NewRunDialog({ open, onClose, strategies, onSubmit }: NewRunDialogProps) {
  const [form, setForm] = useState<NewRunForm>({
    strategy_name: "",
    symbol: "BTCUSDT-PERP",
    interval: "1h",
    start_date: "2024-01-01",
    end_date: "2024-12-31",
    initial_capital: "10000",
  });
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = (k: keyof NewRunForm) => (v: string) =>
    setForm((f) => ({ ...f, [k]: v }));

  const handleSubmit = async () => {
    if (!form.strategy_name) { setErr("请选择策略"); return; }
    setSubmitting(true);
    setErr(null);
    try {
      await onSubmit(form);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>新建回测</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3 py-1">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              策略
            </label>
            <Select
              value={form.strategy_name}
              onValueChange={set("strategy_name")}
              options={[
                { value: "", label: "请选择策略..." },
                ...strategies.map((s) => ({ value: s.name, label: s.name })),
              ]}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              交易对
            </label>
            <Input
              value={form.symbol}
              onChange={(e) => set("symbol")(e.target.value)}
              placeholder="BTCUSDT-PERP"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              时间周期
            </label>
            <Select
              value={form.interval}
              onValueChange={set("interval")}
              options={INTERVAL_OPTIONS}
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Input
              label="开始日期"
              type="date"
              value={form.start_date}
              onChange={(e) => set("start_date")(e.target.value)}
            />
            <Input
              label="结束日期"
              type="date"
              value={form.end_date}
              onChange={(e) => set("end_date")(e.target.value)}
            />
          </div>

          <Input
            label="初始资金 (USDT)"
            type="number"
            value={form.initial_capital}
            onChange={(e) => set("initial_capital")(e.target.value)}
            min="100"
          />

          {err && (
            <span className="text-xs text-[var(--accent-red)]">{err}</span>
          )}
        </div>

        <DialogFooter>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--accent-blue)] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitting ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            {submitting ? "提交中..." : "运行"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [activeTab, setActiveTab] = useState("overview");

  // WS progress updates: { run_id, pct }
  const wsMsg = useWsEvent("backtest.progress");
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});

  // Track result fetch to get trade_log
  const resultCacheRef = useRef<Record<string, BacktestResult>>({});

  // Apply WS progress updates
  useEffect(() => {
    if (!wsMsg?.data) return;
    const { run_id, pct } = wsMsg.data as { run_id: string; pct: number };
    if (run_id) {
      setProgressMap((prev) => ({ ...prev, [run_id]: pct }));
      // Update status in run list if running
      setRuns((prev) =>
        prev.map((r) =>
          r.run_id === run_id && r.status !== "running"
            ? { ...r, status: "running" }
            : r
        )
      );
    }
  }, [wsMsg]);

  // Load runs list
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<BacktestRunSummary[]>("/api/backtest/list");
      if (data) setRuns(data);
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
    // Poll every 5s while page is mounted to pick up status changes
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // Load strategies for new run dialog
  useEffect(() => {
    apiGet<StrategyInfo[]>("/api/strategies")
      .then((d) => d && setStrategies(d))
      .catch(() => {});
  }, []);

  // Load result when run selected (for trade log)
  useEffect(() => {
    if (!selectedRunId) return;
    const cached = resultCacheRef.current[selectedRunId];
    if (cached) {
      setTradeLog(cached.trade_log ?? []);
      return;
    }
    const run = runs.find((r) => r.run_id === selectedRunId);
    if (run?.status !== "completed") return;

    apiGet<BacktestResult>(`/api/backtest/${selectedRunId}/result`)
      .then((data) => {
        if (data) {
          resultCacheRef.current[selectedRunId] = data;
          setTradeLog(data.trade_log ?? []);
        }
      })
      .catch(() => {});
  }, [selectedRunId, runs]);

  // Filter runs
  const filteredRuns = runs.filter((r) => {
    const matchSearch =
      !search ||
      r.strategy_name.toLowerCase().includes(search.toLowerCase()) ||
      r.symbol.toLowerCase().includes(search.toLowerCase());
    const matchStatus = statusFilter === "all" || r.status === statusFilter;
    return matchSearch && matchStatus;
  });

  // Submit new run
  const handleNewRun = async (form: NewRunForm) => {
    await apiPost("/api/backtest/run", {
      strategy_name: form.strategy_name,
      symbol: form.symbol,
      interval: form.interval,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_capital: parseFloat(form.initial_capital),
    });
    await loadRuns();
  };

  const selectedRun = runs.find((r) => r.run_id === selectedRunId) ?? null;

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Left panel ── */}
      <div className="w-[380px] shrink-0 flex flex-col border-r border-[var(--border-gray)] bg-[var(--bg-sidebar)]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-gray)]">
          <div>
            <h1 className="font-heading text-sm font-bold text-[var(--text-primary)]">回测</h1>
            <span className="text-[10px] text-[var(--text-muted)]">
              {runs.length} 条记录
            </span>
          </div>
          <button
            onClick={() => loadRuns()}
            className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] transition-colors"
            title="刷新"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Search + Filter */}
        <div className="flex flex-col gap-2 px-3 py-2.5 border-b border-[var(--border-gray)]">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" />
            <Input
              placeholder="搜索策略或品种..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-7 h-7 text-xs"
            />
          </div>
          <Select
            value={statusFilter}
            onValueChange={setStatusFilter}
            options={[
              { value: "all", label: "全部状态" },
              { value: "queued", label: "排队中" },
              { value: "running", label: "运行中" },
              { value: "completed", label: "已完成" },
              { value: "failed", label: "失败" },
              { value: "cancelled", label: "已取消" },
            ]}
            className="h-7 text-xs"
          />
        </div>

        {/* Runs list */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {runsLoading ? (
            <div className="flex flex-col gap-px p-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded" />
              ))}
            </div>
          ) : filteredRuns.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <span className="text-xs text-[var(--text-muted)]">
                {runs.length === 0 ? "暂无回测记录" : "无匹配结果"}
              </span>
            </div>
          ) : (
            filteredRuns.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                selected={selectedRunId === run.run_id}
                progress={progressMap[run.run_id] ?? null}
                onClick={() => {
                  if (selectedRunId !== run.run_id) {
                    setSelectedRunId(run.run_id);
                    setActiveTab("overview");
                  }
                }}
              />
            ))
          )}
        </div>

        {/* New run button */}
        <div className="p-3 border-t border-[var(--border-gray)]">
          <button
            onClick={() => setDialogOpen(true)}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-[var(--accent-blue)] text-white text-xs font-semibold hover:opacity-90 transition-opacity"
          >
            <Plus className="w-3.5 h-3.5" />
            新建回测
          </button>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden bg-[var(--bg-page)]">
        {!selectedRunId ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-[var(--bg-elevated)] flex items-center justify-center">
                <Play className="w-5 h-5 text-[var(--text-muted)]" />
              </div>
              <span className="text-sm text-[var(--text-muted)]">请选择一个回测查看详情</span>
            </div>
          </div>
        ) : (
          <Tabs
            value={activeTab}
            onValueChange={setActiveTab}
            className="flex flex-col h-full"
          >
            {/* Tab header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--border-gray)] bg-[var(--bg-card)] shrink-0">
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-semibold text-[var(--text-primary)]">
                  {selectedRun?.strategy_name}
                </span>
                <span className="text-[10px] text-[var(--text-muted)]">
                  {selectedRun?.symbol} · {selectedRun?.interval} · {selectedRun?.start_date?.slice(0, 10)} → {selectedRun?.end_date?.slice(0, 10)}
                </span>
              </div>
              {selectedRun && <StatusBadge status={selectedRun.status} />}
            </div>

            <TabsList
              variant="line"
              className="px-4 pt-1 shrink-0 border-b border-[var(--border-gray)] bg-[var(--bg-card)] w-full justify-start rounded-none h-9"
            >
              <TabsTrigger value="overview" className="text-xs px-3">概览</TabsTrigger>
              <TabsTrigger value="tearsheet" className="text-xs px-3">报告</TabsTrigger>
              <TabsTrigger value="tradelog" className="text-xs px-3">交易日志</TabsTrigger>
              <TabsTrigger value="reports" className="text-xs px-3">数据表格</TabsTrigger>
            </TabsList>

            <div className="flex-1 min-h-0 overflow-hidden">
              <TabsContent value="overview" className="h-full overflow-y-auto">
                {selectedRun?.status === "completed" ? (
                  <OverviewTab runId={selectedRunId} />
                ) : (
                  <div className="flex items-center justify-center h-48">
                    <span className="text-xs text-[var(--text-muted)]">
                      {selectedRun?.status === "running"
                        ? `运行中... ${progressMap[selectedRunId] ?? 0}%`
                        : selectedRun?.status === "failed"
                        ? `回测失败: ${selectedRun.error ?? "未知错误"}`
                        : selectedRun?.status === "queued"
                        ? "等待运行..."
                        : "已取消"}
                    </span>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="tearsheet" className="h-full">
                {selectedRun?.status === "completed" ? (
                  <TearsheetTab runId={selectedRunId} />
                ) : (
                  <div className="flex items-center justify-center h-48">
                    <span className="text-xs text-[var(--text-muted)]">回测完成后可查看报告</span>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="tradelog" className="h-full overflow-hidden">
                {selectedRun?.status === "completed" ? (
                  <TradeLogTab tradeLog={tradeLog} />
                ) : (
                  <div className="flex items-center justify-center h-48">
                    <span className="text-xs text-[var(--text-muted)]">回测完成后可查看交易日志</span>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="reports" className="h-full overflow-hidden">
                {selectedRun?.status === "completed" ? (
                  <ReportsTab runId={selectedRunId} />
                ) : (
                  <div className="flex items-center justify-center h-48">
                    <span className="text-xs text-[var(--text-muted)]">回测完成后可查看数据报表</span>
                  </div>
                )}
              </TabsContent>
            </div>
          </Tabs>
        )}
      </div>

      {/* New run dialog */}
      <NewRunDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        strategies={strategies}
        onSubmit={handleNewRun}
      />
    </div>
  );
}
