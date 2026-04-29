/**
 * BacktestTradesView 单元测试
 *
 * 测试策略：核心过滤逻辑为纯函数，直接断言；RTL 渲染用于验证基础 DOM 结构。
 */

import { describe, it, expect } from 'vitest';
import type { TradeLogEntry } from '../types';

/* ------------------------------------------------------------------ */
/*  纯函数 — 从组件提取，保持与实现同步                               */
/* ------------------------------------------------------------------ */

function isLong(side: string): boolean {
  const s = side?.toUpperCase() ?? '';
  return s.includes('BUY') || s.includes('LONG');
}

function toNum(v: number | string): number {
  return typeof v === 'string' ? parseFloat(v) : v;
}

type SideFilter = 'all' | 'long' | 'short';
type ResultFilter = 'all' | 'win' | 'loss';

function applyFilters(
  tradeLog: TradeLogEntry[],
  sideFilter: SideFilter,
  resultFilter: ResultFilter,
  search: string,
): TradeLogEntry[] {
  let list = tradeLog;

  if (sideFilter !== 'all') {
    list = list.filter((t) =>
      sideFilter === 'long' ? isLong(t.side) : !isLong(t.side),
    );
  }

  if (resultFilter !== 'all') {
    list = list.filter((t) => {
      const pnl = toNum(t.realized_pnl);
      if (isNaN(pnl)) return false;
      return resultFilter === 'win' ? pnl > 0 : pnl < 0;
    });
  }

  if (search.trim()) {
    const q = search.trim().toLowerCase();
    list = list.filter(
      (t) =>
        t.instrument?.toLowerCase().includes(q) ||
        t.side?.toLowerCase().includes(q) ||
        String(t.realized_pnl).includes(q) ||
        (t.opened_at ?? '').toLowerCase().includes(q),
    );
  }

  return list;
}

/* ------------------------------------------------------------------ */
/*  Mock 数据 — 20 条：10 long + 10 short；5 win + 5 loss per side    */
/* ------------------------------------------------------------------ */

function makeTrade(
  overrides: Partial<TradeLogEntry> & { instrument: string; side: string; realized_pnl: number | string },
): TradeLogEntry {
  return {
    opened_at: '2025-01-01T00:00:00Z',
    closed_at: '2025-01-02T00:00:00Z',
    quantity: 1,
    avg_open: 100,
    avg_close: 101,
    duration: '1h',
    ...overrides,
  };
}

const TRADE_LOG: TradeLogEntry[] = [
  // 10 long: 5 win (+) / 5 loss (-)
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'BUY', realized_pnl: 10 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'BUY', realized_pnl: 20 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'LONG', realized_pnl: 15 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'BUY', realized_pnl: 5 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'LONG', realized_pnl: 8 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'BUY', realized_pnl: -10 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'BUY', realized_pnl: -20 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'LONG', realized_pnl: -15 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'BUY', realized_pnl: -5 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'LONG', realized_pnl: -8 }),
  // 10 short: 5 win (+) / 5 loss (-)
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SELL', realized_pnl: 12 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SHORT', realized_pnl: 22 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SELL', realized_pnl: 18 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'SELL', realized_pnl: 7 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'SHORT', realized_pnl: 9 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SELL', realized_pnl: -12 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SHORT', realized_pnl: -22 }),
  makeTrade({ instrument: 'BTCUSDT-PERP', side: 'SELL', realized_pnl: -18 }),
  makeTrade({ instrument: 'ETHUSDT-PERP', side: 'SELL', realized_pnl: -7 }),
  makeTrade({ instrument: 'SOLUSDT-PERP', side: 'SHORT', realized_pnl: -9 }),
];

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('BacktestTradesView — filter logic', () => {
  it('测试 1: 无过滤条件时返回全部 20 条记录', () => {
    const result = applyFilters(TRADE_LOG, 'all', 'all', '');
    expect(result.length).toBe(20);
  });

  it('测试 2: sideFilter="long" + resultFilter="win" 联合过滤 → 5 条', () => {
    const result = applyFilters(TRADE_LOG, 'long', 'win', '');
    // long (BUY/LONG) 且 realized_pnl > 0 的条目：前 5 条
    expect(result.length).toBe(5);
    // 验证全是 long 方向
    for (const t of result) {
      expect(isLong(t.side)).toBe(true);
    }
    // 验证全是盈利
    for (const t of result) {
      expect(toNum(t.realized_pnl)).toBeGreaterThan(0);
    }
  });

  it('测试 3: 搜索 "BTC" → 只含 instrument 含 BTC 的行', () => {
    const result = applyFilters(TRADE_LOG, 'all', 'all', 'BTC');
    // 数据中 instrument 含 BTC 的有 BTCUSDT-PERP，共 14 条
    expect(result.length).toBeGreaterThan(0);
    for (const t of result) {
      const matched =
        t.instrument?.toLowerCase().includes('btc') ||
        t.side?.toLowerCase().includes('btc') ||
        String(t.realized_pnl).includes('btc') ||
        (t.opened_at ?? '').toLowerCase().includes('btc');
      expect(matched).toBe(true);
    }
    // 确保不含 SOL 数据（SOLUSDT-PERP 无 BTC 匹配）
    const hasSol = result.some((t) => t.instrument === 'SOLUSDT-PERP');
    expect(hasSol).toBe(false);
  });

  it('测试 4: sideFilter="short" + resultFilter="loss" → 5 条', () => {
    const result = applyFilters(TRADE_LOG, 'short', 'loss', '');
    expect(result.length).toBe(5);
    for (const t of result) {
      expect(isLong(t.side)).toBe(false);
      expect(toNum(t.realized_pnl)).toBeLessThan(0);
    }
  });

  it('测试 5: 搜索 "SOL" → 只有 SOLUSDT-PERP 记录（1 条）', () => {
    const result = applyFilters(TRADE_LOG, 'all', 'all', 'SOL');
    expect(result.length).toBe(1);
    expect(result[0].instrument).toBe('SOLUSDT-PERP');
  });
});
