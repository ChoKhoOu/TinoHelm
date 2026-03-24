"use client";

import { I18nProvider } from "@/i18n";
import { WebSocketProvider } from "@/providers/WebSocketProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LazyMotion, domAnimation } from "framer-motion";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <LazyMotion features={domAnimation}>
      <I18nProvider>
        <WebSocketProvider>
          <TooltipProvider delay={0}>
            {children}
          </TooltipProvider>
        </WebSocketProvider>
      </I18nProvider>
    </LazyMotion>
  );
}
