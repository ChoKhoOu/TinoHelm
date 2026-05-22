/**
 * OverviewEquitySvg 单元测试
 *
 * 测试：
 * 1. data={[]} 时渲染 InlineError 文案「暂无权益曲线数据」，无 <svg>
 * 2. data 有数据时渲染 <svg> + <path> 元素
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { OverviewEquitySvg } from '../components/OverviewEquitySvg';
import type { EquityCurvePoint } from '../types';

/* ------------------------------------------------------------------ */
/*  Mock 数据                                                          */
/* ------------------------------------------------------------------ */

const SAMPLE_DATA: EquityCurvePoint[] = [
  { timestamp: '2025-01-01', equity: 10000, drawdown_pct: 0 },
  { timestamp: '2025-01-02', equity: 10500, drawdown_pct: -0.02 },
  { timestamp: '2025-01-03', equity: 10200, drawdown_pct: -0.05 },
];

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('OverviewEquitySvg', () => {
  it('测试 1: data={[]} 渲染 InlineError 提示且无 <svg>', () => {
    const { container, getByText } = render(<OverviewEquitySvg data={[]} />);

    // InlineError 内容应包含「暂无权益曲线数据」
    expect(getByText('暂无权益曲线数据')).toBeTruthy();

    // 不渲染 SVG
    const svg = container.querySelector('svg');
    expect(svg).toBeNull();
  });

  it('测试 2: data 有数据时渲染 <svg> 及至少 2 条 <path>', () => {
    const { container } = render(<OverviewEquitySvg data={SAMPLE_DATA} />);

    // SVG 存在
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();

    // 至少 2 条 path（equity 线 + drawdown 线，以及 area 填充）
    const paths = container.querySelectorAll('path');
    expect(paths.length).toBeGreaterThanOrEqual(2);
  });

  it('测试 3: data 为 null/undefined 时等价于空数据 — 渲染 InlineError', () => {
    // 类型强制转换模拟 API 返回 null 的边界情况
    const { getByText, container } = render(
      <OverviewEquitySvg data={null as unknown as EquityCurvePoint[]} />,
    );

    expect(getByText('暂无权益曲线数据')).toBeTruthy();
    expect(container.querySelector('svg')).toBeNull();
  });
});
