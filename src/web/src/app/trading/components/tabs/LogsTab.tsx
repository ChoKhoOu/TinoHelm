"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { motion } from "framer-motion";
import { ArrowDown, Activity, TrendingUp, FileText } from "lucide-react";
import { useWsEvent } from "@/providers/WebSocketProvider";

interface Props {
  nodeType: "sandbox" | "live";
}

type LogLevel = "ERROR" | "WARNING" | "INFO" | "DEBUG";

interface LogEntry {
  id: number;
  ts: string;
  level: LogLevel;
  logger: string;
  message: string;
}

interface TimelineEvent {
  id: number;
  type: "fill" | "position" | "order";
  description: string;
  ts: string;
}

const LEVEL_COLORS: Record<string, string> = {
  ERROR: "var(--accent-red)",
  WARNING: "var(--accent-amber)",
  INFO: "var(--foreground)",
  DEBUG: "rgba(255,255,255,0.35)",
};

const LEVEL_BG: Record<string, string> = {
  ERROR: "rgba(239,83,80,0.12)",
  WARNING: "rgba(240,180,41,0.08)",
  INFO: "transparent",
  DEBUG: "transparent",
};

const MAX_LOGS = 500;
const MAX_TIMELINE = 100;

let _logIdCounter = 0;
let _timelineIdCounter = 0;

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

function fmtTs(ts: string): string {
  try {
    const d = new Date(ts);
    const hh = d.getHours().toString().padStart(2, "0");
    const mm = d.getMinutes().toString().padStart(2, "0");
    const ss = d.getSeconds().toString().padStart(2, "0");
    const ms = d.getMilliseconds().toString().padStart(3, "0");
    return `${hh}:${mm}:${ss}.${ms}`;
  } catch {
    return ts.slice(11, 23) || ts;
  }
}

function LevelChip({
  level,
  active,
  onClick,
}: {
  level: LogLevel;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2 py-0.5 rounded text-[9px] font-bold tracking-[1px] uppercase border transition-all duration-150 ${
        active ? "opacity-100" : "opacity-30"
      }`}
      style={{
        color: LEVEL_COLORS[level],
        borderColor: `${LEVEL_COLORS[level]}40`,
        background: active ? `${LEVEL_COLORS[level]}12` : "transparent",
      }}
    >
      {level}
    </button>
  );
}

function TimelineIcon({ type }: { type: TimelineEvent["type"] }) {
  if (type === "fill") return <TrendingUp className="w-3 h-3 text-[var(--accent-green)]" />;
  if (type === "position") return <Activity className="w-3 h-3 text-[var(--accent-blue)]" />;
  return <FileText className="w-3 h-3 text-[var(--accent-amber)]" />;
}

export function LogsTab({ nodeType }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [activeFilters, setActiveFilters] = useState<Set<LogLevel>>(
    new Set(["INFO", "WARNING", "ERROR"])
  );
  const [autoScroll, setAutoScroll] = useState(true);
  const logListRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll]);

  // Detect manual scroll up
  const handleScroll = useCallback(() => {
    const el = logListRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 32;
    setAutoScroll(atBottom);
  }, []);

  const scrollToBottom = useCallback(() => {
    setAutoScroll(true);
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // WS: log entries
  const logMsg = useWsEvent("log.entry");
  useEffect(() => {
    if (!logMsg) return;
    const d = (logMsg.data ?? logMsg) as {
      node_type?: string;
      ts?: string;
      level?: string;
      logger?: string;
      message?: string;
    };
    if (d.node_type && d.node_type !== nodeType) return;

    const entry: LogEntry = {
      id: ++_logIdCounter,
      ts: d.ts ?? new Date().toISOString(),
      level: (d.level?.toUpperCase() as LogLevel) ?? "INFO",
      logger: d.logger ?? "",
      message: d.message ?? "",
    };

    setLogs((prev) => {
      const next = [...prev, entry];
      return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next;
    });
  }, [logMsg, nodeType]);

  // WS: fill.new
  const fillMsg = useWsEvent("fill.new");
  useEffect(() => {
    if (!fillMsg) return;
    const d = (fillMsg.data ?? fillMsg) as {
      node_type?: string;
      symbol?: string;
      side?: string;
      quantity?: number;
      price?: number;
      ts?: string;
    };
    if (d.node_type && d.node_type !== nodeType) return;

    const sym = d.symbol ?? "?";
    const side = d.side ?? "";
    const qty = d.quantity != null ? d.quantity.toFixed(4) : "?";
    const px = d.price != null ? d.price.toFixed(2) : "?";

    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "fill",
        description: `订单成交: ${sym} ${side} ${qty} @ ${px}`,
        ts: d.ts ?? new Date().toISOString(),
      };
      const next = [entry, ...prev];
      return next.length > MAX_TIMELINE ? next.slice(0, MAX_TIMELINE) : next;
    });
  }, [fillMsg, nodeType]);

  // WS: position.update
  const posMsg = useWsEvent("position.update");
  useEffect(() => {
    if (!posMsg) return;
    const d = (posMsg.data ?? posMsg) as {
      node_type?: string;
      position?: { instrument_id?: string; side?: string; quantity?: number };
    };
    if (d.node_type && d.node_type !== nodeType) return;
    const pos = d.position;
    if (!pos) return;

    const sym = pos.instrument_id ?? "?";
    const side = pos.side ?? "";
    const qty = pos.quantity != null ? pos.quantity.toFixed(4) : "?";

    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "position",
        description: `持仓变化: ${sym.replace(".BINANCE", "")} ${side} ${qty}`,
        ts: new Date().toISOString(),
      };
      const next = [entry, ...prev];
      return next.length > MAX_TIMELINE ? next.slice(0, MAX_TIMELINE) : next;
    });
  }, [posMsg, nodeType]);

  // WS: order.update
  const orderMsg = useWsEvent("order.update");
  useEffect(() => {
    if (!orderMsg) return;
    const d = (orderMsg.data ?? orderMsg) as {
      node_type?: string;
      order?: { instrument_id?: string; status?: string; side?: string };
    };
    if (d.node_type && d.node_type !== nodeType) return;
    const ord = d.order;
    if (!ord) return;

    const sym = ord.instrument_id ?? "?";
    const status = ord.status ?? "";
    const side = ord.side ?? "";

    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "order",
        description: `订单状态: ${sym.replace(".BINANCE", "")} ${side} → ${status}`,
        ts: new Date().toISOString(),
      };
      const next = [entry, ...prev];
      return next.length > MAX_TIMELINE ? next.slice(0, MAX_TIMELINE) : next;
    });
  }, [orderMsg, nodeType]);

  const toggleFilter = useCallback((level: LogLevel) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }, []);

  const filteredLogs = logs.filter((l) => activeFilters.has(l.level));

  return (
    <div className="flex flex-col gap-3 p-4 h-full min-h-0">
      {/* Log stream — 70% height */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col min-h-0"
        style={{ flex: "7 1 0" }}
      >
        <GlassCard className="flex flex-col h-full min-h-0">
          {/* Header with filters */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
                日志流
              </span>
              <span className="text-[9px] font-mono text-muted-foreground/30 ml-1">
                {filteredLogs.length}/{MAX_LOGS}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              {(["ERROR", "WARNING", "INFO", "DEBUG"] as LogLevel[]).map((level) => (
                <LevelChip
                  key={level}
                  level={level}
                  active={activeFilters.has(level)}
                  onClick={() => toggleFilter(level)}
                />
              ))}
            </div>
          </div>

          {/* Log entries */}
          <div
            ref={logListRef}
            onScroll={handleScroll}
            className="flex-1 overflow-y-auto min-h-0 font-mono text-[11px] leading-relaxed"
          >
            {filteredLogs.length === 0 ? (
              <div className="flex items-center justify-center h-full text-muted-foreground/30 text-xs">
                等待日志...
              </div>
            ) : (
              filteredLogs.map((entry) => (
                <div
                  key={entry.id}
                  className="flex items-start gap-2 px-4 py-1 hover:bg-white/[0.02] transition-colors"
                  style={{ background: LEVEL_BG[entry.level] }}
                >
                  <span className="text-muted-foreground/30 shrink-0 tabular-nums">
                    {fmtTs(entry.ts)}
                  </span>
                  <span
                    className="shrink-0 font-bold text-[9px] tracking-[1px] uppercase w-14 text-right"
                    style={{ color: LEVEL_COLORS[entry.level] }}
                  >
                    {entry.level}
                  </span>
                  {entry.logger && (
                    <span className="shrink-0 text-muted-foreground/40 text-[10px] max-w-[120px] truncate">
                      {entry.logger}:
                    </span>
                  )}
                  <span
                    className="flex-1 break-all"
                    style={{ color: LEVEL_COLORS[entry.level] }}
                  >
                    {entry.message}
                  </span>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>

          {/* Scroll-to-bottom button */}
          {!autoScroll && (
            <div className="shrink-0 flex justify-center py-2 border-t border-white/[0.06]">
              <button
                onClick={scrollToBottom}
                className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[10px] font-semibold text-[var(--accent-blue)] bg-[var(--accent-blue)]/10 hover:bg-[var(--accent-blue)]/20 transition-colors"
              >
                <ArrowDown className="w-3 h-3" />
                滚动到底部
              </button>
            </div>
          )}
        </GlassCard>
      </motion.div>

      {/* Event timeline — 30% height */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col min-h-0"
        style={{ flex: "3 1 0" }}
      >
        <GlassCard className="flex flex-col h-full min-h-0">
          <div className="px-4 py-3 border-b border-white/[0.06] shrink-0">
            <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
              事件时间线
            </span>
          </div>
          <div className="flex-1 overflow-y-auto min-h-0">
            {timeline.length === 0 ? (
              <div className="flex items-center justify-center h-full text-muted-foreground/30 text-xs">
                等待事件...
              </div>
            ) : (
              <div className="divide-y divide-white/[0.03]">
                {timeline.map((evt) => (
                  <div
                    key={evt.id}
                    className="flex items-center gap-3 px-4 py-2 hover:bg-white/[0.02] transition-colors"
                  >
                    <div className="shrink-0">
                      <TimelineIcon type={evt.type} />
                    </div>
                    <span className="flex-1 text-[11px] text-foreground/70">
                      {evt.description}
                    </span>
                    <span className="shrink-0 text-[9px] font-mono text-muted-foreground/30">
                      {fmtTs(evt.ts)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </GlassCard>
      </motion.div>
    </div>
  );
}
