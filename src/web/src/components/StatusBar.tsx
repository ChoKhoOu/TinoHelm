"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { FillTicker } from "@/components/FillTicker";

type ExchangeLatency = {
  name: string;
  latency_ms: number | null;
  reachable: boolean;
};

/** EMA smoothing over a 12-sample window (α = 2/(12+1) ≈ 0.1538). */
const EMA_ALPHA = 2 / (12 + 1);

function useClock() {
  const [now, setNow] = useState<string>("");
  useEffect(() => {
    const fmt = () => {
      const d = new Date();
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    setNow(fmt());
    const id = setInterval(() => setNow(fmt()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

const POLL_INTERVAL = 5_000; // 5s

export function StatusBar() {
  const clock = useClock();
  const [exchanges, setExchanges] = useState<ExchangeLatency[]>([]);
  const emaRef = useRef<Record<string, number>>({});

  const fetchLatency = useCallback(async () => {
    const data = await api<ExchangeLatency[]>("/api/exchanges/latency");
    if (!data) return;

    const smoothed = data.map((ex) => {
      if (ex.latency_ms == null) return ex;
      const prev = emaRef.current[ex.name];
      const ema = prev == null
        ? ex.latency_ms
        : EMA_ALPHA * ex.latency_ms + (1 - EMA_ALPHA) * prev;
      emaRef.current[ex.name] = ema;
      return { ...ex, latency_ms: ema };
    });
    setExchanges(smoothed);
  }, []);

  useEffect(() => {
    fetchLatency();
    const id = setInterval(fetchLatency, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchLatency]);

  return (
    <footer
      className="flex items-center px-6 bg-input border-t font-mono text-[0.65rem] text-qds-t3 gap-6 overflow-hidden shrink-0"
      style={{ height: 28, minHeight: 28 }}
    >
      {/* Exchange latency dots */}
      {exchanges.map((ex) => (
        <div key={ex.name} className="flex items-center gap-1.5 whitespace-nowrap">
          <span
            className="w-[5px] h-[5px] rounded-full"
            style={{
              background: ex.reachable ? "var(--suc)" : "var(--warn)",
            }}
          />
          <span>
            {ex.name} {ex.latency_ms != null ? `${Math.round(ex.latency_ms)}ms` : "—"}
          </span>
        </div>
      ))}

      {/* Fill ticker */}
      <FillTicker />

      {/* Clock — right-aligned */}
      <span className="ml-auto whitespace-nowrap">{clock}</span>
    </footer>
  );
}
