"use client";

import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard, FlaskConical, Activity, Brain,
  Database, BarChart3, Eye, ArrowUpDown, Settings2, Settings,
  ChevronLeft, ChevronRight,
} from "lucide-react";
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip";
import { useWsConnection } from "@/providers/WebSocketProvider";

type NavItem = { href: string; label: string; icon: React.ComponentType<{ className?: string }> };

const navGroups: { title: string; items: NavItem[] }[] = [
  {
    title: "Core",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/backtest", label: "Backtests", icon: FlaskConical },
    ],
  },
  {
    title: "Trading",
    items: [
      { href: "/trading", label: "Trading Terminal", icon: Activity },
      { href: "/strategies", label: "Strategies", icon: Brain },
    ],
  },
  {
    title: "Data",
    items: [
      { href: "/data-catalog", label: "Data Catalog", icon: Database },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/watchlist", label: "Watchlist", icon: Eye },
    ],
  },
  {
    title: "System",
    items: [
      { href: "/orders", label: "Orders", icon: ArrowUpDown },
      { href: "/optimization", label: "Optimization", icon: Settings2 },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

const LS_KEY = "sidebar-collapsed";

export function Sidebar() {
  const pathname = usePathname();
  const { connected, reconnecting } = useWsConnection();
  const [collapsed, setCollapsed] = useState(false);

  // Hydrate from localStorage after mount
  useEffect(() => {
    const stored = localStorage.getItem(LS_KEY);
    if (stored === "true") setCollapsed(true);
  }, []);

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(LS_KEY, String(next));
      return next;
    });
  };

  const wsColor = connected
    ? "bg-qds-success"
    : reconnecting
      ? "bg-qds-warning animate-pulse"
      : "bg-destructive";
  const wsLabel = connected ? "WS connected" : reconnecting ? "Reconnecting..." : "WS disconnected";

  return (
    <aside
      className="flex flex-col h-full shrink-0 bg-input border-r overflow-hidden z-50"
      style={{
        width: collapsed ? 56 : 220,
        minWidth: collapsed ? 56 : 220,
        transition: `width var(--dur) var(--eo), min-width var(--dur) var(--eo)`,
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-2.5 border-b overflow-hidden shrink-0"
        style={{ height: 48, padding: collapsed ? "0 16px" : "0 16px" }}
      >
        <div className="w-6 h-6 rounded-sm bg-primary flex items-center justify-center shrink-0">
          <span className="font-mono text-[0.7rem] font-semibold text-white">T</span>
        </div>
        <span
          className="font-mono text-[0.9rem] font-semibold text-foreground whitespace-nowrap overflow-hidden"
          style={{
            opacity: collapsed ? 0 : 1,
            transition: `opacity var(--dur) var(--eo)`,
          }}
        >
          TinoHelm<span className="text-primary">.</span>
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 flex flex-col gap-0.5 py-3 px-2 overflow-y-auto">
        {navGroups.map((group, gi) => (
          <div key={group.title} className={gi > 0 ? "mt-3" : ""}>
            {/* Group label */}
            <div
              className="font-mono text-[0.58rem] tracking-[0.12em] uppercase text-qds-t3 whitespace-nowrap overflow-hidden"
              style={{
                padding: collapsed ? "0 0 6px" : "0 8px 6px",
                opacity: collapsed ? 0 : 1,
                height: collapsed ? 0 : "auto",
                transition: `opacity var(--dur) var(--eo), height var(--dur) var(--eo)`,
              }}
            >
              {group.title}
            </div>

            {group.items.map((item) => {
              const isActive =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href));

              const linkContent = (
                <Link
                  href={item.href}
                  className={[
                    "flex items-center gap-2.5 rounded-sm whitespace-nowrap overflow-hidden relative",
                    "text-[0.82rem] border-l-[3px] border-transparent",
                    "transition-all duration-150",
                    isActive
                      ? "text-primary border-l-primary bg-qds-accent-dim"
                      : "text-qds-t1 hover:bg-secondary hover:text-foreground",
                  ].join(" ")}
                  style={{
                    padding: collapsed ? "8px 14px" : "8px 10px",
                  }}
                >
                  <item.icon
                    className={`w-[18px] h-[18px] shrink-0 ${isActive ? "opacity-100" : "opacity-60"}`}
                  />
                  <span
                    className="whitespace-nowrap overflow-hidden"
                    style={{
                      opacity: collapsed ? 0 : 1,
                      width: collapsed ? 0 : "auto",
                      transition: `opacity var(--dur) var(--eo)`,
                    }}
                  >
                    {item.label}
                  </span>
                </Link>
              );

              if (collapsed) {
                return (
                  <Tooltip key={item.href}>
                    <TooltipTrigger render={linkContent}>
                      {null}
                    </TooltipTrigger>
                    <TooltipContent side="right" sideOffset={8}>
                      <p className="text-xs font-medium">{item.label}</p>
                    </TooltipContent>
                  </Tooltip>
                );
              }

              return <div key={item.href}>{linkContent}</div>;
            })}
          </div>
        ))}
      </nav>

      {/* Bottom section */}
      <div className="flex flex-col gap-1 p-2 border-t shrink-0">
        {/* WS status */}
        <div className="flex items-center gap-2 px-2.5 py-1 font-mono text-[0.65rem] text-muted-foreground whitespace-nowrap overflow-hidden">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${wsColor}`} />
          <span
            style={{
              opacity: collapsed ? 0 : 1,
              transition: `opacity var(--dur) var(--eo)`,
            }}
          >
            {wsLabel}
          </span>
        </div>

        {/* Collapse toggle */}
        <button
          onClick={toggle}
          className="flex items-center justify-center p-2 text-muted-foreground hover:text-foreground transition-colors duration-150 cursor-pointer"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      </div>
    </aside>
  );
}
