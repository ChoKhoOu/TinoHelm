"use client";

import { useEffect, useState } from "react";

type ExchangeStatus = {
  name: string;
  connected: boolean;
  latency?: number;
};

const exchanges: ExchangeStatus[] = [
  { name: "Binance", connected: true, latency: 2 },
  { name: "Hyperliquid", connected: true, latency: 8 },
  { name: "OKX", connected: false, latency: 42 },
];

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

export function StatusBar() {
  const clock = useClock();

  return (
    <footer
      className="flex items-center px-6 bg-input border-t font-mono text-[0.65rem] text-qds-t3 gap-6 overflow-hidden shrink-0"
      style={{ height: 28, minHeight: 28 }}
    >
      {/* Exchange connection dots */}
      {exchanges.map((ex) => (
        <div key={ex.name} className="flex items-center gap-1.5 whitespace-nowrap">
          <span
            className="w-[5px] h-[5px] rounded-full"
            style={{
              background: ex.connected ? "var(--suc)" : "var(--warn)",
            }}
          />
          <span>
            {ex.name} {ex.latency != null ? `${ex.latency}ms` : ""}
          </span>
        </div>
      ))}

      {/* System stats — separated by border */}
      <div className="flex items-center gap-6 whitespace-nowrap border-l pl-6">
        <span>Mem <span className="text-qds-t1">4.2G</span></span>
        <span>CPU <span className="text-qds-t1">12%</span></span>
      </div>

      {/* Clock — right-aligned */}
      <span className="ml-auto whitespace-nowrap">{clock}</span>
    </footer>
  );
}
