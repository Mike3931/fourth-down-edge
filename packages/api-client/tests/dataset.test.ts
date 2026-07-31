import { describe, expect, it } from 'vitest';
import { DEMO_DATA_LABEL } from '@fde/shared-types';
import { generateDemoDataset } from '../src/dataset';
import { evaluateGame } from '../src/evaluate';
import { MockFdeApi } from '../src/api';

describe('generateDemoDataset', () => {
  const ds = generateDemoDataset();

  it('is deterministic for a given seed', () => {
    const a = generateDemoDataset(42);
    const b = generateDemoDataset(42);
    expect(JSON.stringify(a.games)).toBe(JSON.stringify(b.games));
    expect(JSON.stringify(a.oddsSnapshots.slice(0, 20))).toBe(JSON.stringify(b.oddsSnapshots.slice(0, 20)));
  });

  it('creates exactly 16 demonstration games with 32 distinct teams', () => {
    expect(ds.games).toHaveLength(16);
    const teamIds = new Set(ds.games.flatMap((g) => [g.homeTeamId, g.awayTeamId]));
    expect(teamIds.size).toBe(32);
  });

  it('is labeled as demonstration data', () => {
    expect(ds.label).toBe(DEMO_DATA_LABEL);
  });

  it('gives every game three immutable prediction vintages', () => {
    for (const g of ds.games) {
      const preds = ds.predictions.filter((p) => p.gameId === g.id);
      expect(preds.length).toBe(3);
      expect(new Set(preds.map((p) => p.vintage)).size).toBe(3);
      expect(preds.filter((p) => p.isOfficial)).toHaveLength(1);
    }
  });

  it('gives every game opening, mid, and current odds for all three markets', () => {
    for (const g of ds.games) {
      for (const market of ['SPREAD', 'TOTAL', 'MONEYLINE'] as const) {
        const snaps = ds.oddsSnapshots.filter((o) => o.gameId === g.id && o.market === market);
        expect(snaps.length).toBe(3);
        expect(snaps.filter((s) => s.isOpening)).toHaveLength(1);
      }
    }
  });

  it('respects point-in-time ordering on every source record', () => {
    for (const r of [...ds.injuryReports, ...ds.weatherSnapshots]) {
      expect(new Date(r.observedAt).getTime()).toBeLessThanOrEqual(new Date(ds.demoNow).getTime());
    }
    for (const p of ds.predictions) {
      expect(new Date(p.asOfAt).getTime()).toBeLessThanOrEqual(new Date(ds.demoNow).getTime());
    }
  });

  it('includes placeholder and demo-approved model versions', () => {
    expect(ds.modelVersions.some((m) => m.isPlaceholder)).toBe(true);
    expect(ds.modelVersions.some((m) => m.status === 'APPROVED_DEMO')).toBe(true);
  });

  it('demonstrates every data-health status vocabulary value', () => {
    const statuses = new Set(ds.feedStatuses.map((f) => f.status));
    for (const expected of ['CURRENT', 'AGING', 'STALE', 'MISSING', 'CONFLICTING'] as const) {
      expect(statuses.has(expected)).toBe(true);
    }
  });

  it('never marks a moneyline backtest record as a push', () => {
    for (const r of ds.backtest) {
      if (r.market === 'MONEYLINE') expect(r.push).toBe(false);
    }
  });

  it('pushed backtest records return the stake (zero profit)', () => {
    for (const r of ds.backtest.filter((x) => x.push && x.stake > 0)) {
      expect(r.profit).toBe(0);
    }
  });

  it('80% interval coverage is close to the nominal 80%', () => {
    const covered = ds.backtest.filter((r) => r.withinInterval80).length / ds.backtest.length;
    expect(covered).toBeGreaterThan(0.7);
    expect(covered).toBeLessThan(0.9);
  });

  it('bankroll balance reconciles with settled paper bets', () => {
    const settled = ds.bets.filter((b) => b.result !== 'PENDING');
    const profit = settled.reduce((acc, b) => {
      if (b.result === 'WIN') return acc + (b.payout! - b.stake);
      if (b.result === 'LOSS') return acc - b.stake;
      return acc;
    }, 0);
    expect(ds.bankrollAccount.currentBalance).toBeCloseTo(10_000 + profit, 2);
  });
});

describe('evaluateGame produces all four recommendation states across the slate', () => {
  const ds = generateDemoDataset();
  const recs = ds.games.map((g) => evaluateGame(ds, g.id));

  it('covers BET, WATCH, PASS, and DATA INCOMPLETE', () => {
    const statuses = new Set(recs.map((r) => r.status));
    expect(statuses.has('BET')).toBe(true);
    expect(statuses.has('WATCH')).toBe(true);
    expect(statuses.has('PASS')).toBe(true);
    expect(statuses.has('DATA INCOMPLETE')).toBe(true);
  });

  it('recommends PASS (or non-BET) more often than BET', () => {
    const bets = recs.filter((r) => r.status === 'BET').length;
    const passes = recs.filter((r) => r.status === 'PASS').length;
    expect(passes).toBeGreaterThan(bets);
  });

  it('every BET has a positive-EV price, stake breakdown, and manual price', () => {
    for (const r of recs.filter((x) => x.status === 'BET')) {
      expect(r.evPerDollar).toBeGreaterThan(0);
      expect(r.stake).not.toBeNull();
      expect(r.stake!.finalStakeAmount).toBeGreaterThan(0);
      expect(r.manualPriceId).toBeDefined();
      expect(r.stake!.finalStakePct).toBeLessThanOrEqual(0.005 + 1e-12);
    }
  });

  it('every recommendation carries status reasons and invalidation conditions', () => {
    for (const r of recs) {
      expect(r.statusReasons.length).toBeGreaterThan(0);
      expect(r.invalidationConditions.length).toBeGreaterThan(0);
    }
  });

  it('game with unresolved QB is DATA INCOMPLETE', () => {
    const qbGame = ds.games[2]!;
    expect(recs.find((r) => r.gameId === qbGame.id)!.status).toBe('DATA INCOMPLETE');
  });

  it('game with stale odds is DATA INCOMPLETE', () => {
    const staleGame = ds.games[3]!;
    const rec = recs.find((r) => r.gameId === staleGame.id)!;
    expect(rec.status).toBe('DATA INCOMPLETE');
    expect(rec.statusReasons.join(' ')).toMatch(/stale/i);
  });
});

describe('MockFdeApi', () => {
  it('serves the typed contract and appends manual prices immutably', async () => {
    const api = new MockFdeApi();
    const before = (await api.getDataset()).manualPrices.length;
    const rec = await api.submitManualPrice({
      userId: 'demo-user',
      gameId: (await api.getGames())[0]!.id,
      sportsbook: 'bet365 (manual entry)',
      market: 'SPREAD',
      selection: 'HOME',
      line: -3,
      american: -105,
      priceObservedAt: '2026-09-10T15:55:00Z',
      enteredAt: '2026-09-10T15:56:00Z',
      confirmedVisible: true,
    });
    expect(rec.id).toMatch(/^mbp_local_/);
    const after = (await api.getDataset()).manualPrices.length;
    expect(after).toBe(before + 1);
  });

  it('rejects unconfirmed manual prices', async () => {
    const api = new MockFdeApi();
    await expect(
      api.submitManualPrice({
        userId: 'demo-user',
        gameId: 'game_x',
        sportsbook: 'bet365 (manual entry)',
        market: 'SPREAD',
        selection: 'HOME',
        line: -3,
        american: -105,
        priceObservedAt: '2026-09-10T15:55:00Z',
        enteredAt: '2026-09-10T15:56:00Z',
        confirmedVisible: false,
      }),
    ).rejects.toThrow(/confirmed/);
  });
});
