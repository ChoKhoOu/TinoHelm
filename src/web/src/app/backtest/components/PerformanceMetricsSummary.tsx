"use client";

import type { BacktestResult, DrawdownPeriod } from "../types";
import { ChartCard, HelpTip, fmtNum } from "./PerformanceHelpers";

interface MetricRow {
  label: string;
  tooltip: string;
  value: string;
  color?: string;
}

export function PerformanceMetricsSummary({
  statistics: s,
  drawdownPeriods,
  benchmarkType,
}: {
  statistics: BacktestResult["statistics"];
  drawdownPeriods?: DrawdownPeriod[];
  benchmarkType?: string;
}) {
  const worstDD = drawdownPeriods?.[0];

  const categories: {
    title: string;
    rows: MetricRow[];
    hidden?: boolean;
  }[] = [
    {
      title: "风险调整收益",
      rows: [
        {
          label: "Sharpe Ratio",
          tooltip: "年化超额收益 / 年化波动率。>1 为良好，>2 为优秀",
          value: fmtNum(s.sharpe_ratio),
          color:
            s.sharpe_ratio != null
              ? s.sharpe_ratio >= 1
                ? "var(--suc)"
                : s.sharpe_ratio >= 0
                  ? "var(--t0)"
                  : "var(--dan)"
              : undefined,
        },
        {
          label: "Sortino Ratio",
          tooltip: "年化超额收益 / 下行波动率。只惩罚负收益，比 Sharpe 更公平",
          value: fmtNum(s.sortino_ratio),
          color:
            s.sortino_ratio != null
              ? s.sortino_ratio >= 1
                ? "var(--suc)"
                : s.sortino_ratio >= 0
                  ? "var(--t0)"
                  : "var(--dan)"
              : undefined,
        },
        {
          label: "Calmar Ratio",
          tooltip: "年化收益 / 最大回撤。衡量收益相对于最坏情况的能力",
          value: fmtNum(s.calmar_ratio),
          color:
            s.calmar_ratio != null
              ? s.calmar_ratio >= 1
                ? "var(--suc)"
                : s.calmar_ratio >= 0
                  ? "var(--t0)"
                  : "var(--dan)"
              : undefined,
        },
        {
          label: "Omega Ratio",
          tooltip: "收益概率加权的积极/消极比。>1 表示收益分布偏向盈利",
          value: fmtNum(s.omega_ratio),
          color:
            s.omega_ratio != null
              ? s.omega_ratio >= 1
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
      ],
    },
    {
      title: "回撤与风险",
      rows: [
        {
          label: "最大回撤",
          tooltip: "峰值到谷底的最大跌幅百分比",
          value: s.max_drawdown != null ? `${s.max_drawdown.toFixed(2)}%` : "N/A",
          color: "var(--dan)",
        },
        {
          label: "最大回撤持续",
          tooltip: "最严重回撤从开始到谷底经历的天数",
          value: worstDD ? `${worstDD.duration_days} 天` : "N/A",
        },
        {
          label: "恢复时间",
          tooltip: "最严重回撤从谷底恢复到新高的天数",
          value:
            worstDD?.recovery_days != null
              ? `${worstDD.recovery_days} 天`
              : "未恢复",
          color: worstDD?.recovery_days == null ? "var(--dan)" : undefined,
        },
        {
          label: "VaR (95%)",
          tooltip: "95% 置信度下单日最大预期亏损",
          value: s.var_95 != null ? `${s.var_95.toFixed(2)}%` : "N/A",
          color: "var(--dan)",
        },
        {
          label: "VaR (99%)",
          tooltip: "99% 置信度下单日最大预期亏损",
          value: s.var_99 != null ? `${s.var_99.toFixed(2)}%` : "N/A",
          color: "var(--dan)",
        },
        {
          label: "CVaR (95%)",
          tooltip: "条件在险价值，超过 VaR 时的平均损失",
          value: s.cvar_95 != null ? `${s.cvar_95.toFixed(2)}%` : "N/A",
          color: "var(--dan)",
        },
        {
          label: "下行偏差",
          tooltip: "仅计算负收益的标准差，衡量下行风险",
          value:
            s.downside_deviation != null
              ? `${(s.downside_deviation * 100).toFixed(2)}%`
              : "N/A",
        },
        {
          label: "Ulcer Index",
          tooltip: "基于回撤深度和持续时间的风险指标，越低越好",
          value: fmtNum(s.ulcer_index, 4),
        },
      ],
    },
    {
      title: "分布特征",
      rows: [
        {
          label: "偏度 (Skewness)",
          tooltip: "收益分布的不对称性。正偏=右尾更长，负偏=左尾更长",
          value: fmtNum(s.skewness, 3),
          color:
            s.skewness != null
              ? s.skewness > 0
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
        {
          label: "峰度 (Kurtosis)",
          tooltip:
            "超额峰度，衡量尾部厚度。>0 表示极端事件概率高于正态分布",
          value: fmtNum(s.kurtosis, 3),
        },
        {
          label: "尾部比率 (Tail Ratio)",
          tooltip: "95%分位 / |5%分位|，>1 表示上行尾部更厚",
          value: fmtNum(s.tail_ratio, 3),
          color:
            s.tail_ratio != null
              ? s.tail_ratio > 1
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
        {
          label: "稳定性 (R²)",
          tooltip: "累计收益对线性回归的拟合度，越接近 1 越稳定",
          value: fmtNum(s.stability, 4),
          color:
            s.stability != null
              ? s.stability > 0.8
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
        {
          label: "最大单日亏损",
          tooltip: "回测期间单日最大亏损率",
          value:
            s.max_daily_loss != null
              ? `${s.max_daily_loss.toFixed(2)}%`
              : "N/A",
          color: "var(--dan)",
        },
      ],
    },
    {
      title: "基准相对指标",
      hidden: benchmarkType === "zero_line",
      rows: [
        {
          label: "Alpha",
          tooltip: "超越基准的年化超额收益",
          value: s.alpha != null ? `${(s.alpha * 100).toFixed(2)}%` : "N/A",
          color:
            s.alpha != null
              ? s.alpha > 0
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
        {
          label: "Beta",
          tooltip: "相对基准的系统性风险敞口。1=同步，<1=防御，>1=激进",
          value: fmtNum(s.beta, 3),
        },
        {
          label: "R²",
          tooltip: "策略收益被基准解释的比例。越高表示越像基准",
          value: fmtNum(s.r_squared, 4),
        },
        {
          label: "Information Ratio",
          tooltip: "超额收益 / 跟踪误差。衡量主动管理的效率",
          value: fmtNum(s.information_ratio, 3),
          color:
            s.information_ratio != null
              ? s.information_ratio > 0
                ? "var(--suc)"
                : "var(--dan)"
              : undefined,
        },
      ],
    },
  ];

  return (
    <ChartCard>
      <div className="grid grid-cols-2 gap-6">
        {categories
          .filter((cat) => !cat.hidden)
          .map((cat) => (
            <div key={cat.title}>
              <span className="inline-flex items-center font-mono text-[0.55rem] tracking-widest uppercase text-primary">
                {cat.title}
              </span>
              <div className="flex flex-col">
                {cat.rows.map((row) => (
                  <div
                    key={row.label}
                    className="flex items-center justify-between py-1.5 border-b border-border last:border-b-0"
                  >
                    <span className="text-xs text-muted-foreground inline-flex items-center gap-0.5">
                      {row.label}
                      <HelpTip text={row.tooltip} />
                    </span>
                    <span
                      className="text-xs font-medium font-mono"
                      style={{ color: row.color ?? "var(--t0)" }}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
      </div>
    </ChartCard>
  );
}
