"use client";

import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { Search, Bell } from "lucide-react";
import { useWsConnection } from "@/providers/WebSocketProvider";

const pathTitles: Record<string, string> = {
  "/": "仪表盘",
  "/trading": "交易终端",
  "/backtest": "回测分析",
  "/strategies": "策略管理",
  "/data-catalog": "数据目录",
  "/analytics": "绩效分析",
  "/orders": "订单历史",
  "/watchlist": "观察列表",
  "/optimization": "参数优化",
  "/settings": "系统设置",
};

export function TopBar() {
  const pathname = usePathname();
  const { connected, reconnecting } = useWsConnection();
  const [clock, setClock] = useState("");

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setClock(now.toLocaleTimeString("zh-CN", { hour12: false }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const title = pathTitles[pathname]
    ?? Object.entries(pathTitles).find(([k]) => k !== "/" && pathname.startsWith(k))?.[1]
    ?? "TinoHelm";

  const wsColor = connected
    ? "bg-[var(--accent-green)]"
    : reconnecting
      ? "bg-[var(--accent-amber)] animate-pulse"
      : "bg-[var(--accent-red)]";

  const wsLabel = connected ? "已连接" : reconnecting ? "重连中..." : "已断开";

  return (
    <header className="flex items-center justify-between h-[52px] px-6 border-b border-[var(--border-gray)] shrink-0">
      <h1 className="font-heading text-base font-semibold text-[var(--text-primary)]">{title}</h1>
      <div className="flex items-center gap-4">
        <button className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors">
          <Search className="w-4 h-4" />
        </button>
        <button className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors relative">
          <Bell className="w-4 h-4" />
        </button>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${wsColor}`} title={wsLabel} />
          <span className="text-[10px] font-mono text-[var(--text-muted)]">{wsLabel}</span>
        </div>
        <span className="text-[11px] font-mono text-[var(--text-muted)] tabular-nums">{clock}</span>
      </div>
    </header>
  );
}
