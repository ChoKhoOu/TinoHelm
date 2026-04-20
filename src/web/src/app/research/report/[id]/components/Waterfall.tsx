interface WaterfallItem {
  label: string;
  value: number;
  type: "positive" | "negative" | "net";
}

interface WaterfallProps {
  items: WaterfallItem[];
}

/** Horizontal waterfall bar chart for the cost-params edge decomposition. */
export function Waterfall({ items }: WaterfallProps) {
  const maxVal = Math.max(...items.map((i) => Math.abs(i.value)), 0.001);

  return (
    <div className="flex flex-col gap-1.5">
      {items.map((item) => {
        const widthPct = (Math.abs(item.value) / maxVal) * 100;
        const isPositive =
          item.type === "positive" || (item.type === "net" && item.value >= 0);
        return (
          <div key={item.label} className="flex items-center gap-2 font-mono text-[0.72rem]">
            <span className="w-20 text-right text-muted-foreground text-[0.68rem] shrink-0">
              {item.label}
            </span>
            <div className="flex-1 h-5 relative">
              <div
                className={`h-full rounded-sm transition-all duration-700 ${
                  isPositive ? "bg-qds-success" : "bg-destructive"
                }`}
                style={{ width: `${widthPct}%`, opacity: 0.7 }}
              />
            </div>
            <span
              className={`font-medium min-w-[70px] ${
                isPositive ? "text-qds-success" : "text-destructive"
              }`}
            >
              {item.value >= 0 ? "+" : ""}
              {(item.value * 100).toFixed(1)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}
