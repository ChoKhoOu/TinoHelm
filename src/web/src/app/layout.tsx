import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Providers } from "@/components/Providers";
import { PageTransition } from "@/components/motion/PageTransition";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "TinoHelm — 量化交易平台",
  description: "基于 NautilusTrader 的量化交易平台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh" className="h-full dark">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="h-full bg-background text-foreground font-mono antialiased">
        <Providers>
          <div className="flex h-full">
            <Sidebar />
            <div className="flex-1 flex flex-col overflow-hidden">
              <TopBar />
              <ErrorBoundary>
                <main className="flex-1 overflow-auto">
                  <PageTransition>
                    {children}
                  </PageTransition>
                </main>
              </ErrorBoundary>
            </div>
          </div>
          <Toaster position="top-right" richColors />
        </Providers>
      </body>
    </html>
  );
}
