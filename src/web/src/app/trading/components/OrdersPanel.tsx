"use client";

import { useState, useCallback } from "react";
import { X, Loader2, Inbox } from "lucide-react";
import { apiPost } from "@/lib/api";

export interface Order {
  client_order_id: string;
  instrument_id: string;
  side: "BUY" | "SELL";
  order_type: string;
  quantity: string;
  price: string;
  status: string;
}

interface Props {
  orders: Order[];
  nodeType: "sandbox" | "live";
  onOrderCancelled?: () => void;
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

export function OrdersPanel({ orders, nodeType, onOrderCancelled }: Props) {
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());

  const handleCancel = useCallback(
    async (orderId: string) => {
      setCancellingIds((prev) => new Set(prev).add(orderId));
      try {
        await apiPost("/api/node/lifecycle", {
          action: "cancel_order",
          client_order_id: orderId,
          mode: nodeType,
        });
        onOrderCancelled?.();
      } catch {
        // silent
      } finally {
        setCancellingIds((prev) => {
          const next = new Set(prev);
          next.delete(orderId);
          return next;
        });
      }
    },
    [nodeType, onOrderCancelled]
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-gray)] shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            挂单
          </span>
          <span
            className="px-1.5 py-0.5 rounded text-[9px] font-bold"
            style={{
              color: "var(--accent-amber)",
              backgroundColor: "var(--accent-amber-20)",
            }}
          >
            {orders.length}
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {orders.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-[var(--text-muted)]">
            <Inbox className="w-6 h-6 opacity-30" />
            <span className="text-[11px]">暂无挂单</span>
          </div>
        ) : (
          <div>
            {orders.map((order) => {
              const isBuy = order.side === "BUY";
              const isCancelling = cancellingIds.has(order.client_order_id);
              const statusColor =
                ORDER_STATUS_COLORS[order.status] ?? "var(--text-muted)";
              const statusLabel =
                ORDER_STATUS_LABELS[order.status] ?? order.status;

              return (
                <div
                  key={order.client_order_id}
                  className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--border-gray)] hover:bg-[var(--bg-elevated)] transition-colors"
                >
                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[11px] font-mono font-semibold text-[var(--text-primary)] truncate">
                        {order.instrument_id}
                      </span>
                      <span
                        className="shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded"
                        style={{
                          color: statusColor,
                          backgroundColor: `${statusColor}18`,
                        }}
                      >
                        {statusLabel}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span
                        className="text-[10px] font-bold"
                        style={{
                          color: isBuy ? "var(--accent-green)" : "var(--accent-red)",
                        }}
                      >
                        {isBuy ? "买" : "卖"}
                      </span>
                      <span className="text-[10px] font-mono text-[var(--text-muted)]">
                        {order.order_type}
                      </span>
                      <span className="text-[10px] font-mono text-[var(--text-secondary)]">
                        {order.quantity} @ {order.price || "市价"}
                      </span>
                    </div>
                  </div>

                  {/* Cancel button */}
                  <button
                    onClick={() => handleCancel(order.client_order_id)}
                    disabled={isCancelling}
                    className="shrink-0 w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-[var(--accent-red-20)] text-[var(--text-muted)] hover:text-[var(--accent-red)] disabled:opacity-40"
                    title="撤单"
                  >
                    {isCancelling ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <X className="w-3 h-3" />
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
