/**
 * notification-router.ts unit tests
 *
 * Covers:
 * 1. ROUTING_TABLE entries for factor.*
 * 2. shouldDedupe() — dedupeKey, window, cache behaviour
 * 3. formatToastMessage() — factor.completed / factor.failed copy
 * 4. data.fetch.partial routing and formatting (#192)
 */

import { describe, it, expect } from 'vitest';
import {
  ROUTING_TABLE,
  shouldDedupe,
  formatToastMessage,
} from '../notification-router';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Reset the module-level _dedupeCache between tests by advancing time. */
function makeEvent(overrides: Record<string, unknown> = {}) {
  return { run_id: 'run-abc-123', factor_name: 'ret_5', rating: 3, ...overrides };
}

// ---------------------------------------------------------------------------
// ROUTING_TABLE — factor.* entries
// ---------------------------------------------------------------------------

describe('ROUTING_TABLE — factor routes', () => {
  it('factor.progress routes to silent', () => {
    expect(ROUTING_TABLE['factor.progress']?.channel).toBe('silent');
  });

  it('factor.completed routes to toast with type success', () => {
    const route = ROUTING_TABLE['factor.completed'];
    expect(route?.channel).toBe('toast');
    expect(route?.type).toBe('success');
  });

  it('factor.failed routes to toast with type error', () => {
    const route = ROUTING_TABLE['factor.failed'];
    expect(route?.channel).toBe('toast');
    expect(route?.type).toBe('error');
  });

  it('factor.completed dedupeKey extracts run_id', () => {
    const route = ROUTING_TABLE['factor.completed'];
    const event = makeEvent();
    expect(route?.dedupeKey?.(event)).toBe('run-abc-123');
  });

  it('factor.failed dedupeKey extracts run_id', () => {
    const route = ROUTING_TABLE['factor.failed'];
    const event = makeEvent({ run_id: 'run-xyz-999' });
    expect(route?.dedupeKey?.(event)).toBe('run-xyz-999');
  });
});

// ---------------------------------------------------------------------------
// shouldDedupe — factor events
// ---------------------------------------------------------------------------

describe('shouldDedupe — factor.completed', () => {
  it('first occurrence is NOT deduped', () => {
    const event = makeEvent({ run_id: `dedup-test-${Math.random()}` });
    expect(shouldDedupe('factor.completed', event)).toBe(false);
  });

  it('second occurrence within window IS deduped', () => {
    const event = makeEvent({ run_id: `dedup-window-${Math.random()}` });
    shouldDedupe('factor.completed', event); // first — record
    expect(shouldDedupe('factor.completed', event)).toBe(true); // second — skip
  });

  it('different run_ids are NOT deduped against each other', () => {
    const id = Math.random().toString(36);
    const e1 = makeEvent({ run_id: `${id}-A` });
    const e2 = makeEvent({ run_id: `${id}-B` });
    shouldDedupe('factor.completed', e1);
    expect(shouldDedupe('factor.completed', e2)).toBe(false);
  });

  it('factor.failed uses run_id dedupeKey independently', () => {
    const event = makeEvent({ run_id: `fail-${Math.random()}` });
    shouldDedupe('factor.failed', event);
    expect(shouldDedupe('factor.failed', event)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// formatToastMessage — factor.completed
// ---------------------------------------------------------------------------

describe('formatToastMessage — factor.completed', () => {
  it('title includes factor_name and short run_id', () => {
    const event = makeEvent({ run_id: 'abcdef1234', factor_name: 'ret_5' });
    const { title } = formatToastMessage('factor.completed', event);
    expect(title).toContain('ret_5');
    expect(title).toContain('abcdef'); // first 6 chars
  });

  it('description is star string when rating is present', () => {
    const event = makeEvent({ rating: 3 });
    const { description } = formatToastMessage('factor.completed', event);
    expect(description).toBe('★★★');
  });

  it('description is undefined when rating absent', () => {
    const event = makeEvent({ rating: undefined });
    const { description } = formatToastMessage('factor.completed', event);
    expect(description).toBeUndefined();
  });

  it('fallback factor_name when not provided', () => {
    const { title } = formatToastMessage('factor.completed', { run_id: 'x' });
    expect(title).toContain('因子');
  });
});

// ---------------------------------------------------------------------------
// formatToastMessage — factor.failed
// ---------------------------------------------------------------------------

describe('formatToastMessage — factor.failed', () => {
  it('title includes factor_name and short run_id', () => {
    const event = makeEvent({ run_id: 'zzz000abc', factor_name: 'mom_20' });
    const { title } = formatToastMessage('factor.failed', event);
    expect(title).toContain('mom_20');
    expect(title).toContain('zzz000'); // first 6 chars
  });

  it('description carries error string when present', () => {
    const event = makeEvent({ error: 'division by zero' });
    const { description } = formatToastMessage('factor.failed', event);
    expect(description).toBe('division by zero');
  });

  it('description is undefined when error absent', () => {
    const event = makeEvent({ error: undefined });
    const { description } = formatToastMessage('factor.failed', event);
    expect(description).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// ROUTING_TABLE — data.fetch.partial (#192)
// ---------------------------------------------------------------------------

describe('ROUTING_TABLE — data.fetch.partial', () => {
  it('routes to toast with type warning', () => {
    const route = ROUTING_TABLE['data.fetch.partial'];
    expect(route).toBeDefined();
    expect(route?.channel).toBe('toast');
    expect(route?.type).toBe('warning');
  });

  it('dedupeKey extracts job_id', () => {
    const route = ROUTING_TABLE['data.fetch.partial'];
    const event = { job_id: 'abc-123', symbol: 'BTCUSDT-PERP', data_type: 'klines' };
    expect(route?.dedupeKey?.(event)).toBe('abc-123');
  });

  it('data.fetch.partial is treated as terminal (not silent)', () => {
    const route = ROUTING_TABLE['data.fetch.partial'];
    expect(route?.channel).not.toBe('silent');
  });
});

// ---------------------------------------------------------------------------
// formatToastMessage — data.fetch.partial (#192)
// ---------------------------------------------------------------------------

describe('formatToastMessage — data.fetch.partial', () => {
  it('title includes symbol and data_type with 部分完成', () => {
    const event = { symbol: 'BTCUSDT-PERP', data_type: 'klines', last_available_date: '2026-05-15' };
    const { title } = formatToastMessage('data.fetch.partial', event);
    expect(title).toContain('BTCUSDT-PERP');
    expect(title).toContain('klines');
    expect(title).toContain('部分完成');
  });

  it('description includes last_available_date when present', () => {
    const event = { symbol: 'BTCUSDT-PERP', data_type: 'klines', last_available_date: '2026-05-15' };
    const { description } = formatToastMessage('data.fetch.partial', event);
    expect(description).toContain('2026-05-15');
  });

  it('description has fallback when last_available_date is absent', () => {
    const event = { symbol: 'BTCUSDT-PERP', data_type: 'klines' };
    const { description } = formatToastMessage('data.fetch.partial', event);
    expect(description).toContain('尾部');
  });
});
