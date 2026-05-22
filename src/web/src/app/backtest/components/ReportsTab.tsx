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
  type SortingState,
  type ColumnDef,
} from "@tanstack/react-table";
import { ChevronUp, ChevronDown, ChevronsUpDown, Download } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { API_BASE } from "@/lib/api";

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
  const [pageIndex, setPageIndex] = useState(0);

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
    setPageIndex(0);

    const url = `${API_BASE}/api/backtest/${runId}/artifacts/${activeSource}_report.csv`;
    fetch(url)
      .then((r) => {
        if (r.status === 404) return "";
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (cancelled || !text) return;
        const result = Papa.parse<CsvRow>(text, {
          header: true,
          skipEmptyLines: true,
          transformHeader: (h) => h.trim() || "datetime",
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
        if (!val) return <span className="text-muted-foreground">—</span>;
        // Color numeric pnl/profit/loss columns
        const lower = h.toLowerCase();
        if (lower.includes("pnl") || lower.includes("profit") || lower.includes("loss")) {
          const num = parseFloat(val);
          if (!isNaN(num)) {
            return (
              <span className={num >= 0 ? "text-qds-success" : "text-destructive"}>
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
    state: { sorting, globalFilter, pagination: { pageIndex, pageSize } },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onPaginationChange: (updater) => {
      const next = typeof updater === "function" ? updater({ pageIndex, pageSize }) : updater;
      setPageIndex(next.pageIndex);
      setPageSize(next.pageSize);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: "includesString",
    manualPagination: false,
  });

  const handleExport = () => {
    window.open(
      `${API_BASE}/api/backtest/${runId}/artifacts/${activeSource}_report.csv`,
      "_blank",
      "noopener,noreferrer"
    );
  };

  const filteredCount = table.getFilteredRowModel().rows.length;
  const from = filteredCount === 0 ? 0 : pageIndex * pageSize + 1;
  const to = Math.min(from + pageSize - 1, filteredCount);

  return (
    <div className="flex flex-col h-full">
      {/* Source selector + search + export */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b bg-card shrink-0 flex-wrap">
        {/* Pills */}
        <div className="flex items-center gap-1">
          {SOURCES.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setActiveSource(key)}
              className={`px-3 py-1 rounded-full text-[11px] font-semibold transition-colors ${
                activeSource === key
                  ? "bg-qds-info-dim text-primary"
                  : "text-muted-foreground hover:text-muted-foreground"
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
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] text-muted-foreground hover:text-primary hover:bg-qds-info-dim border hover:border-primary transition-colors"
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
          <span className="text-xs text-destructive">加载失败: {error}</span>
        </div>
      ) : csvData.length === 0 ? (
        <div className="flex items-center justify-center flex-1">
          <span className="text-xs text-muted-foreground">暂无数据</span>
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="flex-1 min-h-0 overflow-auto">
            <Table className="w-full text-xs border-collapse">
              <TableHeader className="sticky top-0 z-10 bg-card">
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id} className="border-b">
                    {hg.headers.map((header) => {
                      const canSort = header.column.getCanSort();
                      const sorted = header.column.getIsSorted();
                      return (
                        <TableHead
                          key={header.id}
                          className={`px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase whitespace-nowrap select-none ${
                            canSort ? "cursor-pointer hover:text-muted-foreground" : ""
                          }`}
                          onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                        >
                          <div className="flex items-center gap-1">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {canSort && (
                              <span className="text-muted-foreground">
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
                        </TableHead>
                      );
                    })}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.map((row, i) => (
                  <TableRow
                    key={row.id}
                    className={`border-b hover:bg-secondary transition-colors ${
                      i % 2 === 0 ? "" : "bg-background"
                    }`}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id} className="px-3 py-1.5 text-muted-foreground whitespace-nowrap">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-4 py-2 border-t bg-card shrink-0">
            <span className="text-[11px] text-muted-foreground">
              {from}–{to} / {filteredCount} 条
              {globalFilter && ` (筛选自 ${csvData.length} 条)`}
            </span>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">每页</span>
                <Select value={String(pageSize)} onValueChange={(v: string | null) => v && setPageSize(Number(v))}>
                  <SelectTrigger className="w-16 h-7 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((opt) => (
                      <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => table.previousPage()}
                  disabled={!table.getCanPreviousPage()}
                  className="px-2 py-1 rounded text-[11px] text-muted-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  上一页
                </button>
                <span className="text-[11px] text-muted-foreground px-1">
                  {pageIndex + 1} / {table.getPageCount() || 1}
                </span>
                <button
                  onClick={() => table.nextPage()}
                  disabled={!table.getCanNextPage()}
                  className="px-2 py-1 rounded text-[11px] text-muted-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
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
