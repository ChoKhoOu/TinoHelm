"use client";

import { motion, AnimatePresence } from "framer-motion";
import { TrendingUp, TrendingDown, Inbox } from "lucide-react";
import { formatTime } from "@/lib/format";

export interface Fill {
  trade_id: string;
  ts_event: string;
  instrument_id: string;
  side: "BUY" | "SELL";
  quantity: string;
  price: string;
  commission: string;
}

interface Props {
  fills: Fill[];
}


export function FillsStream({ fills }: Props) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
            成交流水
          </span>
          <span
            className="px-1.5 py-0.5 rounded text-[9px] font-bold"
            style={{
              color: "var(--t1)",
              backgroundColor: "var(--acc-d)",
            }}
          >
            {fills.length}
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {fills.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
            <Inbox className="w-6 h-6 opacity-30" />
            <span className="text-[11px]">暂无成交</span>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {fills.map((fill) => {
              const isBuy = fill.side === "BUY";
              return (
                <motion.div
                  key={fill.trade_id}
                  initial={{ opacity: 0, y: -8, height: 0 }}
                  animate={{ opacity: 1, y: 0, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className="overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-4 py-2 border-b border-border hover:bg-popover transition-colors">
                    {/* Side indicator */}
                    <span
                      className="shrink-0 w-5 h-5 rounded flex items-center justify-center"
                      style={{
                        color: isBuy ? "var(--suc)" : "var(--dan)",
                        backgroundColor: isBuy ? "var(--suc-d)" : "var(--dan-d)",
                      }}
                    >
                      {isBuy ? (
                        <TrendingUp className="w-3 h-3" />
                      ) : (
                        <TrendingDown className="w-3 h-3" />
                      )}
                    </span>

                    {/* Main info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-mono font-semibold text-foreground truncate">
                          {fill.instrument_id}
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground shrink-0 ml-2">
                          {formatTime(fill.ts_event)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span
                          className="text-[10px] font-bold"
                          style={{ color: isBuy ? "var(--suc)" : "var(--dan)" }}
                        >
                          {isBuy ? "买入" : "卖出"}
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground">
                          {fill.quantity} @ {fill.price}
                        </span>
                        {fill.commission && (
                          <span className="text-[9px] text-muted-foreground">
                            手续费 {fill.commission}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
