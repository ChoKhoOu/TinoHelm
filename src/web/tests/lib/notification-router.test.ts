import { describe, it, expect } from 'vitest';
import {
  ROUTING_TABLE,
  shouldDedupe,
  formatToastMessage,
} from '@/lib/notification-router';

// ---------------------------------------------------------------------------
// ROUTING_TABLE — factor entries
// ---------------------------------------------------------------------------

describe('ROUTING_TABLE — factor entries', () => {
  it('factor.completed routes to toast channel', () => {
    expect(ROUTING_TABLE['factor.completed']?.channel).toBe('toast');
  });

  it('factor.completed has type success', () => {
    expect(ROUTING_TABLE['factor.completed']?.type).toBe('success');
  });

  it('factor.failed routes to toast channel', () => {
    expect(ROUTING_TABLE['factor.failed']?.channel).toBe('toast');
  });

  it('factor.failed has type error', () => {
    expect(ROUTING_TABLE['factor.failed']?.type).toBe('error');
  });

  it('factor.progress routes to silent channel', () => {
    expect(ROUTING_TABLE['factor.progress']?.channel).toBe('silent');
  });

  it('factor.completed dedupeKey returns event.run_id', () => {
    const route = ROUTING_TABLE['factor.completed'];
    const event = { run_id: 'run-abc-123', factor_name: 'ret_5' };
    expect(route?.dedupeKey?.(event)).toBe('run-abc-123');
  });

  it('factor.failed dedupeKey returns event.run_id', () => {
    const route = ROUTING_TABLE['factor.failed'];
    const event = { run_id: 'run-xyz-999', error: 'boom' };
    expect(route?.dedupeKey?.(event)).toBe('run-xyz-999');
  });

});

// ---------------------------------------------------------------------------
// shouldDedupe — factor events
// ---------------------------------------------------------------------------

describe('shouldDedupe — factor events', () => {
  it('factor.completed: first call returns false (not deduped)', () => {
    const event = { run_id: `unique-${Date.now()}-${Math.random()}`, factor_name: 'f' };
    expect(shouldDedupe('factor.completed', event)).toBe(false);
  });

  it('factor.completed: second call with same run_id within window returns true', () => {
    const event = { run_id: `dup-${Date.now()}-${Math.random()}`, factor_name: 'f' };
    shouldDedupe('factor.completed', event);
    expect(shouldDedupe('factor.completed', event)).toBe(true);
  });

  it('factor.failed: first call returns false', () => {
    const event = { run_id: `fail-${Date.now()}-${Math.random()}`, error: 'err' };
    expect(shouldDedupe('factor.failed', event)).toBe(false);
  });

  it('factor.failed: second call same run_id returns true', () => {
    const event = { run_id: `fail2-${Date.now()}-${Math.random()}`, error: 'err' };
    shouldDedupe('factor.failed', event);
    expect(shouldDedupe('factor.failed', event)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// formatToastMessage — factor events
// ---------------------------------------------------------------------------

describe('formatToastMessage — factor events', () => {
  it('factor.completed: title contains factor_name', () => {
    const msg = formatToastMessage('factor.completed', {
      run_id: 'abc12345',
      factor_name: 'ret_5',
      rating: 3,
    });
    expect(msg.title).toContain('ret_5');
  });

  it('factor.completed: title contains short run_id (first 6 chars)', () => {
    const msg = formatToastMessage('factor.completed', {
      run_id: 'abc12345',
      factor_name: 'ret_5',
    });
    expect(msg.title).toContain('abc123');
  });

  it('factor.completed: description is stars when rating provided', () => {
    const msg = formatToastMessage('factor.completed', {
      run_id: 'r',
      factor_name: 'f',
      rating: 2,
    });
    expect(msg.description).toBe('★★');
  });

  it('factor.completed: description is undefined when no rating', () => {
    const msg = formatToastMessage('factor.completed', {
      run_id: 'r',
      factor_name: 'f',
    });
    expect(msg.description).toBeUndefined();
  });

  it('factor.failed: title contains factor_name', () => {
    const msg = formatToastMessage('factor.failed', {
      run_id: 'xyz99999',
      factor_name: 'mom_20',
      error: 'division by zero',
    });
    expect(msg.title).toContain('mom_20');
  });

  it('factor.failed: description is error string', () => {
    const msg = formatToastMessage('factor.failed', {
      run_id: 'r',
      factor_name: 'f',
      error: 'timeout',
    });
    expect(msg.description).toBe('timeout');
  });

  it('factor.failed: description is undefined when no error field', () => {
    const msg = formatToastMessage('factor.failed', {
      run_id: 'r',
      factor_name: 'f',
    });
    expect(msg.description).toBeUndefined();
  });

  it('factor.completed: fallback factor name when missing', () => {
    const msg = formatToastMessage('factor.completed', { run_id: 'r' });
    expect(msg.title).toContain('因子');
  });
});
