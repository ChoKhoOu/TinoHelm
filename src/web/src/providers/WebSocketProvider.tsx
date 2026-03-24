"use client";

import { createContext, useContext, useReducer, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useWebSocket, type WsEventMessage } from "@/hooks/useWebSocket";

interface WsState {
  connected: boolean;
  reconnecting: boolean;
  lastKnown: Record<string, WsEventMessage>;
}

type WsAction =
  | { type: 'SET_CONNECTED'; connected: boolean }
  | { type: 'SET_MESSAGE'; eventType: string; message: WsEventMessage }
  | { type: 'CLEAR' };

function wsReducer(state: WsState, action: WsAction): WsState {
  switch (action.type) {
    case 'SET_CONNECTED':
      return { ...state, connected: action.connected, reconnecting: !action.connected && state.connected };
    case 'SET_MESSAGE':
      return { ...state, lastKnown: { ...state.lastKnown, [action.eventType]: action.message } };
    case 'CLEAR':
      return { ...state, lastKnown: {}, connected: false, reconnecting: false };
    default:
      return state;
  }
}

interface WsContextValue {
  connected: boolean;
  reconnecting: boolean;
  subscribe: (eventType: string, callback: (msg: WsEventMessage) => void) => () => void;
  getLastKnown: (eventType: string) => WsEventMessage | undefined;
}

const WsContext = createContext<WsContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(wsReducer, {
    connected: false,
    reconnecting: false,
    lastKnown: {},
  });

  const listenersRef = useRef<Map<string, Set<(msg: WsEventMessage) => void>>>(new Map());

  const { connected } = useWebSocket({
    path: "/ws/events",
    channels: [], // subscribe to all channels
    onMessage: useCallback((msg: WsEventMessage) => {
      const eventType = msg.type;
      if (eventType) {
        dispatch({ type: 'SET_MESSAGE', eventType, message: msg });
        // Notify listeners
        const typeListeners = listenersRef.current.get(eventType);
        if (typeListeners) {
          typeListeners.forEach(cb => cb(msg));
        }
        // Also notify wildcard listeners
        const wildcardListeners = listenersRef.current.get('*');
        if (wildcardListeners) {
          wildcardListeners.forEach(cb => cb(msg));
        }
      }
    }, []),
  });

  useEffect(() => {
    dispatch({ type: 'SET_CONNECTED', connected });
  }, [connected]);

  const subscribe = useCallback((eventType: string, callback: (msg: WsEventMessage) => void) => {
    if (!listenersRef.current.has(eventType)) {
      listenersRef.current.set(eventType, new Set());
    }
    listenersRef.current.get(eventType)!.add(callback);

    // Return unsubscribe function
    return () => {
      const listeners = listenersRef.current.get(eventType);
      if (listeners) {
        listeners.delete(callback);
        if (listeners.size === 0) {
          listenersRef.current.delete(eventType);
        }
      }
    };
  }, []);

  const getLastKnown = useCallback((eventType: string) => {
    return state.lastKnown[eventType];
  }, [state.lastKnown]);

  return (
    <WsContext.Provider value={{
      connected: state.connected,
      reconnecting: state.reconnecting,
      subscribe,
      getLastKnown,
    }}>
      {children}
    </WsContext.Provider>
  );
}

// Hooks
export function useWsConnection() {
  const ctx = useContext(WsContext);
  if (!ctx) throw new Error("useWsConnection must be used within WebSocketProvider");
  return { connected: ctx.connected, reconnecting: ctx.reconnecting };
}

export function useWsEvent(eventType: string) {
  const ctx = useContext(WsContext);
  if (!ctx) throw new Error("useWsEvent must be used within WebSocketProvider");
  const [message, setMessage] = useState<WsEventMessage | null>(
    () => ctx.getLastKnown(eventType) ?? null
  );

  useEffect(() => {
    return ctx.subscribe(eventType, setMessage);
  }, [ctx, eventType]);

  return message;
}

export function useWsLastKnown(eventType: string) {
  const ctx = useContext(WsContext);
  if (!ctx) throw new Error("useWsLastKnown must be used within WebSocketProvider");
  return ctx.getLastKnown(eventType) ?? null;
}
