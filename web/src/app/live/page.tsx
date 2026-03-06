"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Pause, Minimize2, Octagon, TrendingUp, TrendingDown } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { apiGet, apiPost } from "@/lib/api";
import { useI18n } from "@/i18n";

interface Ticker { instrument: string; price: string; change: string; changeType: "positive" | "negative"; amount: string }
interface Position { instrument: string; side: string; qty: string; avgPrice: string; markPrice: string; pnl: string; pnlType: string; strategy: string }
interface OpenOrder { instrument: string; type: string; side: string; price: string; qty: string; status: string }


export default function LiveTradingPage() {
  const { t } = useI18n();
  const [env, setEnv] = useState<"sandbox" | "live">("sandbox");
  const [error, setError] = useState<string | null>(null);
  const [tickers, setTickers] = useState<Ticker[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<OpenOrder[]>([]);
  const [riskMetrics, setRiskMetrics] = useState([
    { label: "DAILY VAR", value: "—" },
    { label: "MAX DRAWDOWN", value: "—" },
    { label: "MARGIN USED", value: "—" },
    { label: "LEVERAGE", value: "—" },
    { label: "EXPOSURE", value: "—" },
  ]);

  // Kill button state
  const [killProgress, setKillProgress] = useState(0);
  const killTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const killStartRef = useRef<number>(0);

  // Cleanup kill timer on unmount
  useEffect(() => {
    return () => {
      if (killTimerRef.current) clearInterval(killTimerRef.current);
    };
  }, []);

  // WebSocket
  const { connected, lastMessage } = useWebSocket({
    subscribe: ["positions", "orders", "fills"],
    path: `/ws/events?env=${env}`,
  });

  // Fetch initial data
  useEffect(() => {
    let cancelled = false;
    async function fetchStatus() {
      try {
        const data = await apiGet<{
          tickers?: Ticker[];
          positions?: Position[];
          orders?: OpenOrder[];
          risk_metrics?: {
            daily_var?: number;
            max_drawdown?: number;
            margin_used_pct?: number;
            leverage?: number;
            total_exposure?: number;
          };
        }>("/api/node/status", { env });
        if (cancelled || !data) return;
        if (data.tickers) setTickers(data.tickers);
        if (data.positions) setPositions(data.positions);
        if (data.orders) setOrders(data.orders);
        if (data.risk_metrics) {
          setRiskMetrics([
            { label: "DAILY VAR", value: `$${(data.risk_metrics.daily_var ?? 0).toLocaleString()}` },
            { label: "MAX DRAWDOWN", value: `${(data.risk_metrics.max_drawdown ?? 0).toFixed(1)}%` },
            { label: "MARGIN USED", value: `${(data.risk_metrics.margin_used_pct ?? 0).toFixed(1)}%` },
            { label: "LEVERAGE", value: `${(data.risk_metrics.leverage ?? 0).toFixed(1)}x` },
            { label: "EXPOSURE", value: `$${(data.risk_metrics.total_exposure ?? 0).toLocaleString()}` },
          ]);
        }
      } catch {
        setError("live.loadFailed");
      }
    }
    fetchStatus();
    return () => { cancelled = true; };
  }, [env]);

  // Update from WebSocket messages
  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.channel === "positions" && Array.isArray(lastMessage.data)) {
      setPositions(lastMessage.data as Position[]);
    }
    if (lastMessage.channel === "orders" && Array.isArray(lastMessage.data)) {
      setOrders(lastMessage.data as OpenOrder[]);
    }
  }, [lastMessage]);

  // Kill switch handlers
  const handlePause = useCallback(async () => {
    try {
      await apiPost("/api/node/kill", { level: 1 });
    } catch { setError("live.commandFailed"); }
  }, []);

  const handleFlatten = useCallback(async () => {
    if (window.confirm("Flatten all positions?")) {
      try {
        await apiPost("/api/node/kill", { level: 2 });
      } catch { setError("live.commandFailed"); }
    }
  }, []);

  const handleKillDown = useCallback(() => {
    killStartRef.current = Date.now();
    setKillProgress(0);
    killTimerRef.current = setInterval(() => {
      const elapsed = Date.now() - killStartRef.current;
      const progress = Math.min(elapsed / 3000, 1);
      setKillProgress(progress);
      if (progress >= 1) {
        clearInterval(killTimerRef.current);
        apiPost("/api/node/kill", { level: 3 }).catch(() => { setError("live.commandFailed"); });
        setKillProgress(0);
      }
    }, 50);
  }, []);

  const handleKillUp = useCallback(() => {
    clearInterval(killTimerRef.current);
    setKillProgress(0);
  }, []);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--accent-red)]">{t(error as "live.commandFailed")}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 p-6">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("live.title")}
          </h1>
          <span className="text-xs text-[var(--text-muted)]">
            {t("live.subtitle")}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {/* Environment Toggle */}
          <div className="flex items-center rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] p-0.5">
            <button
              onClick={() => setEnv("sandbox")}
              className={`px-3 py-1.5 rounded-md text-[10px] font-bold tracking-wide transition-all duration-150 ease-out ${
                env === "sandbox"
                  ? "bg-[var(--accent-orange-20)] text-[var(--accent-orange)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`}
            >
              SANDBOX
            </button>
            <button
              onClick={() => setEnv("live")}
              className={`px-3 py-1.5 rounded-md text-[10px] font-bold tracking-wide transition-all duration-150 ease-out ${
                env === "live"
                  ? "bg-[var(--accent-green-20)] text-[var(--accent-green)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`}
            >
              LIVE
            </button>
          </div>

          <Badge variant={connected ? "connected" : "neutral"}>BINANCE</Badge>
          <Badge variant={connected ? "connected" : "disconnected"}>
            {connected ? "CONNECTED" : "DISCONNECTED"}
          </Badge>

          {/* Kill Switch: PAUSE */}
          <button
            onClick={handlePause}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-[10px] text-[11px] font-bold tracking-wide transition-all duration-150 ease-out bg-transparent border border-[var(--accent-green)] text-[var(--accent-green)] hover:bg-[var(--accent-green-10)]"
          >
            <Pause className="w-3 h-3" />
            {t("live.pause")}
          </button>

          {/* Kill Switch: FLATTEN */}
          <button
            onClick={handleFlatten}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-[10px] text-[11px] font-bold tracking-wide transition-all duration-150 ease-out bg-[var(--accent-orange)] text-white hover:opacity-90"
          >
            <Minimize2 className="w-3 h-3" />
            {t("live.flatten")}
          </button>

          {/* Kill Switch: KILL (3s long press) */}
          <button
            onMouseDown={handleKillDown}
            onMouseUp={handleKillUp}
            onMouseLeave={handleKillUp}
            aria-label="Emergency kill switch - hold for 3 seconds"
            className="relative inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-[10px] text-[11px] font-bold tracking-wide bg-[var(--accent-red)] text-white hover:opacity-90 overflow-hidden select-none"
          >
            {/* Progress bar overlay */}
            {killProgress > 0 && (
              <div
                className="absolute inset-0 bg-white/20 origin-left transition-none"
                style={{ transform: `scaleX(${killProgress})` }}
              />
            )}
            <Octagon className="w-3 h-3 relative z-10" />
            <span className="relative z-10">{t("live.kill")}</span>
          </button>
        </div>
      </div>

      {/* Ticker row */}
      <div className="grid grid-cols-4 gap-3">
        {tickers.map((t) => (
          <div
            key={t.instrument}
            className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-[14px] px-4 flex items-center justify-between"
          >
            <div className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                {t.instrument}
              </span>
              <span className="font-heading text-lg font-bold tracking-tight text-[var(--text-primary)]">
                {t.price}
              </span>
            </div>
            <div className="flex flex-col items-end gap-1">
              <span
                className={`text-[11px] font-semibold flex items-center gap-1 ${
                  t.changeType === "positive"
                    ? "text-[var(--accent-green)]"
                    : "text-[var(--accent-red)]"
                }`}
              >
                {t.changeType === "positive" ? (
                  <TrendingUp className="w-3 h-3" />
                ) : (
                  <TrendingDown className="w-3 h-3" />
                )}
                {t.change}
              </span>
              <span className="text-[10px] text-[var(--text-muted)]">
                {t.amount}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Main body */}
      <div className="flex gap-4">
        {/* Positions Panel */}
        <Card className="flex-1" padding={false}>
          <div className="flex items-center justify-between p-5 pb-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold tracking-[0.5px] text-[var(--text-primary)]">
                {t("live.openPositions")}
              </span>
              <Badge variant="info" dot={false}>
                {positions.length}
              </Badge>
            </div>
          </div>
          <div className="overflow-x-auto p-5 pt-4">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-[var(--border-gray)]">
                  {["INSTRUMENT", "SIDE", "QTY", "AVG PRICE", "MARK PRICE", "UNREALIZED PNL", "STRATEGY"].map(
                    (h) => (
                      <th
                        key={h}
                        className="pb-3 text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase"
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr
                    key={p.instrument + p.side}
                    className="border-b border-[var(--border-gray)] last:border-b-0"
                  >
                    <td className="py-3 text-[11px] font-semibold text-[var(--text-primary)]">
                      {p.instrument}
                    </td>
                    <td className="py-3">
                      <span
                        className={`text-[11px] font-bold ${
                          p.side === "BUY"
                            ? "text-[var(--accent-green)]"
                            : "text-[var(--accent-red)]"
                        }`}
                      >
                        {p.side}
                      </span>
                    </td>
                    <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                      {p.qty}
                    </td>
                    <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                      {p.avgPrice}
                    </td>
                    <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                      {p.markPrice}
                    </td>
                    <td className="py-3">
                      <span
                        className={`text-[11px] font-semibold ${
                          p.pnlType === "positive"
                            ? "text-[var(--accent-green)]"
                            : "text-[var(--accent-red)]"
                        }`}
                      >
                        {p.pnl}
                      </span>
                    </td>
                    <td className="py-3 text-[11px] text-[var(--text-muted)]">
                      {p.strategy}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Right column */}
        <div className="flex w-[360px] shrink-0 flex-col gap-4">
          {/* Open Orders */}
          <Card padding={false}>
            <div className="flex items-center justify-between p-5 pb-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold tracking-[0.5px] text-[var(--text-primary)]">
                  {t("live.openOrders")}
                </span>
                <Badge variant="warning" dot={false}>
                  {orders.length}
                </Badge>
              </div>
            </div>
            <div className="overflow-x-auto p-5 pt-4">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-[var(--border-gray)]">
                    {["INSTRUMENT", "TYPE", "SIDE", "PRICE", "QTY", "STATUS"].map(
                      (h) => (
                        <th
                          key={h}
                          className="pb-3 text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o, i) => (
                    <tr
                      key={`${o.instrument}-${o.type}-${i}`}
                      className="border-b border-[var(--border-gray)] last:border-b-0"
                    >
                      <td className="py-3 text-[11px] font-semibold text-[var(--text-primary)]">
                        {o.instrument}
                      </td>
                      <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                        {o.type}
                      </td>
                      <td className="py-3">
                        <span
                          className={`text-[11px] font-bold ${
                            o.side === "BUY"
                              ? "text-[var(--accent-green)]"
                              : "text-[var(--accent-red)]"
                          }`}
                        >
                          {o.side}
                        </span>
                      </td>
                      <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                        {o.price}
                      </td>
                      <td className="py-3 text-[11px] text-[var(--text-secondary)]">
                        {o.qty}
                      </td>
                      <td className="py-3">
                        <Badge
                          variant={o.status === "OPEN" ? "success" : "warning"}
                          dot={false}
                        >
                          {o.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Risk Metrics */}
          <Card>
            <span className="text-xs font-bold tracking-[0.5px] text-[var(--text-primary)]">
              {t("live.riskMetrics")}
            </span>
            <div className="mt-4 flex flex-col gap-3">
              {riskMetrics.map((m) => (
                <div
                  key={m.label}
                  className="flex items-center justify-between"
                >
                  <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                    {m.label}
                  </span>
                  <span className="text-[11px] font-semibold text-[var(--text-primary)]">
                    {m.value}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
