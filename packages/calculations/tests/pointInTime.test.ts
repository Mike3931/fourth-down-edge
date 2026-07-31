import { describe, expect, it } from 'vitest';
import {
  LookaheadError,
  assertForecastUsable,
  assertNoLookahead,
  filterToCutoff,
  isModelUsableAtCutoff,
  isResultUsableAtCutoff,
  isUsableAtCutoff,
  latestAtCutoff,
} from '../src/pointInTime';

const CUTOFF = '2026-09-13T16:00:00Z';

describe('point-in-time cutoff logic', () => {
  it('accepts records observed at or before the cutoff', () => {
    expect(isUsableAtCutoff({ observedAt: '2026-09-13T15:59:59Z' }, CUTOFF)).toBe(true);
    expect(isUsableAtCutoff({ observedAt: CUTOFF }, CUTOFF)).toBe(true);
  });

  it('rejects injury reports observed after the cutoff', () => {
    expect(isUsableAtCutoff({ observedAt: '2026-09-13T16:00:01Z' }, CUTOFF)).toBe(false);
    expect(() =>
      assertNoLookahead([{ observedAt: '2026-09-13T18:00:00Z' }], CUTOFF, 'injury reports'),
    ).toThrow(LookaheadError);
  });

  it('rejects weather forecasts generated after the cutoff', () => {
    expect(() =>
      assertForecastUsable('2026-09-13T17:00:00Z', '2026-09-13T15:00:00Z', CUTOFF),
    ).toThrow(LookaheadError);
  });

  it('rejects weather forecasts observed after the cutoff', () => {
    expect(() =>
      assertForecastUsable('2026-09-13T15:00:00Z', '2026-09-13T17:00:00Z', CUTOFF),
    ).toThrow(LookaheadError);
  });

  it('rejects closing odds in an earlier prediction', () => {
    const closingOdds = { observedAt: '2026-09-13T17:25:00Z' }; // observed near kickoff
    const earlyWeekCutoff = '2026-09-09T12:00:00Z';
    expect(isUsableAtCutoff(closingOdds, earlyWeekCutoff)).toBe(false);
  });

  it('rejects future roster/depth-chart/participation records via the same gate', () => {
    const futureRoster = { observedAt: '2026-09-14T12:00:00Z' };
    expect(filterToCutoff([futureRoster], CUTOFF)).toHaveLength(0);
  });

  it('rejects future game results', () => {
    expect(isResultUsableAtCutoff('2026-09-13T20:05:00Z', CUTOFF)).toBe(false);
    expect(isResultUsableAtCutoff('2026-09-07T03:00:00Z', CUTOFF)).toBe(true);
    expect(isResultUsableAtCutoff(undefined, CUTOFF)).toBe(false);
  });

  it('rejects future model versions', () => {
    expect(isModelUsableAtCutoff('2026-10-01T00:00:00Z', CUTOFF)).toBe(false);
    expect(isModelUsableAtCutoff('2026-08-01T00:00:00Z', CUTOFF)).toBe(true);
    expect(isModelUsableAtCutoff(undefined, CUTOFF)).toBe(false);
  });

  it('latestAtCutoff picks the newest usable record, deterministically', () => {
    const records = [
      { id: 'a', observedAt: '2026-09-10T00:00:00Z' },
      { id: 'b', observedAt: '2026-09-12T00:00:00Z' },
      { id: 'c', observedAt: '2026-09-14T00:00:00Z' }, // future — excluded
    ];
    expect(latestAtCutoff(records, CUTOFF)?.id).toBe('b');
    expect(latestAtCutoff([], CUTOFF)).toBeUndefined();
  });

  it('throws on malformed timestamps instead of silently passing', () => {
    expect(() => isUsableAtCutoff({ observedAt: 'not-a-date' }, CUTOFF)).toThrow(LookaheadError);
  });
});
