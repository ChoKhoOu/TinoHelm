"use client";

import { useEffect, useRef, useState, useCallback } from "react";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

interface WebSocketMessage {
  type: string;
  channel: string;
  data: Record<string, unknown>;
  timestamp: string;
}

interface UseWebSocketOptions {
  path?: string;
  subscribe?: string[];
  onMessage?: (msg: WebSocketMessage) => void;
  reconnectInterval?: number;
  maxRetries?: number;
}

export function useWebSocket({
  path = "/ws/events",
  subscribe = [],
  onMessage,
  reconnectInterval = 3000,
  maxRetries = 10,
}: UseWebSocketOptions = {}) {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const subscribeRef = useRef(subscribe);
  subscribeRef.current = subscribe;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const subs = subscribeRef.current;
    const params = subs.length
      ? `?subscribe=${subs.join(",")}`
      : "";
    const ws = new WebSocket(`${WS_BASE}${path}${params}`);

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WebSocketMessage;
        setLastMessage(msg);
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

  const subscribeKey = subscribe?.join(",") ?? "";

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      retriesRef.current = maxRetries;
      wsRef.current?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connect, maxRetries, subscribeKey]);

  return { connected, lastMessage, send, disconnect };
}
