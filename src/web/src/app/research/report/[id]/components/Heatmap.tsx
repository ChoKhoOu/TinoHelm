import { Fragment } from "react";

interface HeatmapProps {
  xLabels: string[];
  yLabels: string[];
  values: number[][];
}

/**
 * 2D cell heatmap with opacity-scaled green/red cells. Used by the cost-params
 * parameter sweep view. Grid template columns are set inline because they depend
 * on the dynamic xLabels length.
 */
export function Heatmap({ xLabels, yLabels, values }: HeatmapProps) {
  const maxAbs = Math.max(...values.flat().map((v) => Math.abs(v)), 0.001);

  function cellColor(v: number): string {
    const opacity = Math.min(Math.abs(v) / maxAbs, 1) * 0.7 + 0.15;
    if (v > 0) return `rgba(54,136,75,${opacity.toFixed(2)})`;
    if (v < 0) return `rgba(254,129,129,${opacity.toFixed(2)})`;
    return "rgba(128,128,128,0.15)";
  }

  return (
    <div
      className="font-mono text-[0.58rem]"
      style={{
        display: "grid",
        gridTemplateColumns: `auto repeat(${xLabels.length}, 1fr)`,
        gap: 2,
      }}
    >
      {/* top-left corner */}
      <div />
      {/* x header */}
      {xLabels.map((x) => (
        <div
          key={x}
          className="text-center text-muted-foreground flex items-center justify-center text-[0.55rem]"
          style={{ padding: "0.25rem 0.2rem" }}
        >
          {x}
        </div>
      ))}
      {/* rows */}
      {yLabels.map((y, ri) => (
        <Fragment key={`row-${y}`}>
          <div
            className="text-muted-foreground flex items-center justify-center text-[0.55rem]"
            style={{ padding: "0.25rem 0.35rem" }}
          >
            {y}
          </div>
          {xLabels.map((x, ci) => {
            const v = values[ri]?.[ci] ?? 0;
            return (
              <div
                key={`${y}-${x}`}
                className="text-center rounded-sm font-medium cursor-default transition-transform duration-150 hover:scale-110 hover:z-10 text-foreground"
                style={{
                  padding: "0.3rem 0.2rem",
                  background: cellColor(v),
                }}
                title={`${y} x ${x}: ${v.toFixed(3)}`}
              >
                {v.toFixed(2)}
              </div>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}
