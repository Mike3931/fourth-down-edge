import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FEED_THRESHOLDS,
  ageMinutes,
  classifyFreshness,
  worstFreshness,
} from '../src/freshness';
import {
  centralInterval,
  homeWinProbability,
  marginDistribution,
  spreadOutcomeProbabilities,
  totalDistribution,
  totalOutcomeProbabilities,
} from '../src/distributions';

const NOW = '2026-09-13T12:00:00Z';
const odds = DEFAULT_FEED_THRESHOLDS.odds!;

describe('classifyFreshness', () => {
  it('classifies CURRENT within the aging window', () => {
    expect(classifyFreshness('2026-09-13T11:45:00Z', NOW, odds)).toBe('CURRENT');
  });
  it('classifies AGING between windows', () => {
    expect(classifyFreshness('2026-09-13T11:00:00Z', NOW, odds)).toBe('AGING');
  });
  it('classifies STALE beyond the stale window', () => {
    expect(classifyFreshness('2026-09-13T08:00:00Z', NOW, odds)).toBe('STALE');
  });
  it('classifies MISSING when unobserved', () => {
    expect(classifyFreshness(undefined, NOW, odds)).toBe('MISSING');
  });
  it('classifies CONFLICTING when flagged or clock-inverted', () => {
    expect(classifyFreshness('2026-09-13T11:59:00Z', NOW, odds, true)).toBe('CONFLICTING');
    expect(classifyFreshness('2026-09-13T13:00:00Z', NOW, odds)).toBe('CONFLICTING');
  });
  it('aggregates worst-of', () => {
    expect(worstFreshness(['CURRENT', 'AGING', 'STALE'])).toBe('STALE');
    expect(worstFreshness(['CURRENT', 'MISSING'])).toBe('MISSING');
    expect(worstFreshness([])).toBe('CURRENT');
  });
  it('computes age in minutes', () => {
    expect(ageMinutes('2026-09-13T11:30:00Z', NOW)).toBeCloseTo(30, 10);
  });
});

describe('distributions', () => {
  const margin = marginDistribution(3, 13.5);
  const total = totalDistribution(45.5, 10);

  it('margin distribution sums to 1 and excludes zero', () => {
    expect(margin.reduce((a, p) => a + p.probability, 0)).toBeCloseTo(1, 8);
    expect(margin.find((p) => p.value === 0)).toBeUndefined();
  });

  it('home win probability exceeds 50% for a positive expected margin', () => {
    expect(homeWinProbability(margin)).toBeGreaterThan(0.5);
  });

  it('key numbers carry extra mass', () => {
    const at3 = margin.find((p) => p.value === 3)!.probability;
    const at2 = margin.find((p) => p.value === 2)!.probability;
    expect(at3).toBeGreaterThan(at2);
  });

  it('spread outcomes partition probability, with pushes on integers', () => {
    const { cover, push, lose } = spreadOutcomeProbabilities(margin, -3);
    expect(cover + push + lose).toBeCloseTo(1, 8);
    expect(push).toBeGreaterThan(0); // margin exactly 3 pushes home -3
    const halfLine = spreadOutcomeProbabilities(margin, -2.5);
    expect(halfLine.push).toBe(0);
  });

  it('total outcomes partition probability; half lines never push', () => {
    const { over, push, under } = totalOutcomeProbabilities(total, 45.5);
    expect(over + push + under).toBeCloseTo(1, 8);
    expect(push).toBe(0);
    expect(totalOutcomeProbabilities(total, 45).push).toBeGreaterThan(0);
  });

  it('central 80% interval covers at least 80% of mass', () => {
    const [lo, hi] = centralInterval(margin, 0.8);
    const covered = margin
      .filter((p) => p.value >= lo && p.value <= hi)
      .reduce((a, p) => a + p.probability, 0);
    expect(covered).toBeGreaterThanOrEqual(0.8);
    expect(lo).toBeLessThan(hi);
  });
});
