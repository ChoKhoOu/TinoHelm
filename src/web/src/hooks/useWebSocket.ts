"use client";

import { useEffect, useRef, useState, useCallback } from "react";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export interface WsEventMessage {
  type: string;
  channel?: string;
  data: Record<string, unknown>;
  timestamp?: string;
}

export interface PositionUpdateEvent extends WsEventMessage {
  type: 'position.update';
  data: {
    event: string;
    position_id: string;
    instrument_id: string;
    side: string;
    quantity: string;
    signed_qty: number;
    avg_px_open: number;
    realized_pnl: number;
    unrealized_pnl: number | null;
    is_open: boolean;
    [key: string]: unknown;
  };
}

export interface FillEvent extends WsEventMessage {
  type: 'fill.new';
  data: {
    trade_id: string;
    instrument_id: string;
    order_side: string;
    last_qty: string;
    last_px: string;
    commission: string;
    [key: string]: unknown;
  };
}

export interface HeartbeatEvent extends WsEventMessage {
  type: 'node.heartbeat';
  data: {
    node_type: string;
    ts: string;
    strategies: number;
    positions: number;
    trading_state: string;
    strategy_states: Record<string, string>;
    strategies_registry?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface BacktestProgressEvent extends WsEventMessage {
  type: 'backtest.progress';
  data: {
    run_id: string;
    pct: number;
    elapsed_secs?: number;
    [key: string]: unknown;
  };
}

export interface EquitySnapshotEvent extends WsEventMessage {
  type: 'equity.snapshot';
  data: {
    node_type: string;
    equity: number;
    balance: number;
    unrealized_pnl: number;
    ts: string;
    [key: string]: unknown;
  };
}

export interface RiskMetricsEvent extends WsEventMessage {
  type: 'risk.metrics';
  data: {
    node_type: string;
    equity: number;
    peak_equity: number;
    drawdown_pct: number;
    daily_pnl_pct: number;
    total_exposure: number;
    position_count: number;
    breached: boolean;
    breach_reason: string;
    per_instrument_exposure: Record<string, number>;
    [key: string]: unknown;
  };
}

export interface LogEntryEvent extends WsEventMessage {
  type: 'log.entry';
  data: {
    node_type: string;
    level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
    message: string;
    logger_name: string;
    ts: string;
    [key: string]: unknown;
  };
}

export interface SignalSnapshotEvent {
  type: "signal.snapshot";
  node_type: string;
  strategy_id: string;
  instrument_id: string;
  fields: Record<string, unknown>;
  ts: string;
}

interface UseWebSocketOptions {
  path?: string;
  channels?: string[];
  onMessage?: (msg: WsEventMessage) => void;
  /** Base reconnect delay in ms (starting point for exponential backoff). */
  reconnectInterval?: number;
  /** Maximum reconnect delay cap in ms (exponential backoff ceiling). */
  maxReconnectInterval?: number;
}

export function useWebSocket({
  path = "/ws/events",
  channels = [],
  onMessage,
  reconnectInterval = 3000,
  maxReconnectInterval = 60000,
}: UseWebSocketOptions = {}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  // Retry counter feeds exponential backoff (delay = base * 2^n, capped).
  // Never stops retrying — the backend may be down for hours or the
  // laptop may be sleeping; a hard give-up leaves the UI permanently
  // stale with no recovery path.
  const retriesRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Sticky shutdown flag — set by disconnect() and cleanup to block
  // further reconnect attempts after the hook unmounts.
  const shutdownRef = useRef(false);
  // Ref to the latest `connect` implementation. Breaks the self-
  // referential closure problem in the socket `onclose` handler: the
  // handler only needs "whatever connect is at the time a reconnect
  // fires" — capturing the latest via a ref avoids both stale closures
  // and the lint warning about accessing `connect` before declaration.
  const connectRef = useRef<(() => void) | null>(null);

  const channelsRef = useRef(channels);
  const onMessageRef = useRef(onMessage);
  // Mirror the latest props into refs so long-lived callbacks
  // (WebSocket handlers, reconnect timers) can read "current" values
  // without being in the useCallback deps array and thus triggering
  // socket churn on every render.
  useEffect(() => {
    channelsRef.current = channels;
    onMessageRef.current = onMessage;
  });

  const computeBackoff = useCallback(() => {
    // 3s, 6s, 12s, 24s, 48s, 60s cap — then flat 60s.
    const exp = Math.min(
      reconnectInterval * Math.pow(2, retriesRef.current),
      maxReconnectInterval,
    );
    return exp;
  }, [reconnectInterval, maxReconnectInterval]);

  const connect = useCallback(() => {
    if (shutdownRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    if (wsRef.current?.readyState === WebSocket.CONNECTING) return;

    const chans = channelsRef.current;
    const params = chans.length
      ? `?channels=${chans.join(",")}`
      : "";
    const ws = new WebSocket(`${WS_BASE}${path}${params}`);

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsEventMessage;
        onMessageRef.current?.(msg);
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;

      if (shutdownRef.current) return;

      const delay = computeBackoff();
      retriesRef.current++;
      reconnectTimerRef.current = setTimeout(() => {
        connectRef.current?.();
      }, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [path, computeBackoff]);

  // Keep the ref pointing at the latest connect so deferred timers
  // and the visibility handler can invoke the current implementation
  // without introducing stale-closure hazards or a self-referential
  // useCallback.
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(() => {
    shutdownRef.current = true;
    clearTimeout(reconnectTimerRef.current);
    wsRef.current?.close();
  }, []);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const channelsKey = channels?.join(",") ?? "";

  useEffect(() => {
    shutdownRef.current = false;
    connect();

    // Visibility-driven fast resume: when the tab comes back to the
    // foreground after being hidden (laptop sleep, tab switch), reset
    // the backoff counter and kick a reconnect immediately if the
    // socket isn't already OPEN. Also dispatch a window event so
    // polling hooks (useBacktestRuns) can skip the 5s wait.
    const onVisibility = () => {
      if (typeof document === "undefined") return;
      if (document.visibilityState !== "visible") return;

      try {
        window.dispatchEvent(new CustomEvent("tino:ws-visible"));
      } catch {
        // ignore environments without CustomEvent
      }

      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      retriesRef.current = 0;
      clearTimeout(reconnectTimerRef.current);
      connect();
    };

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      shutdownRef.current = true;
      clearTimeout(reconnectTimerRef.current);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
      wsRef.current?.close();
    };
  }, [connect, channelsKey]);

  return { connected, send, disconnect };
}
