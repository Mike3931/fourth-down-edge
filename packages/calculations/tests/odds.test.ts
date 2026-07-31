import { describe, expect, it } from 'vitest';
import {
  americanToDecimal,
  americanToImpliedProbability,
  breakEvenProbability,
  decimalToAmerican,
  noVigProbabilities,
  overround,
  probabilityToFairAmerican,
} from '../src/odds';

describe('americanToDecimal', () => {
  it('converts positive odds', () => {
    expect(americanToDecimal(100)).toBeCloseTo(2.0, 10);
    expect(americanToDecimal(150)).toBeCloseTo(2.5, 10);
    expect(americanToDecimal(250)).toBeCloseTo(3.5, 10);
  });
  it('converts negative odds', () => {
    expect(americanToDecimal(-110)).toBeCloseTo(1 + 100 / 110, 10);
    expect(americanToDecimal(-200)).toBeCloseTo(1.5, 10);
  });
  it('rejects invalid odds', () => {
    expect(() => americanToDecimal(0)).toThrow(RangeError);
    expect(() => americanToDecimal(50)).toThrow(RangeError);
    expect(() => americanToDecimal(-99)).toThrow(RangeError);
    expect(() => americanToDecimal(NaN)).toThrow(RangeError);
  });
});

describe('americanToImpliedProbability', () => {
  it('converts positive odds', () => {
    expect(americanToImpliedProbability(100)).toBeCloseTo(0.5, 10);
    expect(americanToImpliedProbability(300)).toBeCloseTo(0.25, 10);
  });
  it('converts negative odds', () => {
    expect(americanToImpliedProbability(-110)).toBeCloseTo(110 / 210, 10);
    expect(americanToImpliedProbability(-150)).toBeCloseTo(0.6, 10);
  });
  it('is consistent with decimal odds (p = 1/decimal)', () => {
    for (const a of [-500, -240, -110, 105, 180, 400]) {
      expect(americanToImpliedProbability(a)).toBeCloseTo(1 / americanToDecimal(a), 10);
    }
  });
});

describe('probabilityToFairAmerican', () => {
  it('maps 0.5 to +100', () => {
    expect(probabilityToFairAmerican(0.5)).toBe(100);
  });
  it('maps favorites to negative odds', () => {
    expect(probabilityToFairAmerican(0.6)).toBe(-150);
    expect(probabilityToFairAmerican(2 / 3)).toBe(-200);
  });
  it('maps underdogs to positive odds', () => {
    expect(probabilityToFairAmerican(0.25)).toBe(300);
    expect(probabilityToFairAmerican(0.4)).toBe(150);
  });
  it('round-trips with implied probability', () => {
    for (const p of [0.2, 0.35, 0.5, 0.65, 0.8]) {
      const fair = probabilityToFairAmerican(p);
      expect(americanToImpliedProbability(fair)).toBeCloseTo(p, 2);
    }
  });
  it('rejects out-of-range probabilities', () => {
    expect(() => probabilityToFairAmerican(0)).toThrow(RangeError);
    expect(() => probabilityToFairAmerican(1)).toThrow(RangeError);
  });
});

describe('decimalToAmerican', () => {
  it('converts both regimes', () => {
    expect(decimalToAmerican(2.5)).toBe(150);
    expect(decimalToAmerican(1.5)).toBe(-200);
    expect(decimalToAmerican(2.0)).toBe(100);
  });
});

describe('noVigProbabilities', () => {
  it('normalizes a standard -110/-110 spread market to 50/50', () => {
    const [a, b] = noVigProbabilities([-110, -110]);
    expect(a).toBeCloseTo(0.5, 10);
    expect(b).toBeCloseTo(0.5, 10);
  });
  it('normalizes an asymmetric moneyline market', () => {
    const probs = noVigProbabilities([-180, 155]);
    expect(probs.reduce((x, y) => x + y, 0)).toBeCloseTo(1, 10);
    expect(probs[0]!).toBeGreaterThan(0.6);
  });
  it('requires two or more outcomes', () => {
    expect(() => noVigProbabilities([-110])).toThrow(RangeError);
  });
});

describe('overround', () => {
  it('computes standard juice', () => {
    expect(overround([-110, -110])).toBeCloseTo(2 * (110 / 210) - 1, 10);
  });
});

describe('breakEvenProbability', () => {
  it('is the implied probability with no pushes', () => {
    expect(breakEvenProbability(-110)).toBeCloseTo(110 / 210, 10);
  });
  it('decreases when pushes are possible', () => {
    const noPush = breakEvenProbability(-110, 0);
    const withPush = breakEvenProbability(-110, 0.05);
    expect(withPush).toBeLessThan(noPush);
    expect(withPush).toBeCloseTo((1 - 0.05) / (1 + 100 / 110), 10);
  });
});
