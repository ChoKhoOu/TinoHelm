"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Search, Plus, Play, RefreshCw, Loader2, Check, Copy, X } from "lucide-react";
import { format, parse } from "date-fns";
import { zhCN } from "date-fns/locale";
import type { DateRange } from "react-day-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Calendar } from "@/components/ui/calendar";
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
import { StatusBadge } from "@/components/StatusBadge";
import type { RunStatus, TradeLogEntry, BacktestResult } from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { TearsheetTab } from "./components/TearsheetTab";
import { TradeLogTab } from "./components/TradeLogTab";
import { ReportsTab } from "./components/ReportsTab";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface BacktestRunSummary {
  run_id: string;
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  status: RunStatus;
  created_at: string;
  progress_pct?: number | null;
  error?: string;
}

interface StrategyInfo {
  name: string;
  type?: string; // "single" | "portfolio"
}

interface StrategyDefaults {
  symbols: string[];
  interval: string | null;
  starting_balance: number | null;
}

interface NewRunForm {
  strategy_name: string;
  symbols: string[];
  interval: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
}

/* ------------------------------------------------------------------ */
/*  Status badge                                                       */
/* ------------------------------------------------------------------ */


/* ------------------------------------------------------------------ */
/*  Copyable ID                                                        */
/* ------------------------------------------------------------------ */

function CopyableId({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(runId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span
      className="inline-flex items-center gap-0.5 text-[9px] text-[var(--text-muted)] font-mono opacity-60 cursor-pointer hover:text-[var(--accent-blue)] hover:opacity-100 transition-colors"
      title={`点击复制: ${runId}`}
      onClick={handleCopy}
    >
      {runId.slice(0, 8)}
      {copied ? <Check className="w-2.5 h-2.5 text-[var(--accent-green)]" /> : <Copy className="w-2.5 h-2.5" />}
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
  const isRunning = run.status === "running" || run.status === "queued";
  const pct = progress ?? 0;
  const createdAt = run.created_at
    ? new Date(run.created_at).toLocaleString("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      })
    : null;

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
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-[10px] text-[var(--accent-blue)] font-medium">{run.symbol}</span>
        <span className="text-[10px] text-[var(--text-muted)]">{run.interval}</span>
      </div>
      <div className="text-[10px] text-[var(--text-muted)] mb-0.5">{dateRange}</div>
      <div className="flex items-center justify-between">
        <CopyableId runId={run.run_id} />
        {createdAt && <span className="text-[9px] text-[var(--text-muted)] opacity-60">{createdAt}</span>}
      </div>
      {isRunning && (
        <div className="mt-1.5">
          <div className="flex items-center justify-between mb-0.5">
            <div className="flex items-center gap-1">
              <Loader2 className="w-2.5 h-2.5 text-[var(--accent-blue)] animate-spin" />
              <span className="text-[9px] text-[var(--accent-blue)] font-medium">
                {run.status === "queued" ? "排队中" : `${pct}%`}
              </span>
            </div>
          </div>
          <div className="h-1 rounded-full bg-[var(--bg-subtle)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--accent-blue)] transition-all duration-500 ease-out relative overflow-hidden"
              style={{ width: `${pct}%` }}
            >
              <div className="progress-shimmer absolute inset-0" />
            </div>
          </div>
        </div>
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  New Run Dialog                                                     */
/* ------------------------------------------------------------------ */

interface NewRunDialogProps {
  open: boolean;
  onClose: () => void;
  strategies: StrategyInfo[];
  onSubmit: (form: NewRunForm) => Promise<void>;
}

function NewRunDialog({ open, onClose, strategies, onSubmit }: NewRunDialogProps) {
  const [form, setForm] = useState<NewRunForm>({
    strategy_name: "",
    symbols: [],
    interval: "5m",
    start_date: "2025-01-01",
    end_date: "2025-03-01",
    initial_capital: "10000",
  });
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
  const [isPortfolio, setIsPortfolio] = useState(false);
  const strategyRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!strategyDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (strategyRef.current && !strategyRef.current.contains(e.target as Node)) {
        setStrategyDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [strategyDropdownOpen]);

  // Fetch portfolio defaults when strategy changes
  useEffect(() => {
    if (!form.strategy_name) return;
    const strat = strategies.find((s) => s.name === form.strategy_name);
    const isPf = strat?.type === "portfolio";
    setIsPortfolio(isPf);
    if (!isPf) return;

    apiGet<StrategyDefaults>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/defaults`)
      .then((d) => {
        if (d) {
          setForm((f) => ({
            ...f,
            symbols: d.symbols.length > 0 ? d.symbols : f.symbols,
            interval: d.interval ?? f.interval,
            initial_capital: d.starting_balance ? String(d.starting_balance) : f.initial_capital,
          }));
        }
      })
      .catch(() => {});
  }, [form.strategy_name, strategies]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase())
  );

  const addSymbol = () => {
    const sym = symbolInput.trim().toUpperCase();
    if (sym && !form.symbols.includes(sym)) {
      setForm((f) => ({ ...f, symbols: [...f.symbols, sym] }));
    }
    setSymbolInput("");
  };

  const removeSymbol = (sym: string) => {
    setForm((f) => ({ ...f, symbols: f.symbols.filter((s) => s !== sym) }));
  };

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

  const labelCls = "text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建回测</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3.5 py-1">
          {/* Strategy searchable selector */}
          <div className="flex flex-col gap-1" ref={strategyRef}>
            <label className={labelCls}>策略</label>
            <div className="relative">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" />
                <input
                  value={strategyDropdownOpen ? strategySearch : form.strategy_name || ""}
                  onChange={(e) => { setStrategySearch(e.target.value); setStrategyDropdownOpen(true); }}
                  onFocus={() => { setStrategySearch(""); }}
                  placeholder="搜索策略..."
                  className="w-full h-8 pl-8 pr-3 rounded-md border border-[var(--border-gray)] bg-[var(--bg-elevated)] text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-blue)]/50 transition-all"
                />
              </div>
              {strategyDropdownOpen && (
                <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border border-[var(--border-gray)] bg-[var(--bg-elevated)] shadow-xl animate-in fade-in slide-in-from-top-1 duration-150">
                  {filteredStrategies.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-[var(--text-muted)]">无匹配策略</div>
                  ) : (
                    filteredStrategies.map((s) => (
                      <button
                        key={s.name}
                        type="button"
                        onClick={() => {
                          setForm((f) => ({ ...f, strategy_name: s.name }));
                          setStrategyDropdownOpen(false);
                          setStrategySearch("");
                        }}
                        className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-[var(--bg-subtle)] transition-colors ${
                          form.strategy_name === s.name ? "text-[var(--accent-blue)]" : "text-[var(--text-primary)]"
                        }`}
                      >
                        <span className="font-medium">{s.name}</span>
                        {s.type === "portfolio" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--accent-purple-20)] text-[var(--accent-purple)] font-medium">
                            组合
                          </span>
                        )}
                        {form.strategy_name === s.name && <Check className="w-3.5 h-3.5 text-[var(--accent-blue)]" />}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Symbols — tags with input */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label className={labelCls}>交易对</label>
              {isPortfolio && form.symbols.length > 0 && (
                <span className="text-[9px] text-[var(--accent-purple)]">从配置自动填充</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 p-2 min-h-[36px] rounded-md border border-[var(--border-gray)] bg-[var(--bg-elevated)]">
              {form.symbols.map((sym) => (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[var(--accent-blue-20)] text-[var(--accent-blue)] text-[10px] font-medium animate-in fade-in zoom-in-95 duration-150"
                >
                  {sym}
                  <button type="button" onClick={() => removeSymbol(sym)} className="hover:text-[var(--accent-red)] transition-colors">
                    <X className="w-2.5 h-2.5" />
                  </button>
                </span>
              ))}
              <input
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addSymbol(); }
                  if (e.key === "Backspace" && !symbolInput && form.symbols.length > 0) {
                    removeSymbol(form.symbols[form.symbols.length - 1]);
                  }
                }}
                placeholder={form.symbols.length === 0 ? "输入交易对，回车添加" : ""}
                className="flex-1 min-w-[100px] bg-transparent text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none"
              />
            </div>
          </div>

          {/* Interval — free text */}
          <div className="flex flex-col gap-1">
            <label className={labelCls}>时间周期</label>
            <Input
              value={form.interval}
              onChange={(e) => setForm((f) => ({ ...f, interval: e.target.value }))}
              placeholder="5m, 1h, 4h, 1d..."
              className="h-8 text-xs"
            />
          </div>

          {/* Date range — shadcn Range Calendar */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label className={labelCls}>回测区间</label>
              {form.start_date && form.end_date && (
                <span className="text-[10px] text-[var(--accent-blue)] font-medium animate-in fade-in duration-300">
                  {form.start_date} → {form.end_date}
                </span>
              )}
            </div>
            <div className="rounded-lg border border-[var(--border-gray)] bg-[var(--bg-elevated)] flex justify-center overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-300">
              <Calendar
                mode="range"
                numberOfMonths={2}
                locale={zhCN}
                selected={{
                  from: form.start_date ? parse(form.start_date, "yyyy-MM-dd", new Date()) : undefined,
                  to: form.end_date ? parse(form.end_date, "yyyy-MM-dd", new Date()) : undefined,
                }}
                onSelect={(range: DateRange | undefined) => {
                  setForm((f) => ({
                    ...f,
                    start_date: range?.from ? format(range.from, "yyyy-MM-dd") : f.start_date,
                    end_date: range?.to ? format(range.to, "yyyy-MM-dd") : f.end_date,
                  }));
                }}
                defaultMonth={form.start_date ? parse(form.start_date, "yyyy-MM-dd", new Date()) : undefined}
                className="p-3"
              />
            </div>
          </div>

          {/* Initial capital */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label className={labelCls}>初始资金 (USDT)</label>
              {isPortfolio && (
                <span className="text-[9px] text-[var(--accent-purple)]">从配置读取</span>
              )}
            </div>
            <Input
              type="number"
              value={form.initial_capital}
              onChange={(e) => setForm((f) => ({ ...f, initial_capital: e.target.value }))}
              min="100"
              className="h-8 text-xs"
            />
          </div>

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
      const data = await apiGet<{ runs: BacktestRunSummary[]; total: number }>("/api/backtest/runs");
      if (data) setRuns(data.runs ?? []);
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
      strategy: form.strategy_name,
      symbols: form.symbols,
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
          <Select value={statusFilter} onValueChange={(v: string | null) => v && setStatusFilter(v)}>
            <SelectTrigger className="h-7 text-xs w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="queued">排队中</SelectItem>
              <SelectItem value="running">运行中</SelectItem>
              <SelectItem value="completed">已完成</SelectItem>
              <SelectItem value="failed">失败</SelectItem>
              <SelectItem value="cancelled">已取消</SelectItem>
            </SelectContent>
          </Select>
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
                progress={progressMap[run.run_id] ?? run.progress_pct ?? null}
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
                ) : selectedRun?.status === "running" || selectedRun?.status === "queued" ? (
                  <div className="flex items-center justify-center h-full">
                    <div className="flex flex-col items-center gap-6 w-80">
                      {/* SVG Progress Ring */}
                      {(() => {
                        const pct = selectedRun.status === "running"
                          ? (progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0)
                          : 0;
                        const isQueued = selectedRun.status === "queued";
                        const radius = 80;
                        const stroke = 6;
                        const circumference = 2 * Math.PI * radius;
                        const offset = circumference - (pct / 100) * circumference;

                        return (
                          <>
                            <div className="relative progress-ring-glow">
                              <svg width="184" height="184" viewBox="0 0 184 184">
                                {/* Track */}
                                <circle cx="92" cy="92" r={radius} fill="none"
                                  stroke="var(--bg-subtle)" strokeWidth={stroke} />
                                {/* Progress arc */}
                                {!isQueued && (
                                  <circle cx="92" cy="92" r={radius} fill="none"
                                    stroke="url(#ringGradient)" strokeWidth={stroke}
                                    strokeLinecap="round"
                                    strokeDasharray={circumference}
                                    strokeDashoffset={offset}
                                    style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
                                  />
                                )}
                                {/* Spinning tail for queued */}
                                {isQueued && (
                                  <circle cx="92" cy="92" r={radius} fill="none"
                                    stroke="var(--accent-blue)" strokeWidth={stroke}
                                    strokeLinecap="round" opacity="0.6"
                                    strokeDasharray={`${circumference * 0.25} ${circumference * 0.75}`}
                                    style={{ transform: "rotate(-90deg)", transformOrigin: "center", animation: "spin 1.5s linear infinite" }}
                                  />
                                )}
                                <defs>
                                  <linearGradient id="ringGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                                    <stop offset="0%" stopColor="#4C9EEB" />
                                    <stop offset="100%" stopColor="#A78BFA" />
                                  </linearGradient>
                                </defs>
                              </svg>
                              {/* Center text */}
                              <div className="absolute inset-0 flex flex-col items-center justify-center">
                                {isQueued ? (
                                  <span className="text-sm font-medium text-[var(--text-muted)] progress-breathe">排队中</span>
                                ) : (
                                  <>
                                    <span className="text-4xl font-bold font-heading text-[var(--text-primary)] progress-pop" key={pct}>
                                      {pct}
                                    </span>
                                    <span className="text-sm font-medium text-[var(--text-muted)] -mt-0.5">%</span>
                                  </>
                                )}
                              </div>
                            </div>

                            {/* Status label */}
                            <span className="text-sm font-medium text-[var(--text-secondary)]">
                              {isQueued ? "等待运行..." : "回测运行中"}
                            </span>

                          </>
                        );
                      })()}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-48">
                    <span className="text-xs text-[var(--text-muted)]">
                      {selectedRun?.status === "failed"
                        ? `回测失败: ${selectedRun.error ?? "未知错误"}`
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
