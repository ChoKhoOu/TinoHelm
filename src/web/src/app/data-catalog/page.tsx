"use client";

import { Fragment, useState, useEffect, useMemo } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { InlineError, StatCard, SectionLabel } from "@/components/qds";
import { Badge } from "@/components/ui/badge";

import {
  CatalogEntry, SortKey, SortDir, formatBytes,
  FILTER_GROUPS, TYPE_BADGE_CLS, SOURCE_TYPE_LABELS,
} from "./types";
import { FilterTabs } from "./FilterTabs";
import { FetchDialog } from "./FetchDialog";
import { JobQueue } from "./JobQueue";
import { DeleteDialog } from "./DeleteDialog";

/* ── helpers ─────────────────────────────────────────────────────── */

function fmtRecords(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function staleness(dateStr: string | null): { label: string; cls: string; trend: "up" | "down" | "neutral" } {
  if (!dateStr) return { label: "—", cls: "", trend: "neutral" };
  const d = new Date(dateStr);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days <= 1) return { label: days === 0 ? "今天" : "昨天", cls: "text-qds-success", trend: "up" };
  if (days <= 7) return { label: `${days}天前`, cls: "", trend: "neutral" };
  if (days <= 30) return { label: `${days}天前`, cls: "text-qds-warning", trend: "neutral" };
  return { label: `${days}天前`, cls: "text-destructive", trend: "down" };
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function DataCatalogPage() {
  const [datasets, setDatasets] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialogs
  const [fetchOpen, setFetchOpen] = useState(false);
  const [jobRefresh, setJobRefresh] = useState(0);
  const [deleteEntry, setDeleteEntry] = useState<CatalogEntry | null>(null);

  // Filter
  const [activeGroup, setActiveGroup] = useState("all");
  const [activeSub, setActiveSub] = useState<string | null>(null);

  // Coverage
  const [covId, setCovId] = useState<number | null>(null);
  const [covRows, setCovRows] = useState<CatalogEntry[]>([]);

  // Sort
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  useEffect(() => { loadCatalog(); }, []);

  async function loadCatalog() {
    setLoading(true);
    try {
      const data = await apiGet<CatalogEntry[]>("/api/data/catalog");
      if (data) setDatasets(data);
    } finally {
      setLoading(false);
    }
  }

  const compact = useAction(() => apiPost("/api/data/compact"), { onSuccess: loadCatalog });
  const scan = useAction(() => apiPost("/api/data/scan"), { onSuccess: loadCatalog });

  function handleSort(col: SortKey) {
    if (sortKey === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(col); setSortDir("asc"); }
    setPage(1);
  }

  /* ── derived data ──────────────────────────────────────────────── */

  const typeCounts = useMemo(() => {
    const m: Record<string, number> = {};
    datasets.forEach((d) => { m[d.data_type] = (m[d.data_type] ?? 0) + 1; });
    return m;
  }, [datasets]);

  const subTypeCounts = useMemo(() => {
    const group = FILTER_GROUPS[activeGroup];
    if (!group || group.types === null) return {};
    const m: Record<string, number> = {};
    datasets.forEach((d) => {
      if (group.types!.includes(d.data_type)) {
        const st = d.source_type || d.data_type;
        m[st] = (m[st] ?? 0) + 1;
      }
    });
    return m;
  }, [datasets, activeGroup]);

  const filtered = useMemo(() => {
    const group = FILTER_GROUPS[activeGroup];
    if (!group || group.types === null) {
      return activeSub ? datasets.filter((d) => (d.source_type || d.data_type) === activeSub) : datasets;
    }
    const groupData = datasets.filter((d) => group.types!.includes(d.data_type));
    if (activeSub) return groupData.filter((d) => (d.source_type || d.data_type) === activeSub);
    return groupData;
  }, [datasets, activeGroup, activeSub]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let va: string | number, vb: string | number;
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

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const slice = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  // Stats
  const totalRecords = filtered.reduce((s, d) => s + (d.record_count ?? 0), 0);
  const totalSize = filtered.reduce((s, d) => s + d.size_bytes, 0);
  const latestDate = filtered.length > 0
    ? filtered.reduce((m, d) => (d.end_date > m ? d.end_date : m), filtered[0].end_date)
    : null;
  const stale = staleness(latestDate);

  // Dynamic columns
  const isKlineGrp = activeGroup === "klines";
  const isTradeGrp = activeGroup === "trades";
  const showType = activeGroup === "all" || (!activeSub && (isKlineGrp || isTradeGrp));
  const showInterval = activeGroup === "all" || isKlineGrp;
  const recLabel = isKlineGrp ? "Bars" : isTradeGrp ? "Ticks" : "记录数";
  const visColCount = 3 + (showType ? 1 : 0) + (showInterval ? 1 : 0);

  function toggleCoverage(id: number, symbol: string) {
    if (covId === id) { setCovId(null); return; }
    setCovId(id);
    setCovRows(datasets.filter((d) => d.symbol === symbol));
  }

  /* ── sort header ───────────────────────────────────────────────── */
  function TH({ col, label, right }: { col: SortKey; label: string; right?: boolean }) {
    const isSorted = sortKey === col;
    const icon = isSorted ? (sortDir === "asc" ? "▲" : "▼") : "▽";
    return (
      <th
        className={`px-[.65rem] py-2 font-normal text-[.58rem] tracking-[.06em] uppercase whitespace-nowrap cursor-pointer select-none border-b border-border transition-colors duration-150 hover:text-qds-t1 ${right ? "text-right" : "text-left"} ${isSorted ? "text-foreground" : "text-qds-t3"}`}
        onClick={() => handleSort(col)}
      >
        {label} <span className={`inline-block ml-[.2rem] text-[.5rem] ${isSorted ? "text-primary" : "text-qds-t3"}`}>{icon}</span>
      </th>
    );
  }

  /* ── pagination ────────────────────────────────────────────────── */
  function renderPager() {
    if (totalPages <= 1) return null;
    const start = (safePage - 1) * pageSize + 1;
    const end = Math.min(safePage * pageSize, sorted.length);
    const maxBtns = 7;
    let pS = Math.max(1, safePage - 3);
    const pE = Math.min(totalPages, pS + maxBtns - 1);
    if (pE - pS < maxBtns - 1) pS = Math.max(1, pE - maxBtns + 1);
    const btns: React.ReactNode[] = [];

    const pagerBtnCls = "w-[26px] h-6 inline-flex items-center justify-center border border-border rounded-[var(--rs)] bg-transparent text-muted-foreground cursor-pointer transition-all duration-150 font-mono text-[.65rem] hover:border-qds-border-hover hover:text-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-default disabled:pointer-events-none";
    const pagerBtnActiveCls = "w-[26px] h-6 inline-flex items-center justify-center border rounded-[var(--rs)] font-mono text-[.65rem] bg-qds-accent-dim border-primary text-primary";

    btns.push(<button key="f" className={pagerBtnCls} disabled={safePage <= 1} onClick={() => setPage(1)}>«</button>);
    btns.push(<button key="p" className={pagerBtnCls} disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>‹</button>);
    if (pS > 1) { btns.push(<button key="p1" className={pagerBtnCls} onClick={() => setPage(1)}>1</button>); if (pS > 2) btns.push(<span key="d1" className="w-4 text-center text-qds-t3 text-[.65rem]">…</span>); }
    for (let i = pS; i <= pE; i++) btns.push(<button key={i} className={i === safePage ? pagerBtnActiveCls : pagerBtnCls} onClick={() => setPage(i)}>{i}</button>);
    if (pE < totalPages) { if (pE < totalPages - 1) btns.push(<span key="d2" className="w-4 text-center text-qds-t3 text-[.65rem]">…</span>); btns.push(<button key="pl" className={pagerBtnCls} onClick={() => setPage(totalPages)}>{totalPages}</button>); }
    btns.push(<button key="n" className={pagerBtnCls} disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>›</button>);
    btns.push(<button key="l" className={pagerBtnCls} disabled={safePage >= totalPages} onClick={() => setPage(totalPages)}>»</button>);
    return (
      <div className="flex items-center justify-between py-[.55rem] font-mono text-[.68rem] text-muted-foreground mt-2">
        <span>{start}–{end} / {sorted.length}</span>
        <div className="flex items-center gap-0.5">{btns}</div>
      </div>
    );
  }

  /* ── render ────────────────────────────────────────────────────── */
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto" style={{ padding: "1.25rem 2rem 4rem" }}>
        {/* 1. Page header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
          <div>
            <div className="text-[1.1rem] font-bold mb-[.2rem]">数据目录</div>
            <div className="font-mono text-[.72rem] text-muted-foreground">{"// 本地 ParquetDataCatalog"}</div>
          </div>
          <div style={{ display: "flex", gap: ".4rem", alignItems: "flex-start" }}>
            <button className="btn btn-p" onClick={() => setFetchOpen(true)}>↓ 拉取数据</button>
            <div>
              <button
                className={`btn ${compact.state === 'error' ? 'btn-d' : compact.state === 'success' ? 'btn-p' : 'btn-o'}`}
                onClick={compact.execute}
                disabled={compact.state === 'loading'}
              >
                {compact.state === 'loading' ? '压缩中...' : compact.state === 'success' ? '✓ 压缩完成' : compact.state === 'error' ? '✕ 失败' : '⊕ 压缩'}
              </button>
              {compact.state === 'error' && compact.error && <InlineError>{compact.error}</InlineError>}
            </div>
            <div>
              <button
                className={`btn ${scan.state === 'error' ? 'btn-d' : scan.state === 'success' ? 'btn-p' : 'btn-o'}`}
                onClick={scan.execute}
                disabled={scan.state === 'loading'}
              >
                {scan.state === 'loading' ? '扫描中...' : scan.state === 'success' ? '✓ 扫描完成' : scan.state === 'error' ? '✕ 失败' : '↻ 扫描'}
              </button>
              {scan.state === 'error' && scan.error && <InlineError>{scan.error}</InlineError>}
            </div>
          </div>
        </div>

        {/* 2. Filter tabs */}
        <FilterTabs
          totalCount={datasets.length}
          typeCounts={typeCounts}
          subTypeCounts={subTypeCounts}
          activeGroup={activeGroup}
          activeSub={activeSub}
          onGroupChange={(g) => { setActiveGroup(g); setPage(1); }}
          onSubChange={(s) => { setActiveSub(s); setPage(1); }}
        />

        {/* 3. Stats cards */}
        <div className="grid grid-cols-4 gap-4" style={{ marginBottom: "1.5rem" }}>
          <StatCard
            label="数据集"
            value={String(filtered.length)}
          />
          <StatCard
            label="总记录数"
            value={totalRecords >= 1_000_000 ? `${(totalRecords / 1_000_000).toFixed(1)}M` : totalRecords > 0 ? totalRecords.toLocaleString() : "—"}
          />
          <StatCard
            label="最新数据"
            value={latestDate ?? "—"}
            sub={latestDate ? stale.label : undefined}
            trend={stale.trend}
          />
          <StatCard
            label="磁盘占用"
            value={formatBytes(totalSize)}
          />
        </div>

        {/* 4. Queue */}
        <JobQueue refreshTrigger={jobRefresh} onJobComplete={loadCatalog} />

        {/* 5. Data table header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".65rem" }}>
          <SectionLabel>数据集</SectionLabel>
          <div className="font-mono text-[.65rem]">
            <select
              className="qds-select"
              style={{ padding: ".22rem .45rem", paddingRight: "1.6rem" }}
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            >
              <option value={10}>10 条/页</option>
              <option value={20}>20 条/页</option>
              <option value={50}>50 条/页</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="bg-card border border-border rounded-lg" style={{ padding: "2rem", textAlign: "center" }}>
            <div className="text-qds-t3 text-[.75rem]">加载中...</div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-card border border-border rounded-lg overflow-hidden">
            <div className="flex flex-col items-center justify-center py-16 px-8 text-center">
              <div className="text-[2rem] mb-4 text-qds-t3">⊞</div>
              <div className="text-[.75rem] text-muted-foreground">{activeGroup !== "all" ? "该类型暂无数据集" : "暂无数据集，请先拉取数据"}</div>
            </div>
          </div>
        ) : (
          <>
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <table className="w-full border-collapse font-mono text-[.72rem]">
                <thead>
                  <tr>
                    <TH col="symbol" label="品种" />
                    {showType && <TH col="data_type" label="类型" />}
                    {showInterval && <TH col="interval" label="周期" />}
                    <TH col="record_count" label={recLabel} right />
                    <TH col="start_date" label="日期范围" />
                    <TH col="size_bytes" label="文件大小" right />
                    <th style={{ width: 40 }} />
                  </tr>
                </thead>
                <tbody>
                  {slice.map((ds) => {
                    const typeCls = TYPE_BADGE_CLS[ds.source_type || ds.data_type] ?? "bg-qds-info-dim text-qds-info";
                    const typeLabel = SOURCE_TYPE_LABELS[ds.source_type || ds.data_type] ?? (ds.source_type || ds.data_type);
                    return (
                      <Fragment key={ds.id}>
                        <tr className="border-b border-border last:border-b-0 hover:bg-secondary transition-colors duration-150">
                          <td className="px-[.65rem] py-2 whitespace-nowrap">
                            <span
                              className="text-foreground cursor-pointer transition-colors duration-150 border-b border-dashed border-transparent hover:text-primary hover:border-primary"
                              onClick={() => toggleCoverage(ds.id, ds.symbol)}
                            >{ds.symbol}</span>
                          </td>
                          {showType && <td className="px-[.65rem] py-2 whitespace-nowrap"><Badge className={`text-[.58rem] px-[.35rem] py-[.1rem] rounded-[3px] font-mono ${typeCls}`}>{typeLabel}</Badge></td>}
                          {showInterval && <td className="px-[.65rem] py-2 whitespace-nowrap text-muted-foreground">{ds.interval}</td>}
                          <td className="px-[.65rem] py-2 whitespace-nowrap text-right">{fmtRecords(ds.record_count)}</td>
                          <td className="px-[.65rem] py-2 whitespace-nowrap text-muted-foreground">{ds.start_date && ds.end_date ? `${ds.start_date} → ${ds.end_date}` : "—"}</td>
                          <td className="px-[.65rem] py-2 whitespace-nowrap text-right">{formatBytes(ds.size_bytes)}</td>
                          <td className="px-[.65rem] py-2 whitespace-nowrap">
                            <button
                              className="font-mono text-[.58rem] px-[.35rem] py-[.15rem] rounded-[3px] border border-transparent bg-transparent text-qds-t3 cursor-pointer transition-all duration-150 hover:border-destructive hover:text-destructive hover:bg-qds-danger-dim"
                              onClick={() => setDeleteEntry(ds)}
                            >删除</button>
                          </td>
                        </tr>
                        <tr><td colSpan={visColCount} style={{ padding: 0 }}>
                          <div
                            className="overflow-hidden transition-[max-height] duration-[400ms] ease-out bg-input"
                            style={{ maxHeight: covId === ds.id ? "300px" : "0" }}
                          >
                            {covId === ds.id && (
                              <div className="p-[.75rem_.85rem]">
                                <div className="font-mono text-[.72rem] font-semibold mb-2">{ds.symbol} — 数据覆盖</div>
                                {covRows.map((r, i) => (
                                  <div key={`${r.id}-${i}`} className="grid items-center gap-2 py-[.3rem] font-mono text-[.68rem] border-b border-border last:border-b-0" style={{ gridTemplateColumns: "100px 80px 1fr 60px" }}>
                                    <Badge className={`text-[.58rem] px-[.35rem] py-[.1rem] rounded-[3px] font-mono ${TYPE_BADGE_CLS[r.source_type || r.data_type] ?? "bg-qds-info-dim text-qds-info"}`}>{SOURCE_TYPE_LABELS[r.source_type || r.data_type] ?? (r.source_type || r.data_type)}</Badge>
                                    <span className="text-muted-foreground">{r.interval}</span>
                                    <div className="h-1.5 bg-secondary rounded-sm overflow-hidden"><div className="h-full rounded-sm bg-qds-success" style={{ width: "60%" }} /></div>
                                    <span className="text-muted-foreground">{r.start_date}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </td></tr>
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {renderPager()}
          </>
        )}
      </div>

      {/* Dialogs */}
      <FetchDialog open={fetchOpen} onClose={() => setFetchOpen(false)} onSuccess={() => { loadCatalog(); setJobRefresh((n) => n + 1); }} />
      <DeleteDialog entry={deleteEntry} open={!!deleteEntry} onClose={() => setDeleteEntry(null)} onDeleted={loadCatalog} />
    </div>
  );
}
