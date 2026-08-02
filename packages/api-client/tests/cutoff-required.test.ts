import { describe, expect, it } from 'vitest';
import {
  evaluateCandidates,
  generateDemoDataset,
  latestSnapshotAsOf,
  latestSnapshotForDisplay,
  type DemoDataset,
} from '../src';

/**
 * Point-in-time integrity has failed here once already: the guard functions
 * existed and were unit-tested, but `evaluateGame` never called them
 * (docs/limitations.md #17). It survived because the demo dataset is
 * generated in correct chronological order, so the defect produced no wrong
 * numbers to notice.
 *
 * That property has not changed — the dataset still contains zero records
 * after `demoNow`. So a second omission would be just as invisible. These
 * tests pin the structural guards that make omission impossible rather than
 * merely absent: the evaluation-path functions take a REQUIRED cutoff, and
 * the unrestricted variant is reachable only by asking for it by name.
 */

function ds(): DemoDataset {
  return generateDemoDataset();
}

describe('the dataset cannot itself be the guarantee', () => {
  it('has no records dated after demoNow, which is why omission hides', () => {
    const d = ds();
    const after = (arr: { observedAt: string }[]) =>
      arr.filter((x) => x.observedAt > d.demoNow).length;

    expect(after(d.oddsSnapshots)).toBe(0);
    expect(after(d.weatherSnapshots)).toBe(0);
    expect(after(d.injuryReports)).toBe(0);
    expect(after(d.availabilitySnapshots)).toBe(0);
  });
});

describe('latestSnapshotAsOf respects the cutoff', () => {
  it('excludes a snapshot observed after the cutoff', () => {
    const d = ds();
    const gameId = d.games[0]!.id;
    const spreads = d.oddsSnapshots
      .filter((o) => o.gameId === gameId && o.market === 'SPREAD')
      .sort((a, b) => a.observedAt.localeCompare(b.observedAt));
    expect(spreads.length).toBeGreaterThan(1);

    const newest = spreads.at(-1)!;
    const previous = spreads.at(-2)!;

    // A cutoff just before the newest observation must not return it.
    const picked = latestSnapshotAsOf(d.oddsSnapshots, gameId, 'SPREAD', previous.observedAt);
    expect(picked?.id).toBe(previous.id);
    expect(picked?.id).not.toBe(newest.id);
  });

  it('returns the newest observation when the cutoff is current', () => {
    const d = ds();
    const gameId = d.games[0]!.id;
    const picked = latestSnapshotAsOf(d.oddsSnapshots, gameId, 'SPREAD', d.demoNow);
    expect(picked).toBeDefined();
    expect(picked!.observedAt <= d.demoNow).toBe(true);
  });
});

describe('latestSnapshotForDisplay is the opt-in unrestricted variant', () => {
  it('agrees with the as-of variant while no future records exist', () => {
    const d = ds();
    const gameId = d.games[0]!.id;
    const display = latestSnapshotForDisplay(d.oddsSnapshots, gameId, 'SPREAD');
    const asOf = latestSnapshotAsOf(d.oddsSnapshots, gameId, 'SPREAD', d.demoNow);
    expect(display?.id).toBe(asOf?.id);
  });

  it('diverges the moment a future record exists — the case that used to be silent', () => {
    const d = ds();
    const gameId = d.games[0]!.id;
    const spreads = d.oddsSnapshots.filter(
      (o) => o.gameId === gameId && o.market === 'SPREAD',
    );
    const future = { ...spreads.at(-1)!, id: 'odds_from_the_future', observedAt: '2099-01-01T00:00:00Z' };
    const polluted = [...d.oddsSnapshots, future];

    // Display shows it; the evaluation path must not.
    expect(latestSnapshotForDisplay(polluted, gameId, 'SPREAD')?.id).toBe('odds_from_the_future');
    expect(latestSnapshotAsOf(polluted, gameId, 'SPREAD', d.demoNow)?.id).not.toBe(
      'odds_from_the_future',
    );
  });
});

describe('evaluateCandidates prices only on observable records', () => {
  it('produces candidates at the demo cutoff', () => {
    const d = ds();
    const out = evaluateCandidates(d, d.games[0]!.id, d.demoNow);
    expect(Array.isArray(out)).toBe(true);
  });

  it('ignores an injected future snapshot', () => {
    const d = ds();
    const gameId = d.games[0]!.id;
    const baseline = evaluateCandidates(d, gameId, d.demoNow);

    const spreads = d.oddsSnapshots.filter(
      (o) => o.gameId === gameId && o.market === 'SPREAD',
    );
    // A wildly different future line would move every edge if it leaked in.
    const future = {
      ...spreads.at(-1)!,
      id: 'odds_from_the_future',
      observedAt: '2099-01-01T00:00:00Z',
      line: (spreads.at(-1)!.line ?? 0) + 21,
    };
    const polluted: DemoDataset = { ...d, oddsSnapshots: [...d.oddsSnapshots, future] };

    const after = evaluateCandidates(polluted, gameId, d.demoNow);
    expect(after.map((c) => [c.market, c.selection, c.line, c.edge])).toEqual(
      baseline.map((c) => [c.market, c.selection, c.line, c.edge]),
    );
  });
});
