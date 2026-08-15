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
  describeBookCoverage,
  describeConsensus,
  describeQuote,
  formatLine,
  lineForSelection,
  describeBrierDelta,
  describeStrengthEdge,
  formatDrawdownUnits,
  formatUnits,
  soonestScheduledWeek,
  valuesDisagree,
  weekLabel,
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

describe('formatUnits and formatDrawdownUnits', () => {
  // The Forward Test screen renders every unit figure through one signed
  // formatter, and one of those figures is a DRAWDOWN. `max_drawdown_units`
  // is computed as `max(peak - bankroll)`, so it is always positive and
  // always means a loss — and it was printed "+3.00u" directly beside
  // "P&L (paper) +1.20u", where the same plus sign means a gain. The
  // reader gets the sign convention from the neighbour.
  it('signs a P&L, because the direction is the information', () => {
    expect(formatUnits(1.2)).toBe('+1.20u');
    expect(formatUnits(-6.59)).toBe('-6.59u');
    expect(formatUnits(0)).toBe('+0.00u');
  });

  it('never signs a drawdown', () => {
    expect(formatDrawdownUnits(3)).toBe('3.00u');
    expect(formatDrawdownUnits(0)).toBe('0.00u');
  });

  it('a drawdown of three units does not read as a gain of three', () => {
    // The regression stated directly.
    expect(formatDrawdownUnits(3)).not.toBe(formatUnits(3));
  });

  it('reports a negative drawdown as absent rather than as a gain', () => {
    // Not reachable from `max(peak - bankroll)`, which is why it must not
    // be rendered as if it were meaningful if it ever arrives.
    expect(formatDrawdownUnits(-1)).toBe('—');
  });

  it('renders a missing figure as an em dash, never as zero', () => {
    expect(formatUnits(null)).toBe('—');
    expect(formatDrawdownUnits(null)).toBe('—');
    expect(formatUnits(NaN)).toBe('—');
    expect(formatDrawdownUnits(NaN)).toBe('—');
  });
});

describe('describeBrierDelta', () => {
  // Model Audit rendered a model that beat the benchmark by more than the
  // noise band as "better than the market", in success green. Those scores
  // come from seasons 2024 and 2025, which docs/model-governance.md records
  // as BURNED: "Neither 2024 nor 2025 may be presented as an out-of-sample
  // test result." A superiority claim is exactly what a burned period
  // cannot support, and the project's standing constraints forbid claiming
  // predictive superiority outright.
  //
  // The measurement is real and worth showing. The inference is not.
  const BAND = 0.02903; // the real band at n=285

  it('calls a difference inside the band indistinguishable', () => {
    expect(describeBrierDelta(-0.00025, BAND).label).toBe('indistinguishable from the market');
    expect(describeBrierDelta(0.01282, BAND).label).toBe('indistinguishable from the market');
  });

  it('never says a model is better than the market', () => {
    // The regression. -0.05 clears the band comfortably.
    expect(describeBrierDelta(-0.05, BAND).label).not.toMatch(/better/i);
  });

  it('states the lower score as a fact about these games only', () => {
    const { label } = describeBrierDelta(-0.05, BAND);
    expect(label).toContain('lower');
    expect(label).toContain('these games');
  });

  it('still reports the other direction plainly', () => {
    // naive-homefield-v1 in 2024: +0.04468, genuinely worse.
    const { label } = describeBrierDelta(0.04468, BAND);
    expect(label).toContain('higher');
    expect(label).toContain('these games');
  });

  it('does not tone a lower score as a success', () => {
    // Green is the visual form of the same claim the words no longer make.
    expect(describeBrierDelta(-0.05, BAND).tone).not.toBe('success');
    expect(describeBrierDelta(0.04468, BAND).tone).toBe('warning');
  });

  it('the benchmark compared with itself is not a verdict', () => {
    expect(describeBrierDelta(0, BAND).label).toBe('indistinguishable from the market');
  });
});

describe('weekLabel and soonestScheduledWeek', () => {
  // The Slate screen derived its week filter with `if (g.week)` and picked
  // its default with `.filter((g) => g.week && ...)`. Both are falsy for
  // week 0, which is what preseason games carry — so eleven fixtures
  // vanished from every week option, and the screen labelled REG week 1
  // "(next)" while those eleven kicked off four weeks sooner. They were
  // also the only games with captured prices, so the Live Slate one click
  // away was showing exactly the games this screen said were not next.
  const PRE = { week: 0, kickoff_utc: '2026-08-14T23:00:00Z' };
  const REG1 = { week: 1, kickoff_utc: '2026-09-10T00:35:00Z' };
  const REG2 = { week: 2, kickoff_utc: '2026-09-17T00:20:00Z' };
  const NOW = new Date('2026-08-11T23:00:00Z').getTime();

  it('names week zero rather than hiding it', () => {
    expect(weekLabel(0)).toBe('Preseason');
    expect(weekLabel(1)).toBe('Week 1');
    expect(weekLabel(18)).toBe('Week 18');
  });

  it('reports an absent week as absent, not as preseason', () => {
    expect(weekLabel(null)).toBe('—');
    expect(weekLabel(undefined)).toBe('—');
  });

  it('picks the soonest upcoming week, including week zero', () => {
    expect(soonestScheduledWeek([REG1, PRE, REG2], NOW)).toBe(0);
  });

  it('skips a week whose games have all kicked off', () => {
    const after = new Date('2026-08-15T00:00:00Z').getTime();
    expect(soonestScheduledWeek([REG1, PRE, REG2], after)).toBe(1);
  });

  it('ignores games with no week at all', () => {
    expect(
      soonestScheduledWeek([{ week: null, kickoff_utc: '2026-08-12T00:00:00Z' }, REG1], NOW),
    ).toBe(1);
  });

  it('returns null when nothing is upcoming', () => {
    const later = new Date('2027-01-01T00:00:00Z').getTime();
    expect(soonestScheduledWeek([REG1, PRE, REG2], later)).toBeNull();
    expect(soonestScheduledWeek([], NOW)).toBeNull();
  });

  it('ignores an unparseable kickoff rather than treating it as imminent', () => {
    expect(soonestScheduledWeek([{ week: 0, kickoff_utc: 'not a date' }, REG1], NOW)).toBe(1);
  });
});

describe('describeStrengthEdge', () => {
  /**
   * The primary justification line on the primary recommendation card said
   * this, verbatim, for the demo dataset's headline pick:
   *
   *   "Opponent-adjusted team strength favors Los Angeles Rams by 6.1
   *    points vs market 9"
   *
   * The pick was LA +9. `expectedMargin` is HOME-relative and was -6.1, so
   * the model favours CINCINNATI by 6.1 — `homeWinProbability` was 0.319,
   * which says the same thing. Two errors compounded: the team named was
   * the one being BET rather than the one favoured, and `Math.abs()` threw
   * away the sign that identifies the favourite.
   *
   * The underlying analysis was sound and is worth stating: the model has
   * Cincinnati by 6.1, the market prices them at 9, so nine points is more
   * than the model thinks Cincinnati is worth. Recommending an underdog is
   * the ordinary shape of a value bet, which is exactly when the old
   * sentence was guaranteed to be backwards.
   */
  const TEAMS = { homeTeamId: 'LAR', awayTeamId: 'CIN' };

  it('names the team the model actually favours, not the one being bet', () => {
    // The regression, with the real numbers.
    const line = describeStrengthEdge(-6.1, 9, TEAMS);
    expect(line).toContain('CIN');
    expect(line).not.toMatch(/favou?rs LAR/i);
  });

  it('names the home team when the margin is positive', () => {
    expect(describeStrengthEdge(6.1, -9, TEAMS)).toContain('LAR');
  });

  it('states the model margin and the market number as comparable', () => {
    const line = describeStrengthEdge(-6.1, 9, TEAMS);
    expect(line).toContain('6.1');
    expect(line).toContain('9');
  });

  it('says the market is asking more than the model when it is', () => {
    // Model: CIN by 6.1. Market: CIN laying 9. The market wants 2.9 more.
    expect(describeStrengthEdge(-6.1, 9, TEAMS)).toMatch(/2\.9/);
  });

  it('says nothing at a pick-em rather than naming an arbitrary side', () => {
    expect(describeStrengthEdge(0, 0, TEAMS)).toBeNull();
    expect(describeStrengthEdge(0.05, 0, TEAMS)).toBeNull();
  });

  it('refuses a non-finite margin rather than printing NaN', () => {
    expect(describeStrengthEdge(NaN, 9, TEAMS)).toBeNull();
    expect(describeStrengthEdge(-6.1, NaN, TEAMS)).toBeNull();
  });

  it('the two sides are symmetric', () => {
    const a = describeStrengthEdge(-6.1, 9, TEAMS);
    const b = describeStrengthEdge(6.1, -9, TEAMS);
    expect(a).toContain('CIN');
    expect(b).toContain('LAR');
    expect(a).not.toBe(b);
  });
});

describe('describeBookCoverage', () => {
  it('does not say "1 books"', () => {
    expect(describeBookCoverage(1, 1).label).toBe('1 book');
    expect(describeBookCoverage(3, 3).label).toBe('3 books');
  });

  it('says in words that one permitted book is not a market consensus', () => {
    // The burn-in cohort admits a single book on purpose. Rendering
    // "-2.5 · 1 book" and stopping would let a reader take one book's
    // price for a market number, which is the whole thing the consensus
    // machinery exists to avoid.
    const { caveat } = describeBookCoverage(1, 1);
    expect(caveat).toContain('not a market consensus');
    expect(caveat).toContain('this cohort permits');
  });

  it('adds nothing to an ordinary three-book consensus', () => {
    expect(describeBookCoverage(3, 3).caveat).toBeNull();
    expect(describeBookCoverage(7, 3).caveat).toBeNull();
  });

  it('states the rule when coverage is thin but not the minimum', () => {
    // Two books under a one-book minimum: not the permitted floor, still
    // thinner than the normal rule, so the applied minimum is stated
    // rather than implied.
    expect(describeBookCoverage(2, 1).caveat).toBe('minimum applied: 1');
  });

  it('says nothing extra when the rule was never recorded', () => {
    // Rows written before `min_books_applied` existed. Silence is
    // correct; inventing "minimum applied: 3" would assert something the
    // row does not carry.
    expect(describeBookCoverage(1, null).caveat).toBeNull();
    expect(describeBookCoverage(1, undefined).caveat).toBeNull();
    expect(describeBookCoverage(1, null).label).toBe('1 book');
  });

  it('never claims a consensus is better than it is', () => {
    for (const n of [1, 2]) {
      const { label, caveat } = describeBookCoverage(n, 1);
      expect(label).not.toContain('consensus');
      expect((caveat ?? '').toLowerCase()).not.toContain('reliable');
    }
  });
});
