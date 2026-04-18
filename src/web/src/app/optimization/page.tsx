"use client";

import { Fragment, useState, useEffect, useCallback, useRef } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { FlaskConical, Play, Check, ChevronRight } from "lucide-react";
import { formatDateTime } from "@/lib/format";
import { apiGet } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { EmptyState } from "@/components/EmptyState";
import { ConfirmModal } from "@/components/ConfirmModal";
import { Pagination } from "@/components/Pagination";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* ── Types ──────────────────────────────────────────────── */

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
}

type Phase = "configure" | "running" | "results";

interface ParamRow {
  name: string;
  min: string;
  max: string;
  step: string;
  current: string;
}

/* ── Constants ──────────────────────────────────────────── */

const METHODS = ["Grid Search", "Random"] as const;
const OBJECTIVES = ["Sharpe Ratio", "Sortino", "Calmar", "Max DD"] as const;

/* ── Mock Data ──────────────────────────────────────────── */

function mockStrategies(): string[] {
  return ["MM-perp v3.2", "Stat-arb v1.8", "Funding arb v2.0"];
}

function mockParams(): ParamRow[] {
  return [
    { name: "spread_bps", min: "1.0", max: "5.0", step: "0.5", current: "2.5" },
    { name: "order_size", min: "0.1", max: "2.0", step: "0.1", current: "0.5" },
    { name: "max_position", min: "1", max: "10", step: "1", current: "5" },
    { name: "cancel_after_ms", min: "100", max: "1000", step: "100", current: "500" },
  ];
}

function mockHeatmap(): { xs: string[]; ys: string[]; values: number[][] } {
  const xs = ["1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"];
  const ys = ["0.1", "0.3", "0.5", "0.7", "0.9", "1.2", "1.5", "1.8", "2.0"];
  const values = ys.map((_, yi) =>
    xs.map((_, xi) => {
      const v = 1.5 + Math.sin(xi * 0.5) * Math.cos(yi * 0.4) + Math.random() * 0.5;
      return Number(v.toFixed(2));
    }),
  );
  return { xs, ys, values };
}

function mockTop10(): { rank: number; params: string; sharpe: number; maxDD: number; winRate: number; trades: number }[] {
  return Array.from({ length: 10 }, (_, i) => ({
    rank: i + 1,
    params: `spread=${(1 + i * 0.4).toFixed(1)} size=${(0.1 + i * 0.15).toFixed(1)} pos=${3 + (i % 4)}`,
    sharpe: Number((2.8 - i * 0.15 + Math.random() * 0.1).toFixed(2)),
    maxDD: Number((-1.5 - i * 0.3 - Math.random()).toFixed(1)),
    winRate: Number((65 - i * 1.5 + Math.random() * 3).toFixed(1)),
    trades: Math.floor(800 + Math.random() * 400),
  }));
}

/* ── Heatmap Color ───────────────────────────────────────── */

function heatColor(v: number, min: number, max: number): string {
  const norm = max === min ? 0.5 : Math.max(0, Math.min(1, (v - min) / (max - min)));
  const r = Math.round(254 - 200 * norm);
  const g = Math.round(80 + 60 * norm);
  const b = Math.round(80 + 40 * norm);
  return `rgba(${r},${g},${b},${0.2 + norm * 0.6})`;
}

/* ── Page ────────────────────────────────────────────────── */

export default function OptimizationPage() {
  /* Existing run list (left panel) */
  const [runs, setRuns] = useState<OptRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const hasAutoSelected = useRef(false);

  /* Configuration phase */
  const [phase, setPhase] = useState<Phase>("configure");
  const [strategies] = useState(mockStrategies);
  const [selectedStrategy, setSelectedStrategy] = useState(mockStrategies()[0]);
  const [method, setMethod] = useState<(typeof METHODS)[number]>("Grid Search");
  const [objective, setObjective] = useState<(typeof OBJECTIVES)[number]>("Sharpe Ratio");
  const [params, setParams] = useState<ParamRow[]>(mockParams);
  const [constraint, setConstraint] = useState("MaxDD < 5%");

  /* Running phase */
  const [progress, setProgress] = useState(0);
  const [runningBest, setRunningBest] = useState<number | null>(null);
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* Results phase */
  const [heatmap, setHeatmap] = useState(mockHeatmap);
  const [topResults, setTopResults] = useState(mockTop10);
  const [resultsPage, setResultsPage] = useState(1);
  const [resultsPageSize, setResultsPageSize] = useState(20);
  const [applyModal, setApplyModal] = useState<{ open: boolean; rank: number }>({ open: false, rank: 0 });

  /* Detail panel for past runs */
  const [detailResult, setDetailResult] = useState<OptResult | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  /* Load runs */
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<OptRun[]>("/api/backtest/optimize/runs");
      if (data && data.length > 0) {
        setRuns(data);
        if (!hasAutoSelected.current) {
          hasAutoSelected.current = true;
          setSelectedId(data[0].id);
        }
      }
    } catch {
      // no runs available
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  /* Load detail when selected */
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    async function load() {
      setDetailLoading(true);
      try {
        const data = await apiGet<OptResult>(`/api/backtest/optimize/${selectedId}/result`);
        if (!cancelled) setDetailResult(data ?? null);
      } catch {
        if (!cancelled) setDetailResult(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [selectedId]);

  const selected = runs.find((r) => r.id === selectedId);

  /* Param editing */
  function updateParam(idx: number, field: keyof ParamRow, value: string) {
    setParams((prev) => prev.map((p, i) => (i === idx ? { ...p, [field]: value } : p)));
  }

  /* Compute combination count */
  const combinationCount = params.reduce((acc, p) => {
    const min = parseFloat(p.min);
    const max = parseFloat(p.max);
    const step = parseFloat(p.step);
    if (isNaN(min) || isNaN(max) || isNaN(step) || step <= 0) return acc;
    return acc * (Math.floor((max - min) / step) + 1);
  }, 1);

  /* Start optimization */
  function startOptimization() {
    setPhase("running");
    setProgress(0);
    setRunningBest(null);

    progressRef.current = setInterval(() => {
      setProgress((prev) => {
        const next = prev + 2 + Math.random() * 3;
        if (next >= 100) {
          if (progressRef.current) clearInterval(progressRef.current);
          setTimeout(() => {
            setPhase("results");
            setHeatmap(mockHeatmap());
            setTopResults(mockTop10());
          }, 500);
          return 100;
        }
        setRunningBest(1.2 + next / 60);
        return next;
      });
    }, 400);
  }

  useEffect(() => {
    return () => {
      if (progressRef.current) clearInterval(progressRef.current);
    };
  }, []);

  const combo = Math.round((progress / 100) * combinationCount);
  const eta = progress < 100 ? `~${Math.round((100 - progress) / 4)}s` : "Done";

  /* Show new optimization UI vs past run detail */
  const showNewOpt = !selectedId || runs.length === 0;

  return (
    <>
      <ConfirmModal
        open={applyModal.open}
        onClose={() => setApplyModal({ open: false, rank: 0 })}
        onConfirm={() => setApplyModal({ open: false, rank: 0 })}
        level="warning"
        title={`Apply parameters from Rank #${applyModal.rank}?`}
        description="This will overwrite the current strategy parameters. Make sure to review the results before applying."
        confirmLabel="Apply"
      />

      <div className="flex h-full">
        {/* Left panel — run list */}
        <div className="w-[340px] shrink-0 flex flex-col border-r">
          <FadeIn direction="down" duration={0.25} className="shrink-0">
            <div className="flex flex-col gap-1 px-4 py-4 border-b">
              <div className="flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-muted-foreground" />
                <h1 className="font-heading text-[1.1rem] font-bold tracking-tight text-foreground">
                  Optimization
                </h1>
              </div>
              <span className="text-[0.68rem] font-mono text-muted-foreground">
                Parameter search & results
              </span>
            </div>
          </FadeIn>

          {/* New optimization button */}
          <div className="px-4 py-3 border-b">
            <Button
              variant="ghost"
              onClick={() => { setSelectedId(null); setPhase("configure"); }}
              className={`w-full justify-start text-[0.72rem] font-mono ${
                showNewOpt ? "bg-qds-accent-dim text-primary" : "text-qds-t1 hover:bg-secondary"
              }`}
            >
              <Play className="w-3.5 h-3.5 mr-2" />
              New Optimization
            </Button>
          </div>

          {/* Past runs list */}
          <div className="flex-1 overflow-y-auto">
            {runsLoading ? (
              <div className="flex flex-col">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="flex flex-col gap-2 px-4 py-3 border-b animate-pulse">
                    <div className="h-3.5 w-28 rounded bg-secondary" />
                    <div className="h-3 w-20 rounded bg-secondary" />
                  </div>
                ))}
              </div>
            ) : runs.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <p className="text-[0.72rem] text-muted-foreground font-mono">No past runs</p>
              </div>
            ) : (
              runs.map((run) => (
                <button
                  key={run.id}
                  onClick={() => { setSelectedId(run.id); setPhase("configure"); }}
                  className={`w-full text-left flex items-center gap-3 px-4 py-3 border-b transition-colors duration-150 ${
                    selectedId === run.id ? "bg-secondary" : "hover:bg-secondary/50"
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[0.78rem] font-semibold font-mono text-foreground truncate">
                        {run.strategy_name}
                      </span>
                      <StatusBadge status={run.status} />
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[0.65rem] font-mono text-muted-foreground">
                        {run.n_trials} trials
                      </span>
                      {run.best_value != null && (
                        <span className="text-[0.65rem] font-mono text-qds-success">
                          Best: {Number(run.best_value).toFixed(4)}
                        </span>
                      )}
                      <span className="text-[0.65rem] font-mono text-qds-t3 ml-auto">
                        {formatDateTime(run.created_at)}
                      </span>
                    </div>
                  </div>
                  {selectedId === run.id && (
                    <ChevronRight className="w-3.5 h-3.5 text-primary shrink-0" />
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right panel */}
        <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
          {showNewOpt ? (
            <div className="flex flex-col gap-5 p-6">
              {/* ── Configure Phase ── */}
              {phase === "configure" && (
                <FadeIn direction="up" duration={0.3}>
                  <div className="flex flex-col gap-5">
                    <div className="qds-section-label">
                      Configuration
                    </div>

                    <div className="rounded-xl bg-card border p-5">
                      {/* Strategy + Method + Objective row */}
                      <div className="flex items-center gap-4 flex-wrap mb-5">
                        <div className="flex items-center gap-2">
                          <span className="text-[0.72rem] text-muted-foreground">Strategy</span>
                          <select
                            value={selectedStrategy}
                            onChange={(e) => setSelectedStrategy(e.target.value)}
                            className="font-mono text-[0.75rem] px-2 py-1.5 bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                          >
                            {strategies.map((s) => (
                              <option key={s} value={s}>{s}</option>
                            ))}
                          </select>
                        </div>

                        <div className="flex items-center gap-[2px] rounded-sm bg-input p-[3px]">
                          {METHODS.map((m) => (
                            <button
                              key={m}
                              onClick={() => setMethod(m)}
                              className={`rounded px-3 py-1.5 text-[0.72rem] font-mono font-medium transition-all duration-150 ${
                                method === m
                                  ? "bg-secondary text-foreground shadow-sm"
                                  : "text-muted-foreground hover:text-qds-t1"
                              }`}
                            >
                              {m}
                            </button>
                          ))}
                        </div>

                        <div className="flex items-center gap-2">
                          <span className="text-[0.72rem] text-muted-foreground">Objective</span>
                          <select
                            value={objective}
                            onChange={(e) => setObjective(e.target.value as typeof objective)}
                            className="font-mono text-[0.75rem] px-2 py-1.5 bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                          >
                            {OBJECTIVES.map((o) => (
                              <option key={o} value={o}>{o}</option>
                            ))}
                          </select>
                        </div>
                      </div>

                      {/* Parameter table */}
                      <div className="mb-4">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Parameter</TableHead>
                              <TableHead className="text-right">Min</TableHead>
                              <TableHead className="text-right">Max</TableHead>
                              <TableHead className="text-right">Step</TableHead>
                              <TableHead className="text-right">Current</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {params.map((p, i) => (
                              <TableRow key={p.name}>
                                <TableCell>{p.name}</TableCell>
                                <TableCell className="text-right">
                                  <input
                                    value={p.min}
                                    onChange={(e) => updateParam(i, "min", e.target.value)}
                                    className="w-[80px] px-2 py-1 text-right font-mono text-[0.72rem] bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                                  />
                                </TableCell>
                                <TableCell className="text-right">
                                  <input
                                    value={p.max}
                                    onChange={(e) => updateParam(i, "max", e.target.value)}
                                    className="w-[80px] px-2 py-1 text-right font-mono text-[0.72rem] bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                                  />
                                </TableCell>
                                <TableCell className="text-right">
                                  <input
                                    value={p.step}
                                    onChange={(e) => updateParam(i, "step", e.target.value)}
                                    className="w-[80px] px-2 py-1 text-right font-mono text-[0.72rem] bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                                  />
                                </TableCell>
                                <TableCell className="text-right">{p.current}</TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>

                      {/* Constraint + Start */}
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-[0.72rem] text-muted-foreground">Constraints:</span>
                          <input
                            value={constraint}
                            onChange={(e) => setConstraint(e.target.value)}
                            className="w-[140px] px-2 py-1 font-mono text-[0.72rem] bg-input border rounded text-foreground outline-none focus:border-primary transition-colors"
                          />
                        </div>
                        <button
                          onClick={startOptimization}
                          className="flex items-center gap-2 px-4 py-2 rounded-sm bg-primary text-white font-mono text-[0.72rem] font-medium hover:opacity-90 transition-opacity duration-150"
                        >
                          <Play className="w-3.5 h-3.5" />
                          Start Optimization · {combinationCount} combinations
                        </button>
                      </div>
                    </div>
                  </div>
                </FadeIn>
              )}

              {/* ── Running Phase ── */}
              {phase === "running" && (
                <FadeIn direction="up" duration={0.3}>
                  <div className="flex flex-col gap-5">
                    <div className="qds-section-label">
                      Running
                    </div>

                    <div className="rounded-xl bg-card border p-5">
                      {/* Progress bar with shimmer */}
                      <div className="relative h-2 bg-secondary rounded overflow-hidden">
                        <div
                          className="h-full bg-primary rounded transition-[width] duration-[1.5s] ease-out"
                          style={{ width: `${progress}%` }}
                        />
                        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-[shimmer_2.5s_ease-in-out_infinite]" />
                      </div>

                      {/* Stats grid */}
                      <div className="grid grid-cols-4 gap-3 mt-4">
                        <div>
                          <div className="qds-stat-label">Progress</div>
                          <div className="font-mono text-[0.78rem] font-medium text-primary mt-0.5">{Math.round(progress)}%</div>
                        </div>
                        <div>
                          <div className="qds-stat-label">Combinations</div>
                          <div className="font-mono text-[0.78rem] font-medium text-foreground mt-0.5">{combo} / {combinationCount}</div>
                        </div>
                        <div>
                          <div className="qds-stat-label">Best {objective.split(" ")[0]}</div>
                          <div className="font-mono text-[0.78rem] font-medium text-qds-success mt-0.5">
                            {runningBest != null ? runningBest.toFixed(2) : "..."}
                          </div>
                        </div>
                        <div>
                          <div className="qds-stat-label">ETA</div>
                          <div className="font-mono text-[0.78rem] font-medium text-primary mt-0.5">{eta}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </FadeIn>
              )}

              {/* ── Results Phase ── */}
              {phase === "results" && (
                <FadeIn direction="up" duration={0.3}>
                  <div className="flex flex-col gap-5">
                    <div className="qds-section-label">
                      Results
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      {/* Heatmap */}
                      <div className="rounded-xl bg-card border overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-3 border-b">
                          <span className="text-[0.8rem] font-semibold text-foreground">Parameter Heatmap</span>
                          <span className="font-mono text-[0.65rem] text-muted-foreground">spread_bps x order_size</span>
                        </div>
                        <div className="p-4">
                          {(() => {
                            const allVals = heatmap.values.flat();
                            const min = Math.min(...allVals);
                            const max = Math.max(...allVals);
                            return (
                              <div
                                className="grid gap-[2px] font-mono text-[0.62rem]"
                                style={{ gridTemplateColumns: `auto repeat(${heatmap.xs.length}, 1fr)` }}
                              >
                                <div className="px-1 py-1 text-center text-qds-t3" />
                                {heatmap.xs.map((x) => (
                                  <div key={x} className="px-1 py-1 text-center text-muted-foreground text-[0.6rem]">{x}</div>
                                ))}
                                {heatmap.ys.map((y, yi) => (
                                  <Fragment key={`row-${yi}`}>
                                    <div className="px-1 py-1 text-muted-foreground text-[0.62rem] flex items-center justify-center">{y}</div>
                                    {heatmap.values[yi].map((v, xi) => (
                                      <div
                                        key={`${yi}-${xi}`}
                                        className="rounded-[3px] px-1 py-1.5 text-center font-medium transition-transform duration-150 hover:scale-110 hover:z-10"
                                        style={{
                                          background: heatColor(v, min, max),
                                          color: (v - min) / (max - min) > 0.5 ? "#fff" : "var(--t1)",
                                        }}
                                      >
                                        {v.toFixed(2)}
                                      </div>
                                    ))}
                                  </Fragment>
                                ))}
                              </div>
                            );
                          })()}
                        </div>
                      </div>

                      {/* Top 10 */}
                      <div className="rounded-xl bg-card border overflow-hidden flex flex-col">
                        <div className="flex items-center justify-between px-4 py-3 border-b">
                          <span className="text-[0.8rem] font-semibold text-foreground">Top 10</span>
                        </div>
                        <div className="overflow-x-auto flex-1">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>#</TableHead>
                                <TableHead>Params</TableHead>
                                <TableHead className="text-right">Sharpe</TableHead>
                                <TableHead className="text-right">Max DD</TableHead>
                                <TableHead className="text-right">Win%</TableHead>
                                <TableHead className="text-right">Trades</TableHead>
                                <TableHead className="text-right" />
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {topResults.map((r) => (
                                <TableRow key={r.rank}>
                                  <TableCell>#{r.rank}</TableCell>
                                  <TableCell>{r.params}</TableCell>
                                  <TableCell className="text-right text-qds-success">{r.sharpe.toFixed(2)}</TableCell>
                                  <TableCell className="text-right text-destructive">{r.maxDD}%</TableCell>
                                  <TableCell className="text-right">{r.winRate}%</TableCell>
                                  <TableCell className="text-right">
                                    <span className={r.trades < 100 ? "text-qds-warning" : ""}>
                                      {r.trades}
                                    </span>
                                  </TableCell>
                                  <TableCell className="text-right">
                                    <button
                                      onClick={() => setApplyModal({ open: true, rank: r.rank })}
                                      className="rounded px-2 py-0.5 text-[0.62rem] font-mono border text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all duration-150"
                                    >
                                      {r.rank === 1 ? "★ Apply" : "Apply"}
                                    </button>
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </div>
                  </div>
                </FadeIn>
              )}
            </div>
          ) : selected ? (
            /* Past run detail */
            <div className="flex flex-col h-full">
              {/* Detail header */}
              <div className="flex items-center justify-between px-6 py-4 border-b shrink-0">
                <div className="flex items-center gap-3">
                  <FlaskConical className="w-4 h-4 text-muted-foreground" />
                  <span className="text-[0.9rem] font-bold font-mono text-foreground">
                    {selected.strategy_name}
                  </span>
                  <StatusBadge status={selected.status} />
                </div>
                <div className="flex items-center gap-4 font-mono text-[0.72rem]">
                  <div className="text-right">
                    <div className="text-[0.6rem] text-muted-foreground uppercase tracking-wide">Metric</div>
                    <div className="text-qds-t1">{selected.metric ?? "..."}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[0.6rem] text-muted-foreground uppercase tracking-wide">Trials</div>
                    <div className="text-qds-t1">{selected.n_trials}</div>
                  </div>
                  {selected.best_value != null && (
                    <div className="text-right">
                      <div className="text-[0.6rem] text-muted-foreground uppercase tracking-wide">Best</div>
                      <div className="text-qds-success font-semibold">{Number(selected.best_value).toFixed(4)}</div>
                    </div>
                  )}
                </div>
              </div>

              {/* Detail body */}
              <div className="flex-1 overflow-y-auto p-6">
                {detailLoading ? (
                  <div className="flex flex-col gap-4 animate-pulse">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div key={i} className="h-28 rounded-xl bg-card border" />
                    ))}
                  </div>
                ) : !detailResult ? (
                  <EmptyState variant="first-use" title="No result data" description="Run completed but no result data available" />
                ) : (
                  <div className="flex flex-col gap-5">
                    {/* Best Params */}
                    <section>
                      <div className="text-[0.62rem] font-mono text-primary uppercase tracking-[0.15em] flex items-center gap-2 mb-3">
                        Best Parameters
                        <span className="flex-1 h-px bg-border" />
                      </div>
                      <div className="rounded-xl bg-card border overflow-hidden">
                        {Object.entries(detailResult.best_params ?? {}).map(([k, v], i, arr) => (
                          <div
                            key={k}
                            className={`flex items-center justify-between px-4 py-2.5 text-[0.75rem] font-mono ${
                              i < arr.length - 1 ? "border-b" : ""
                            }`}
                          >
                            <span className="text-muted-foreground">{k}</span>
                            <span className="text-foreground font-semibold">{String(v)}</span>
                          </div>
                        ))}
                        {detailResult.best_value != null && (
                          <div className="flex items-center justify-between px-4 py-2.5 text-[0.75rem] font-mono bg-qds-success-dim border-t">
                            <span className="text-qds-success font-semibold">Best {detailResult.metric ?? "Metric"}</span>
                            <span className="text-qds-success font-bold">{Number(detailResult.best_value).toFixed(4)}</span>
                          </div>
                        )}
                      </div>
                    </section>

                    {/* Trial History */}
                    <section>
                      <div className="text-[0.62rem] font-mono text-primary uppercase tracking-[0.15em] flex items-center gap-2 mb-3">
                        Trial History ({(detailResult.trials ?? []).length})
                        <span className="flex-1 h-px bg-border" />
                      </div>
                      <div className="rounded-xl bg-card border overflow-hidden">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-14">#</TableHead>
                              <TableHead>Params</TableHead>
                              <TableHead className="text-right w-24">Value</TableHead>
                              <TableHead className="text-right w-20">State</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {(detailResult.trials ?? []).length === 0 ? (
                              <TableRow>
                                <TableCell colSpan={4}>No trial data</TableCell>
                              </TableRow>
                            ) : (
                              (detailResult.trials ?? []).map((trial) => (
                                <TableRow key={trial.number}>
                                  <TableCell>{trial.number}</TableCell>
                                  <TableCell>
                                    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                                      {Object.entries(trial.params ?? {}).map(([k, v]) => (
                                        <span key={k} className="text-[0.68rem] text-muted-foreground">
                                          {k}=<span className="text-foreground">{String(v)}</span>
                                        </span>
                                      ))}
                                    </div>
                                  </TableCell>
                                  <TableCell className="text-right font-semibold">
                                    {trial.value != null ? Number(trial.value).toFixed(4) : "..."}
                                  </TableCell>
                                  <TableCell className="text-right">
                                    <span className={`text-[0.65rem] font-semibold ${trial.state === "COMPLETE" ? "text-qds-success" : "text-muted-foreground"}`}>
                                      {trial.state === "COMPLETE" ? "Done" : trial.state}
                                    </span>
                                  </TableCell>
                                </TableRow>
                              ))
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </section>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
              <EmptyState
                variant="first-use"
                icon={<FlaskConical className="size-6 text-muted-foreground" />}
                title="Select a run or start new"
                description="Choose a past optimization run from the list, or start a new one"
              />
            </div>
          )}
        </div>
      </div>
    </>
  );
}
