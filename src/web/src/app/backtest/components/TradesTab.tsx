"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { API_BASE } from "@/lib/api";
import type { BacktestResult } from "../types";
import { CARD_CLS, CARD_BODY_CLS, SectionTitle } from "./TradesHelpers";
import { MetricCard } from "./TradesMetricCard";
import {
  PnlDistributionChart,
  CumulativePnlChart,
  PnlScatterChart,
  MaeMfeChart,
  HoldingTimeChart,
  StreakChart,
  LongShortChart,
  ReturnByGroupChart,
} from "./TradesCharts";

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

interface TradesTabProps {
  runId: string;
}

export function TradesTab({ runId }: TradesTabProps) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);

    fetch(`${API_BASE}/api/backtest/${runId}/result`, {
      headers: {
        ...(process.env.NEXT_PUBLIC_API_KEY
          ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY }
          : {}),
      },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setResult(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-5">
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-xl" />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-destructive">{error ?? "加载失败"}</span>
      </div>
    );
  }

  const s = result.statistics;

  return (
    <div className="flex flex-col gap-4 p-5">

      {/* ── KPI Grid 4×3 ── */}
      <div className="grid grid-cols-4 gap-3">
        {/* Row 1 */}
        <MetricCard
          label="中位交易盈亏"
          tooltip="所有已平仓交易盈亏的中位数，比均值更能反映典型单笔表现"
          value={s.median_trade_pnl}
          showSign
          positive={s.median_trade_pnl != null ? s.median_trade_pnl >= 0 : null}
          suffix=" U"
          index={0}
        />
        <MetricCard
          label="盈亏标准差"
          tooltip="单笔交易盈亏的标准差，衡量收益的离散程度（波动性）"
          value={s.std_trade_pnl}
          positive={null}
          suffix=" U"
          index={1}
        />
        <MetricCard
          label="成交率"
          tooltip="实际成交订单数占提交订单总数的百分比"
          value={s.fill_rate}
          suffix="%"
          positive={s.fill_rate != null ? s.fill_rate >= 90 : null}
          index={2}
        />
        <MetricCard
          label="日均交易"
          tooltip="每个交易日平均完成的交易笔数"
          value={s.avg_trades_per_day}
          positive={null}
          index={3}
        />
        {/* Row 2 */}
        <MetricCard
          label="恢复系数"
          tooltip="净利润除以最大回撤绝对值，衡量策略从亏损中恢复的能力，> 3 为优秀"
          value={s.recovery_factor}
          positive={s.recovery_factor != null ? s.recovery_factor >= 3 : null}
          index={4}
        />
        <MetricCard
          label="SQN"
          tooltip="系统质量数 (System Quality Number)，> 2 可接受，> 3 优秀，> 5 卓越"
          value={s.sqn}
          positive={s.sqn != null ? s.sqn >= 2 : null}
          index={5}
        />
        <MetricCard
          label="凯利比例"
          tooltip="凯利准则建议的最优仓位比例，实际使用时通常取半凯利"
          value={s.kelly_criterion}
          suffix="%"
          positive={s.kelly_criterion != null ? s.kelly_criterion > 0 : null}
          index={6}
        />
        <MetricCard
          label="K-Ratio"
          tooltip="衡量权益曲线增长一致性的指标，越高代表越稳定的上升趋势"
          value={s.k_ratio}
          positive={s.k_ratio != null ? s.k_ratio > 0 : null}
          index={7}
        />
        {/* Row 3 */}
        <MetricCard
          label="期望值 (R)"
          tooltip="以初始风险 R 为单位的平均期望收益，> 0.2R 为较好策略"
          value={s.expectancy_r}
          showSign
          positive={s.expectancy_r != null ? s.expectancy_r >= 0 : null}
          suffix="R"
          index={8}
        />
        <MetricCard
          label="总交易笔数"
          tooltip="回测期间完成平仓的总交易笔数"
          value={s.total_trades}
          decimals={0}
          positive={null}
          index={9}
        />
        <MetricCard
          label="最大盈利"
          tooltip="单笔最大盈利金额"
          value={s.largest_win}
          positive={null}
          showSign
          suffix=" U"
          index={10}
        />
        <MetricCard
          label="最大亏损"
          tooltip="单笔最大亏损金额（绝对值）"
          value={s.largest_loss != null ? Math.abs(s.largest_loss) : null}
          prefix="-"
          positive={null}
          suffix=" U"
          index={11}
        />
      </div>

      {/* ── Trade PnL ── */}
      <span className="qds-section-label">Trade PnL</span>
      <div className="grid grid-cols-2 gap-4">
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>盈亏分布</SectionTitle>
          <PnlDistributionChart data={result.trade_pnl_distribution} />
        </div></div>
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>累积盈亏</SectionTitle>
          <CumulativePnlChart data={result.cumulative_trade_pnl} />
        </div></div>
      </div>

      {/* ── Scatter & MAE/MFE ── */}
      <span className="qds-section-label">Scatter &amp; MAE / MFE</span>
      <div className="grid grid-cols-2 gap-4">
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>逐笔盈亏散点</SectionTitle>
          <PnlScatterChart data={result.trade_pnl_scatter} />
        </div></div>
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>MAE/MFE 分析</SectionTitle>
          <MaeMfeChart data={result.mae_mfe} />
        </div></div>
      </div>

      {/* ── Patterns & Streaks ── */}
      <span className="qds-section-label">Patterns &amp; Streaks</span>
      <div className="grid grid-cols-2 gap-4">
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>持仓时长分布</SectionTitle>
          <HoldingTimeChart data={result.holding_time_distribution} />
        </div></div>
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>连盈/连亏序列</SectionTitle>
          <StreakChart data={result.streak_sequence} />
        </div></div>
      </div>

      {/* ── Long vs Short ── */}
      <span className="qds-section-label">Long vs Short</span>
      <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
        <LongShortChart data={result.long_vs_short} />
      </div></div>

      {/* ── By Time ── */}
      <span className="qds-section-label">By Time</span>
      <div className="grid grid-cols-2 gap-4">
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>按星期收益分布</SectionTitle>
          <ReturnByGroupChart
            data={result.return_by_dow}
            labelKey="dow_name"
            title="按星期"
          />
        </div></div>
        <div className={CARD_CLS}><div className={CARD_BODY_CLS}>
          <SectionTitle>按小时收益分布</SectionTitle>
          <ReturnByGroupChart
            data={result.return_by_hour}
            labelKey="hour"
            title="按小时"
          />
        </div></div>
      </div>
    </div>
  );
}
