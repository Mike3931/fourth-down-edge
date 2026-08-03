import { describe, expect, it } from 'vitest';
import {
  americanToDecimal,
  americanToImpliedProbability,
  breakEvenProbability,
  decimalToAmerican,
  noVigProbabilities,
  probabilityToFairAmerican,
} from '../src/odds';
import { expectedValuePerDollar } from '../src/ev';
import { fullKelly, fractionalKelly } from '../src/kelly';

/**
 * The existing tests check each formula against known values. These check
 * the formulas against EACH OTHER.
 *
 * EV, break-even, and Kelly are three separate implementations of the same
 * underlying bet arithmetic, each handling push probability in its own
 * expression. Point tests pass happily when one of them is edited and the
 * others are not — the numbers all still look plausible, and no single
 * assertion contradicts. The identities below are what actually break.
 *
 * Swept over a grid rather than spot-checked, because the push term is
 * exactly where the disagreement would hide.
 */

const AMERICANS = [-1000, -400, -220, -150, -110, -105, -100, 100, 105, 110, 150, 250, 400, 1000];
const PROBS = [0.05, 0.2, 0.35, 0.5, 0.55, 0.65, 0.8, 0.95];
const PUSHES = [0, 0.01, 0.035, 0.1, 0.25];

function* cases(): Generator<[number, number, number]> {
  for (const american of AMERICANS) {
    for (const p of PROBS) {
      for (const q of PUSHES) {
        if (p + q > 1) continue;
        yield [p, american, q];
      }
    }
  }
}

describe('EV and break-even describe the same bet', () => {
  it('EV equals decimal odds times edge, for every price and push', () => {
    for (const [p, american, q] of cases()) {
      const d = americanToDecimal(american);
      const edge = p - breakEvenProbability(american, q);
      expect(expectedValuePerDollar(p, american, q)).toBeCloseTo(d * edge, 12);
    }
  });

  it('EV is exactly zero at the break-even probability', () => {
    for (const american of AMERICANS) {
      for (const q of PUSHES) {
        const be = breakEvenProbability(american, q);
        if (be + q > 1) continue;
        expect(expectedValuePerDollar(be, american, q)).toBeCloseTo(0, 12);
      }
    }
  });

  it('EV and edge always agree on sign', () => {
    for (const [p, american, q] of cases()) {
      const ev = expectedValuePerDollar(p, american, q);
      const edge = p - breakEvenProbability(american, q);
      expect(Math.sign(Number(ev.toFixed(12)))).toBe(Math.sign(Number(edge.toFixed(12))));
    }
  });
});

describe('Kelly agrees with EV about whether a bet is worth making', () => {
  it('stakes nothing when EV is not positive', () => {
    for (const [p, american, q] of cases()) {
      const ev = expectedValuePerDollar(p, american, q);
      const f = fullKelly(p, american, q);
      if (ev > 1e-12) {
        expect(f).toBeGreaterThan(0);
      } else {
        // Not exact zero: at the break-even price the two formulas round
        // differently in the last bit, so Kelly can come back at ~2e-16
        // where EV is ~0. Math.max(0, f) clamps genuine negatives; it
        // cannot clamp a positive rounding artefact. A stake of 2e-16 of
        // bankroll is zero in every sense that matters, so the assertion
        // is a tolerance rather than a pretence of exactness.
        expect(f).toBeLessThan(1e-9);
      }
    }
  });

  it('matches the push-adjusted optimum derived independently', () => {
    // Maximising E[log] over f for the three-outcome bet gives
    //   f* = [p*b - (1 - p - q)] / [b * (1 - q)]
    // The implementation instead conditions on the bet resolving. Those are
    // the same expression; this is the check that they stay the same.
    for (const [p, american, q] of cases()) {
      const b = americanToDecimal(american) - 1;
      const pConditional = p / (1 - q);
      if (pConditional >= 1) continue;
      const expected = Math.max(0, (p * b - (1 - p - q)) / (b * (1 - q)));
      expect(fullKelly(p, american, q)).toBeCloseTo(expected, 12);
    }
  });

  it('never returns a fraction above 1 or below 0', () => {
    for (const [p, american, q] of cases()) {
      const f = fullKelly(p, american, q);
      expect(f).toBeGreaterThanOrEqual(0);
      expect(f).toBeLessThanOrEqual(1);
    }
  });

  it('fractional Kelly scales linearly and never exceeds full Kelly', () => {
    for (const [p, american, q] of cases()) {
      const full = fullKelly(p, american, q);
      for (const fraction of [0.1, 0.25, 0.5, 1]) {
        const part = fractionalKelly(p, american, fraction, q);
        expect(part).toBeCloseTo(full * fraction, 12);
        expect(part).toBeLessThanOrEqual(full + 1e-12);
      }
    }
  });
});

describe('odds conversions round-trip', () => {
  it('american -> decimal -> american is the identity', () => {
    for (const american of AMERICANS) {
      expect(decimalToAmerican(americanToDecimal(american))).toBe(
        // -100 and +100 are the same price; the converter returns +100.
        american === -100 ? 100 : american,
      );
    }
  });

  it('probability -> fair american -> implied probability returns the probability', () => {
    for (const p of PROBS) {
      const american = probabilityToFairAmerican(p);
      // Rounding to an integer price costs a little precision; 3dp is well
      // inside what a displayed price can represent.
      expect(americanToImpliedProbability(american)).toBeCloseTo(p, 3);
    }
  });

  it('a fair price carries no vig', () => {
    for (const p of PROBS) {
      const [a, b] = [probabilityToFairAmerican(p), probabilityToFairAmerican(1 - p)];
      const total = americanToImpliedProbability(a) + americanToImpliedProbability(b);
      expect(total).toBeCloseTo(1, 2);
    }
  });
});

describe('no-vig normalisation', () => {
  it('always produces probabilities summing to one', () => {
    for (const a of AMERICANS) {
      for (const b of AMERICANS) {
        const probs = noVigProbabilities([a, b]);
        expect(probs.reduce((x, y) => x + y, 0)).toBeCloseTo(1, 12);
      }
    }
  });

  it('preserves the ordering of the raw implied probabilities', () => {
    for (const a of AMERICANS) {
      for (const b of AMERICANS) {
        const [na, nb] = noVigProbabilities([a, b]);
        const [ra, rb] = [americanToImpliedProbability(a), americanToImpliedProbability(b)];
        expect(Math.sign(na! - nb!)).toBe(Math.sign(ra - rb));
      }
    }
  });

  it('leaves an already-fair two-way market unchanged', () => {
    const probs = noVigProbabilities([100, -100]);
    expect(probs[0]).toBeCloseTo(0.5, 12);
    expect(probs[1]).toBeCloseTo(0.5, 12);
  });

  it('removes exactly the overround', () => {
    // A -110/-110 market implies 0.5238 each, summing to 1.0476. Fair is 0.5.
    const probs = noVigProbabilities([-110, -110]);
    expect(probs[0]).toBeCloseTo(0.5, 12);
  });
});

describe('a break-even bet is never worth staking', () => {
  it('Kelly is zero at exactly the break-even probability', () => {
    for (const american of AMERICANS) {
      for (const q of PUSHES) {
        const be = breakEvenProbability(american, q);
        if (be + q > 1) continue;
        expect(fullKelly(be, american, q)).toBeCloseTo(0, 10);
      }
    }
  });
});
