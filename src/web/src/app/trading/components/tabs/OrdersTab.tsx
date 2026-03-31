"use client";

import { useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Inbox, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import type { Order, Fill } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
  orders: Order[];
  fills: Fill[];
  onRefresh: () => void;
}

const ORDER_STATUS_COLORS: Record<string, string> = {
  ACCEPTED: "var(--accent-blue)",
  SUBMITTED: "var(--accent-blue)",
  PARTIALLY_FILLED: "var(--accent-amber)",
  PENDING_UPDATE: "var(--accent-amber)",
  PENDING_CANCEL: "var(--accent-amber)",
};

const ORDER_STATUS_LABELS: Record<string, string> = {
  ACCEPTED: "已接受",
  SUBMITTED: "已提交",
  PARTIALLY_FILLED: "部分成交",
  PENDING_UPDATE: "修改中",
  PENDING_CANCEL: "撤单中",
};

function GlassCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}

function SectionHeader({
  label,
  count,
  countColor = "var(--accent-blue)",
  countBg = "rgba(76,158,235,0.12)",
  right,
}: {
  label: string;
  count?: number;
  countColor?: string;
  countBg?: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          {label}
        </span>
        {count != null && (
          <span
            className="px-1.5 py-0.5 rounded text-[9px] font-bold"
            style={{ color: countColor, backgroundColor: countBg }}
          >
            {count}
          </span>
        )}
      </div>
      {right && <div>{right}</div>}
    </div>
  );
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function OrdersTab({ nodeType, orders, fills, onRefresh }: Props) {
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());
  const [strategyFilter, setStrategyFilter] = useState<string>("all");

  const handleCancel = useCallback(
    async (clientOrderId: string) => {
      setCancellingIds((prev) => new Set(prev).add(clientOrderId));
      try {
        const res = await fetch(
          `/api/trading/orders/${clientOrderId}?mode=${nodeType}`,
          { method: "DELETE" }
        );
        if (res.ok) {
          toast.success("订单已取消");
          onRefresh();
        } else {
          const data = await res.json().catch(() => ({}));
          toast.error(data?.detail ?? "撤单失败");
        }
      } catch {
        toast.error("撤单失败");
      } finally {
        setCancellingIds((prev) => {
          const next = new Set(prev);
          next.delete(clientOrderId);
          return next;
        });
      }
    },
    [nodeType, onRefresh]
  );

  // Unique strategy tags from fills
  const strategyTags = Array.from(
    new Set(fills.map((f) => f.strategy_id_tag).filter((t): t is string => !!t))
  );

  const filteredFills =
    strategyFilter === "all"
      ? fills
      : fills.filter((f) => f.strategy_id_tag === strategyFilter);

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* Active Orders */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard>
          <SectionHeader
            label="活跃订单"
            count={orders.length}
            countColor="var(--accent-amber)"
            countBg="rgba(240,180,41,0.12)"
          />
          {orders.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无活跃订单</span>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-white/[0.04]">
                    {["订单ID", "品种", "方向", "类型", "数量", "价格", "状态", "操作"].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-4 py-2 text-[10px] font-semibold tracking-[0.5px] uppercase text-muted-foreground/40 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((order) => {
                    const isBuy =
                      order.side === "BUY" || order.side === "buy";
                    const statusColor =
                      ORDER_STATUS_COLORS[order.status] ??
                      "var(--muted-foreground)";
                    const statusLabel =
                      ORDER_STATUS_LABELS[order.status] ?? order.status;
                    const isCancelling = cancellingIds.has(
                      order.client_order_id
                    );
                    return (
                      <tr
                        key={order.client_order_id}
                        className="border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors"
                      >
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground/60 whitespace-nowrap">
                          {order.client_order_id.slice(0, 12)}…
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono font-semibold text-foreground whitespace-nowrap">
                          {order.instrument_id}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-bold whitespace-nowrap">
                          <span
                            style={{
                              color: isBuy
                                ? "var(--accent-green)"
                                : "var(--accent-red)",
                            }}
                          >
                            {isBuy ? "买" : "卖"}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                          {order.type}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                          {order.quantity}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                          {order.price ?? "市价"}
                        </td>
                        <td className="px-4 py-2.5 whitespace-nowrap">
                          <span
                            className="px-1.5 py-0.5 rounded text-[9px] font-bold"
                            style={{
                              color: statusColor,
                              backgroundColor: `${statusColor}18`,
                            }}
                          >
                            {statusLabel}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 whitespace-nowrap">
                          <button
                            onClick={() =>
                              handleCancel(order.client_order_id)
                            }
                            disabled={isCancelling}
                            className="w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-red-500/10 text-muted-foreground/40 hover:text-red-400 disabled:opacity-30"
                            title="撤单"
                          >
                            {isCancelling ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <X className="w-3 h-3" />
                            )}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </motion.div>

      {/* Trade History */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.06, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard>
          <SectionHeader
            label="成交记录"
            count={filteredFills.length}
            countColor="var(--accent-purple)"
            countBg="rgba(167,139,250,0.12)"
            right={
              strategyTags.length > 0 ? (
                <select
                  value={strategyFilter}
                  onChange={(e) => setStrategyFilter(e.target.value)}
                  className="bg-white/[0.04] border border-white/[0.08] rounded px-2 py-1 text-[11px] text-muted-foreground focus:outline-none focus:border-[var(--accent-blue)]/50 transition-colors"
                >
                  <option value="all">全部策略</option>
                  {strategyTags.map((tag) => (
                    <option key={tag} value={tag}>
                      {tag}
                    </option>
                  ))}
                </select>
              ) : undefined
            }
          />
          {filteredFills.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无成交记录</span>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-white/[0.04]">
                    {["成交ID", "品种", "方向", "数量", "价格", "手续费", "时间"].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-4 py-2 text-[10px] font-semibold tracking-[0.5px] uppercase text-muted-foreground/40 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {filteredFills.map((fill) => {
                    const isBuy =
                      fill.order_side === "BUY" ||
                      fill.order_side === "buy";
                    return (
                      <tr
                        key={fill.trade_id}
                        className="border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors"
                      >
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground/60 whitespace-nowrap">
                          {fill.trade_id.slice(0, 12)}…
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono font-semibold text-foreground whitespace-nowrap">
                          {fill.instrument_id}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-bold whitespace-nowrap">
                          <span
                            style={{
                              color: isBuy
                                ? "var(--accent-green)"
                                : "var(--accent-red)",
                            }}
                          >
                            {isBuy ? "买" : "卖"}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                          {fill.last_qty}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                          {fill.last_px}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground/60 whitespace-nowrap">
                          {fill.commission ?? "—"}
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground/60 whitespace-nowrap">
                          {fmtTime(fill.ts_event)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </motion.div>
    </div>
  );
}
