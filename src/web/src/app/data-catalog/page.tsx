"use client";

import { useState, useEffect, useMemo } from "react";
import {
  Download, ScanLine, HardDrive, Minimize2, ChevronUp, ChevronDown, X, Trash2,
} from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";
import { Button } from "@/components/ui/button";

import {
  CatalogEntry, SortKey, SortDir, CATEGORY_LABELS, formatBytes, formatNumber,
} from "./types";
import { FilterTabs } from "./FilterTabs";
import { FetchDialog } from "./FetchDialog";
import { CoveragePanel } from "./CoveragePanel";
import { DeleteDialog } from "./DeleteDialog";
import { BatchFetchDialog } from "./BatchFetchDialog";

/* ── Sort header cell ────────────────────────────────────────────── */

function SortCell({
  label, sortKey, current, dir, onSort, className,
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
        dir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
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

  // Dialogs
  const [fetchOpen, setFetchOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [deleteEntry, setDeleteEntry] = useState<CatalogEntry | null>(null);

  // Filtering
  const [activeCategory, setActiveCategory] = useState<string | null>(null);

  // Coverage panel
  const [coverageSymbol, setCoverageSymbol] = useState<string | null>(null);

  // Sort
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Action states
  const [actionState, setActionState] = useState<Record<string, boolean>>({});
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => { loadCatalog(); }, []);

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

  // Derive categories from actual data
  const categories = useMemo(
    () => [...new Set(datasets.map((d) => d.data_type))].sort(),
    [datasets],
  );

  // Filter by active category
  const filtered = useMemo(
    () => activeCategory ? datasets.filter((d) => d.data_type === activeCategory) : datasets,
    [datasets, activeCategory],
  );

  // Sort
  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let va: string | number;
      let vb: string | number;
      switch (sortKey) {
        case "symbol": va = a.symbol; vb = b.symbol; break;
        case "data_type": va = a.data_type; vb = b.data_type; break;
        case "interval": va = a.interval; vb = b.interval; break;
        case "record_count": va = a.record_count ?? 0; vb = b.record_count ?? 0; break;
        case "start_date": va = a.start_date; vb = b.start_date; break;
        case "size_bytes": va = a.size_bytes; vb = b.size_bytes; break;
        default: return 0;
      }
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  // Stats
  const totalSize = filtered.reduce((s, d) => s + d.size_bytes, 0);
  const totalRecords = filtered.reduce((s, d) => s + (d.record_count ?? 0), 0);
  const allDates = filtered.flatMap((d) => [d.start_date, d.end_date]).filter(Boolean).sort();
  const dateRange = allDates.length >= 2 ? `${allDates[0]} → ${allDates[allDates.length - 1]}` : "—";

  const stats = [
    { label: "数据集", value: String(filtered.length) },
    { label: "总记录数", value: totalRecords > 0 ? totalRecords.toLocaleString() : "—" },
    { label: "日期跨度", value: dateRange },
    { label: "总大小", value: formatBytes(totalSize) },
  ];

  // Dynamic columns based on active category
  const isBar = activeCategory === "bar";
  const isTick = activeCategory === "trade_tick" || activeCategory === "quote_tick";
  const showTypeCol = !activeCategory; // "All" view shows type column

  return (
    <div className="flex flex-col gap-4 p-6 h-full">
      {/* Top bar */}
      <div className="flex items-end justify-between shrink-0">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-mono text-[22px] font-bold tracking-tight text-foreground">
            数据目录
          </h1>
          <span className="qds-section-label">
            // 本地 ParquetDataCatalog
          </span>
        </div>
        {/* Toolbar */}
        <div className="flex items-center gap-2">
          <Button
            onClick={() => setFetchOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-qds-success text-input px-4 py-2 text-[11px] font-bold hover:opacity-90 transition-all"
          >
            <Download className="w-3 h-3" />
            拉取数据
          </Button>
          <Button
            variant="outline"
            onClick={() => runAction("compact", "/api/data/compact", "压缩完成")}
            disabled={actionState.compact}
            className="inline-flex items-center gap-1.5 rounded-lg border bg-input px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-qds-success hover:text-foreground transition-all disabled:opacity-50"
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
            className="inline-flex items-center gap-1.5 rounded-lg border bg-input px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-qds-success hover:text-foreground transition-all disabled:opacity-50"
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
            onClick={() => setBatchOpen(true)}
            disabled={actionState.batch}
            className="inline-flex items-center gap-1.5 rounded-lg border bg-input px-3 py-2 text-[11px] font-semibold text-muted-foreground hover:border-qds-success hover:text-foreground transition-all disabled:opacity-50"
          >
            <HardDrive className="w-3 h-3" />
            批量拉取
          </Button>
        </div>
      </div>

      {/* Action message */}
      {actionMsg && (
        <div className="shrink-0 flex items-center justify-between rounded-lg bg-input border px-4 py-2">
          <span className="text-[11px] text-muted-foreground">{actionMsg}</span>
          <Button variant="ghost" size="icon" onClick={() => setActionMsg(null)}>
            <X className="w-3 h-3 text-muted-foreground hover:text-foreground" />
          </Button>
        </div>
      )}

      {/* Filter tabs */}
      <FilterTabs
        categories={categories}
        activeCategory={activeCategory}
        onCategoryChange={setActiveCategory}
      />

      {/* Stats row */}
      <FadeIn className="grid grid-cols-4 gap-4 shrink-0">
        {stats.map((s) => (
          <div key={s.label} className="rounded-xl bg-card border p-4">
            <span className="qds-stat-label">
              {s.label}
            </span>
            <div className="font-mono text-2xl font-bold mt-2 text-foreground">
              {s.value}
            </div>
          </div>
        ))}
      </FadeIn>

      {/* Coverage panel */}
      <CoveragePanel symbol={coverageSymbol} onClose={() => setCoverageSymbol(null)} />

      {/* Table */}
      {loading ? (
        <div className="rounded-xl bg-card border p-5 flex flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full bg-input" />
          ))}
        </div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-[11px] text-destructive">{error}</span>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex-1 rounded-xl bg-card border flex items-center justify-center">
          <span className="text-[11px] text-muted-foreground">
            {activeCategory ? "该类型暂无数据集" : "暂无数据集，请先拉取数据"}
          </span>
        </div>
      ) : (
        <FadeIn delay={0.1} className="rounded-xl bg-card border overflow-hidden flex-1">
          {/* Header */}
          <div className="flex items-center px-5 py-3 border-b">
            <div className="w-[160px]">
              <SortCell label="品种" sortKey="symbol" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            {showTypeCol && (
              <div className="w-[100px]">
                <SortCell label="类型" sortKey="data_type" current={sortKey} dir={sortDir} onSort={handleSort} />
              </div>
            )}
            {!isTick && (
              <div className="w-[80px]">
                <SortCell label="周期" sortKey="interval" current={sortKey} dir={sortDir} onSort={handleSort} />
              </div>
            )}
            <div className="w-[100px]">
              <SortCell
                label={isBar ? "K线数" : isTick ? "Tick数" : "记录数"}
                sortKey="record_count"
                current={sortKey}
                dir={sortDir}
                onSort={handleSort}
              />
            </div>
            <div className="flex-1">
              <SortCell label="日期范围" sortKey="start_date" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="w-[80px]">
              <SortCell label="文件大小" sortKey="size_bytes" current={sortKey} dir={sortDir} onSort={handleSort} />
            </div>
            <div className="w-[50px]" />
          </div>
          {/* Rows */}
          <div className="overflow-y-auto">
            {sorted.map((ds, i) => (
              <div
                key={`${ds.id}-${ds.symbol}-${ds.data_type}-${ds.interval}`}
                className={`flex items-center px-5 py-[11px] text-[11px] font-medium hover:bg-input/50 transition-colors ${
                  i < sorted.length - 1 ? "border-b" : ""
                }`}
              >
                <div className="w-[160px] flex items-center gap-2">
                  <button
                    onClick={() => setCoverageSymbol(ds.symbol === coverageSymbol ? null : ds.symbol)}
                    className="text-foreground font-mono hover:text-qds-info transition-colors cursor-pointer"
                    title="查看数据覆盖"
                  >
                    {ds.symbol}
                  </button>
                </div>
                {showTypeCol && (
                  <div className="w-[100px]">
                    <span className="inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold bg-qds-accent-dim text-qds-info">
                      {CATEGORY_LABELS[ds.data_type] || ds.data_type}
                    </span>
                  </div>
                )}
                {!isTick && (
                  <div className="w-[80px]">
                    <span className="inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold bg-qds-info-dim text-qds-info">
                      {ds.interval}
                    </span>
                  </div>
                )}
                <div className="w-[100px] text-muted-foreground font-mono">
                  {formatNumber(ds.record_count)}
                </div>
                <div className="flex-1 text-muted-foreground">
                  {ds.start_date && ds.end_date ? `${ds.start_date} → ${ds.end_date}` : "—"}
                </div>
                <div className="w-[80px] text-muted-foreground">
                  {formatBytes(ds.size_bytes)}
                </div>
                <div className="w-[50px] flex justify-end">
                  <button
                    onClick={() => setDeleteEntry(ds)}
                    className="text-muted-foreground hover:text-destructive transition-colors p-1 rounded"
                    title="删除数据集"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </FadeIn>
      )}

      {/* Dialogs */}
      <FetchDialog open={fetchOpen} onClose={() => setFetchOpen(false)} onSuccess={loadCatalog} />
      <BatchFetchDialog open={batchOpen} onClose={() => setBatchOpen(false)} onSuccess={loadCatalog} />
      <DeleteDialog entry={deleteEntry} open={!!deleteEntry} onClose={() => setDeleteEntry(null)} onDeleted={loadCatalog} />
    </div>
  );
}
