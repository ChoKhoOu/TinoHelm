"use client";

import { useRef, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Maximize2, ExternalLink, Download } from "lucide-react";
import { API_BASE } from "@/lib/api";

interface TearsheetTabProps {
  runId: string;
}

const CSV_REPORTS = [
  { key: "fills", label: "成交报告" },
  { key: "orders", label: "订单报告" },
  { key: "positions", label: "持仓报告" },
  { key: "account", label: "账户报告" },
  { key: "order_fills", label: "订单成交" },
] as const;

export function TearsheetTab({ runId }: TearsheetTabProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);

  const src = `${API_BASE}/api/backtest/${runId}/artifacts/tearsheet.html`;

  const handleFullscreen = () => {
    if (iframeRef.current) {
      iframeRef.current.requestFullscreen?.();
    }
  };

  const handleOpenTab = () => {
    window.open(src, "_blank", "noopener,noreferrer");
  };

  const handleDownloadCsv = (key: string) => {
    window.open(
      `${API_BASE}/api/backtest/${runId}/artifacts/${key}_report.csv`,
      "_blank",
      "noopener,noreferrer"
    );
  };

  return (
    <div className="flex flex-col h-full">
      {/* Controls bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-card shrink-0">
        <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-muted-foreground">
          回测报告
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={handleOpenTab}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            新标签页打开
          </button>
          <button
            onClick={handleFullscreen}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <Maximize2 className="w-3.5 h-3.5" />
            全屏
          </button>
        </div>
      </div>

      {/* iframe container */}
      <div className="relative flex-1 min-h-0">
        {!loaded && (
          <div className="absolute inset-0 flex flex-col gap-3 p-4">
            <Skeleton className="h-40 w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
            <Skeleton className="h-32 w-full rounded-lg" />
          </div>
        )}
        <iframe
          ref={iframeRef}
          src={src}
          onLoad={() => setLoaded(true)}
          className={`w-full h-full border-0 transition-opacity duration-300 ${loaded ? "opacity-100" : "opacity-0"}`}
          title="Tearsheet"
          sandbox="allow-scripts allow-same-origin"
        />
      </div>

      {/* CSV download bar */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-t border-border bg-card shrink-0 flex-wrap">
        <span className="text-[10px] font-semibold tracking-[0.5px] uppercase text-muted-foreground mr-1">
          下载报表
        </span>
        {CSV_REPORTS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => handleDownloadCsv(key)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] text-muted-foreground hover:text-primary hover:bg-[var(--accent-blue-20)] border border-border hover:border-primary transition-colors"
          >
            <Download className="w-3 h-3" />
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
