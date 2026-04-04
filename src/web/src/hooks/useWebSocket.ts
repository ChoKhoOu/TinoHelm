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
  reconnectInterval?: number;
  maxRetries?: number;
}

export function useWebSocket({
  path = "/ws/events",
  channels = [],
  onMessage,
  reconnectInterval = 3000,
  maxRetries = 10,
}: UseWebSocketOptions = {}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const channelsRef = useRef(channels);
  channelsRef.current = channels;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

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

      if (retriesRef.current < maxRetries) {
        retriesRef.current++;
        reconnectTimerRef.current = setTimeout(connect, reconnectInterval);
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [path, reconnectInterval, maxRetries]);

  const disconnect = useCallback(() => {
    clearTimeout(reconnectTimerRef.current);
    retriesRef.current = maxRetries; // prevent reconnect
    wsRef.current?.close();
  }, [maxRetries]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const channelsKey = channels?.join(",") ?? "";

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      retriesRef.current = maxRetries;
      wsRef.current?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connect, maxRetries, channelsKey]);

  return { connected, send, disconnect };
}
