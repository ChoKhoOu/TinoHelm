"use client";

import { useState, useCallback } from "react";
import { Loader2, Pause, Minimize2, OctagonAlert, PowerOff, Wifi, WifiOff } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { apiPost } from "@/lib/api";
import { useWsConnection } from "@/providers/WebSocketProvider";

interface RiskMetrics {
  total_exposure?: number;
  margin_used_pct?: number;
  leverage?: number;
  daily_var?: number;
}

interface Props {
  nodeType: "sandbox" | "live";
  riskMetrics: RiskMetrics;
}

type ConfirmAction = "halt" | "shutdown" | null;

export function ActionBar({ nodeType, riskMetrics }: Props) {
  const { connected, reconnecting } = useWsConnection();
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);

  const executeAction = useCallback(
    async (action: string) => {
      setLoadingAction(action);
      try {
        await apiPost("/api/node/lifecycle", { action, mode: nodeType });
      } catch {
        // silent — backend may drop connection on halt/shutdown
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

  const exposure = riskMetrics.total_exposure ?? 0;
  const margin = riskMetrics.margin_used_pct ?? 0;
  const leverage = riskMetrics.leverage ?? 0;
  const dailyVar = riskMetrics.daily_var ?? 0;

  const marginHigh = margin > 80;

  const wsStatus = reconnecting ? "重连中" : connected ? "已连接" : "未连接";
  const WsIcon = connected ? Wifi : WifiOff;
  const wsColor = reconnecting
    ? "var(--accent-amber)"
    : connected
    ? "var(--accent-green)"
    : "var(--accent-red)";

  return (
    <>
      <div
        className="h-14 shrink-0 flex items-center px-4 gap-6 border-t border-border bg-sidebar"
      >
        {/* Risk metrics */}
        <div className="flex items-center gap-5">
          <RiskMetric
            label="风险敞口"
            value={`$${exposure >= 1000 ? `${(exposure / 1000).toFixed(1)}K` : exposure.toFixed(0)}`}
          />
          <Separator orientation="vertical" className="h-5" />
          <RiskMetric
            label="保证金"
            value={`${margin.toFixed(1)}%`}
            valueColor={marginHigh ? "var(--accent-red)" : undefined}
          />
          <Separator orientation="vertical" className="h-5" />
          <RiskMetric label="杠杆" value={`${leverage.toFixed(2)}x`} />
          <Separator orientation="vertical" className="h-5" />
          <RiskMetric
            label="日度VaR"
            value={`$${dailyVar >= 1000 ? `${(dailyVar / 1000).toFixed(1)}K` : dailyVar.toFixed(0)}`}
          />
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* WS indicator */}
        <div className="flex items-center gap-1.5">
          <WsIcon className="w-3 h-3" style={{ color: wsColor }} />
          <span className="text-[10px] font-mono" style={{ color: wsColor }}>
            {wsStatus}
          </span>
        </div>

        <Separator orientation="vertical" className="h-5" />

        {/* Lifecycle action buttons */}
        <div className="flex items-center gap-2">
          <ActionButton
            label="全部暂停"
            icon={<Pause className="w-3 h-3" />}
            loading={loadingAction === "pause"}
            onClick={() => executeAction("pause")}
            variant="outline"
          />
          <ActionButton
            label="全部平仓"
            icon={<Minimize2 className="w-3 h-3" />}
            loading={loadingAction === "flatten"}
            onClick={() => executeAction("flatten")}
            variant="outline"
          />
          <ActionButton
            label="紧急暂停"
            icon={<OctagonAlert className="w-3 h-3" />}
            loading={loadingAction === "halt"}
            onClick={() => setConfirmAction("halt")}
            variant="destructive-outline"
          />
          <ActionButton
            label="关闭节点"
            icon={<PowerOff className="w-3 h-3" />}
            loading={loadingAction === "shutdown"}
            onClick={() => setConfirmAction("shutdown")}
            variant="destructive"
          />
        </div>
      </div>

      {/* Confirmation dialog */}
      <Dialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
        <DialogContent className="bg-card border-border sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-foreground">
              {confirmAction === "halt" ? "紧急暂停确认" : "关闭节点确认"}
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-[12px]">
              {confirmAction === "halt"
                ? "此操作将立即暂停所有交易，阻止新订单提交。确认继续？"
                : "此操作将关闭交易节点进程。所有策略将停止运行。确认继续？"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmAction(null)}
              className="text-[11px]"
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleConfirmedAction}
              className="text-[11px]"
            >
              {confirmAction === "halt" ? "确认暂停" : "确认关闭"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function RiskMetric({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] font-semibold tracking-[0.5px] text-muted-foreground uppercase whitespace-nowrap">
        {label}
      </span>
      <span
        className="text-[11px] font-mono font-semibold"
        style={{ color: valueColor ?? "var(--accent-amber)" }}
      >
        {value}
      </span>
    </div>
  );
}

function ActionButton({
  label,
  icon,
  loading,
  onClick,
  variant,
}: {
  label: string;
  icon: React.ReactNode;
  loading: boolean;
  onClick: () => void;
  variant: "outline" | "destructive-outline" | "destructive";
}) {
  const isDestructiveOutline = variant === "destructive-outline";
  const buttonVariant = variant === "destructive" ? "destructive" : "outline";

  return (
    <Button
      variant={buttonVariant}
      size="sm"
      onClick={onClick}
      disabled={loading}
      className={
        isDestructiveOutline
          ? "text-[var(--accent-red)] border-[var(--accent-red)] text-[10px] font-bold tracking-wide whitespace-nowrap"
          : "text-[10px] font-bold tracking-wide whitespace-nowrap"
      }
    >
      {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : icon}
      {label}
    </Button>
  );
}
