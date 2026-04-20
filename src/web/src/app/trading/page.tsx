"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { useWsConnection } from "@/providers/WebSocketProvider";
import { ConfirmModal } from "@/components/ConfirmModal";
// Action button icons are unicode chars per QDS spec (⏸ ⇄ ◼)
import { OverviewTab } from "./components/tabs/OverviewTab";
import { OrdersTab } from "./components/tabs/OrdersTab";
import { StrategiesTab } from "./components/tabs/StrategiesTab";
import { RiskTab } from "./components/tabs/RiskTab";
import { LogsTab } from "./components/tabs/LogsTab";
import { StrategyDetailPanel } from "./components/StrategyDetailPanel";

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

const TABS = [
  { id: "overview", label: "概览" },
  { id: "positions", label: "持仓" },
  { id: "orders", label: "订单" },
  { id: "strategies", label: "策略" },
  { id: "risk", label: "风控" },
  { id: "logs", label: "日志" },
] as const;

export default function TradingPage() {
  const [nodeType, setNodeType] = useState<NodeType>("sandbox");
  const [activeTab, setActiveTab] = useState("overview");
  const [positions, setPositions] = useState<Position[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [clock, setClock] = useState("");
  const { connected, reconnecting } = useWsConnection();

  // Strategy detail panel
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);

  // Confirm modals
  const [confirmLive, setConfirmLive] = useState(false);
  const [confirmFlatten, setConfirmFlatten] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const nodeTypeRef = useRef<NodeType>(nodeType);
  useEffect(() => { nodeTypeRef.current = nodeType; }, [nodeType]);

  // Clock
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setClock(now.toLocaleTimeString("zh-CN", { hour12: false }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

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

  const handleSwitchToLive = () => {
    setConfirmLive(true);
  };

  const handleNodeChange = (mode: NodeType) => {
    if (mode === "live" && nodeType !== "live") {
      handleSwitchToLive();
    } else {
      setNodeType(mode);
    }
  };

  const executeAction = useCallback(async (action: string) => {
    setActionLoading(true);
    try {
      await apiPost("/api/node/lifecycle", { action, mode: nodeType });
    } catch {
      // silent
    } finally {
      setActionLoading(false);
    }
  }, [nodeType]);

  const envIsSandbox = nodeType === "sandbox";
  const envColor = envIsSandbox ? "var(--info)" : "var(--dan)";
  const envBg = envIsSandbox ? "var(--info-d)" : "var(--dan-d)";

  const wsStatus = reconnecting ? "stale" : connected ? "live" : "disconnected";
  const wsDot = reconnecting ? "bg-qds-warning" : connected ? "bg-qds-success" : "bg-destructive";
  const wsLabel = reconnecting ? "重连中" : connected ? "已连接" : "离线";

  const posCount = positions.length;
  const orderCount = orders.length;

  const renderTab = () => {
    if (selectedStrategy) {
      return (
        <StrategyDetailPanel
          strategyId={selectedStrategy}
          nodeType={nodeType}
          positions={positions}
          fills={fills}
          onBack={() => setSelectedStrategy(null)}
        />
      );
    }
    switch (activeTab) {
      case "overview":
        return (
          <OverviewTab
            nodeType={nodeType}
            positions={positions}
            fills={fills}
            loading={loading}
            onSelectStrategy={setSelectedStrategy}
          />
        );
      case "positions":
        return (
          <OverviewTab
            nodeType={nodeType}
            positions={positions}
            fills={fills}
            loading={loading}
            onSelectStrategy={setSelectedStrategy}
            positionsOnly
          />
        );
      case "orders":
        return <OrdersTab nodeType={nodeType} orders={orders} fills={fills} onRefresh={() => fetchData(nodeType)} />;
      case "strategies":
        return <StrategiesTab nodeType={nodeType} />;
      case "risk":
        return <RiskTab nodeType={nodeType} />;
      case "logs":
        return <LogsTab nodeType={nodeType} />;
      default:
        return (
          <OverviewTab
            nodeType={nodeType}
            positions={positions}
            fills={fills}
            loading={loading}
            onSelectStrategy={setSelectedStrategy}
          />
        );
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-background">
      {/* ── Environment Bar ─────────────────────────────────── */}
      <div
        className="shrink-0 flex items-center justify-between"
        style={{ background: envBg, color: envColor, padding: ".5rem 1.25rem" }}
      >
        <div className="flex items-center gap-4">
          <span className="font-mono text-[.68rem] font-semibold tracking-[.1em] uppercase">
            {envIsSandbox ? "SANDBOX" : "LIVE"}
          </span>
          <div className="flex rounded-sm p-[2px] gap-[2px] bg-input">
            {(["sandbox", "live"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => handleNodeChange(mode)}
                className="font-mono"
                style={{
                  fontSize: ".68rem",
                  padding: ".25rem .7rem",
                  borderRadius: "4px",
                  border: "none",
                  background: nodeType === mode ? envBg : "transparent",
                  color: nodeType === mode ? envColor : "var(--t2)",
                  cursor: "pointer",
                  transition: "all 150ms",
                  display: "flex",
                  alignItems: "center",
                  gap: ".3rem",
                }}
              >
                <span
                  className="inline-block size-[5px] rounded-full"
                  style={{ background: "currentColor" }}
                />
                {mode === "sandbox" ? "沙盒" : "实盘"}
              </button>
            ))}
          </div>
        </div>
        <span className="font-mono text-[.68rem]">
          {envIsSandbox ? "模拟环境 · 不会产生真实交易" : "真实交易环境 · 所有操作将产生实际损益"}
        </span>
      </div>

      {/* ── Title + Actions ─────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-5 py-4">
        <div className="flex items-center gap-2">
          <h1 className="font-mono text-[1rem] font-semibold text-foreground">交易终端</h1>
          <span
            className="px-[.45rem] py-[.12rem] rounded-full text-[0.58rem] font-medium"
            style={{ background: envBg, color: envColor }}
          >
            {envIsSandbox ? "SANDBOX" : "LIVE"}
          </span>
        </div>
        <div className="flex items-center gap-[.4rem] flex-wrap">
          <div className={`flex items-center gap-[.3rem] font-mono text-[.7rem] ${wsStatus === "live" ? "text-qds-success" : wsStatus === "stale" ? "text-qds-warning" : "text-destructive"}`}>
            <span className={`inline-block size-[5px] rounded-full ${wsDot}`} />
            {wsLabel}
          </div>
          <span className="font-mono text-[.7rem] text-muted-foreground">{clock}</span>
          <button
            onClick={() => executeAction("pause")}
            className="ab-btn font-mono text-[.7rem]"
            style={{ padding: ".35rem .7rem", borderRadius: "var(--rs)", border: "1px solid var(--bd)", background: "none", color: "var(--t1)", cursor: "pointer", transition: "all 150ms" }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--bdh)"; e.currentTarget.style.color = "var(--t0)"; e.currentTarget.style.background = "var(--bg-t)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--bd)"; e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = "none"; }}
          >
            ⏸ 暂停
          </button>
          <button
            onClick={() => setConfirmFlatten(true)}
            className="font-mono text-[.7rem]"
            style={{ padding: ".35rem .7rem", borderRadius: "var(--rs)", border: "1px solid var(--warn)", background: "none", color: "var(--warn)", cursor: "pointer", transition: "all 150ms" }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--warn)"; e.currentTarget.style.color = "#141413"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--warn)"; }}
          >
            ⇄ 全部平仓
          </button>
          <button
            onClick={() => setConfirmStop(true)}
            className="font-mono text-[.7rem]"
            style={{ padding: ".35rem .7rem", borderRadius: "var(--rs)", border: "1px solid var(--dan)", background: "none", color: "var(--dan)", cursor: "pointer", transition: "all 150ms" }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--dan)"; e.currentTarget.style.color = "#fff"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--dan)"; }}
          >
            ◼ 停止
          </button>
        </div>
      </div>

      {/* ── Tab Navigation ──────────────────────────────────── */}
      <div className="shrink-0 flex gap-0 px-5 bg-background pt-[.35rem]">
        {TABS.map((tab) => {
          const isActive = !selectedStrategy && activeTab === tab.id;
          const badge =
            tab.id === "positions" ? posCount :
            tab.id === "orders" ? orderCount :
            null;
          return (
            <button
              key={tab.id}
              onClick={() => {
                setSelectedStrategy(null);
                setActiveTab(tab.id);
              }}
              className={`relative font-mono text-[0.75rem] px-[.9rem] py-[.6rem] border-0 bg-transparent cursor-pointer transition-colors whitespace-nowrap ${
                isActive ? "text-primary" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
              {badge != null && badge > 0 && (
                <span className="ml-1 px-[.3rem] py-[.08rem] rounded-full text-[0.58rem] bg-secondary text-muted-foreground">
                  {badge}
                </span>
              )}
              {isActive && (
                <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] rounded-sm bg-primary" />
              )}
            </button>
          );
        })}
      </div>

      {/* ── Tab Content ─────────────────────────────────────── */}
      <div className="flex-1 overflow-auto bg-background">
        {renderTab()}
      </div>

      {/* ── Confirm Modals ──────────────────────────────────── */}
      <ConfirmModal
        open={confirmLive}
        onClose={() => setConfirmLive(false)}
        onConfirm={() => {
          setConfirmLive(false);
          setNodeType("live");
        }}
        level="danger"
        title="切换到实盘环境"
        description="切换到实盘环境，所有操作将产生真实交易。确认切换？"
        confirmText="LIVE"
        confirmLabel="确认切换"
      />
      <ConfirmModal
        open={confirmFlatten}
        onClose={() => setConfirmFlatten(false)}
        onConfirm={async () => {
          setConfirmFlatten(false);
          await executeAction("flatten");
        }}
        level="warning"
        title="全部平仓"
        description="此操作将平掉当前环境所有持仓。确认继续？"
        confirmLabel="确认平仓"
        loading={actionLoading}
      />
      <ConfirmModal
        open={confirmStop}
        onClose={() => setConfirmStop(false)}
        onConfirm={async () => {
          setConfirmStop(false);
          await executeAction("halt");
        }}
        level="danger"
        title="停止交易节点"
        description="此操作将立即暂停所有交易，阻止新订单提交。确认继续？"
        confirmText="STOP"
        confirmLabel="确认停止"
        loading={actionLoading}
      />
    </div>
  );
}
