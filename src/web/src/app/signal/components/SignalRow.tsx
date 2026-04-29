"use client";

import Link from "next/link";
import { ChevronRight, X } from "lucide-react";
import { ShimmerBar, StatusBadge } from "@/components/qds";
import { apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import type { SignalRunInfo } from "../types";

interface SignalRowProps {
  run: SignalRunInfo;
}

/**
 * Single SignalRun row — 3px accent stripe + meta + status + progress.
 *
 * Mirrors the row pattern from
 * ``.claude/skills/TinoHelmDS/preview/component-row.html`` (and the
 * production ``BacktestRunRow``):
 *
 *   ┌─3px─┬─info────────────────┬─status──┬─progress───┬─tm──┬─≫─┐
 *   │     │ name                │  badge  │  shimmer   │ 2h  │   │
 *   │     │ factor_ref · stage  │         │            │ ago │   │
 *   └─────┴─────────────────────┴─────────┴────────────┴─────┴───┘
 *
 * Stripe color follows status semantics:
 *   completed → success green
 *   running   → info blue
 *   failed    → destructive red
 *   queued    → t3 (neutral grey)
 *   cancelled → t3
 */

const STRIPE_COLOR: Record<string, string> = {
  completed: "bg-qds-success",
  running:   "bg-qds-info",
  failed:    "bg-destructive",
  queued:    "bg-qds-t3",
  cancelled: "bg-qds-t3",
};

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "—";
  const diffSec = (Date.now() - t) / 1000;
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

export function SignalRow({ run }: SignalRowProps) {
  const stripeCls = STRIPE_COLOR[run.status] ?? "bg-qds-t3";
  const isRunning = run.status === "running";
  const isQueued = run.status === "queued";
  const isInflight = isRunning || isQueued;
  const isFailed = run.status === "failed";
  const pct = run.progress ?? 0;

  const cancelAction = useAction(async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    await apiPost<{ run_id: string; cancel_set: boolean }>(
      `/api/signal/cancel/${run.run_id}`,
    );
  });

  /* Sub-line text — failed shows error, running shows stage, else shows factor_ref. */
  const subLine = isFailed
    ? run.error ?? "Failed without message"
    : isRunning
    ? `${run.factor_ref} · ${run.progress_stage ?? "running"}`
    : run.factor_ref;

  const lastUpdated =
    run.finished_at ?? run.started_at ?? run.created_at;

  return (
    <Link
      href={`/signal/${run.run_id}`}
      className="block bg-card border-b border-border last:border-b-0 hover:bg-secondary transition-colors"
    >
      <div
        className="grid items-center"
        style={{ gridTemplateColumns: "3px 1fr auto auto auto auto auto" }}
      >
        {/* col1: 3px accent stripe */}
        <div className={`self-stretch ${stripeCls}`} />

        {/* col2: signal name + factor_ref / stage / error */}
        <div className="flex flex-col gap-1 px-3 py-3 min-w-0">
          <div className="font-mono text-[0.82rem] font-medium text-foreground truncate">
            {run.signal_name}
          </div>
          <div
            className={`font-mono text-[0.65rem] truncate ${
              isFailed ? "text-destructive" : "text-muted-foreground"
            }`}
          >
            {subLine}
          </div>
        </div>

        {/* col3: status badge */}
        <div className="px-2 py-3 whitespace-nowrap" data-meta-cell>
          <StatusBadge status={run.status} locale="en" />
        </div>

        {/* col4: progress shimmer / progress numeric */}
        <div className="px-2 py-3 min-w-[160px]">
          {isRunning && (
            <div className="flex flex-col gap-1.5">
              <ShimmerBar progress={pct} height="sm" active variant="accent" />
              <div className="font-mono text-[0.6rem] text-primary text-right">
                {pct}%
              </div>
            </div>
          )}
          {isQueued && (
            <div className="flex items-center gap-1 text-qds-t3 justify-end">
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.2s" }} />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.4s" }} />
            </div>
          )}
          {!isRunning && !isQueued && (
            <div className="font-mono text-[0.7rem] text-muted-foreground text-right">
              {run.run_id.slice(0, 8)}
            </div>
          )}
        </div>

        {/* col5: cancel button (inflight only, always occupies column slot) */}
        <div className="px-1 py-3 flex items-center" onClick={(e) => e.preventDefault()}>
          {isInflight && (
            <button
              type="button"
              title="取消运行"
              disabled={cancelAction.state === "loading" || cancelAction.state === "success"}
              onClick={cancelAction.execute}
              className="flex items-center justify-center w-5 h-5 rounded text-muted-foreground hover:text-destructive hover:bg-qds-danger-dim transition-colors disabled:opacity-40"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        {/* col6: relative time */}
        <div className="px-3 py-3 font-mono text-[0.65rem] text-qds-t3 whitespace-nowrap">
          {formatRelative(lastUpdated)}
        </div>

        {/* col7: chevron */}
        <div className="flex items-center pr-3 py-3">
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
        </div>
      </div>
    </Link>
  );
}
