/**
 * What the screen is allowed to say about a stored line.
 *
 * These assert the DISPLAY contract, not the storage contract. Storage is
 * home-relative and proven on the engine side by
 * `apps/api/tests/test_storage_conventions.py`, which drives the real
 * capture path. Here the stored number is the given, and the question is
 * whether a person reading the screen ends up believing the right thing.
 *
 * The case that matters is the away spread. The old table printed the
 * stored value under the label "AWAY", so a book hanging Carolina at -1.5
 * appeared on screen as "AWAY +1.5" — accurate to the column, and exactly
 * backwards to the reader. Every assertion about `AWAY` below fails
 * against that behaviour.
 */

import { describe, expect, it } from 'vitest';
import {
  describeConsensus,
  describeQuote,
  formatLine,
  lineForSelection,
  valuesDisagree,
} from '../src/marketDisplay';

const TEAMS = { homeTeamId: 'ARI', awayTeamId: 'CAR' };

describe('lineForSelection', () => {
  it('leaves the home spread as stored', () => {
    expect(lineForSelection('SPREAD', 'HOME', -3)).toBe(-3);
  });

  it('mirrors the away spread back to the away team', () => {
    // The regression this file exists for: stored -3 means the HOME team
    // is laying 3, so the away side is +3 and must not print as -3.
    expect(lineForSelection('SPREAD', 'AWAY', -3)).toBe(3);
    expect(lineForSelection('SPREAD', 'AWAY', 1.5)).toBe(-1.5);
  });

  it('never produces negative zero on a pick-em', () => {
    const away = lineForSelection('SPREAD', 'AWAY', 0);
    expect(away).toBe(0);
    expect(Object.is(away, -0)).toBe(false);
  });

  it('does not mirror totals, which are not two-sided handicaps', () => {
    expect(lineForSelection('TOTAL', 'OVER', 44.5)).toBe(44.5);
    expect(lineForSelection('TOTAL', 'UNDER', 44.5)).toBe(44.5);
  });

  it('reports no line for a moneyline', () => {
    expect(lineForSelection('MONEYLINE', 'HOME', null)).toBeNull();
    expect(lineForSelection('MONEYLINE', 'HOME', 0)).toBeNull();
  });

  it('passes a missing line through rather than inventing a zero', () => {
    expect(lineForSelection('SPREAD', 'HOME', null)).toBeNull();
  });
});

describe('formatLine', () => {
  it('signs a spread, because the sign is the information', () => {
    expect(formatLine('SPREAD', -3)).toBe('-3');
    expect(formatLine('SPREAD', 1.5)).toBe('+1.5');
  });

  it('does not sign a total, which has no direction', () => {
    // The old shared formatter rendered this as "+34.5", which reads as a
    // spread and made a 34.5-point total look like a 34.5-point handicap.
    expect(formatLine('TOTAL', 34.5)).toBe('34.5');
    expect(formatLine('TOTAL', 44)).toBe('44');
  });

  it('calls a pick-em PK rather than showing a signed zero', () => {
    expect(formatLine('SPREAD', 0)).toBe('PK');
  });

  it('renders an absent line as an em dash, never as zero', () => {
    expect(formatLine('SPREAD', null)).toBe('—');
    expect(formatLine('TOTAL', null)).toBe('—');
    expect(formatLine('SPREAD', NaN)).toBe('—');
  });
});

describe('describeQuote', () => {
  it('names the team that owns the number', () => {
    // ARI is home and stored at +1.5, so ARI is the underdog and CAR lays it.
    expect(describeQuote('SPREAD', 'HOME', 1.5, TEAMS)).toBe('ARI +1.5');
    expect(describeQuote('SPREAD', 'AWAY', 1.5, TEAMS)).toBe('CAR -1.5');
  });

  it('reads correctly when the home team is favoured', () => {
    expect(describeQuote('SPREAD', 'HOME', -7, TEAMS)).toBe('ARI -7');
    expect(describeQuote('SPREAD', 'AWAY', -7, TEAMS)).toBe('CAR +7');
  });

  it('states the two sides of a total without signing it', () => {
    expect(describeQuote('TOTAL', 'OVER', 34.5, TEAMS)).toBe('Over 34.5');
    expect(describeQuote('TOTAL', 'UNDER', 34.5, TEAMS)).toBe('Under 34.5');
  });

  it('gives a moneyline the team alone, since the price is the quote', () => {
    expect(describeQuote('MONEYLINE', 'HOME', null, TEAMS)).toBe('ARI');
    expect(describeQuote('MONEYLINE', 'AWAY', null, TEAMS)).toBe('CAR');
  });

  it('falls back to the team name when no line was captured', () => {
    expect(describeQuote('SPREAD', 'AWAY', null, TEAMS)).toBe('CAR');
  });

  it('the two spread sides are always opposite, across the range', () => {
    // A sweep rather than a single case: a sign bug that survives one
    // example rarely survives the sign changing under it.
    for (const stored of [-14, -7, -3, -0.5, 0, 2.5, 10]) {
      const home = lineForSelection('SPREAD', 'HOME', stored)!;
      const away = lineForSelection('SPREAD', 'AWAY', stored)!;
      expect(home + away).toBe(0);
    }
  });
});

describe('describeConsensus', () => {
  it('states a consensus spread against the home team by name', () => {
    // A bare "-2.5" on a consensus card does not say whose -2.5 it is.
    expect(describeConsensus('SPREAD', -2.5, TEAMS)).toBe('ARI -2.5');
  });

  it('states a consensus total unsigned', () => {
    expect(describeConsensus('TOTAL', 47, TEAMS)).toBe('47');
  });

  it('renders a refused consensus as an em dash', () => {
    expect(describeConsensus('SPREAD', null, TEAMS)).toBe('—');
  });
});

describe('valuesDisagree', () => {
  // The real numbers from the Model Audit screen. Comparing raw floats made
  // every model trip the reproducibility warning after a rerun, which is how
  // "6 models scored differently" appeared with "0.2028300 vs 0.2028300"
  // offered as the evidence.
  const FLOAT_NOISE = [0.2028300123456789, 0.2028300123456791]; // ~2e-16 apart
  const REAL_DELTA = [0.2158905902667402, 0.2159002395310044]; // 9.65e-06 apart

  it('does not call float noise a disagreement', () => {
    expect(valuesDisagree(FLOAT_NOISE)).toBe(false);
  });

  it('still catches the delta that mattered', () => {
    // team-ratings-v1 before and after the determinism correction. If the
    // tolerance ever swallows this, the warning stops being able to find
    // the defect it was built to find.
    expect(valuesDisagree(REAL_DELTA)).toBe(true);
  });

  it('there are six orders of magnitude between those two cases', () => {
    const noise = Math.abs(FLOAT_NOISE[1]! - FLOAT_NOISE[0]!);
    const real = Math.abs(REAL_DELTA[1]! - REAL_DELTA[0]!);
    expect(real / noise).toBeGreaterThan(1e6);
  });

  it('a single run cannot disagree with itself', () => {
    expect(valuesDisagree([0.2])).toBe(false);
    expect(valuesDisagree([])).toBe(false);
  });

  it('identical values agree', () => {
    expect(valuesDisagree([0.2, 0.2, 0.2])).toBe(false);
  });

  it('compares the extremes, not adjacent pairs', () => {
    // Three runs each a hair apart but spanning a real gap must disagree.
    expect(valuesDisagree([0.20, 0.20 + 4e-10, 0.20 + 8e-10])).toBe(false);
    expect(valuesDisagree([0.20, 0.20 + 4e-10, 0.20 + 1e-5])).toBe(true);
  });

  it('ignores values that are not finite rather than throwing', () => {
    expect(valuesDisagree([0.2, NaN])).toBe(false);
    expect(valuesDisagree([0.2, Infinity, 0.2])).toBe(false);
  });

  it('accepts an explicit tolerance', () => {
    expect(valuesDisagree(REAL_DELTA, 1e-3)).toBe(false);
    expect(valuesDisagree(FLOAT_NOISE, 0)).toBe(true);
  });
});
