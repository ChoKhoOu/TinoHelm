"use client";

import { Code, Save, Rocket, FileText, Terminal } from "lucide-react";
import { useState } from "react";

const files = [
  { name: "ema_cross_demo.py", active: true },
  { name: "config.json", active: false },
  { name: "requirements.txt", active: false },
  { name: "backtest_config.yaml", active: false },
];

const codeLines = [
  { text: "# ema_cross_demo.py", color: "text-[var(--text-muted)]" },
  {
    text: "from nautilus_trader.trading import Strategy",
    parts: [
      { text: "from", color: "text-[var(--accent-red)]" },
      { text: " nautilus_trader.trading ", color: "text-[var(--text-primary)]" },
      { text: "import", color: "text-[var(--accent-red)]" },
      { text: " Strategy", color: "text-[var(--accent-green)]" },
    ],
  },
  {
    text: "from nautilus_trader.config import StrategyConfig",
    parts: [
      { text: "from", color: "text-[var(--accent-red)]" },
      { text: " nautilus_trader.config ", color: "text-[var(--text-primary)]" },
      { text: "import", color: "text-[var(--accent-red)]" },
      { text: " StrategyConfig", color: "text-[var(--accent-green)]" },
    ],
  },
  { text: "", color: "" },
  {
    text: "class EmaCrossConfig(StrategyConfig):",
    parts: [
      { text: "class", color: "text-[var(--accent-red)]" },
      { text: " EmaCrossConfig", color: "text-[var(--accent-blue)]" },
      { text: "(", color: "text-[var(--text-primary)]" },
      { text: "StrategyConfig", color: "text-[var(--accent-green)]" },
      { text: "):", color: "text-[var(--text-primary)]" },
    ],
  },
  {
    text: "    instrument_id: str",
    parts: [
      { text: "    instrument_id", color: "text-[var(--text-primary)]" },
      { text: ": ", color: "text-[var(--text-muted)]" },
      { text: "str", color: "text-[var(--accent-orange)]" },
    ],
  },
  {
    text: "    fast_ema: int = 10",
    parts: [
      { text: "    fast_ema", color: "text-[var(--text-primary)]" },
      { text: ": ", color: "text-[var(--text-muted)]" },
      { text: "int", color: "text-[var(--accent-orange)]" },
      { text: " = ", color: "text-[var(--text-muted)]" },
      { text: "10", color: "text-[var(--accent-green)]" },
    ],
  },
  {
    text: "    slow_ema: int = 21",
    parts: [
      { text: "    slow_ema", color: "text-[var(--text-primary)]" },
      { text: ": ", color: "text-[var(--text-muted)]" },
      { text: "int", color: "text-[var(--accent-orange)]" },
      { text: " = ", color: "text-[var(--text-muted)]" },
      { text: "21", color: "text-[var(--accent-green)]" },
    ],
  },
  { text: "", color: "" },
  {
    text: "class EmaCrossDemo(Strategy):",
    parts: [
      { text: "class", color: "text-[var(--accent-red)]" },
      { text: " EmaCrossDemo", color: "text-[var(--accent-blue)]" },
      { text: "(", color: "text-[var(--text-primary)]" },
      { text: "Strategy", color: "text-[var(--accent-green)]" },
      { text: "):", color: "text-[var(--text-primary)]" },
    ],
  },
  {
    text: "    def on_start(self):",
    parts: [
      { text: "    ", color: "" },
      { text: "def", color: "text-[var(--accent-red)]" },
      { text: " on_start", color: "text-[var(--accent-blue)]" },
      { text: "(", color: "text-[var(--text-primary)]" },
      { text: "self", color: "text-[var(--accent-orange)]" },
      { text: "):", color: "text-[var(--text-primary)]" },
    ],
  },
  { text: "        ...", color: "text-[var(--text-muted)]" },
];

const terminalLines = [
  { text: "$ python -m nautilus_trader.backtest ema_cross_demo", color: "text-[var(--accent-green)]" },
  { text: "[INFO] Loading strategy: EmaCrossDemo", color: "text-[var(--text-secondary)]" },
  { text: "[INFO] Instrument: ETHUSDT-PERP.BINANCE", color: "text-[var(--text-secondary)]" },
  { text: "[INFO] Fast EMA: 10 | Slow EMA: 21", color: "text-[var(--text-secondary)]" },
  { text: "[OK] Strategy compiled successfully.", color: "text-[var(--accent-green)]" },
];

export default function EditorClient() {
  const [activeFile, setActiveFile] = useState("ema_cross_demo.py");

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between bg-[var(--bg-card)] border-b border-[var(--border-gray)] p-[12px] px-5">
        <div className="flex items-center gap-3">
          <Code className="w-4 h-4 text-[var(--accent-green)]" />
          <span className="font-heading text-[18px] font-bold text-[var(--text-primary)]">
            STRATEGY EDITOR
          </span>
          <span className="rounded-md bg-[var(--bg-elevated)] px-[10px] py-1 text-[11px] font-medium text-[var(--text-secondary)]">
            ema_cross_demo.py
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-5 py-[10px] text-[11px] font-bold tracking-wide text-[var(--text-secondary)] hover:border-[var(--border-light)] transition-all duration-150">
            <Save className="w-3 h-3" />
            SAVE
          </button>
          <button className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-5 py-[10px] text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150">
            <Rocket className="w-3 h-3" />
            DEPLOY
          </button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        <div className="w-[200px] bg-[var(--bg-card)] border-r border-[var(--border-gray)] flex flex-col">
          <div className="px-4 py-3 text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
            EXPLORER
          </div>
          <div className="flex flex-col">
            {files.map((f) => (
              <button
                key={f.name}
                onClick={() => setActiveFile(f.name)}
                className={`flex items-center gap-2 px-4 py-[8px] text-[11px] font-medium transition-colors duration-150 text-left ${
                  activeFile === f.name
                    ? "bg-[var(--accent-green-10)] text-[var(--accent-green)]"
                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)]"
                }`}
              >
                <FileText className="w-3.5 h-3.5 shrink-0" />
                {f.name}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 bg-[#0D0D14] p-4 overflow-auto">
            <pre className="text-[12px] leading-[22px]">
              {codeLines.map((line, i) => (
                <div key={i} className="flex">
                  <span className="w-8 shrink-0 text-right pr-4 text-[var(--text-muted)] select-none text-[11px]">
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

          <div className="h-[160px] bg-[var(--bg-card)] border-t border-[var(--border-gray)] flex flex-col">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--border-gray)]">
              <Terminal className="w-3 h-3 text-[var(--text-muted)]" />
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
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
