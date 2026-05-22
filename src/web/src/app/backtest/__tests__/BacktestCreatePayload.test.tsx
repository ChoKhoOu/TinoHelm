import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/react';

const apiPostMock = vi.fn();

vi.mock('@/lib/api', () => ({
  apiPost: (...args: unknown[]) => apiPostMock(...args),
}));

vi.mock('@/hooks/use-action', () => ({
  useAction: (fn: () => Promise<unknown>) => ({
    execute: fn,
    state: 'idle',
    error: null,
  }),
}));

vi.mock('@/components/qds', () => ({
  InlineError: () => null,
  SectionLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../components/BacktestSubscriptionTable', () => ({
  BacktestSubscriptionTable: () => null,
}));

import { BacktestCreateStep2 } from '../components/BacktestCreateStep2';
import { BacktestCreateStep3 } from '../components/BacktestCreateStep3';

describe('BacktestCreateStep payload', () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    apiPostMock.mockResolvedValue({ run_id: 'r-1', status: 'queued' });
  });

  it('submits /api/backtest/estimate without interval', async () => {
    const form = {
      start_date: '2026-01-01',
      end_date: '2026-02-01',
    };

    const subscriptions = [
      {
        exchange: 'binance',
        symbol: 'BTCUSDT-PERP',
        granularity: 'bar' as const,
        dataType: 'klines',
        timeframe: '4h',
        timeframeValue: 4,
        timeframeUnit: 'h',
        auto: true,
      },
    ];

    render(
      <BacktestCreateStep2
        form={form}
        onFormChange={() => {}}
        subscriptions={subscriptions as any}
        onSubscriptionsChange={() => {}}
      />,
    );

    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    const [path, body] = apiPostMock.mock.calls[0];
    expect(path).toBe('/api/backtest/estimate');
    expect(body.symbols).toEqual(['BTCUSDT-PERP']);
    expect(body.interval).toBeUndefined();
  });

  it('submits /api/backtest/run without interval', async () => {
    const form = {
      initial_capital: '100000',
      maker_fee: '',
      taker_fee: '',
      latency_mode: 'off',
      latency_ms: '',
      fill_model_type: 'default',
      prob_fill_on_limit: '1.0',
      prob_slippage: '0.0',
      warmup_bars: '',
      tags: '',
    };

    const subscriptions = [
      {
        exchange: 'binance',
        symbol: 'BTCUSDT-PERP',
        granularity: 'bar' as const,
        dataType: 'klines',
        timeframe: '1h',
        timeframeValue: '1',
        timeframeUnit: 'h',
        auto: true,
      },
    ];

    const { getByText } = render(
      <BacktestCreateStep3
        form={form}
        onFormChange={() => {}}
        strategy_name="trend_pullback_v3"
        start_date="2026-01-01"
        end_date="2026-02-01"
        subscriptions={subscriptions as any}
        onSubscriptionsChange={() => {}}
        strategyParams={[]}
        paramOverrides={{}}
        paramsExpanded={false}
        onParamOverridesChange={() => {}}
        onParamsExpandedChange={() => {}}
        advancedExpanded={false}
        onAdvancedExpandedChange={() => {}}
      />,
    );

    fireEvent.click(getByText(/提交回测/));

    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    const [, body] = apiPostMock.mock.calls[0];
    expect(body.strategy).toBe('trend_pullback_v3');
    expect(body.symbols).toEqual(['BTCUSDT-PERP']);
    expect(body.interval).toBeUndefined();
  });
});
