"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { TopBar } from "./components/TopBar";
import { OverviewTab } from "./components/tabs/OverviewTab";
import { OrdersTab } from "./components/tabs/OrdersTab";
import { StrategiesTab } from "./components/tabs/StrategiesTab";
import { RiskTab } from "./components/tabs/RiskTab";
import { MarketDataTab } from "./components/tabs/MarketDataTab";
import { LogsTab } from "./components/tabs/LogsTab";
import { BacktestCompareTab } from "./components/tabs/BacktestCompareTab";
import { PaperSettingsTab } from "./components/tabs/PaperSettingsTab";

export type NodeType = "sandbox" | "live";

export interface Position {
  id: number;
  node_type: string;
  position_id: string;
  strategy_id_tag: string;
  instrument_id: string;
  side: string;
  quantity: string;
  signed_qty: number;
  avg_px_open: number | null;
  avg_px_close: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  currency: string | null;
  entry_side: string | null;
  peak_qty: string | null;
  ts_opened: string | null;
  ts_closed: string | null;
  duration: string | null;
  is_open: boolean;
  event_count: number;
  updated_at: string | null;
}

export interface Fill {
  id: number;
  node_type: string;
  trade_id: string;
  position_id: string | null;
  client_order_id: string;
  venue_order_id: string | null;
  strategy_id_tag: string | null;
  instrument_id: string;
  order_side: string;
  last_qty: string;
  last_px: string;
  commission: string | null;
  liquidity_side: string | null;
  ts_event: string;
  created_at: string | null;
}

export interface Order {
  client_order_id: string;
  instrument_id: string;
  side: string;
  type: string;
  quantity: string;
  price: string | null;
  status: string;
  strategy_id?: string;
  ts_event?: string;
}

const MAX_FILLS = 50;

export default function TradingPage() {
  const [nodeType, setNodeType] = useState<NodeType>("sandbox");
  const [activeTab, setActiveTab] = useState("overview");
  const [positions, setPositions] = useState<Position[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);

  const nodeTypeRef = useRef<NodeType>(nodeType);
  useEffect(() => { nodeTypeRef.current = nodeType; }, [nodeType]);

  const fetchData = useCallback(async (mode: NodeType) => {
    setLoading(true);
    try {
      const [posData, fillsData] = await Promise.all([
        apiGet<Position[]>("/api/trading/positions", { node_type: mode, is_open: "true" }),
        apiGet<Fill[]>("/api/trading/fills", { node_type: mode, limit: "50" }),
      ]);
      setPositions(posData ?? []);
      setFills((fillsData ?? []).slice(0, MAX_FILLS));
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setPositions([]);
    setFills([]);
    setOrders([]);
    fetchData(nodeType);
  }, [nodeType, fetchData]);

  // WS: position updates
  const positionMsg = useWsEvent("position.update");
  useEffect(() => {
    if (!positionMsg) return;
    const envelope = positionMsg as unknown as Record<string, unknown>;
    const data = (envelope.data ?? envelope) as { node_type?: string; position?: Position; position_id?: string };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    const pos = data?.position ?? (data?.position_id ? (data as unknown as Position) : null);
    if (!pos) return;
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
    const envelope = fillMsg as unknown as Record<string, unknown>;
    const data = (envelope.data ?? envelope) as { node_type?: string; fill?: Fill; trade_id?: string };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    const fill = data?.fill ?? (data?.trade_id ? (data as unknown as Fill) : null);
    if (!fill) return;
    setFills((prev) => {
      if (prev.some((f) => f.trade_id === fill.trade_id)) return prev;
      return [fill, ...prev].slice(0, MAX_FILLS);
    });
  }, [fillMsg]);

  // WS: order events
  const orderMsg = useWsEvent("order.update");
  useEffect(() => {
    if (!orderMsg) return;
    const envelope = orderMsg as unknown as Record<string, unknown>;
    const data = (envelope.data ?? envelope) as { node_type?: string; order?: Order; client_order_id?: string };
    if (data?.node_type && data.node_type !== nodeTypeRef.current) return;
    const ord = data?.order ?? (data?.client_order_id ? (data as unknown as Order) : null);
    if (!ord) return;
    setOrders((prev) => {
      const terminal = ["FILLED", "CANCELED", "EXPIRED", "DENIED", "REJECTED"];
      if (terminal.includes(ord.status)) {
        return prev.filter((o) => o.client_order_id !== ord.client_order_id);
      }
      const idx = prev.findIndex((o) => o.client_order_id === ord.client_order_id);
      if (idx === -1) return [ord, ...prev];
      const next = [...prev];
      next[idx] = ord;
      return next;
    });
  }, [orderMsg]);

  // When switching to live mode, hide sandbox-only tabs
  useEffect(() => {
    if (nodeType === "live" && (activeTab === "compare" || activeTab === "settings")) {
      setActiveTab("overview");
    }
  }, [nodeType, activeTab]);

  const renderTab = () => {
    switch (activeTab) {
      case "overview":
        return <OverviewTab nodeType={nodeType} positions={positions} loading={loading} />;
      case "orders":
        return <OrdersTab nodeType={nodeType} orders={orders} fills={fills} onRefresh={() => fetchData(nodeType)} />;
      case "strategies":
        return <StrategiesTab nodeType={nodeType} />;
      case "risk":
        return <RiskTab nodeType={nodeType} />;
      case "market":
        return <MarketDataTab nodeType={nodeType} />;
      case "logs":
        return <LogsTab nodeType={nodeType} />;
      case "compare":
        return <BacktestCompareTab nodeType={nodeType} />;
      case "settings":
        return <PaperSettingsTab nodeType={nodeType} />;
      default:
        return <OverviewTab nodeType={nodeType} positions={positions} loading={loading} />;
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-background">
      <TopBar nodeType={nodeType} onNodeTypeChange={setNodeType} activeTab={activeTab} onTabChange={setActiveTab} />
      <div className="flex-1 overflow-auto">
        {renderTab()}
      </div>
    </div>
  );
}
