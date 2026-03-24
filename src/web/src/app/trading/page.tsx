"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { apiGet } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { StrategyPanel } from "./components/StrategyPanel";
import { PositionsTable, type Position } from "./components/PositionsTable";
import { FillsStream, type Fill } from "./components/FillsStream";
import { OrdersPanel, type Order } from "./components/OrdersPanel";
import { ActionBar } from "./components/ActionBar";

const MAX_FILLS = 50;

type NodeType = "sandbox" | "live";

interface NodeStatus {
  positions?: Position[];
  orders?: Order[];
  risk_metrics?: {
    total_exposure?: number;
    margin_used_pct?: number;
    leverage?: number;
    daily_var?: number;
  };
}

export default function TradingPage() {
  const [nodeType, setNodeType] = useState<NodeType>("sandbox");
  const [positions, setPositions] = useState<Position[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [riskMetrics, setRiskMetrics] = useState<NodeStatus["risk_metrics"]>({});
  const [loading, setLoading] = useState(true);

  // Track current nodeType in ref to prevent stale closures in WS handlers
  const nodeTypeRef = useRef<NodeType>(nodeType);
  useEffect(() => { nodeTypeRef.current = nodeType; }, [nodeType]);

  // Fetch initial data
  const fetchStatus = useCallback(async (mode: NodeType) => {
    setLoading(true);
    try {
      const [statusData, fillsData] = await Promise.all([
        apiGet<NodeStatus>("/api/node/status", { mode }),
        apiGet<{ fills: Fill[] }>("/api/trading/fills", { mode, limit: "50" }),
      ]);
      if (statusData) {
        setPositions(statusData.positions ?? []);
        setOrders(statusData.orders ?? []);
        setRiskMetrics(statusData.risk_metrics ?? {});
      }
      if (fillsData?.fills) {
        setFills(fillsData.fills.slice(0, MAX_FILLS));
      }
    } catch {
      // silent — show stale/empty state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setPositions([]);
    setFills([]);
    setOrders([]);
    fetchStatus(nodeType);
  }, [nodeType, fetchStatus]);

  // WS: position updates
  const positionMsg = useWsEvent("position.update");
  useEffect(() => {
    if (!positionMsg) return;
    const data = positionMsg.data as { node_type?: string; position?: Position };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    if (!data?.position) return;
    const pos = data.position;
    setPositions((prev) => {
      const idx = prev.findIndex((p) => p.position_id === pos.position_id);
      if (idx === -1) return [pos, ...prev];
      const next = [...prev];
      next[idx] = pos;
      return next;
    });
  }, [positionMsg]);

  // WS: fill events
  const fillMsg = useWsEvent("fill.new");
  useEffect(() => {
    if (!fillMsg) return;
    const data = fillMsg.data as { node_type?: string; fill?: Fill };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    if (!data?.fill) return;
    setFills((prev) => {
      // Deduplicate by trade_id
      if (prev.some((f) => f.trade_id === data.fill!.trade_id)) return prev;
      return [data.fill!, ...prev].slice(0, MAX_FILLS);
    });
  }, [fillMsg]);

  // WS: order events
  const orderMsg = useWsEvent("order.update");
  useEffect(() => {
    if (!orderMsg) return;
    const data = orderMsg.data as { node_type?: string; order?: Order };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    if (!data?.order) return;
    const ord = data.order;
    setOrders((prev) => {
      const terminalStatuses = ["FILLED", "CANCELED", "EXPIRED", "DENIED", "REJECTED"];
      if (terminalStatuses.includes(ord.status)) {
        return prev.filter((o) => o.client_order_id !== ord.client_order_id);
      }
      const idx = prev.findIndex((o) => o.client_order_id === ord.client_order_id);
      if (idx === -1) return [ord, ...prev];
      const next = [...prev];
      next[idx] = ord;
      return next;
    });
  }, [orderMsg]);

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[var(--bg-page)]">
      {/* Node type tabs */}
      <div className="shrink-0 flex items-center gap-0 px-4 border-b border-[var(--border-gray)] bg-[var(--bg-sidebar)]">
        {(["sandbox", "live"] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => setNodeType(mode)}
            className="relative px-5 py-3 text-[11px] font-bold tracking-wide uppercase transition-colors"
            style={{
              color:
                nodeType === mode
                  ? mode === "live"
                    ? "var(--accent-green)"
                    : "var(--accent-amber)"
                  : "var(--text-muted)",
            }}
          >
            {mode === "sandbox" ? "沙盒" : "实盘"}
            {nodeType === mode && (
              <motion.div
                layoutId="tab-underline"
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{
                  backgroundColor:
                    mode === "live" ? "var(--accent-green)" : "var(--accent-amber)",
                }}
                transition={{ type: "spring", stiffness: 400, damping: 35 }}
              />
            )}
          </button>
        ))}
      </div>

      {/* Main 3-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: Strategy panel */}
        <div
          className="shrink-0 flex flex-col border-r border-[var(--border-gray)] bg-[var(--bg-card)]"
          style={{ width: 280 }}
        >
          <StrategyPanel nodeType={nodeType} />
        </div>

        {/* Center: Positions + Equity area */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Positions table */}
          <div className="flex-1 overflow-hidden border-b border-[var(--border-gray)] bg-[var(--bg-card)]">
            {loading ? (
              <PositionsSkeleton />
            ) : (
              <PositionsTable positions={positions} />
            )}
          </div>

          {/* Equity chart placeholder */}
          <div
            className="shrink-0 border-t border-[var(--border-gray)] bg-[var(--bg-card)] flex items-center justify-center"
            style={{ height: 160 }}
          >
            <span className="text-[10px] text-[var(--text-muted)] tracking-[0.5px] uppercase">
              权益曲线 — 即将推出
            </span>
          </div>
        </div>

        {/* Right column: Fills + Orders */}
        <div
          className="shrink-0 flex flex-col border-l border-[var(--border-gray)] bg-[var(--bg-card)]"
          style={{ width: 320 }}
        >
          {/* Fills stream — top half */}
          <div className="flex-1 overflow-hidden border-b border-[var(--border-gray)]">
            <FillsStream fills={fills} />
          </div>

          {/* Orders panel — bottom half */}
          <div className="flex-1 overflow-hidden">
            <OrdersPanel
              orders={orders}
              nodeType={nodeType}
              onOrderCancelled={() => fetchStatus(nodeType)}
            />
          </div>
        </div>
      </div>

      {/* Bottom action bar */}
      <ActionBar nodeType={nodeType} riskMetrics={riskMetrics ?? {}} />
    </div>
  );
}

function PositionsSkeleton() {
  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2 mb-4">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-6" />
      </div>
      {[1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}
