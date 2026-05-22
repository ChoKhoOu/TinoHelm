import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Providers } from "@/components/Providers";
import { PageTransition } from "@/components/motion/PageTransition";
import { StatusBar } from "@/components/StatusBar";
import { Toaster } from "@/components/ui/sonner";
import { NotificationListener } from "@/components/NotificationListener";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

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
    <html lang="zh" className={`h-full ${inter.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <head>
        {/* FOUC prevention: set theme class before paint */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{if(localStorage.getItem('theme')==='light')document.documentElement.classList.add('light')}catch(e){}})()`,
          }}
        />
      </head>
      <body className="h-full bg-background text-foreground antialiased">
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
              <StatusBar />
            </div>
          </div>
          <Toaster position="bottom-right" />
          <NotificationListener />
        </Providers>
      </body>
    </html>
  );
}
