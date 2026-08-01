import { describe, expect, it } from 'vitest';
import { generateDemoDataset } from '../src/dataset';
import { evaluateGame } from '../src/evaluate';

/**
 * Regression coverage for a real defect found during audit: the staking
 * caps (per-game, per-team, weekly, correlated-cluster) were always
 * evaluated against a hardcoded zero baseline because evaluateGame was
 * never called with the caller's actual portfolio state. This meant the
 * platform's core capital-preservation controls were structurally inert —
 * a BET could be recommended, and its stake fully sized, with no regard
 * for how much of the bankroll was already committed elsewhere.
 *
 * game_2026_w1_CIN_LAR is a known-BET game at the default seed: HOME
 * spread, conservative edge ~7.4%, $10,000 bankroll, per-bet cap ($50)
 * binds before any other cap at zero existing exposure.
 */
const GAME_ID = 'game_2026_w1_CIN_LAR';
const HOME_TEAM_ID = 'team_LAR';
const AWAY_TEAM_ID = 'team_CIN';

describe('evaluateGame exposure caps against real portfolio state', () => {
  const ds = generateDemoDataset();

  it('baseline: BET with a positive stake when no context is supplied', () => {
    const rec = evaluateGame(ds, GAME_ID);
    expect(rec.status).toBe('BET');
    expect(rec.stake).not.toBeNull();
    expect(rec.stake!.finalStakeAmount).toBeGreaterThan(0);
    expect(rec.stake!.bindingConstraint).toBe('Per-bet cap');
  });

  it('a fully exhausted per-game cap downgrades BET to PASS with a zero stake, not a $0 BET card', () => {
    const rec = evaluateGame(ds, GAME_ID, {
      existingGameExposureByGame: { [GAME_ID]: 10_000 }, // far beyond the 1% ($100) game cap
    });
    expect(rec.status).toBe('PASS');
    expect(rec.stake).toBeNull();
    expect(rec.statusReasons).toContain('Portfolio exposure would exceed a limit');
  });

  it('a fully exhausted per-team cap downgrades BET to PASS for that team\'s selection', () => {
    const rec = evaluateGame(ds, GAME_ID, {
      existingTeamExposureByTeam: { [HOME_TEAM_ID]: 10_000 }, // far beyond the 1.5% team cap
    });
    expect(rec.status).toBe('PASS');
    expect(rec.stake).toBeNull();
  });

  it('exposure on the OPPOSING team has no effect on a HOME-selection recommendation', () => {
    const rec = evaluateGame(ds, GAME_ID, {
      existingTeamExposureByTeam: { [AWAY_TEAM_ID]: 10_000 },
    });
    expect(rec.status).toBe('BET');
    expect(rec.stake).not.toBeNull();
  });

  it('a fully exhausted weekly-open-stake cap downgrades BET to PASS', () => {
    const rec = evaluateGame(ds, GAME_ID, { existingWeeklyOpenStakePct: 0.10 }); // cap is 4%
    expect(rec.status).toBe('PASS');
    expect(rec.stake).toBeNull();
  });

  it('partial (non-exhausting) game exposure reduces available room and can become the binding constraint', () => {
    // Room under the 1% game cap after $70 already committed is tighter
    // than the $50 (0.5%) per-bet cap, so the game cap should now bind.
    const bankroll = ds.bankrollAccount.currentBalance;
    const expectedRoom = bankroll * 0.01 - 70;
    const rec = evaluateGame(ds, GAME_ID, {
      existingGameExposureByGame: { [GAME_ID]: 70 },
    });
    expect(rec.status).toBe('BET');
    expect(rec.stake).not.toBeNull();
    expect(rec.stake!.bindingConstraint).toBe('Same-game cap');
    expect(rec.stake!.finalStakeAmount).toBeCloseTo(expectedRoom, 2);
  });

  it('surfaces existing exposure as an opposing factor when present', () => {
    const rec = evaluateGame(ds, GAME_ID, {
      existingGameExposureByGame: { [GAME_ID]: 70 },
    });
    expect(rec.opposingFactors.some((f) => f.includes('Existing exposure on this game'))).toBe(true);
  });

  it('downgrades BET to PASS instead of showing a sub-$1 stake as actionable', () => {
    // Room under the game cap after $99.90 committed is a few cents — legal
    // per the cap math, but not a real, placeable wager.
    const bankroll = ds.bankrollAccount.currentBalance;
    const nearlyExhausted = bankroll * 0.01 - 0.10;
    const rec = evaluateGame(ds, GAME_ID, {
      existingGameExposureByGame: { [GAME_ID]: nearlyExhausted },
    });
    expect(rec.status).toBe('PASS');
    expect(rec.stake).toBeNull();
    expect(rec.statusReasons.some((r) => r.includes('minimum viable stake'))).toBe(true);
  });

  it('is a pure function of its inputs: identical context always produces identical output', () => {
    const ctx = { existingGameExposureByGame: { [GAME_ID]: 40 } };
    const a = evaluateGame(ds, GAME_ID, ctx);
    const b = evaluateGame(ds, GAME_ID, ctx);
    expect(a).toEqual(b);
  });
});
