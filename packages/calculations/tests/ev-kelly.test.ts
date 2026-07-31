import { describe, expect, it } from 'vitest';
import { expectedValuePerDollar } from '../src/ev';
import { fractionalKelly, fullKelly } from '../src/kelly';

describe('expectedValuePerDollar', () => {
  it('is zero at the break-even probability', () => {
    // -110: break-even = 110/210
    expect(expectedValuePerDollar(110 / 210, -110)).toBeCloseTo(0, 10);
  });
  it('is positive when model probability exceeds break-even', () => {
    expect(expectedValuePerDollar(0.55, -110)).toBeGreaterThan(0);
  });
  it('is negative when model probability is below break-even', () => {
    expect(expectedValuePerDollar(0.5, -110)).toBeLessThan(0);
  });
  it('handles pushes: push mass contributes zero', () => {
    // 50% win, 10% push, 40% loss at +100: EV = 0.5*1 - 0.4 = 0.10
    expect(expectedValuePerDollar(0.5, 100, 0.1)).toBeCloseTo(0.1, 10);
  });
  it('rejects impossible probability combinations', () => {
    expect(() => expectedValuePerDollar(0.9, 100, 0.2)).toThrow(RangeError);
    expect(() => expectedValuePerDollar(1.2, 100)).toThrow(RangeError);
  });
});

describe('fullKelly', () => {
  it('matches the closed form for a fair-coin at +100 with edge', () => {
    // p=0.55 at +100 (b=1): f* = (1*0.55 - 0.45)/1 = 0.10
    expect(fullKelly(0.55, 100)).toBeCloseTo(0.1, 10);
  });
  it('never returns a negative stake', () => {
    expect(fullKelly(0.4, -110)).toBe(0);
    expect(fullKelly(0.5, -110)).toBe(0);
  });
  it('is zero exactly at break-even', () => {
    expect(fullKelly(110 / 210, -110)).toBeCloseTo(0, 10);
  });
  it('conditions on non-push outcomes', () => {
    // With pushes, conditional win prob rises: p=0.5, q=0.1 -> p'=0.5556 at +100
    const f = fullKelly(0.5, 100, 0.1);
    expect(f).toBeCloseTo((0.5 / 0.9 - (1 - 0.5 / 0.9)) / 1, 10);
    expect(f).toBeGreaterThan(0);
  });
});

describe('fractionalKelly', () => {
  it('scales full Kelly', () => {
    expect(fractionalKelly(0.55, 100, 0.25)).toBeCloseTo(0.025, 10);
  });
  it('never negative', () => {
    expect(fractionalKelly(0.3, 100, 0.25)).toBe(0);
  });
  it('rejects invalid fractions', () => {
    expect(() => fractionalKelly(0.55, 100, 0)).toThrow(RangeError);
    expect(() => fractionalKelly(0.55, 100, 1.5)).toThrow(RangeError);
  });
});
