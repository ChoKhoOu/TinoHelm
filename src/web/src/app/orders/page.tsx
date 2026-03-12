"use client";

import { Download, Search } from "lucide-react";
import { useState, useEffect } from "react";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";

const STATUS_TABS = ["ALL", "FILLED", "CANCELED", "REJECTED"] as const;
type StatusTab = (typeof STATUS_TABS)[number];

interface Order {
  time: string;
  instrument: string;
  type: string;
  side: "BUY" | "SELL";
  price: string;
  qty: string;
  status: string;
}

const statusBadge: Record<string, string> = {
  FILLED:
    "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  CANCELED:
    "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  REJECTED:
    "bg-[var(--accent-orange-20)] text-[var(--accent-orange)]",
};

export default function OrdersPage() {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState<StatusTab>("ALL");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    async function loadOrders() {
      setLoading(true);
      try {
        const params: Record<string, string> = {};
        if (activeTab !== "ALL") params.status = activeTab;
        if (debouncedSearch) params.instrument = debouncedSearch;
        const data = await apiGet<{
          id: number;
          node_type: string;
          order_id: string;
          instrument_id: string;
          side: string;
          order_type: string;
          quantity: string;
          price: string | null;
          status: string;
          created_at: string | null;
        }[]>("/api/orders", params);
        if (cancelled || !data) return;
        setOrders(data.map(o => ({
          time: o.created_at?.replace("T", " ").slice(0, 16) ?? "",
          instrument: o.instrument_id,
          type: o.order_type,
          side: (o.side === "BUY" || o.side === "SELL") ? o.side : "BUY",
          price: o.price ?? "—",
          qty: o.quantity,
          status: o.status,
        })));
      } catch {
        setError("orders.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadOrders();
    return () => { cancelled = true; };
  }, [activeTab, debouncedSearch]);

  return (
    <div className="flex flex-col gap-6 p-8">
      {/* Top bar */}
      <div className="flex items-end justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("orders.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("orders.subtitle")}
          </span>
        </div>
        <button aria-label="Export orders" className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-5 py-[10px] text-[11px] font-bold tracking-wide text-[var(--text-secondary)] hover:border-[var(--border-light)] transition-all duration-150">
          <Download className="w-3 h-3" />
          {t("orders.export")}
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`rounded-lg px-[14px] py-[6px] text-[11px] font-semibold transition-colors duration-150 ${
              activeTab === tab
                ? "bg-[var(--accent-green-10)] text-[var(--accent-green)]"
                : "bg-[var(--bg-card)] border border-[var(--border-gray)] text-[var(--text-secondary)]"
            }`}
          >
            {tab}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-[14px] py-[6px] focus-within:border-[var(--accent-green)] transition-colors duration-150">
          <Search className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("orders.filterPlaceholder")}
            className="w-[160px] bg-transparent text-[11px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none"
          />
        </div>
      </div>

      {/* Order table */}
      <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)]">
        {/* Header */}
        <div className="flex items-center px-5 py-3 border-b border-[var(--border-gray)]">
          <span className="w-[130px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            TIME
          </span>
          <span className="w-[160px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            INSTRUMENT
          </span>
          <span className="w-[100px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            TYPE
          </span>
          <span className="w-[60px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            SIDE
          </span>
          <span className="w-[100px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            PRICE
          </span>
          <span className="w-[80px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            QTY
          </span>
          <span className="w-[100px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            STATUS
          </span>
        </div>
        {/* Rows */}
        {error ? (
          <div className="flex items-center justify-center h-full p-8">
            <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("orders.loadFailed")}</span>
          </div>
        ) : loading ? (
          <div className="px-5 py-8 text-center text-[11px] text-[var(--text-muted)]">
            {t("orders.loading")}
          </div>
        ) : orders.length === 0 ? (
          <div className="px-5 py-8 text-center text-[11px] text-[var(--text-muted)]">
            {t("orders.noOrders")}
          </div>
        ) : (
          orders.map((order, i) => (
            <div
              key={`${order.time}-${order.instrument}-${i}`}
              className={`flex items-center px-5 py-[11px] text-[11px] font-medium ${
                i < orders.length - 1
                  ? "border-b border-[var(--border-gray)]"
                  : ""
              }`}
            >
              <span className="w-[130px] text-[var(--text-secondary)]">
                {order.time}
              </span>
              <span className="w-[160px] text-[var(--text-primary)]">
                {order.instrument}
              </span>
              <span className="w-[100px] text-[var(--text-secondary)]">
                {order.type}
              </span>
              <span
                className={`w-[60px] font-semibold ${
                  order.side === "BUY"
                    ? "text-[var(--accent-green)]"
                    : "text-[var(--accent-red)]"
                }`}
              >
                {order.side}
              </span>
              <span className="w-[100px] text-[var(--text-primary)]">
                {order.price}
              </span>
              <span className="w-[80px] text-[var(--text-secondary)]">
                {order.qty}
              </span>
              <span className="w-[100px]">
                <span
                  className={`inline-flex rounded-full px-[10px] py-1 text-[9px] font-bold ${statusBadge[order.status] ?? "bg-[var(--bg-elevated)] text-[var(--text-secondary)]"}`}
                >
                  {order.status}
                </span>
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
