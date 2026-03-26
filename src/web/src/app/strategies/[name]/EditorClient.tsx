"use client";

import { Code, Save, Rocket, FileText, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState } from "react";

const files = [
  { name: "ema_cross_demo.py", active: true },
  { name: "config.json", active: false },
  { name: "requirements.txt", active: false },
  { name: "backtest_config.yaml", active: false },
];

const codeLines = [
  { text: "# ema_cross_demo.py", color: "text-muted-foreground" },
  {
    text: "from nautilus_trader.trading import Strategy",
    parts: [
      { text: "from", color: "text-destructive" },
      { text: " nautilus_trader.trading ", color: "text-foreground" },
      { text: "import", color: "text-destructive" },
      { text: " Strategy", color: "text-[var(--accent-green)]" },
    ],
  },
  {
    text: "from nautilus_trader.config import StrategyConfig",
    parts: [
      { text: "from", color: "text-destructive" },
      { text: " nautilus_trader.config ", color: "text-foreground" },
      { text: "import", color: "text-destructive" },
      { text: " StrategyConfig", color: "text-[var(--accent-green)]" },
    ],
  },
  { text: "", color: "" },
  {
    text: "class EmaCrossConfig(StrategyConfig):",
    parts: [
      { text: "class", color: "text-destructive" },
      { text: " EmaCrossConfig", color: "text-primary" },
      { text: "(", color: "text-foreground" },
      { text: "StrategyConfig", color: "text-[var(--accent-green)]" },
      { text: "):", color: "text-foreground" },
    ],
  },
  {
    text: "    instrument_id: str",
    parts: [
      { text: "    instrument_id", color: "text-foreground" },
      { text: ": ", color: "text-muted-foreground" },
      { text: "str", color: "text-[var(--accent-orange)]" },
    ],
  },
  {
    text: "    fast_ema: int = 10",
    parts: [
      { text: "    fast_ema", color: "text-foreground" },
      { text: ": ", color: "text-muted-foreground" },
      { text: "int", color: "text-[var(--accent-orange)]" },
      { text: " = ", color: "text-muted-foreground" },
      { text: "10", color: "text-[var(--accent-green)]" },
    ],
  },
  {
    text: "    slow_ema: int = 21",
    parts: [
      { text: "    slow_ema", color: "text-foreground" },
      { text: ": ", color: "text-muted-foreground" },
      { text: "int", color: "text-[var(--accent-orange)]" },
      { text: " = ", color: "text-muted-foreground" },
      { text: "21", color: "text-[var(--accent-green)]" },
    ],
  },
  { text: "", color: "" },
  {
    text: "class EmaCrossDemo(Strategy):",
    parts: [
      { text: "class", color: "text-destructive" },
      { text: " EmaCrossDemo", color: "text-primary" },
      { text: "(", color: "text-foreground" },
      { text: "Strategy", color: "text-[var(--accent-green)]" },
      { text: "):", color: "text-foreground" },
    ],
  },
  {
    text: "    def on_start(self):",
    parts: [
      { text: "    ", color: "" },
      { text: "def", color: "text-destructive" },
      { text: " on_start", color: "text-primary" },
      { text: "(", color: "text-foreground" },
      { text: "self", color: "text-[var(--accent-orange)]" },
      { text: "):", color: "text-foreground" },
    ],
  },
  { text: "        ...", color: "text-muted-foreground" },
];

const terminalLines = [
  { text: "$ python -m nautilus_trader.backtest ema_cross_demo", color: "text-[var(--accent-green)]" },
  { text: "[INFO] Loading strategy: EmaCrossDemo", color: "text-muted-foreground" },
  { text: "[INFO] Instrument: ETHUSDT-PERP.BINANCE", color: "text-muted-foreground" },
  { text: "[INFO] Fast EMA: 10 | Slow EMA: 21", color: "text-muted-foreground" },
  { text: "[OK] Strategy compiled successfully.", color: "text-[var(--accent-green)]" },
];

export default function EditorClient() {
  const [activeFile, setActiveFile] = useState("ema_cross_demo.py");

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between bg-card border-b border-border p-[12px] px-5">
        <div className="flex items-center gap-3">
          <Code className="w-4 h-4 text-[var(--accent-green)]" />
          <span className="font-heading text-[18px] font-bold text-foreground">
            STRATEGY EDITOR
          </span>
          <span className="rounded-md bg-popover px-[10px] py-1 text-[11px] font-medium text-muted-foreground">
            ema_cross_demo.py
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" className="inline-flex items-center gap-1.5 rounded-lg bg-card border border-border px-5 py-[10px] text-[11px] font-bold tracking-wide text-muted-foreground hover:border-input transition-all duration-150">
            <Save className="w-3 h-3" />
            SAVE
          </Button>
          <Button className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-primary-foreground px-5 py-[10px] text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150">
            <Rocket className="w-3 h-3" />
            DEPLOY
          </Button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        <div className="w-[200px] bg-card border-r border-border flex flex-col">
          <div className="px-4 py-3 text-[10px] font-semibold tracking-[0.5px] text-muted-foreground">
            EXPLORER
          </div>
          <div className="flex flex-col">
            {files.map((f) => (
              <Button
                key={f.name}
                variant="ghost"
                onClick={() => setActiveFile(f.name)}
                className={`w-full justify-start h-auto flex items-center gap-2 px-4 py-[8px] text-[11px] font-medium transition-colors duration-150 ${
                  activeFile === f.name
                    ? "bg-[var(--accent-green-10)] text-[var(--accent-green)]"
                    : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <FileText className="w-3.5 h-3.5 shrink-0" />
                {f.name}
              </Button>
            ))}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 bg-[#0D0D14] p-4 overflow-auto">
            <pre className="text-[12px] leading-[22px]">
              {codeLines.map((line, i) => (
                <div key={i} className="flex">
                  <span className="w-8 shrink-0 text-right pr-4 text-muted-foreground select-none text-[11px]">
                    {i + 1}
                  </span>
                  {"parts" in line && line.parts ? (
                    <span>
                      {line.parts.map((p, j) => (
                        <span key={j} className={p.color}>
                          {p.text}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className={line.color}>{line.text}</span>
                  )}
                </div>
              ))}
            </pre>
          </div>

          <div className="h-[160px] bg-card border-t border-border flex flex-col">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-border">
              <Terminal className="w-3 h-3 text-muted-foreground" />
              <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground">
                TERMINAL
              </span>
            </div>
            <div className="flex-1 overflow-auto px-4 py-2">
              {terminalLines.map((line, i) => (
                <div key={i} className={`text-[11px] leading-[20px] ${line.color}`}>
                  {line.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
