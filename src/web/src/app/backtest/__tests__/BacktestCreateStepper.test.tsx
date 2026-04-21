/**
 * BacktestCreateStepper 单元测试
 *
 * 断言基于 FIX-H5 修复后的预期行为：
 * - completed dot → bg-qds-success + Check icon（lucide-react <Check>）
 * - active dot    → bg-primary（STEPPER_DOT_CLS_MAP.active）
 * - pending dot   → border border-border text-muted-foreground bg-transparent
 *
 * 如 FIX-H5 尚未合并，completed 相关测试可能失败 — 这是预期行为。
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { BacktestCreateStepper } from '../components/BacktestCreateStepper';
import { STEPPER_DOT_CLS_MAP } from '../components/backtestStyles';

/* ------------------------------------------------------------------ */
/*  辅助：提取 3 个 dot 元素                                           */
/* ------------------------------------------------------------------ */

/**
 * 找到所有 step dot 容器：className 含 "rounded-full" 的 div。
 * BacktestCreateStepper 为每个步骤渲染一个 w-6 h-6 rounded-full div。
 */
function getDots(container: HTMLElement): HTMLElement[] {
  const all = Array.from(container.querySelectorAll<HTMLElement>('div'));
  return all.filter((el) => el.className.includes('rounded-full'));
}

/* ------------------------------------------------------------------ */
/*  STEPPER_DOT_CLS_MAP 中各状态的标志 class（用于断言）              */
/* ------------------------------------------------------------------ */

// active 状态：bg-primary
const ACTIVE_FLAG = 'bg-primary';
// completed 状态（FIX-H5 后）：bg-qds-success
const COMPLETED_FLAG = 'bg-qds-success';
// pending 状态：bg-transparent
const PENDING_FLAG = 'bg-transparent';

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('BacktestCreateStepper', () => {
  it('测试 1: step=1 → dot1 active，dot2/dot3 pending', () => {
    const { container } = render(<BacktestCreateStepper step={1} />);
    const dots = getDots(container);

    // 应有 3 个 dot
    expect(dots.length).toBe(3);

    // dot1 active
    expect(dots[0].className).toContain(ACTIVE_FLAG);

    // dot2 pending（含 bg-transparent）
    expect(dots[1].className).toContain(PENDING_FLAG);

    // dot3 pending
    expect(dots[2].className).toContain(PENDING_FLAG);
  });

  it('测试 2: step=2 → dot1 completed（bg-qds-success），dot2 active，dot3 pending', () => {
    const { container } = render(<BacktestCreateStepper step={2} />);
    const dots = getDots(container);

    expect(dots.length).toBe(3);

    // dot1 completed — FIX-H5 后应为 bg-qds-success
    expect(dots[0].className).toContain(COMPLETED_FLAG);

    // dot1 completed 时应有 Check icon（lucide-react <Check> 渲染为 <svg>）
    const checkIcon = dots[0].querySelector('svg');
    expect(checkIcon).not.toBeNull();

    // dot2 active
    expect(dots[1].className).toContain(ACTIVE_FLAG);

    // dot3 pending
    expect(dots[2].className).toContain(PENDING_FLAG);
  });

  it('测试 3: step=3 → dot1/dot2 completed（bg-qds-success），dot3 active', () => {
    const { container } = render(<BacktestCreateStepper step={3} />);
    const dots = getDots(container);

    expect(dots.length).toBe(3);

    // dot1 completed
    expect(dots[0].className).toContain(COMPLETED_FLAG);

    // dot2 completed
    expect(dots[1].className).toContain(COMPLETED_FLAG);

    // dot3 active
    expect(dots[2].className).toContain(ACTIVE_FLAG);
  });

  it('测试 4: STEPPER_DOT_CLS_MAP 的 active/pending class 与实现一致', () => {
    // 验证 backtestStyles 导出的 map 含预期 class
    expect(STEPPER_DOT_CLS_MAP.active).toContain('bg-primary');
    expect(STEPPER_DOT_CLS_MAP.pending).toContain('bg-transparent');
    // FIX-H5 后 completed 应为 bg-qds-success
    expect(STEPPER_DOT_CLS_MAP.completed).toContain('bg-qds-success');
  });
});
