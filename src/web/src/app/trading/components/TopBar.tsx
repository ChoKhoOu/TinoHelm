"use client";

import { useState, useCallback } from "react";
import { Loader2, Pause, Minimize2, OctagonAlert, Wifi, WifiOff } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { apiPost } from "@/lib/api";
import { useWsConnection } from "@/providers/WebSocketProvider";
import { motion } from "framer-motion";

type NodeType = "sandbox" | "live";
type ConfirmAction = "halt" | null;

export interface TabDef {
  id: string;
  label: string;
  shared: boolean;
}

export const TABS: TabDef[] = [
  { id: "overview", label: "概览", shared: true },
  { id: "orders", label: "订单", shared: true },
  { id: "strategies", label: "策略", shared: true },
  { id: "risk", label: "风控", shared: true },
  { id: "market", label: "行情", shared: true },
  { id: "logs", label: "日志", shared: true },
  { id: "compare", label: "回测对比", shared: false },
  { id: "settings", label: "沙盒设置", shared: false },
];

interface Props {
  nodeType: NodeType;
  onNodeTypeChange: (t: NodeType) => void;
  activeTab: string;
  onTabChange: (id: string) => void;
}

export function TopBar({ nodeType, onNodeTypeChange, activeTab, onTabChange }: Props) {
  const { connected, reconnecting } = useWsConnection();
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);

  const visibleTabs = TABS.filter((t) => t.shared || nodeType === "sandbox");

  const executeAction = useCallback(
    async (action: string) => {
      setLoadingAction(action);
      try {
        await apiPost("/api/node/lifecycle", { action, mode: nodeType });
      } catch {
        // silent
      } finally {
        setLoadingAction(null);
      }
    },
    [nodeType]
  );

  const handleConfirmedAction = useCallback(async () => {
    if (!confirmAction) return;
    const action = confirmAction;
    setConfirmAction(null);
    await executeAction(action);
  }, [confirmAction, executeAction]);

  const wsColor = reconnecting ? "var(--accent-amber)" : connected ? "var(--accent-green)" : "var(--accent-red)";
  const WsIcon = connected ? Wifi : WifiOff;

  return (
    <>
      <div className="h-11 shrink-0 flex items-center px-3 gap-0 border-b border bg-sidebar">
        {/* Mode selector */}
        {(["sandbox", "live"] as const).map((mode) => (
          <Button
            key={mode}
            variant="ghost"
            onClick={() => onNodeTypeChange(mode)}
            className="relative px-3 py-2 text-[11px] font-bold tracking-wide uppercase transition-colors h-11 rounded-none"
            style={{
              color: nodeType === mode
                ? mode === "live" ? "var(--accent-green)" : "var(--accent-amber)"
                : "var(--muted-foreground)",
            }}
          >
            {mode === "sandbox" ? "沙盒" : "实盘"}
            {nodeType === mode && (
              <motion.div
                layoutId="mode-underline"
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: mode === "live" ? "var(--accent-green)" : "var(--accent-amber)" }}
                transition={{ type: "spring", stiffness: 400, damping: 35 }}
              />
            )}
          </Button>
        ))}

        <Separator orientation="vertical" className="h-5 mx-1" />

        {/* Tab navigation */}
        <div className="flex items-center gap-0 overflow-x-auto">
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className="relative px-3 py-2 text-[11px] font-semibold tracking-wide transition-colors whitespace-nowrap h-11"
              style={{
                color: activeTab === tab.id ? "var(--foreground)" : "var(--muted-foreground)",
              }}
            >
              {tab.label}
              {activeTab === tab.id && (
                <motion.div
                  layoutId="tab-indicator"
                  className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full"
                  style={{ backgroundColor: "var(--accent-blue)" }}
                  transition={{ type: "spring", stiffness: 400, damping: 35 }}
                />
              )}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        {/* WS indicator */}
        <div className="flex items-center gap-1 mr-2">
          <WsIcon className="w-3 h-3" style={{ color: wsColor }} />
          <span className="text-[9px] font-mono" style={{ color: wsColor }}>
            {reconnecting ? "重连" : connected ? "在线" : "离线"}
          </span>
        </div>

        <Separator orientation="vertical" className="h-5" />

        {/* Kill switch buttons */}
        <div className="flex items-center gap-1 ml-2">
          <KillButton label="暂停" icon={<Pause className="w-3 h-3" />} loading={loadingAction === "pause"} onClick={() => executeAction("pause")} />
          <KillButton label="平仓" icon={<Minimize2 className="w-3 h-3" />} loading={loadingAction === "flatten"} onClick={() => executeAction("flatten")} />
          <KillButton label="停止" icon={<OctagonAlert className="w-3 h-3" />} loading={loadingAction === "halt"} onClick={() => setConfirmAction("halt")} destructive />
        </div>
      </div>

      {/* Confirmation dialog */}
      <Dialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
        <DialogContent className="bg-card border-border sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-foreground">
              紧急停止确认
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-[12px]">
              此操作将立即暂停所有交易，阻止新订单提交。确认继续？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" size="sm" onClick={() => setConfirmAction(null)} className="text-[11px]">取消</Button>
            <Button variant="destructive" size="sm" onClick={handleConfirmedAction} className="text-[11px]">
              确认停止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function KillButton({ label, icon, loading, onClick, destructive }: {
  label: string; icon: React.ReactNode; loading: boolean; onClick: () => void; destructive?: boolean;
}) {
  return (
    <Button
      variant={destructive ? "destructive" : "outline"}
      size="sm"
      onClick={onClick}
      disabled={loading}
      className={`text-[10px] font-bold tracking-wide whitespace-nowrap h-7 px-2 ${destructive ? "" : "text-foreground"}`}
    >
      {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : icon}
      {label}
    </Button>
  );
}
