"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { ROUTING_TABLE, shouldDedupe, formatToastMessage } from "@/lib/notification-router";

// Events that should trigger toast notifications
const TOAST_EVENTS = Object.entries(ROUTING_TABLE)
  .filter(([, r]) => r.channel === "toast")
  .map(([key]) => key);

function ToastListener({ eventType }: { eventType: string }) {
  const event = useWsEvent(eventType);
  const lastRef = useRef<string | null>(null);

  useEffect(() => {
    if (!event) return;
    // Dedupe by stringified event to avoid re-firing on re-render
    const key = JSON.stringify(event);
    if (key === lastRef.current) return;
    lastRef.current = key;

    const route = ROUTING_TABLE[eventType];
    if (!route || route.channel !== "toast") return;
    if (shouldDedupe(eventType, event)) return;

    const { title, description } = formatToastMessage(eventType, event);

    switch (route.type) {
      case "success": toast.success(title, { description }); break;
      case "error":   toast.error(title, { description }); break;
      case "warning": toast.warning(title, { description }); break;
      case "info":    toast.info(title, { description }); break;
      default:        toast(title, { description });
    }
  }, [event, eventType]);

  return null;
}

export function NotificationListener() {
  return (
    <>
      {TOAST_EVENTS.map((et) => (
        <ToastListener key={et} eventType={et} />
      ))}
    </>
  );
}
