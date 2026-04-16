import type React from "react";

/**
 * QDS Chart Theme — Recharts 配色
 *
 * 使用 CSS 变量引用，自动跟随 dark/light 主题切换。
 * Recharts 的 inline style 直接消费 var()，无需 JS 判断主题。
 */

/* === Axis Tick Style === */
export const CHART_AXIS_STYLE = {
  fontSize: 10,
  fill: "var(--chart-tick)",
  fontFamily: "var(--font-d)",
};

/* === Tooltip Styles === */
export const CHART_TOOLTIP_STYLE: React.CSSProperties = {
  backgroundColor: "var(--bg-p)",
  border: "1px solid var(--bd)",
  borderRadius: "8px",
  fontSize: ".72rem",
  fontFamily: "var(--font-d)",
  color: "var(--t0)",
  padding: "10px",
};

export const CHART_TOOLTIP_LABEL_STYLE: React.CSSProperties = {
  color: "var(--t2)",
  fontFamily: "var(--font-d)",
  fontWeight: 600,
};

export const CHART_TOOLTIP_ITEM_STYLE: React.CSSProperties = {
  color: "var(--t0)",
  fontFamily: "var(--font-d)",
};

/** Spread on Recharts <Tooltip> for consistent QDS styling */
export const CHART_TOOLTIP_PROPS = {
  contentStyle: CHART_TOOLTIP_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
  cursor: { fill: "var(--bg-t)", opacity: 0.3 },
};

/* === Grid Style === */
export const CHART_GRID_STYLE = {
  stroke: "var(--chart-grid)",
  strokeDasharray: "none" as const,
};

/* === Semantic Colors === */
export const CHART_COLORS = {
  accent: "var(--acc)",
  success: "var(--suc)",
  danger: "var(--dan)",
  info: "var(--info)",
  warning: "var(--warn)",
};

/* === Gradient Definitions (common patterns) === */
export const CHART_GRADIENT_OPACITY = {
  /** Equity curve fill — matches ref hex alpha 0x12 ≈ 7% */
  equityFill: 0.07,
  /** Area chart standard fill */
  areaFill: 0.12,
  /** Danger zone fill (drawdown) */
  dangerFill: 0.25,
};

/* === Reference Line Style === */
export const CHART_REFERENCE_LINE = {
  stroke: "var(--warn)",
  strokeDasharray: "4 4",
  strokeOpacity: 0.4,
};

/* === Animation Defaults === */
export const CHART_ANIMATION = {
  duration: 800,
  easing: "ease-out" as const,
};
