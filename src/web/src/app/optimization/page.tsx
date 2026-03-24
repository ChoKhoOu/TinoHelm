"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { FlaskConical, ChevronRight } from "lucide-react";
import { formatDateTime } from "@/lib/format";
import { apiGet } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";

/* ── Types ──────────────────────────────────────────────────── */

interface OptRun {
  id: string;
  strategy_name: string;
  status: "running" | "completed" | "failed";
  n_trials: number;
  best_value: number | null;
  metric: string;
  created_at: string | null;
}

interface Trial {
  number: number;
  params: Record<string, number | string | boolean>;
  value: number | null;
  state: string;
}

interface OptResult {
  best_params: Record<string, number | string | boolean>;
  best_value: number | null;
  metric: string;
  trials: Trial[];
  importances: Record<string, number>;
  validation?: Record<string, number | string>;
}

/* ── Constants ──────────────────────────────────────────────── */

import { CHART_AXIS_STYLE as AXIS_STYLE, CHART_TOOLTIP_STYLE as TOOLTIP_STYLE } from "@/lib/chartTheme";
import { StatusBadge } from "@/components/StatusBadge";

/* ── Detail Panel ────────────────────────────────────────────── */

function DetailPanel({ runId }: { runId: string }) {
  const [result, setResult] = useState<OptResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await apiGet<OptResult>(`/api/backtest/optimize/${runId}/result`);
        if (!cancelled) setResult(data ?? null);
      } catch {
        if (!cancelled) setError("加载结果失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex flex-col gap-5 p-6 animate-pulse">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-32 rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)]" />
        ))}
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-full">
        <span className="font-mono text-[12px] text-[#EF5350]">{error ?? "暂无结果"}</span>
      </div>
    );
  }

  const importanceEntries = Object.entries(result.importances ?? {})
    .map(([name, value]) => ({ name, value: Number(value) }))
    .sort((a, b) => b.value - a.value);

  return (
    <div className="flex flex-col gap-4 p-6 overflow-y-auto h-full">
      {/* Best Params */}
      <section>
        <div className="text-[10px] font-semibold tracking-[0.8px] uppercase font-mono text-[var(--text-muted)] mb-3">
          最优参数
        </div>
        <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
          {Object.entries(result.best_params ?? {}).map(([k, v], i, arr) => (
            <div
              key={k}
              className={`flex items-center justify-between px-4 py-[10px] text-[11px] font-mono ${
                i < arr.length - 1 ? "border-b border-[var(--border-gray)]" : ""
              }`}
            >
              <span className="text-[var(--text-secondary)]">{k}</span>
              <span className="text-[var(--text-primary)] font-semibold">{String(v)}</span>
            </div>
          ))}
          {result.best_value !== null && result.best_value !== undefined && (
            <div className="flex items-center justify-between px-4 py-[10px] text-[11px] font-mono bg-[#0d2e1c] border-t border-[var(--border-gray)]">
              <span className="text-[#26D97F] font-semibold">最优 {result.metric ?? "指标"}</span>
              <span className="text-[#26D97F] font-bold">{Number(result.best_value).toFixed(4)}</span>
            </div>
          )}
          {Object.keys(result.best_params ?? {}).length === 0 && (
            <div className="px-4 py-3 text-[11px] font-mono text-[var(--text-muted)]">暂无参数数据</div>
          )}
        </div>
      </section>

      {/* Trial History */}
      <section>
        <div className="text-[10px] font-semibold tracking-[0.8px] uppercase font-mono text-[var(--text-muted)] mb-3">
          试验历史 ({(result.trials ?? []).length} 次)
        </div>
        <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
          {/* Header */}
          <div className="flex items-center px-4 py-2 border-b border-[var(--border-gray)] bg-[var(--bg-elevated)]">
            <span className="w-14 text-[10px] font-semibold font-mono text-[var(--text-muted)] uppercase">#</span>
            <span className="flex-1 text-[10px] font-semibold font-mono text-[var(--text-muted)] uppercase">参数</span>
            <span className="w-24 text-right text-[10px] font-semibold font-mono text-[var(--text-muted)] uppercase">指标值</span>
            <span className="w-20 text-right text-[10px] font-semibold font-mono text-[var(--text-muted)] uppercase">状态</span>
          </div>
          {/* Rows */}
          <div className="max-h-[280px] overflow-y-auto">
            {(result.trials ?? []).length === 0 ? (
              <div className="px-4 py-3 text-[11px] font-mono text-[var(--text-muted)]">暂无试验数据</div>
            ) : (
              (result.trials ?? []).map((trial) => (
                <div
                  key={trial.number}
                  className="flex items-start px-4 py-[9px] border-b border-[var(--border-gray)] last:border-b-0 hover:bg-[var(--bg-elevated)] transition-colors duration-100"
                >
                  <span className="w-14 text-[11px] font-mono text-[var(--text-muted)]">
                    {trial.number}
                  </span>
                  <div className="flex-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {Object.entries(trial.params ?? {}).map(([k, v]) => (
                      <span key={k} className="text-[10px] font-mono text-[var(--text-secondary)]">
                        <span className="text-[var(--text-muted)]">{k}=</span>
                        {String(v)}
                      </span>
                    ))}
                  </div>
                  <span className="w-24 text-right text-[11px] font-mono text-[var(--text-primary)] font-semibold">
                    {trial.value !== null && trial.value !== undefined ? Number(trial.value).toFixed(4) : "—"}
                  </span>
                  <span className="w-20 text-right">
                    <span
                      className={`text-[9px] font-bold font-mono ${
                        trial.state === "COMPLETE" ? "text-[#26D97F]" : "text-[var(--text-muted)]"
                      }`}
                    >
                      {trial.state === "COMPLETE" ? "完成" : trial.state}
                    </span>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      {/* Parameter Importances */}
      {importanceEntries.length > 0 && (
        <section>
          <div className="text-[10px] font-semibold tracking-[0.8px] uppercase font-mono text-[var(--text-muted)] mb-3">
            参数重要度
          </div>
          <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-4">
            <ResponsiveContainer width="100%" height={importanceEntries.length * 32 + 20}>
              <BarChart
                data={importanceEntries}
                layout="vertical"
                margin={{ top: 0, right: 16, bottom: 0, left: 80 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-gray)" horizontal={false} />
                <XAxis
                  type="number"
                  tick={AXIS_STYLE}
                  tickLine={false}
                  axisLine={{ stroke: "var(--border-gray)" }}
                  tickFormatter={(v: number) => v.toFixed(2)}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={AXIS_STYLE}
                  tickLine={false}
                  axisLine={false}
                  width={75}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: number | undefined) => [v != null ? v.toFixed(4) : "—", "重要度"]}
                />
                <Bar dataKey="value" fill="#4C9EEB" radius={[0, 3, 3, 0]} barSize={16} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */

export default function OptimizationPage() {
  const [runs, setRuns] = useState<OptRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const hasAutoSelected = useRef(false);
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<OptRun[]>("/api/backtest/optimize/runs");
      if (data) {
        setRuns(data);
        if (data.length > 0 && !hasAutoSelected.current) {
          hasAutoSelected.current = true;
          setSelectedId(data[0].id);
        }
      }
    } catch {
      setError("加载优化记录失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const selected = runs.find((r) => r.id === selectedId);


  return (
    <div className="flex h-full">
      {/* Left panel */}
      <div className="w-[380px] shrink-0 flex flex-col border-r border-[var(--border-gray)]">
        {/* Header */}
        <FadeIn direction="down" duration={0.25} className="shrink-0">
          <div className="flex flex-col gap-1 px-5 py-5 border-b border-[var(--border-gray)]">
            <div className="flex items-center gap-2">
              <FlaskConical className="w-4 h-4 text-[var(--text-muted)]" />
              <h1 className="font-heading text-[20px] font-bold tracking-tight text-[var(--text-primary)]">
                参数优化
              </h1>
            </div>
            <span className="text-[11px] font-mono text-[var(--text-muted)]">
              // 策略参数搜索记录
            </span>
          </div>
        </FadeIn>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {error ? (
            <div className="flex items-center justify-center h-24">
              <span className="font-mono text-[12px] text-[#EF5350]">{error}</span>
            </div>
          ) : loading ? (
            <div className="flex flex-col divide-y divide-[var(--border-gray)]">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex flex-col gap-2 px-4 py-4 animate-pulse">
                  <div className="h-4 w-36 rounded bg-[var(--border-gray)]" />
                  <div className="h-3 w-24 rounded bg-[var(--border-gray)]" />
                </div>
              ))}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-2 px-6 text-center">
              <FlaskConical className="w-8 h-8 text-[var(--text-muted)]" />
              <p className="text-[11px] text-[var(--text-muted)]">
                暂无优化记录，运行参数优化后查看结果
              </p>
            </div>
          ) : (
            runs.map((run) => (
              <button
                key={run.id}
                onClick={() => setSelectedId(run.id)}
                className={`w-full text-left flex items-center gap-3 px-4 py-4 border-b border-[var(--border-gray)] transition-colors duration-100 ${
                  selectedId === run.id
                    ? "bg-[var(--bg-elevated)]"
                    : "hover:bg-[var(--bg-elevated)]/50"
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[12px] font-semibold font-mono text-[var(--text-primary)] truncate">
                      {run.strategy_name}
                    </span>
                    <StatusBadge status={run.status} />
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-[var(--text-muted)]">
                      {run.n_trials} 次试验
                    </span>
                    {run.best_value !== null && run.best_value !== undefined && (
                      <span className="text-[10px] font-mono text-[#26D97F]">
                        最优: {Number(run.best_value).toFixed(4)}
                      </span>
                    )}
                    <span className="text-[10px] font-mono text-[var(--text-muted)] ml-auto">
                      {formatDateTime(run.created_at)}
                    </span>
                  </div>
                </div>
                {selectedId === run.id && (
                  <ChevronRight className="w-3.5 h-3.5 text-[#4C9EEB] shrink-0" />
                )}
              </button>
            ))
          )}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {!selected ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
            <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-gray)]">
              <FlaskConical className="w-6 h-6 text-[var(--text-muted)]" />
            </div>
            <p className="text-[13px] font-semibold text-[var(--text-secondary)]">
              {runs.length === 0 ? "暂无优化记录" : "选择一个优化记录"}
            </p>
            <p className="text-[11px] text-[var(--text-muted)]">
              {runs.length === 0
                ? "在回测页面运行参数优化后，结果将显示在此处"
                : "点击左侧列表查看详细结果"}
            </p>
          </div>
        ) : (
          <div className="flex flex-col h-full">
            {/* Detail header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-gray)] shrink-0">
              <div className="flex items-center gap-3">
                <FlaskConical className="w-4 h-4 text-[var(--text-muted)]" />
                <span className="text-[14px] font-bold font-mono text-[var(--text-primary)]">
                  {selected.strategy_name}
                </span>
                <StatusBadge status={selected.status} />
              </div>
              <div className="flex items-center gap-4">
                <div className="text-right">
                  <div className="text-[9px] font-mono text-[var(--text-muted)] uppercase tracking-wide">目标指标</div>
                  <div className="text-[11px] font-mono text-[var(--text-secondary)]">{selected.metric ?? "—"}</div>
                </div>
                <div className="text-right">
                  <div className="text-[9px] font-mono text-[var(--text-muted)] uppercase tracking-wide">试验次数</div>
                  <div className="text-[11px] font-mono text-[var(--text-secondary)]">{selected.n_trials}</div>
                </div>
                {selected.best_value !== null && selected.best_value !== undefined && (
                  <div className="text-right">
                    <div className="text-[9px] font-mono text-[var(--text-muted)] uppercase tracking-wide">最优值</div>
                    <div className="text-[12px] font-bold font-mono text-[#26D97F]">
                      {Number(selected.best_value).toFixed(4)}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Detail body */}
            <div className="flex-1 overflow-hidden">
              <DetailPanel runId={selected.id} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
