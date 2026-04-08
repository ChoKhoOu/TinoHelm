// src/lib/notification-router.ts
// Event → notification channel routing for the QDS 4-layer notification system.

export type NotificationChannel = "silent" | "ticker" | "inline" | "toast" | "modal";
export type ToastType = "success" | "error" | "warning" | "info";

export interface RouteConfig {
  channel: NotificationChannel;
  type?: ToastType;
  dedupeKey?: (event: any) => string;
  dedupeWindowMs?: number;
}

export const ROUTING_TABLE: Record<string, RouteConfig> = {
  // Layer 1: Silent — data flows into UI components, no notification
  "fill.new":            { channel: "ticker" },
  "order.update":        { channel: "silent" },
  "order.cancelled":     { channel: "silent" },
  "position.update":     { channel: "silent" },
  "mark_price.update":   { channel: "silent" },
  "funding.settled":     { channel: "silent" },
  "backtest.progress":   { channel: "silent" },
  "data.fetch.progress": { channel: "silent" },
  "research.progress":   { channel: "silent" },

  // Layer 2: Inline — handled by useAction hook, NOT here

  // Layer 3: Toast — async background events
  "backtest.completed":   { channel: "toast", type: "success", dedupeKey: (e) => e.run_id ?? e.id },
  "backtest.failed":      { channel: "toast", type: "error",   dedupeKey: (e) => e.run_id ?? e.id },
  "data.fetch.completed": { channel: "toast", type: "success", dedupeKey: (e) => e.job_id },
  "data.fetch.failed":    { channel: "toast", type: "error",   dedupeKey: (e) => e.job_id },
  "research.completed":   { channel: "toast", type: "success", dedupeKey: (e) => e.job_id },
  "research.failed":      { channel: "toast", type: "error",   dedupeKey: (e) => e.job_id },
  "strategy.started":     { channel: "toast", type: "info" },
  "strategy.stopped":     { channel: "toast", type: "info" },
  "connection.degraded":  { channel: "toast", type: "warning", dedupeKey: (e) => e.exchange, dedupeWindowMs: 30000 },
  "connection.restored":  { channel: "toast", type: "success", dedupeKey: (e) => e.exchange },

  // Layer 4: Modal — critical alerts
  "risk.daily_limit_hit":     { channel: "modal" },
  "risk.max_drawdown":        { channel: "modal" },
  "risk.liquidation_warning": { channel: "modal" },
  "connection.all_lost":      { channel: "modal" },
};

/** Dedupe tracking: eventKey → last timestamp */
const _dedupeCache = new Map<string, number>();

/** Check if an event should be deduped (returns true if it should be SKIPPED) */
export function shouldDedupe(eventType: string, event: any): boolean {
  const route = ROUTING_TABLE[eventType];
  if (!route?.dedupeKey) return false;

  const key = `${eventType}:${route.dedupeKey(event)}`;
  const now = Date.now();
  const lastSeen = _dedupeCache.get(key);
  const windowMs = route.dedupeWindowMs ?? 5000; // default 5s dedupe window

  if (lastSeen && now - lastSeen < windowMs) return true;

  _dedupeCache.set(key, now);
  // Clean old entries periodically
  if (_dedupeCache.size > 100) {
    for (const [k, ts] of _dedupeCache) {
      if (now - ts > 60000) _dedupeCache.delete(k);
    }
  }
  return false;
}

/** Format a toast message for an event */
export function formatToastMessage(eventType: string, event: any): { title: string; description?: string } {
  switch (eventType) {
    case "backtest.completed": {
      const id = (event.run_id ?? "").slice(0, 6);
      const sharpe = event.summary?.sharpe_ratio;
      return {
        title: `回测完成 ${id ? `· ${id}` : ""}`,
        description: sharpe != null ? `Sharpe ${sharpe.toFixed(2)}` : undefined,
      };
    }
    case "backtest.failed": {
      const id = (event.run_id ?? "").slice(0, 6);
      return { title: `回测失败 ${id ? `· ${id}` : ""}`, description: event.error ?? undefined };
    }
    case "data.fetch.completed":
      return { title: `${event.symbol ?? ""} ${event.data_type ?? ""} 拉取完成` };
    case "data.fetch.failed":
      return { title: `${event.symbol ?? ""} ${event.data_type ?? ""} 拉取失败`, description: event.error ?? undefined };
    case "research.completed": {
      const stars = event.rating ? "\u2605".repeat(event.rating) : "\u2014";
      return { title: `${event.factor_name ?? "因子"} 诊断完成`, description: stars };
    }
    case "research.failed":
      return { title: `${event.factor_name ?? "因子"} 诊断失败`, description: event.error ?? undefined };
    case "strategy.started":
      return { title: `${event.strategy_id ?? "策略"} 已启动` };
    case "strategy.stopped":
      return { title: `${event.strategy_id ?? "策略"} 已停止` };
    case "connection.degraded":
      return { title: `${event.exchange ?? "交易所"} 连接降级`, description: `延迟 ${event.latency_ms ?? "?"}ms` };
    case "connection.restored":
      return { title: `${event.exchange ?? "交易所"} 连接恢复` };
    default:
      return { title: eventType, description: JSON.stringify(event).slice(0, 80) };
  }
}
