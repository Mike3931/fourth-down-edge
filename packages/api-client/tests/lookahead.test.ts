import { describe, expect, it } from 'vitest';
import { generateDemoDataset } from '../src/dataset';
import { evaluateGame } from '../src/evaluate';

/**
 * Regression coverage for a real defect found during audit: the point-in-
 * time guard functions in @fde/calculations (assertNoLookahead etc.) — the
 * module implementing the spec's "future information must never enter a
 * historical prediction" requirement — were fully unit-tested in isolation
 * but never called anywhere in the actual evaluation pipeline. evaluateGame
 * just used "whatever is latest" with no cutoff check at all. The demo
 * dataset happens to always be generated correctly, so this defect was
 * invisible in normal operation — which is exactly why it needs a test that
 * deliberately corrupts a record, not just tests against valid data.
 */
const GAME_ID = 'game_2026_w1_CIN_LAR';

describe('evaluateGame point-in-time enforcement', () => {
  it('degrades to DATA INCOMPLETE (not a thrown error) when an odds snapshot is future-dated', () => {
    const ds = generateDemoDataset();
    const futureAt = new Date(new Date(ds.demoNow).getTime() + 3600_000).toISOString();
    const corrupted = {
      ...ds,
      oddsSnapshots: ds.oddsSnapshots.map((o) =>
        o.gameId === GAME_ID && o.market === 'SPREAD' && o.isOpening === false && o.isClosing === false
          ? { ...o, observedAt: futureAt }
          : o,
      ),
    };

    expect(() => evaluateGame(corrupted, GAME_ID)).not.toThrow();
    const rec = evaluateGame(corrupted, GAME_ID);
    expect(rec.status).toBe('DATA INCOMPLETE');
    expect(rec.stake).toBeNull();
    expect(rec.statusReasons.some((r) => r.includes('Point-in-time integrity violation'))).toBe(true);
  });

  it('a lookahead violation on one game does not affect evaluation of other games', () => {
    const ds = generateDemoDataset();
    const futureAt = new Date(new Date(ds.demoNow).getTime() + 3600_000).toISOString();
    // CIN@LAR plays at a dome (SoFi Stadium) and has no weather snapshots at
    // all, so it can't be used to test weather-record corruption — pick a
    // game that genuinely has one instead of assuming.
    const weatherGameId = ds.weatherSnapshots[0]!.gameId;
    const otherGameId = ds.games.find((g) => g.id !== weatherGameId)!.id;
    const corrupted = {
      ...ds,
      weatherSnapshots: ds.weatherSnapshots.map((w) =>
        w.gameId === weatherGameId ? { ...w, observedAt: futureAt } : w,
      ),
    };

    const corruptedGameRec = evaluateGame(corrupted, weatherGameId);
    const otherGameRec = evaluateGame(corrupted, otherGameId);
    expect(corruptedGameRec.status).toBe('DATA INCOMPLETE');
    // The other game's recommendation is byte-identical to evaluating the
    // uncorrupted dataset — proves the corruption is fully contained.
    expect(otherGameRec).toEqual(evaluateGame(ds, otherGameId));
  });

  it('a future-dated injury report also triggers DATA INCOMPLETE', () => {
    const ds = generateDemoDataset();
    const futureAt = new Date(new Date(ds.demoNow).getTime() + 3600_000).toISOString();
    const targetReport = ds.injuryReports.find((r) => r.gameId === GAME_ID);
    if (!targetReport) return; // this game has no injury reports at this seed; nothing to corrupt
    const corrupted = {
      ...ds,
      injuryReports: ds.injuryReports.map((r) =>
        r.id === targetReport.id ? { ...r, observedAt: futureAt } : r,
      ),
    };
    const rec = evaluateGame(corrupted, GAME_ID);
    expect(rec.status).toBe('DATA INCOMPLETE');
  });

  it('a future-dated model approval fails the modelApproved check', () => {
    const ds = generateDemoDataset();
    const futureAt = new Date(new Date(ds.demoNow).getTime() + 3600_000).toISOString();
    const corrupted = {
      ...ds,
      modelVersions: ds.modelVersions.map((m) =>
        m.id === 'mv_ensemble' ? { ...m, approvedAt: futureAt } : m,
      ),
    };
    const rec = evaluateGame(corrupted, GAME_ID);
    expect(rec.status).toBe('DATA INCOMPLETE');
  });

  it('the unmodified demo dataset never violates its own cutoff (baseline sanity)', () => {
    // Some games are legitimately DATA INCOMPLETE for other scripted reasons
    // (QB unresolved, stale odds, missing weather) — that's expected and
    // covered by other tests. What this checks is narrower: evaluateGame
    // never throws, and no recommendation's reasons ever cite a lookahead
    // violation, across the whole slate.
    const ds = generateDemoDataset();
    for (const g of ds.games) {
      expect(() => evaluateGame(ds, g.id)).not.toThrow();
      const rec = evaluateGame(ds, g.id);
      expect(rec.statusReasons.some((r) => r.includes('Point-in-time integrity violation'))).toBe(false);
    }
  });
});
