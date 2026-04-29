// src/lib/notification-router.ts
// Event → notification channel routing for the QDS 4-layer notification system.

export type NotificationChannel = "silent" | "ticker" | "inline" | "toast" | "modal";
export type ToastType = "success" | "error" | "warning" | "info";

export type WsEventPayload = {
  type?: string;
  channel?: string;
  data?: Record<string, unknown>;
  timestamp?: string;
  run_id?: string;
  id?: string;
  job_id?: string;
  factor_name?: string;
  progress?: number;
  rating?: number;
  error?: string;
  exchange?: string;
  latency_ms?: number;
  strategy_id?: string;
  symbol?: string;
  data_type?: string;
  summary?: { sharpe_ratio?: number; [key: string]: unknown };
  // signal events
  signal_name?: string;
  sharpe?: number;
  // commission deviation events (canonical: signal.commission.deviation)
  fill_id?: string;
  instrument_id?: string;
  metric?: string;                  // "commission_only"
  expected_commission_bps?: number;
  actual_commission_bps?: number;
  deviation_bps?: number;
  // DEPRECATED legacy aliases (signal.cost.deviation) — retained for one
  // release cycle so transitional payloads keep working.
  expected_bps?: number;
  actual_bps?: number;
  [key: string]: unknown;
};

export interface RouteConfig {
  channel: NotificationChannel;
  type?: ToastType;
  dedupeKey?: (event: WsEventPayload) => string;
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
  "factor.progress":     { channel: "silent" },

  // Layer 2: Inline — handled by useAction hook, NOT here

  // Layer 3: Toast — async background events
  "backtest.completed":   { channel: "toast", type: "success", dedupeKey: (e) => e.run_id ?? e.id ?? "" },
  "backtest.failed":      { channel: "toast", type: "error",   dedupeKey: (e) => e.run_id ?? e.id ?? "" },
  "backtest.cancelled":   { channel: "toast", type: "warning", dedupeKey: (e) => e.run_id ?? e.id ?? "" },
  "data.fetch.completed": { channel: "toast", type: "success", dedupeKey: (e) => e.job_id ?? "" },
  "data.fetch.failed":    { channel: "toast", type: "error",   dedupeKey: (e) => e.job_id ?? "" },
  "factor.completed":     { channel: "toast", type: "success", dedupeKey: (e) => e.run_id ?? "" },
  "factor.failed":        { channel: "toast", type: "error",   dedupeKey: (e) => e.run_id ?? "" },
  "signal.completed":     { channel: "toast", type: "success", dedupeKey: (e) => `signal-completed-${e.run_id ?? ""}` },
  "signal.failed":        { channel: "toast", type: "error",   dedupeKey: (e) => `signal-failed-${e.run_id ?? ""}` },
  "signal.commission.deviation": { channel: "toast", type: "warning", dedupeKey: (e) => `commission-dev-${e.fill_id ?? ""}`, dedupeWindowMs: 10000 },
  // DEPRECATED: signal.cost.deviation kept for one release cycle. Subscribers
  // should migrate to signal.commission.deviation. The runtime warning is
  // emitted in formatToastMessage() the first time this event is observed.
  "signal.cost.deviation":     { channel: "toast", type: "warning", dedupeKey: (e) => `commission-dev-${e.fill_id ?? ""}`, dedupeWindowMs: 10000 },
  "strategy.started":     { channel: "toast", type: "info" },
  "strategy.stopped":     { channel: "toast", type: "info" },
  "connection.degraded":  { channel: "toast", type: "warning", dedupeKey: (e) => e.exchange ?? "", dedupeWindowMs: 30000 },
  "connection.restored":  { channel: "toast", type: "success", dedupeKey: (e) => e.exchange ?? "" },

  // Layer 4: Modal — critical alerts
  "risk.daily_limit_hit":     { channel: "modal" },
  "risk.max_drawdown":        { channel: "modal" },
  "risk.liquidation_warning": { channel: "modal" },
  "connection.all_lost":      { channel: "modal" },
};

/** Dedupe tracking: eventKey → last timestamp */
const _dedupeCache = new Map<string, number>();

/** One-time deprecation warning tracking (per session). */
const _loggedDeprecationKeys = new Set<string>();

/** Check if an event should be deduped (returns true if it should be SKIPPED) */
export function shouldDedupe(eventType: string, event: object): boolean {
  const route = ROUTING_TABLE[eventType];
  if (!route?.dedupeKey) return false;

  const key = `${eventType}:${route.dedupeKey(event as WsEventPayload)}`;
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
export function formatToastMessage(eventType: string, event: object): { title: string; description?: string } {
  const e = event as WsEventPayload;
  switch (eventType) {
    case "backtest.completed": {
      const id = (e.run_id ?? "").slice(0, 6);
      const sharpe = e.summary?.sharpe_ratio;
      return {
        title: `回测完成 ${id ? `· ${id}` : ""}`,
        description: sharpe != null ? `Sharpe ${sharpe.toFixed(2)}` : undefined,
      };
    }
    case "backtest.failed": {
      const id = (e.run_id ?? "").slice(0, 6);
      return { title: `回测失败 ${id ? `· ${id}` : ""}`, description: e.error ?? undefined };
    }
    case "backtest.cancelled": {
      const id = (e.run_id ?? "").slice(0, 6);
      return { title: `回测已取消 ${id ? `· ${id}` : ""}` };
    }
    case "data.fetch.completed":
      return { title: `${e.symbol ?? ""} ${e.data_type ?? ""} 拉取完成` };
    case "data.fetch.failed":
      return { title: `${e.symbol ?? ""} ${e.data_type ?? ""} 拉取失败`, description: e.error ?? undefined };
    case "factor.completed": {
      const id = (e.run_id ?? "").slice(0, 6);
      const stars = e.rating ? "★".repeat(e.rating) : undefined;
      return {
        title: `${e.factor_name ?? "因子"} 评估完成 ${id ? `· ${id}` : ""}`,
        description: stars,
      };
    }
    case "factor.failed": {
      const id = (e.run_id ?? "").slice(0, 6);
      return {
        title: `${e.factor_name ?? "因子"} 评估失败 ${id ? `· ${id}` : ""}`,
        description: e.error ?? undefined,
      };
    }
    case "signal.completed": {
      const id = (e.run_id ?? "").slice(0, 6);
      const sharpe = typeof e.sharpe === "number" ? e.sharpe.toFixed(2) : null;
      return {
        title: `${e.signal_name ?? "Signal"} 完成 ${id ? `· ${id}` : ""}`,
        description: sharpe != null ? `Sharpe ${sharpe}` : undefined,
      };
    }
    case "signal.failed": {
      const id = (e.run_id ?? "").slice(0, 6);
      return {
        title: `${e.signal_name ?? "Signal"} 失败 ${id ? `· ${id}` : ""}`,
        description: e.error ?? undefined,
      };
    }
    case "signal.commission.deviation":
    case "signal.cost.deviation": {
      // signal.cost.deviation is DEPRECATED — payloads emitted by MetricsActor
      // include both *_commission_bps and the legacy *_bps aliases.  Prefer
      // the canonical fields and fall back to the aliases for older publishers.
      if (eventType === "signal.cost.deviation" && typeof console !== "undefined") {
        // One-time deprecation warning per session per dedupe key.
        const dedupeId = `cost-dev-warn-${e.fill_id ?? ""}`;
        if (!_loggedDeprecationKeys.has(dedupeId)) {
          _loggedDeprecationKeys.add(dedupeId);
          console.warn(
            "[notification-router] signal.cost.deviation is DEPRECATED; " +
            "publishers should switch to signal.commission.deviation."
          );
        }
      }
      const devBps = typeof e.deviation_bps === "number" ? e.deviation_bps.toFixed(1) : "?";
      const expBps = typeof e.expected_commission_bps === "number"
        ? e.expected_commission_bps.toFixed(1)
        : typeof e.expected_bps === "number" ? e.expected_bps.toFixed(1) : "?";
      const actBps = typeof e.actual_commission_bps === "number"
        ? e.actual_commission_bps.toFixed(1)
        : typeof e.actual_bps === "number" ? e.actual_bps.toFixed(1) : "?";
      return {
        title: `手续费偏离 · ${e.instrument_id ?? ""}`,
        description: `偏差 ${devBps}bps（预期 ${expBps}bps，实际 ${actBps}bps；仅监控交易所手续费）`,
      };
    }
    case "strategy.started":
      return { title: `${e.strategy_id ?? "策略"} 已启动` };
    case "strategy.stopped":
      return { title: `${e.strategy_id ?? "策略"} 已停止` };
    case "connection.degraded":
      return { title: `${e.exchange ?? "交易所"} 连接降级`, description: `延迟 ${e.latency_ms ?? "?"}ms` };
    case "connection.restored":
      return { title: `${e.exchange ?? "交易所"} 连接恢复` };
    default:
      return { title: eventType, description: JSON.stringify(e).slice(0, 80) };
  }
}
