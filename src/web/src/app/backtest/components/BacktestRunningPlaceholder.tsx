"use client";

import { useState } from "react";
import { Check } from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Progress ring placeholder for running/queued backtests             */
/* ------------------------------------------------------------------ */

interface BacktestRunningPlaceholderProps {
  status?: string;
  pct: number;
  message?: string;
  fallbackMsg?: string;
}

export function BacktestRunningPlaceholder({
  status,
  pct,
  message,
  fallbackMsg,
}: BacktestRunningPlaceholderProps) {
  const isRunning = status === "running" || status === "queued";
  if (!isRunning) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-muted-foreground">
          {status === "failed" ? "回测失败" : (fallbackMsg ?? "回测完成后可查看")}
        </span>
      </div>
    );
  }
  const isQueued = status === "queued";
  const radius = 80;
  const stroke = 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex items-center justify-center h-full">
      <div className="flex flex-col items-center gap-6 w-80">
        <div className="relative">
          <svg width="184" height="184" viewBox="0 0 184 184">
            <circle cx="92" cy="92" r={radius} fill="none" stroke="var(--bg-t)" strokeWidth={stroke} />
            {!isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke}
                strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={offset}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            {isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--info)" strokeWidth={stroke}
                strokeLinecap="round" opacity="0.6"
                strokeDasharray={`${circumference * 0.25} ${circumference * 0.75}`}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", animation: "spin 1.5s linear infinite" }}
              />
            )}
            {!isQueued && pct > 0 && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke + 4}
                strokeLinecap="round" opacity="0.35"
                strokeDasharray={circumference} strokeDashoffset={offset}
                filter="url(#arcGlow)"
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            <defs>
              <filter id="arcGlow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="6" />
              </filter>
            </defs>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {isQueued ? (
              <span className="text-sm font-medium text-muted-foreground">排队中</span>
            ) : (
              <>
                <span className="text-4xl font-bold font-mono text-foreground" key={pct}>{pct}</span>
                <span className="text-sm font-medium text-muted-foreground -mt-0.5">%</span>
              </>
            )}
          </div>
        </div>
        <span className="text-sm font-medium text-muted-foreground">
          {isQueued ? "等待运行..." : (message || "回测运行中")}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Copyable ID                                                        */
/* ------------------------------------------------------------------ */

export function BacktestCopyableId({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(runId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span
      className="inline-flex items-center gap-0.5 font-mono text-[0.65rem] text-qds-t3 cursor-pointer border-b border-dashed border-transparent transition-colors hover:text-qds-t1 hover:border-qds-t2"
      title={`点击复制: ${runId}`}
      onClick={handleCopy}
    >
      {copied ? (
        <><Check className="w-2.5 h-2.5 text-qds-success" /> copied</>
      ) : (
        runId.slice(0, 8)
      )}
    </span>
  );
}
