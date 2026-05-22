"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Search } from "lucide-react";
import { apiGet } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { FadeIn } from "@/components/motion/FadeIn";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";
import { EmptyState } from "@/components/EmptyState";
import { Pagination } from "@/components/Pagination";
import { ConfirmModal } from "@/components/ConfirmModal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* ── Types ──────────────────────────────────────────────── */

type NodeType = "all" | "sandbox" | "live";
interface Order {
  id: number;
  node_type: string;
  order_id: string;
  instrument_id: string;
  strategy_name?: string;
  side: "BUY" | "SELL";
  order_type: string;
  quantity: string;
  price: string | null;
  filled_qty?: string;
  fee?: string;
  latency_ms?: number;
  cancel_reason?: string;
  status: string;
  created_at: string | null;
}

/* ── Constants ──────────────────────────────────────────── */

const NODE_TYPE_OPTIONS: { value: NodeType; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sandbox", label: "Sandbox" },
  { value: "live", label: "Live" },
];


const TYPE_VARIANT: Record<string, string> = {
  LIMIT: "bg-secondary text-qds-t1",
  MARKET: "bg-qds-info-dim text-qds-info",
  STOP: "bg-qds-warning-dim text-qds-warning",
  "POST-ONLY": "bg-qds-accent-dim text-primary",
  "POST_ONLY": "bg-qds-accent-dim text-primary",
};

type TabKey = "open" | "filled" | "cancelled";

const TAB_STATUS_MAP: Record<TabKey, string[]> = {
  open: ["ACCEPTED", "PARTIALLY_FILLED"],
  filled: ["FILLED"],
  cancelled: ["CANCELED", "REJECTED", "EXPIRED"],
};

/* ── Mock Data ──────────────────────────────────────────── */

function mockOrders(): Order[] {
  const syms = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP"];
  const strats = ["MM-perp", "Stat-arb", "Funding"];
  const types = ["LIMIT", "POST_ONLY", "STOP", "MARKET"];
  const reasons = ["User cancel", "Expired", "Self-trade prevention", "IOC unfilled"];
  const orders: Order[] = [];

  // Open orders
  for (let i = 0; i < 12; i++) {
    const sym = syms[i % 3];
    const base = sym.startsWith("BTC") ? 67000 : sym.startsWith("ETH") ? 3400 : 170;
    orders.push({
      id: i + 1,
      node_type: i % 3 === 0 ? "live" : "sandbox",
      order_id: `O-${1000 + i}`,
      instrument_id: sym,
      strategy_name: strats[i % 3],
      side: i % 2 === 0 ? "BUY" : "SELL",
      order_type: types[i % 4],
      quantity: (Math.random() * 2 + 0.1).toFixed(3),
      price: (base + Math.random() * base * 0.01).toFixed(2),
      filled_qty: (Math.random() * 0.8).toFixed(3),
      status: "ACCEPTED",
      created_at: new Date(Date.now() - i * 120000).toISOString(),
    });
  }

  // Filled orders
  for (let i = 0; i < 30; i++) {
    const sym = syms[i % 3];
    const base = sym.startsWith("BTC") ? 67000 : sym.startsWith("ETH") ? 3400 : 170;
    orders.push({
      id: 100 + i,
      node_type: i % 2 === 0 ? "live" : "sandbox",
      order_id: `O-${2000 + i}`,
      instrument_id: sym,
      strategy_name: strats[i % 3],
      side: i % 2 === 0 ? "BUY" : "SELL",
      order_type: types[i % 4],
      quantity: (Math.random() * 2 + 0.1).toFixed(3),
      price: (base + Math.random() * base * 0.01).toFixed(2),
      fee: (Math.random() * 5).toFixed(2),
      latency_ms: Math.floor(Math.random() * 50) + 2,
      status: "FILLED",
      created_at: new Date(Date.now() - i * 240000).toISOString(),
    });
  }

  // Cancelled orders
  for (let i = 0; i < 8; i++) {
    const sym = syms[i % 3];
    const base = sym.startsWith("BTC") ? 67000 : sym.startsWith("ETH") ? 3400 : 170;
    orders.push({
      id: 200 + i,
      node_type: "sandbox",
      order_id: `O-${3000 + i}`,
      instrument_id: sym,
      strategy_name: strats[i % 3],
      side: i % 2 === 0 ? "BUY" : "SELL",
      order_type: types[i % 4],
      quantity: (Math.random() * 2).toFixed(3),
      price: (base + Math.random() * base * 0.01).toFixed(2),
      cancel_reason: reasons[i % 4],
      status: "CANCELED",
      created_at: new Date(Date.now() - i * 720000).toISOString(),
    });
  }

  return orders;
}

/* ── Helpers ─────────────────────────────────────────────── */

function filledPercent(o: Order): number {
  if (!o.filled_qty || !o.quantity) return 0;
  const f = parseFloat(o.filled_qty);
  const q = parseFloat(o.quantity);
  if (q === 0) return 0;
  return Math.min(100, Math.round((f / q) * 100));
}

/* ── Page ────────────────────────────────────────────────── */

export default function OrdersPage() {
  const [nodeType, setNodeType] = useState<NodeType>("all");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [activeTab, setActiveTab] = useState<TabKey>("open");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<Order | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  /* debounce search */
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => { setPage(1); }, [activeTab, nodeType, debouncedSearch, pageSize]);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (nodeType !== "all") params.node_type = nodeType;
      if (debouncedSearch) params.instrument = debouncedSearch;

      const data = await apiGet<{ items: Order[]; total: number } | Order[]>("/api/orders", params);
      if (!data) {
        setOrders(mockOrders());
        return;
      }
      if (Array.isArray(data)) {
        setOrders(data.length > 0 ? data : mockOrders());
      } else {
        setOrders(data.items?.length > 0 ? data.items : mockOrders());
      }
    } catch {
      setOrders(mockOrders());
    } finally {
      setLoading(false);
    }
  }, [nodeType, debouncedSearch]);

  useEffect(() => { loadOrders(); }, [loadOrders]);

  /* Filter by tab */
  const filtered = useMemo(() => {
    const statuses = TAB_STATUS_MAP[activeTab];
    return orders.filter((o) => statuses.includes(o.status));
  }, [orders, activeTab]);

  const tabCounts: Record<TabKey, number> = useMemo(() => ({
    open: orders.filter((o) => ["ACCEPTED", "PARTIALLY_FILLED"].includes(o.status)).length,
    filled: orders.filter((o) => o.status === "FILLED").length,
    cancelled: orders.filter((o) => ["CANCELED", "REJECTED", "EXPIRED"].includes(o.status)).length,
  }), [orders]);

  const paged = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  /* KPI stats */
  const kpis = useMemo(() => {
    const open = tabCounts.open;
    const filledToday = tabCounts.filled;
    const totalQty = orders
      .filter((o) => o.status === "FILLED")
      .reduce((sum, o) => sum + parseFloat(o.quantity || "0"), 0);
    const allOrders = orders.length;
    const fillRate = allOrders > 0 ? ((filledToday / allOrders) * 100).toFixed(1) : "0.0";
    return { open, filledToday, volume: totalQty.toFixed(1), fillRate };
  }, [orders, tabCounts]);

  function handleCancelSingle(o: Order) {
    setCancelTarget(o);
    setCancelModalOpen(true);
  }

  function handleCancelConfirm() {
    setCancelModalOpen(false);
    setCancelTarget(null);
    setSelectedIds(new Set());
  }

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const openOrders = useMemo(
    () => orders.filter((o) => ["ACCEPTED", "PARTIALLY_FILLED"].includes(o.status)),
    [orders],
  );

  return (
    <>
      <ConfirmModal
        open={cancelModalOpen}
        onClose={() => { setCancelModalOpen(false); setCancelTarget(null); }}
        onConfirm={handleCancelConfirm}
        level="warning"
        title={cancelTarget ? `Cancel order ${cancelTarget.order_id}?` : `Cancel ${selectedIds.size} orders?`}
        description="This action will attempt to cancel the selected order(s). Already-filled portions cannot be reversed."
        confirmLabel="Cancel Order"
      />

      <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
        {/* Header */}
        <FadeIn direction="down" duration={0.25}>
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-1">
              <h1 className="font-heading text-[1.3rem] font-bold tracking-tight text-foreground">
                Orders
              </h1>
              <span className="text-[0.68rem] font-mono text-muted-foreground">
                All exchange orders — filterable
              </span>
            </div>
          </div>
        </FadeIn>

        {/* KPI Cards */}
        <StaggerContainer className="grid grid-cols-4 gap-4" staggerDelay={0.06}>
          {[
            { label: "Open Orders", value: String(kpis.open) },
            { label: "Filled Today", value: String(kpis.filledToday) },
            { label: "Volume", value: kpis.volume },
            { label: "Fill Rate", value: `${kpis.fillRate}%` },
          ].map((c) => (
            <StaggerItem key={c.label}>
              <div className="rounded-xl bg-card border p-4 hover:border-qds-border-hover transition-colors duration-[var(--dur)]">
                <div className="qds-stat-label mb-1">
                  {c.label}
                </div>
                <div className="font-mono text-[1.35rem] font-semibold tracking-tight text-foreground">
                  {loading ? "..." : c.value}
                </div>
              </div>
            </StaggerItem>
          ))}
        </StaggerContainer>

        {/* Tab bar + filters */}
        <FadeIn direction="up" duration={0.25} delay={0.05}>
          <div className="flex items-center justify-between gap-4">
            {/* Tabs */}
            <div className="flex items-center gap-[2px] rounded-sm bg-input p-[3px]">
              {(["open", "filled", "cancelled"] as TabKey[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`rounded px-3 py-1.5 text-[0.72rem] font-mono font-medium transition-all duration-150 whitespace-nowrap ${
                    activeTab === tab
                      ? "bg-secondary text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-qds-t1"
                  }`}
                >
                  {tab === "open" ? "Open" : tab === "filled" ? "Filled" : "Cancelled"} ({tabCounts[tab]})
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2">
              {/* Node type filter */}
              <div className="flex items-center gap-[2px] rounded-sm bg-input p-[3px]">
                {NODE_TYPE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setNodeType(opt.value)}
                    className={`rounded px-2.5 py-1 text-[0.68rem] font-mono font-medium transition-all duration-150 ${
                      nodeType === opt.value
                        ? "bg-secondary text-foreground"
                        : "text-muted-foreground hover:text-qds-t1"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              {/* Search */}
              <div className="flex items-center gap-1.5 rounded-sm bg-input border px-2.5 py-1 focus-within:border-primary transition-colors duration-150">
                <Search className="w-3 h-3 text-qds-t3" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search symbol..."
                  className="w-[130px] h-auto border-0 bg-transparent p-0 text-[0.72rem] font-mono text-foreground placeholder:text-qds-t3 outline-none focus-visible:ring-0"
                />
              </div>

              {/* Batch cancel for open tab */}
              {activeTab === "open" && selectedIds.size > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setCancelTarget(null); setCancelModalOpen(true); }}
                  className="text-[0.68rem] font-mono text-destructive border border-destructive hover:bg-qds-danger-dim"
                >
                  Cancel Selected ({selectedIds.size})
                </Button>
              )}
            </div>
          </div>
        </FadeIn>

        {/* Table */}
        <FadeIn direction="up" duration={0.25} delay={0.1} className="flex-1 flex flex-col min-h-0">
          <div className="rounded-xl bg-card border overflow-hidden flex flex-col min-h-0">
            {/* Table content */}
            <div className="flex-1 overflow-x-auto">
              {error ? (
                <EmptyState variant="error" title="Failed to load orders" description={error} />
              ) : loading ? (
                <div className="flex flex-col">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-3 border-b animate-pulse">
                      <div className="h-3 w-[80px] rounded bg-secondary" />
                      <div className="h-3 w-[60px] rounded bg-secondary" />
                      <div className="h-3 w-[100px] rounded bg-secondary" />
                      <div className="h-3 w-[40px] rounded bg-secondary" />
                      <div className="h-3 w-[50px] rounded bg-secondary" />
                      <div className="h-3 w-[70px] rounded bg-secondary" />
                      <div className="h-3 w-[50px] rounded bg-secondary" />
                    </div>
                  ))}
                </div>
              ) : paged.length === 0 ? (
                <EmptyState
                  variant="no-results"
                  title={`No ${activeTab} orders`}
                  description={activeTab === "open" ? "No pending orders at this time" : undefined}
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      {activeTab === "open" && (
                        <TableHead className="w-8">
                          <input
                            type="checkbox"
                            checked={selectedIds.size === openOrders.length && openOrders.length > 0}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedIds(new Set(openOrders.map((o) => o.id)));
                              } else {
                                setSelectedIds(new Set());
                              }
                            }}
                            className="accent-primary"
                          />
                        </TableHead>
                      )}
                      <TableHead>Time</TableHead>
                      <TableHead>Strategy</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Side</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead className="text-right">Price</TableHead>
                      <TableHead className="text-right">Size</TableHead>
                      {activeTab === "open" && (
                        <>
                          <TableHead className="text-right">Filled</TableHead>
                          <TableHead className="text-right" />
                        </>
                      )}
                      {activeTab === "filled" && (
                        <>
                          <TableHead className="text-right">Fee</TableHead>
                          <TableHead className="text-right">Latency</TableHead>
                        </>
                      )}
                      {activeTab === "cancelled" && (
                        <TableHead>Reason</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {paged.map((o) => (
                      <TableRow key={o.id}>
                        {activeTab === "open" && (
                          <TableCell>
                            <input
                              type="checkbox"
                              checked={selectedIds.has(o.id)}
                              onChange={() => toggleSelect(o.id)}
                              className="accent-primary"
                            />
                          </TableCell>
                        )}
                        <TableCell className="whitespace-nowrap">{formatDateTime(o.created_at)}</TableCell>
                        <TableCell>{o.strategy_name ?? "—"}</TableCell>
                        <TableCell className="font-semibold">{o.instrument_id}</TableCell>
                        <TableCell>
                          <span className={o.side === "BUY" ? "text-qds-success font-semibold" : "text-destructive font-semibold"}>
                            {o.side === "BUY" ? "Long" : "Short"}
                          </span>
                        </TableCell>
                        <TableCell>
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[0.6rem] font-medium ${
                              TYPE_VARIANT[o.order_type.toUpperCase()] ?? "bg-secondary text-qds-t1"
                            }`}
                          >
                            {o.order_type}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">{o.price ?? "Market"}</TableCell>
                        <TableCell className="text-right">{o.quantity}</TableCell>
                        {activeTab === "open" && (
                          <>
                            <TableCell className="text-right">
                              <div className="flex items-center justify-end gap-2">
                                <div className="w-10 h-1 rounded-full bg-secondary overflow-hidden">
                                  <div
                                    className="h-full rounded-full bg-primary"
                                    style={{ width: `${filledPercent(o)}%` }}
                                  />
                                </div>
                                <span className="text-[0.65rem] text-muted-foreground">{filledPercent(o)}%</span>
                              </div>
                            </TableCell>
                            <TableCell className="text-right">
                              <button
                                onClick={() => handleCancelSingle(o)}
                                className="rounded px-2 py-0.5 text-[0.65rem] font-mono border text-muted-foreground hover:border-destructive hover:text-destructive hover:bg-qds-danger-dim transition-all duration-150"
                              >
                                Cancel
                              </button>
                            </TableCell>
                          </>
                        )}
                        {activeTab === "filled" && (
                          <>
                            <TableCell className="text-right">
                              {o.fee ? `$${o.fee}` : "—"}
                            </TableCell>
                            <TableCell className="text-right">
                              <span
                                className={`${
                                  o.latency_ms != null && o.latency_ms < 10
                                    ? "text-qds-success"
                                    : o.latency_ms != null && o.latency_ms > 30
                                      ? "text-destructive"
                                      : "text-muted-foreground"
                                }`}
                              >
                                {o.latency_ms != null ? `${o.latency_ms}ms` : "—"}
                              </span>
                            </TableCell>
                          </>
                        )}
                        {activeTab === "cancelled" && (
                          <TableCell>{o.cancel_reason ?? "—"}</TableCell>
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>

            {/* Pagination */}
            {!loading && filtered.length > 0 && (
              <div className="px-4 py-3 border-t">
                <Pagination
                  total={filtered.length}
                  page={page}
                  pageSize={pageSize}
                  onPageChange={setPage}
                  onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
                />
              </div>
            )}
          </div>
        </FadeIn>
      </div>
    </>
  );
}
