"use client";

import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { apiGet } from "@/lib/api";
import { CoverageItem, SOURCE_TYPE_LABELS, formatBytes } from "./types";

interface CoveragePanelProps {
  symbol: string | null;
  onClose: () => void;
}

export function CoveragePanel({ symbol, onClose }: CoveragePanelProps) {
  const [items, setItems] = useState<CoverageItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    apiGet<CoverageItem[]>(`/api/data/coverage/${encodeURIComponent(symbol)}`)
      .then((data) => { if (data) setItems(data); })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [symbol]);

  if (!symbol) return null;

  return (
    <div className="rounded-xl border bg-card p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[11px] font-bold text-foreground tracking-wide">
          {symbol} — 数据覆盖
        </h3>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {loading ? (
        <div className="text-[10px] text-muted-foreground">加载中...</div>
      ) : items.length === 0 ? (
        <div className="text-[10px] text-muted-foreground">暂无数据</div>
      ) : (
        <div className="grid gap-1">
          {/* Header */}
          <div className="grid grid-cols-4 gap-2 text-[9px] font-semibold text-muted-foreground uppercase tracking-wider pb-1 border-b">
            <span>数据类型</span>
            <span>周期</span>
            <span>日期范围</span>
            <span>文件大小</span>
          </div>
          {items.map((item, i) => (
            <div key={i} className="grid grid-cols-4 gap-2 text-[11px] py-1.5">
              <span className="text-foreground">
                {SOURCE_TYPE_LABELS[item.source_type || item.data_type] ?? (item.source_type || item.data_type)}
              </span>
              <span className="text-muted-foreground">{item.interval}</span>
              <span className="text-muted-foreground">
                {item.start_date} → {item.end_date}
              </span>
              <span className="text-muted-foreground">
                {formatBytes(item.size_bytes)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
