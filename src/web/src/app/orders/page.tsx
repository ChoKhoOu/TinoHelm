"use client";

import { useState, useEffect, useCallback } from "react";
import { Search, ChevronLeft, ChevronRight } from "lucide-react";
import { apiGet } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { FadeIn } from "@/components/motion/FadeIn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

/* ── Types ──────────────────────────────────────────────────── */

type NodeType = "all" | "sandbox" | "live";
type OrderStatus = "ALL" | "ACCEPTED" | "FILLED" | "CANCELED" | "REJECTED" | "EXPIRED";

interface Order {
  id: number;
  node_type: string;
  order_id: string;
  instrument_id: string;
  side: "BUY" | "SELL";
  order_type: string;
  quantity: string;
  price: string | null;
  status: string;
  created_at: string | null;
}

/* ── Constants ──────────────────────────────────────────────── */

const NODE_TYPE_OPTIONS: { value: NodeType; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "sandbox", label: "Sandbox" },
  { value: "live", label: "Live" },
];

const STATUS_OPTIONS: OrderStatus[] = ["ALL", "ACCEPTED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"];

const STATUS_ZH: Record<string, string> = {
  ALL: "全部",
  ACCEPTED: "已接受",
  FILLED: "已成交",
  CANCELED: "已撤单",
  REJECTED: "已拒绝",
  EXPIRED: "已过期",
};

const STATUS_BADGE: Record<string, string> = {
  ACCEPTED: "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]",
  FILLED: "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  CANCELED: "bg-popover text-muted-foreground",
  REJECTED: "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  EXPIRED: "bg-[var(--accent-amber-20)] text-[var(--accent-amber)]",
  PARTIALLY_FILLED: "bg-[var(--accent-purple-20)] text-[var(--accent-purple)]",
};

const PAGE_SIZE_OPTIONS = [25, 50, 100];

const COL_HEADER = "text-[10px] font-semibold tracking-[0.5px] text-muted-foreground font-mono uppercase";

/* ── Page ───────────────────────────────────────────────────── */

export default function OrdersPage() {
  const [nodeType, setNodeType] = useState<NodeType>("all");
  const [statusFilter, setStatusFilter] = useState<OrderStatus>("ALL");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(0);
  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* debounce search */
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  /* reset page on filter change */
  useEffect(() => { setPage(0); }, [nodeType, statusFilter, debouncedSearch, pageSize]);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        limit: String(pageSize),
        offset: String(page * pageSize),
      };
      if (nodeType !== "all") params.node_type = nodeType;
      if (statusFilter !== "ALL") params.status = statusFilter;
      if (debouncedSearch) params.instrument = debouncedSearch;

      const data = await apiGet<{ items: Order[]; total: number } | Order[]>("/api/orders", params);
      if (!data) { setOrders([]); setTotal(0); return; }

      /* support both {items, total} and bare array */
      if (Array.isArray(data)) {
        setOrders(data as Order[]);
        setTotal((data as Order[]).length);
      } else {
        setOrders((data as { items: Order[]; total: number }).items ?? []);
        setTotal((data as { items: Order[]; total: number }).total ?? 0);
      }
    } catch {
      setError("加载订单失败");
    } finally {
      setLoading(false);
    }
  }, [nodeType, statusFilter, debouncedSearch, pageSize, page]);

  useEffect(() => { loadOrders(); }, [loadOrders]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));


  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Header */}
      <FadeIn direction="down" duration={0.25}>
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-[26px] font-bold tracking-tight text-foreground">
              订单记录
            </h1>
            <span className="text-[11px] font-mono text-muted-foreground">
              // 全部交易所订单 — 可筛选
            </span>
          </div>
        </div>
      </FadeIn>

      {/* Filters */}
      <FadeIn direction="up" duration={0.25} delay={0.05}>
        <div className="flex flex-wrap items-center gap-2">
          {/* Node type */}
          <div className="flex items-center rounded-lg bg-card border border-border overflow-hidden">
            {NODE_TYPE_OPTIONS.map((opt) => (
              <Button
                key={opt.value}
                variant="ghost"
                size="sm"
                onClick={() => setNodeType(opt.value)}
                className={`px-4 py-[7px] text-[11px] font-semibold transition-colors duration-150 rounded-none ${
                  nodeType === opt.value
                    ? "bg-popover text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {opt.label}
              </Button>
            ))}
          </div>

          {/* Status multi-select */}
          <div className="flex items-center gap-1 flex-wrap">
            {STATUS_OPTIONS.map((s) => (
              <Button
                key={s}
                variant="ghost"
                size="sm"
                onClick={() => setStatusFilter(s)}
                className={`rounded-lg px-3 py-[6px] text-[10px] font-semibold tracking-wide transition-colors duration-150 ${
                  statusFilter === s
                    ? "bg-[#1a3a5c] text-[#4C9EEB] border border-[#4C9EEB]/40"
                    : "bg-card border border-border text-muted-foreground hover:border-input"
                }`}
              >
                {STATUS_ZH[s]}
              </Button>
            ))}
          </div>

          {/* Search */}
          <div className="ml-auto flex items-center gap-2 rounded-lg bg-card border border-border px-3 py-[7px] focus-within:border-[#4C9EEB] transition-colors duration-150">
            <Search className="w-3.5 h-3.5 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索交易对..."
              className="w-[160px] h-auto border-0 bg-transparent p-0 text-[11px] font-mono text-foreground placeholder:text-muted-foreground outline-none focus-visible:ring-0 focus-visible:border-0"
            />
          </div>

          {/* Page size */}
          <div className="flex items-center gap-1 rounded-lg bg-card border border-border overflow-hidden">
            {PAGE_SIZE_OPTIONS.map((n) => (
              <Button
                key={n}
                variant="ghost"
                size="sm"
                onClick={() => setPageSize(n)}
                className={`px-3 py-[7px] text-[10px] font-semibold font-mono transition-colors duration-150 rounded-none ${
                  pageSize === n
                    ? "bg-popover text-foreground"
                    : "text-muted-foreground"
                }`}
              >
                {n}
              </Button>
            ))}
          </div>
        </div>
      </FadeIn>

      {/* Table */}
      <FadeIn direction="up" duration={0.25} delay={0.1} className="flex-1 flex flex-col min-h-0">
        <div className="flex-1 flex flex-col rounded-xl bg-card border border-border overflow-hidden min-h-0">
          {/* Header row */}
          <div className="flex items-center px-5 py-3 border-b border-border shrink-0">
            <span className={`w-[140px] ${COL_HEADER}`}>时间</span>
            <span className={`w-[180px] ${COL_HEADER}`}>交易对</span>
            <span className={`w-[60px] ${COL_HEADER}`}>方向</span>
            <span className={`w-[100px] ${COL_HEADER}`}>类型</span>
            <span className={`w-[100px] ${COL_HEADER}`}>数量</span>
            <span className={`w-[110px] ${COL_HEADER}`}>价格</span>
            <span className={`w-[120px] ${COL_HEADER}`}>状态</span>
            <span className={`flex-1 ${COL_HEADER}`}>节点</span>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto">
            {error ? (
              <div className="flex items-center justify-center h-32">
                <span className="font-mono text-[12px] text-[#EF5350]">{error}</span>
              </div>
            ) : loading ? (
              <div className="flex flex-col divide-y divide-border">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="flex items-center px-5 py-[11px] gap-2 animate-pulse">
                    <div className="w-[140px] h-3 rounded bg-border" />
                    <div className="w-[180px] h-3 rounded bg-border" />
                    <div className="w-[60px] h-3 rounded bg-border" />
                    <div className="w-[100px] h-3 rounded bg-border" />
                    <div className="w-[100px] h-3 rounded bg-border" />
                    <div className="w-[110px] h-3 rounded bg-border" />
                    <div className="w-[120px] h-5 rounded bg-border" />
                    <div className="flex-1 h-3 rounded bg-border" />
                  </div>
                ))}
              </div>
            ) : orders.length === 0 ? (
              <div className="flex items-center justify-center h-32">
                <span className="font-mono text-[11px] text-muted-foreground">没有匹配的订单</span>
              </div>
            ) : (
              orders.map((o, i) => (
                <div
                  key={o.id ?? i}
                  className="flex items-center px-5 py-[11px] text-[11px] font-mono border-b border-border last:border-b-0 hover:bg-popover transition-colors duration-100"
                >
                  <span className="w-[140px] text-muted-foreground">{formatDateTime(o.created_at)}</span>
                  <span className="w-[180px] text-foreground font-semibold">{o.instrument_id}</span>
                  <span
                    className={`w-[60px] font-bold ${
                      o.side === "BUY" ? "text-[#26D97F]" : "text-[#EF5350]"
                    }`}
                  >
                    {o.side === "BUY" ? "买入" : "卖出"}
                  </span>
                  <span className="w-[100px] text-muted-foreground">{o.order_type}</span>
                  <span className="w-[100px] text-foreground">{o.quantity}</span>
                  <span className="w-[110px] text-foreground">{o.price ?? "市价"}</span>
                  <span className="w-[120px]">
                    <Badge
                      className={`text-[9px] font-bold uppercase ${
                        STATUS_BADGE[o.status] ?? "bg-popover text-muted-foreground"
                      }`}
                    >
                      {STATUS_ZH[o.status] ?? o.status}
                    </Badge>
                  </span>
                  <span className="flex-1 text-muted-foreground capitalize">{o.node_type}</span>
                </div>
              ))
            )}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-5 py-3 border-t border-border shrink-0">
            <span className="text-[10px] font-mono text-muted-foreground">
              共 {total} 条 · 第 {page + 1} / {totalPages} 页
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="border-border text-muted-foreground disabled:opacity-30 hover:border-input transition-colors duration-150"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
              </Button>
              {Array.from({ length: Math.min(5, totalPages) }).map((_, i) => {
                const start = Math.max(0, Math.min(page - 2, totalPages - 5));
                const p = start + i;
                return (
                  <Button
                    key={p}
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setPage(p)}
                    className={`text-[10px] font-mono font-semibold transition-colors duration-150 ${
                      p === page
                        ? "bg-[#4C9EEB] text-white hover:bg-[#4C9EEB] hover:text-white"
                        : "border border-border text-muted-foreground hover:border-input"
                    }`}
                  >
                    {p + 1}
                  </Button>
                );
              })}
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="border-border text-muted-foreground disabled:opacity-30 hover:border-input transition-colors duration-150"
              >
                <ChevronRight className="w-3.5 h-3.5" />
              </Button>
            </div>
          </div>
        </div>
      </FadeIn>
    </div>
  );
}
