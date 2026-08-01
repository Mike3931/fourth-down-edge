import { describe, expect, it } from 'vitest';
import type { DemoDataset } from '../src/dataset';
import { movingTowardAcceptablePrice } from '../src/evaluate';

/**
 * Regression coverage for a second real defect found during audit:
 * marketMovingTowardAcceptablePrice was hardcoded false, so that WATCH
 * signal could never fire despite the app already ingesting and displaying
 * opening-vs-current line movement on Market Monitor. Tests use synthetic
 * snapshots (not the full random dataset) so the expected direction is
 * exact and doesn't depend on what a given seed happens to produce.
 */

const GAME_ID = 'g1';

function snapshot(overrides: Partial<DemoDataset['oddsSnapshots'][number]>) {
  return {
    id: 'x', gameId: GAME_ID, market: 'SPREAD' as const, book: 'DEMO', awayAmerican: -110,
    homeAmerican: -110, isOpening: false, isClosing: false, sourceUpdatedAt: '2026-01-01T00:00:00Z',
    observedAt: '2026-01-01T00:00:00Z', ingestedAt: '2026-01-01T00:00:00Z', source: 'demo',
    ...overrides,
  };
}

function dsWith(oddsSnapshots: DemoDataset['oddsSnapshots']): DemoDataset {
  return { oddsSnapshots } as DemoDataset;
}

describe('movingTowardAcceptablePrice — SPREAD', () => {
  it('HOME improves when the home line rises (less negative / more points)', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'SPREAD', isOpening: true, line: -6 }),
      snapshot({ id: 'c', market: 'SPREAD', line: -3 }),
    ]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'HOME')).toBe(true);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'AWAY')).toBe(false);
  });

  it('AWAY improves when the home line falls (more negative)', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'SPREAD', isOpening: true, line: -3 }),
      snapshot({ id: 'c', market: 'SPREAD', line: -6 }),
    ]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'AWAY')).toBe(true);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'HOME')).toBe(false);
  });
});

describe('movingTowardAcceptablePrice — TOTAL', () => {
  it('OVER improves when the total falls', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'TOTAL', isOpening: true, line: 48 }),
      snapshot({ id: 'c', market: 'TOTAL', line: 44 }),
    ]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'TOTAL', 'OVER')).toBe(true);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'TOTAL', 'UNDER')).toBe(false);
  });

  it('UNDER improves when the total rises', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'TOTAL', isOpening: true, line: 42 }),
      snapshot({ id: 'c', market: 'TOTAL', line: 47 }),
    ]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'TOTAL', 'UNDER')).toBe(true);
  });
});

describe('movingTowardAcceptablePrice — noise and edge cases', () => {
  it('ignores sub-half-point movement as noise', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'SPREAD', isOpening: true, line: -3 }),
      snapshot({ id: 'c', market: 'SPREAD', line: -3.25 }),
    ]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'AWAY')).toBe(false);
  });

  it('returns false when there is only one snapshot (no movement to observe)', () => {
    const ds = dsWith([snapshot({ id: 'o', market: 'SPREAD', isOpening: true, line: -3 })]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'SPREAD', 'HOME')).toBe(false);
  });

  it('returns false for a market with no snapshots at all', () => {
    const ds = dsWith([]);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'MONEYLINE', 'HOME')).toBe(false);
  });

  it('MONEYLINE falls back to no-vig probability movement', () => {
    const ds = dsWith([
      snapshot({ id: 'o', market: 'MONEYLINE', isOpening: true, homeAmerican: 120, awayAmerican: -145, line: undefined }),
      snapshot({ id: 'c', market: 'MONEYLINE', homeAmerican: 150, awayAmerican: -180, line: undefined }),
    ]);
    // Home's American odds lengthened from +120 to +150: a longer price
    // means a lower implied win-probability is now required to break even,
    // which is an improvement for a HOME moneyline bettor.
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'MONEYLINE', 'HOME')).toBe(true);
    expect(movingTowardAcceptablePrice(ds, GAME_ID, 'MONEYLINE', 'AWAY')).toBe(false);
  });
});
