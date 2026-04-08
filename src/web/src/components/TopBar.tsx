"use client";

import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/ThemeToggle";

const pathTitles: Record<string, string> = {
  "/": "Dashboard",
  "/trading": "Trading Terminal",
  "/backtest": "Backtests",
  "/strategies": "Strategies",
  "/data-catalog": "Data Catalog",
  "/analytics": "Analytics",
  "/orders": "Orders",
  "/watchlist": "Watchlist",
  "/optimization": "Optimization",
  "/settings": "Settings",
  "/research": "Factor Research",
};

export function TopBar() {
  const pathname = usePathname();

  const title =
    pathTitles[pathname] ??
    Object.entries(pathTitles).find(
      ([k]) => k !== "/" && pathname.startsWith(k),
    )?.[1] ??
    "Tino.Helm";

  return (
    <header
      className="flex items-center justify-between shrink-0 px-6 border-b bg-background"
      style={{ height: 48, minHeight: 48 }}
    >
      {/* Breadcrumb */}
      <div className="font-mono text-[0.75rem] text-muted-foreground flex items-center gap-1.5">
        <span>Tino.Helm</span>
        <span className="opacity-40">/</span>
        <span className="text-foreground font-medium">{title}</span>
      </div>

      {/* Right actions */}
      <div className="flex items-center gap-4">
        <ThemeToggle />
      </div>
    </header>
  );
}
