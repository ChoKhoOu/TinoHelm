"use client";

import { useState, useCallback } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { EmptyState } from "@/components/EmptyState";
import { FadeIn } from "@/components/motion/FadeIn";
import type { Order, Fill } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
  orders: Order[];
  fills: Fill[];
  onRefresh: () => void;
}

const ORDER_STATUS_COLORS: Record<string, string> = {
  ACCEPTED: "var(--info)",
  SUBMITTED: "var(--info)",
  PARTIALLY_FILLED: "var(--warn)",
  PENDING_UPDATE: "var(--warn)",
  PENDING_CANCEL: "var(--warn)",
};

const ORDER_STATUS_LABELS: Record<string, string> = {
  ACCEPTED: "已接受",
  SUBMITTED: "已提交",
  PARTIALLY_FILLED: "部分成交",
  PENDING_UPDATE: "修改中",
  PENDING_CANCEL: "撤单中",
};

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
        const res = await fetch(`/api/trading/orders/${clientOrderId}?mode=${nodeType}`, { method: "DELETE" });
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

  const strategyTags = Array.from(new Set(fills.map((f) => f.strategy_id_tag).filter((t): t is string => !!t)));
  const filteredFills = strategyFilter === "all" ? fills : fills.filter((f) => f.strategy_id_tag === strategyFilter);

  // KPIs
  const pendingCount = orders.length;
  const filledCount = fills.length;
  const fillRate = pendingCount + filledCount > 0 ? ((filledCount / (pendingCount + filledCount)) * 100).toFixed(1) : "—";

  return (
    <div className="flex flex-col gap-5 p-5 min-h-0">
      {/* KPIs */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "挂单中", value: String(pendingCount), color: "var(--warn)" },
          { label: "已成交", value: String(filledCount), color: "var(--suc)" },
          { label: "成交率", value: fillRate === "—" ? "—" : `${fillRate}%`, color: "var(--info)" },
          { label: "平均延迟", value: "—", color: "var(--t2)" },
        ].map((kpi, i) => (
          <FadeIn key={kpi.label} delay={i * 0.05}>
            <div className="rounded-[var(--r)] border border-[var(--bd)] bg-[var(--bg-p)] p-3 hover:bg-[var(--bg-t)] transition-colors" style={{ transitionDuration: "var(--dur)" }}>
              <div className="qds-stat-label">{kpi.label}</div>
              <div className="text-[1.1rem] font-bold font-mono" style={{ color: kpi.color }}>{kpi.value}</div>
            </div>
          </FadeIn>
        ))}
      </div>

      {/* Active Orders */}
      <FadeIn delay={0.2}>
        <div className="rounded-[var(--r)] border border-[var(--bd)] bg-[var(--bg-p)] overflow-hidden">
          <div className="qds-card-header">
            <div className="flex items-center gap-2">
              <span className="qds-section-label">活跃订单</span>
              {orders.length > 0 && (
                <span className="px-1.5 py-0.5 rounded text-[0.56rem] font-bold bg-[var(--warn-d)] text-[var(--warn)]">{orders.length}</span>
              )}
            </div>
          </div>
          {orders.length === 0 ? (
            <EmptyState variant="first-use" title="暂无活跃订单" className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {["订单ID", "品种", "方向", "类型", "数量", "价格", "状态", "操作"].map((h) => (
                      <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {orders.map((order) => {
                    const isBuy = order.side === "BUY" || order.side === "buy";
                    const statusColor = ORDER_STATUS_COLORS[order.status] ?? "var(--t2)";
                    const statusLabel = ORDER_STATUS_LABELS[order.status] ?? order.status;
                    const isCancelling = cancellingIds.has(order.client_order_id);
                    return (
                      <TableRow key={order.client_order_id}>
                        <TableCell className="font-mono whitespace-nowrap">{order.client_order_id.slice(0, 12)}...</TableCell>
                        <TableCell className="font-mono font-semibold whitespace-nowrap">{order.instrument_id}</TableCell>
                        <TableCell className="font-bold whitespace-nowrap" style={{ color: isBuy ? "var(--suc)" : "var(--dan)" }}>{isBuy ? "买" : "卖"}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <span className="px-1.5 py-0.5 rounded-[var(--rs)] text-[0.56rem] font-bold bg-[var(--bg-t)] text-[var(--t1)]">{order.type}</span>
                        </TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{order.quantity}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{order.price ?? "市价"}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <span className="px-1.5 py-0.5 rounded text-[0.56rem] font-bold" style={{ color: statusColor, background: `color-mix(in srgb, ${statusColor} 12%, transparent)` }}>{statusLabel}</span>
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          <button
                            onClick={() => handleCancel(order.client_order_id)}
                            disabled={isCancelling}
                            className="size-6 flex items-center justify-center rounded transition-colors hover:bg-[var(--dan-d)] text-[var(--t3)] hover:text-[var(--dan)] disabled:opacity-30"
                            title="撤单"
                          >
                            {isCancelling ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
                          </button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </FadeIn>

      {/* Trade History */}
      <FadeIn delay={0.3}>
        <div className="rounded-[var(--r)] border border-[var(--bd)] bg-[var(--bg-p)] overflow-hidden">
          <div className="qds-card-header">
            <div className="flex items-center gap-2">
              <span className="qds-section-label">成交记录</span>
              <span className="px-1.5 py-0.5 rounded text-[0.56rem] font-bold bg-[var(--info-d)] text-[var(--info)]">{filteredFills.length}</span>
            </div>
            {strategyTags.length > 0 && (
              <select
                value={strategyFilter}
                onChange={(e) => setStrategyFilter(e.target.value)}
                className="bg-[var(--bg-in)] border border-[var(--bd)] rounded-[var(--rs)] px-2 py-1 text-[0.68rem] text-[var(--t1)] focus:outline-none focus:border-[var(--acc)] transition-colors"
              >
                <option value="all">全部策略</option>
                {strategyTags.map((tag) => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
            )}
          </div>
          {filteredFills.length === 0 ? (
            <EmptyState variant="first-use" title="暂无成交记录" className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {["成交ID", "品种", "方向", "数量", "价格", "手续费", "时间"].map((h) => (
                      <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredFills.map((fill) => {
                    const isBuy = fill.order_side === "BUY" || fill.order_side === "buy";
                    return (
                      <TableRow key={fill.trade_id}>
                        <TableCell className="font-mono whitespace-nowrap">{fill.trade_id.slice(0, 12)}...</TableCell>
                        <TableCell className="font-mono font-semibold whitespace-nowrap">{fill.instrument_id}</TableCell>
                        <TableCell className="font-bold whitespace-nowrap" style={{ color: isBuy ? "var(--suc)" : "var(--dan)" }}>{isBuy ? "买" : "卖"}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{fill.last_qty}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{fill.last_px}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{fill.commission ?? "—"}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{fmtTime(fill.ts_event)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </FadeIn>
    </div>
  );
}
