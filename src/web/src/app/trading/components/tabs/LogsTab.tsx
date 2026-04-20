"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { ArrowDown, Activity, TrendingUp, FileText } from "lucide-react";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { FadeIn } from "@/components/motion/FadeIn";

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
  ERROR: "var(--dan)",
  WARNING: "var(--warn)",
  INFO: "var(--t1)",
  DEBUG: "var(--t3)",
};

const LEVEL_BG: Record<string, string> = {
  ERROR: "var(--dan-d)",
  WARNING: "var(--warn-d)",
  INFO: "transparent",
  DEBUG: "transparent",
};

const MAX_LOGS = 500;
const MAX_TIMELINE = 100;

let _logIdCounter = 0;
let _timelineIdCounter = 0;

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

function LevelChip({ level, active, onClick }: { level: LogLevel; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="px-2 py-0.5 rounded-[var(--rs)] text-[0.56rem] font-bold tracking-[1px] uppercase border transition-all"
      style={{
        color: LEVEL_COLORS[level],
        borderColor: active ? `color-mix(in srgb, ${LEVEL_COLORS[level]} 40%, transparent)` : "transparent",
        background: active ? `color-mix(in srgb, ${LEVEL_COLORS[level]} 8%, transparent)` : "transparent",
        opacity: active ? 1 : 0.3,
        transitionDuration: "150ms",
      }}
    >
      {level}
    </button>
  );
}

function TimelineIcon({ type }: { type: TimelineEvent["type"] }) {
  if (type === "fill") return <TrendingUp className="size-3 text-qds-success" />;
  if (type === "position") return <Activity className="size-3 text-qds-info" />;
  return <FileText className="size-3 text-qds-warning" />;
}

export function LogsTab({ nodeType }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [activeFilters, setActiveFilters] = useState<Set<LogLevel>>(new Set(["INFO", "WARNING", "ERROR"]));
  const [autoScroll, setAutoScroll] = useState(true);
  const logListRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll]);

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
    const d = (logMsg.data ?? logMsg) as { node_type?: string; ts?: string; level?: string; logger?: string; message?: string };
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
    const d = (fillMsg.data ?? fillMsg) as { node_type?: string; symbol?: string; side?: string; quantity?: number; price?: number; ts?: string };
    if (d.node_type && d.node_type !== nodeType) return;
    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "fill",
        description: `订单成交: ${d.symbol ?? "?"} ${d.side ?? ""} ${d.quantity?.toFixed(4) ?? "?"} @ ${d.price?.toFixed(2) ?? "?"}`,
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
    const d = (posMsg.data ?? posMsg) as { node_type?: string; position?: { instrument_id?: string; side?: string; quantity?: number } };
    if (d.node_type && d.node_type !== nodeType) return;
    const pos = d.position;
    if (!pos) return;
    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "position",
        description: `持仓变化: ${(pos.instrument_id ?? "?").replace(".BINANCE", "")} ${pos.side ?? ""} ${pos.quantity?.toFixed(4) ?? "?"}`,
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
    const d = (orderMsg.data ?? orderMsg) as { node_type?: string; order?: { instrument_id?: string; status?: string; side?: string } };
    if (d.node_type && d.node_type !== nodeType) return;
    const ord = d.order;
    if (!ord) return;
    setTimeline((prev) => {
      const entry: TimelineEvent = {
        id: ++_timelineIdCounter,
        type: "order",
        description: `订单状态: ${(ord.instrument_id ?? "?").replace(".BINANCE", "")} ${ord.side ?? ""} -> ${ord.status ?? ""}`,
        ts: new Date().toISOString(),
      };
      const next = [entry, ...prev];
      return next.length > MAX_TIMELINE ? next.slice(0, MAX_TIMELINE) : next;
    });
  }, [orderMsg, nodeType]);

  const toggleFilter = useCallback((level: LogLevel) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  }, []);

  const filteredLogs = logs.filter((l) => activeFilters.has(l.level));

  return (
    <div className="flex flex-col gap-3 p-5 h-full min-h-0">
      {/* Log stream -- 70% height */}
      <FadeIn className="flex flex-col min-h-0 [flex:7_1_0]">
        <div className="rounded-lg border bg-input flex flex-col h-full min-h-0 overflow-hidden">
          {/* Header with filters */}
          <div className="qds-card-header shrink-0">
            <div className="flex items-center gap-2">
              <span className="qds-section-label">日志流</span>
              <span className="text-[0.56rem] font-mono text-qds-t3 ml-1">{filteredLogs.length}/{MAX_LOGS}</span>
            </div>
            <div className="flex items-center gap-1.5">
              {(["ERROR", "WARNING", "INFO", "DEBUG"] as LogLevel[]).map((level) => (
                <LevelChip key={level} level={level} active={activeFilters.has(level)} onClick={() => toggleFilter(level)} />
              ))}
            </div>
          </div>

          {/* Log entries */}
          <div
            ref={logListRef}
            onScroll={handleScroll}
            className="flex-1 overflow-y-auto min-h-0 font-mono text-[0.68rem] leading-[1.8]"
          >
            {filteredLogs.length === 0 ? (
              <div className="flex items-center justify-center h-full text-qds-t3 text-[0.72rem]">等待日志...</div>
            ) : (
              filteredLogs.map((entry, i) => (
                <div
                  key={entry.id}
                  className="flex items-start gap-2 px-4 py-0.5 hover:bg-secondary transition-colors"
                  style={{ background: i % 2 === 1 ? "var(--bg-t)" : LEVEL_BG[entry.level] }}
                >
                  <span className="text-qds-t3 shrink-0 tabular-nums">{fmtTs(entry.ts)}</span>
                  <span
                    className="shrink-0 font-bold text-[0.56rem] tracking-[1px] uppercase w-14 text-right"
                    style={{ color: LEVEL_COLORS[entry.level] }}
                  >
                    {entry.level}
                  </span>
                  {entry.logger && (
                    <span className="shrink-0 text-qds-t3 text-[0.62rem] max-w-[120px] truncate">{entry.logger}:</span>
                  )}
                  <span className="flex-1 break-all" style={{ color: LEVEL_COLORS[entry.level] }}>{entry.message}</span>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>

          {/* Scroll-to-bottom */}
          {!autoScroll && (
            <div className="shrink-0 flex justify-center py-2 border-t">
              <button
                onClick={scrollToBottom}
                className="flex items-center gap-1.5 px-3 py-1 rounded text-[0.62rem] font-semibold text-qds-info bg-qds-info-dim hover:brightness-110 transition-colors"
              >
                <ArrowDown className="size-3" />
                滚动到底部
              </button>
            </div>
          )}
        </div>
      </FadeIn>

      {/* Event timeline -- 30% height */}
      <FadeIn delay={0.1} className="flex flex-col min-h-0 [flex:3_1_0]">
        <div className="rounded-lg border bg-card flex flex-col h-full min-h-0 overflow-hidden">
          <div className="qds-card-header shrink-0">
            <span className="qds-section-label">事件时间线</span>
          </div>
          <div className="flex-1 overflow-y-auto min-h-0">
            {timeline.length === 0 ? (
              <div className="flex items-center justify-center h-full text-qds-t3 text-[0.72rem]">等待事件...</div>
            ) : (
              <div className="divide-y divide-[var(--bd)]">
                {timeline.map((evt) => (
                  <div key={evt.id} className="flex items-center gap-3 px-4 py-2 hover:bg-secondary transition-colors">
                    <div className="shrink-0"><TimelineIcon type={evt.type} /></div>
                    <span className="flex-1 text-[0.68rem] text-qds-t1">{evt.description}</span>
                    <span className="shrink-0 text-[0.56rem] font-mono text-qds-t3">{fmtTs(evt.ts)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </FadeIn>
    </div>
  );
}
