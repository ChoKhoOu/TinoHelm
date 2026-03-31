"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Circle } from "lucide-react";
import { apiGet } from "@/lib/api";

interface Props {
  nodeType: "sandbox" | "live";
}

interface DataStatus {
  status?: string;
  last_seen?: string;
  strategies?: number;
  positions?: number;
  balance_total?: number;
  balance_free?: number;
}

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

function fmtTimestamp(ts?: string): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function MarketDataTab({ nodeType }: Props) {
  const [status, setStatus] = useState<DataStatus>({});

  // Poll data-status every 5s
  useEffect(() => {
    let cancelled = false;

    async function fetchStatus() {
      try {
        const data = await apiGet<DataStatus>("/api/node/data-status", { mode: nodeType });
        if (!cancelled && data) setStatus(data);
      } catch {
        // silent
      }
    }

    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [nodeType]);

  const isOnline = status.status === "online";
  const dotColor = isOnline ? "var(--accent-green)" : "var(--accent-red)";
  const statusLabel = isOnline ? "在线" : "离线";

  const balanceTotal = status.balance_total;
  const balanceFree = status.balance_free;
  const marginUsed = balanceTotal != null && balanceFree != null ? balanceTotal - balanceFree : null;
  const marginPct = balanceTotal && balanceTotal > 0 && marginUsed != null ? (marginUsed / balanceTotal) * 100 : null;

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Data source status card */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-5">
          <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-4">
            节点状态
          </div>
          <div className="flex items-center gap-6 flex-wrap">
            {/* Online indicator */}
            <div className="flex items-center gap-2">
              <Circle className="w-2.5 h-2.5 fill-current" style={{ color: dotColor }} />
              <span className="text-[12px] font-bold" style={{ color: dotColor }}>
                {statusLabel}
              </span>
            </div>

            <div className="w-px h-4 bg-white/[0.08]" />

            <div className="flex items-center gap-2">
              <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                最后心跳
              </span>
              <span className="text-[11px] font-mono text-muted-foreground/70">
                {fmtTimestamp(status.last_seen)}
              </span>
            </div>

            <div className="w-px h-4 bg-white/[0.08]" />

            <div className="flex items-center gap-2">
              <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                策略数
              </span>
              <span className="text-[12px] font-bold font-mono text-[var(--accent-blue)]">
                {status.strategies ?? "—"}
              </span>
            </div>

            <div className="w-px h-4 bg-white/[0.08]" />

            <div className="flex items-center gap-2">
              <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                持仓数
              </span>
              <span className="text-[12px] font-bold font-mono text-[var(--accent-amber)]">
                {status.positions ?? "—"}
              </span>
            </div>
          </div>
        </GlassCard>
      </motion.div>

      {/* Account balance card */}
      {balanceTotal != null && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
        >
          <GlassCard className="p-5">
            <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-4">
              账户余额
            </div>
            <div className="flex items-center gap-6 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                  总余额
                </span>
                <span className="text-[12px] font-bold font-mono text-foreground">
                  ${balanceTotal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>

              <div className="w-px h-4 bg-white/[0.08]" />

              <div className="flex items-center gap-2">
                <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                  可用
                </span>
                <span className="text-[12px] font-bold font-mono text-[var(--accent-green)]">
                  ${(balanceFree ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>

              {marginPct != null && (
                <>
                  <div className="w-px h-4 bg-white/[0.08]" />
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-semibold tracking-[1px] uppercase text-muted-foreground/40">
                      已用保证金
                    </span>
                    <span
                      className="text-[12px] font-bold font-mono"
                      style={{
                        color: marginPct > 80 ? "var(--accent-red)" : marginPct > 50 ? "var(--accent-amber)" : "var(--foreground)",
                      }}
                    >
                      {marginPct.toFixed(1)}%
                    </span>
                  </div>
                </>
              )}
            </div>
          </GlassCard>
        </motion.div>
      )}
    </div>
  );
}
