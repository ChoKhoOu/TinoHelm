"use client";

import { useState, useEffect, useMemo } from "react";
import Papa from "papaparse";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  getFilteredRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
  type ColumnDef,
} from "@tanstack/react-table";
import { ChevronUp, ChevronDown, ChevronsUpDown, Download } from "lucide-react";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type CsvRow = Record<string, string>;

const SOURCES = [
  { key: "positions", label: "持仓" },
  { key: "fills", label: "成交" },
  { key: "orders", label: "订单" },
  { key: "account", label: "账户" },
  { key: "order_fills", label: "订单成交" },
] as const;

type SourceKey = (typeof SOURCES)[number]["key"];

const PAGE_SIZE_OPTIONS = ["25", "50", "100"];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface ReportsTabProps {
  runId: string;
}

export function ReportsTab({ runId }: ReportsTabProps) {
  const [activeSource, setActiveSource] = useState<SourceKey>("positions");
  const [csvData, setCsvData] = useState<CsvRow[]>([]);
  const [headers, setHeaders] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [globalFilter, setGlobalFilter] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [pageSize, setPageSize] = useState(25);

  // Fetch and parse CSV when source or runId changes
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCsvData([]);
    setHeaders([]);
    setGlobalFilter("");
    setSorting([]);

    const url = `${API_BASE}/api/backtest/${runId}/artifacts/${activeSource}_report.csv`;
    fetch(url, {
      headers: {
        ...(process.env.NEXT_PUBLIC_API_KEY ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY } : {}),
      },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (cancelled) return;
        const result = Papa.parse<CsvRow>(text, {
          header: true,
          skipEmptyLines: true,
          transformHeader: (h) => h.trim(),
        });
        setHeaders(result.meta.fields ?? []);
        setCsvData(result.data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [runId, activeSource]);

  // Build columns dynamically from CSV headers
  const columns = useMemo<ColumnDef<CsvRow>[]>(() => {
    return headers.map((h) => ({
      id: h,
      accessorKey: h,
      header: h,
      cell: (info: { getValue: () => unknown }) => {
        const val = info.getValue() as string;
        if (!val) return <span className="text-[var(--text-muted)]">—</span>;
        // Color numeric pnl/profit/loss columns
        const lower = h.toLowerCase();
        if (lower.includes("pnl") || lower.includes("profit") || lower.includes("loss")) {
          const num = parseFloat(val);
          if (!isNaN(num)) {
            return (
              <span className={num >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>
                {num >= 0 ? "+" : ""}{val}
              </span>
            );
          }
        }
        return <span>{val}</span>;
      },
      enableSorting: true,
      enableGlobalFilter: true,
    }));
  }, [headers]);

  const table = useReactTable({
    data: csvData,
    columns,
    state: { sorting, globalFilter, pagination: { pageIndex: 0, pageSize } },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: "includesString",
    manualPagination: false,
  });

  // Sync pageSize
  useEffect(() => {
    table.setPageSize(pageSize);
  }, [pageSize, table]);

  const handleExport = () => {
    window.open(
      `${API_BASE}/api/backtest/${runId}/artifacts/${activeSource}_report.csv`,
      "_blank",
      "noopener,noreferrer"
    );
  };

  const filteredCount = table.getFilteredRowModel().rows.length;
  const { pageIndex } = table.getState().pagination;
  const from = filteredCount === 0 ? 0 : pageIndex * pageSize + 1;
  const to = Math.min(from + pageSize - 1, filteredCount);

  return (
    <div className="flex flex-col h-full">
      {/* Source selector + search + export */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--border-gray)] bg-[var(--bg-card)] shrink-0 flex-wrap">
        {/* Pills */}
        <div className="flex items-center gap-1">
          {SOURCES.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setActiveSource(key)}
              className={`px-3 py-1 rounded-full text-[11px] font-semibold transition-colors ${
                activeSource === key
                  ? "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 min-w-0" />

        {/* Search */}
        <Input
          placeholder="搜索..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="w-44 h-7 text-xs"
        />

        {/* Export */}
        <button
          onClick={handleExport}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] text-[var(--text-secondary)] hover:text-[var(--accent-blue)] hover:bg-[var(--accent-blue-20)] border border-[var(--border-gray)] hover:border-[var(--accent-blue)] transition-colors"
        >
          <Download className="w-3 h-3" />
          导出 CSV
        </button>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex flex-col gap-3 p-4 flex-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full rounded" />
          ))}
        </div>
      ) : error ? (
        <div className="flex items-center justify-center flex-1">
          <span className="text-xs text-[var(--accent-red)]">加载失败: {error}</span>
        </div>
      ) : csvData.length === 0 ? (
        <div className="flex items-center justify-center flex-1">
          <span className="text-xs text-[var(--text-muted)]">暂无数据</span>
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="flex-1 min-h-0 overflow-auto">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-[var(--bg-card)]">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="border-b border-[var(--border-gray)]">
                    {hg.headers.map((header) => {
                      const canSort = header.column.getCanSort();
                      const sorted = header.column.getIsSorted();
                      return (
                        <th
                          key={header.id}
                          className={`px-3 py-2 text-left font-semibold text-[var(--text-muted)] tracking-[0.4px] uppercase whitespace-nowrap select-none ${
                            canSort ? "cursor-pointer hover:text-[var(--text-secondary)]" : ""
                          }`}
                          onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                        >
                          <div className="flex items-center gap-1">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {canSort && (
                              <span className="text-[var(--text-muted)]">
                                {sorted === "asc" ? (
                                  <ChevronUp className="w-3 h-3" />
                                ) : sorted === "desc" ? (
                                  <ChevronDown className="w-3 h-3" />
                                ) : (
                                  <ChevronsUpDown className="w-3 h-3 opacity-40" />
                                )}
                              </span>
                            )}
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row, i) => (
                  <tr
                    key={row.id}
                    className={`border-b border-[var(--border-gray)]/30 hover:bg-[var(--bg-subtle)]/50 transition-colors ${
                      i % 2 === 0 ? "" : "bg-[var(--bg-page)]/30"
                    }`}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-1.5 text-[var(--text-secondary)] whitespace-nowrap">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-4 py-2 border-t border-[var(--border-gray)] bg-[var(--bg-card)] shrink-0">
            <span className="text-[11px] text-[var(--text-muted)]">
              {from}–{to} / {filteredCount} 条
              {globalFilter && ` (筛选自 ${csvData.length} 条)`}
            </span>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-[var(--text-muted)]">每页</span>
                <Select
                  value={String(pageSize)}
                  options={PAGE_SIZE_OPTIONS}
                  onValueChange={(v) => setPageSize(Number(v))}
                  className="w-16 h-7 text-xs"
                />
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => table.previousPage()}
                  disabled={!table.getCanPreviousPage()}
                  className="px-2 py-1 rounded text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  上一页
                </button>
                <span className="text-[11px] text-[var(--text-muted)] px-1">
                  {pageIndex + 1} / {table.getPageCount() || 1}
                </span>
                <button
                  onClick={() => table.nextPage()}
                  disabled={!table.getCanNextPage()}
                  className="px-2 py-1 rounded text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  下一页
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
