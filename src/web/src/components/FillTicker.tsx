"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useWsEvent } from "@/providers/WebSocketProvider";

interface FillEventPayload {
  instrument_id?: string;
  order_side?: string;
  last_qty?: string;
  last_px?: string;
}

export function FillTicker() {
  const router = useRouter();
  const fillEvent = useWsEvent("fill.new");
  const [display, setDisplay] = useState<{ text: string; side: string } | null>(null);
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!fillEvent) return;
    // WS payload is flat JSON (no data wrapper)
    const fill = fillEvent as FillEventPayload;

    // Format: strip .BINANCE suffix and -PERP suffix
    const sym = (fill.instrument_id ?? "").replace(/\.BINANCE$/, "").replace(/-PERP$/, "");
    const side = (fill.order_side ?? "").toUpperCase();
    const sign = side === "BUY" ? "+" : "-";
    const qty = parseFloat((fill.last_qty as string) || "0");
    const px = parseFloat((fill.last_px as string) || "0");
    const text = `${sym} ${sign}${qty} @ ${px.toLocaleString()}`;

    setDisplay({ text, side });
    setVisible(true);

    // Clear previous timer, set new 5s fade-out
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(false), 5000);
  }, [fillEvent]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!display) return null;

  return (
    <span
      onClick={() => router.push("/trading")}
      className={`cursor-pointer whitespace-nowrap font-mono ${
        display.side === "BUY" ? "text-qds-success" : "text-destructive"
      }`}
      style={{
        opacity: visible ? 1 : 0,
        transition: "opacity 200ms ease",
        borderLeft: "1px solid var(--bd)",
        paddingLeft: "1.5rem",
      }}
    >
      {display.text}
    </span>
  );
}
