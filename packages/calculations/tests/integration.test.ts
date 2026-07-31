import { describe, expect, it } from 'vitest';
import { DEFAULT_RISK_CONTROLS, type Prediction, type Recommendation } from '@fde/shared-types';
import {
  breakEvenProbability,
  noVigProbabilities,
} from '../src/odds';
import { expectedValuePerDollar } from '../src/ev';
import { computeStake } from '../src/staking';
import { decideRecommendation } from '../src/recommendation';
import {
  VintageOverwriteError,
  addPredictionVintage,
  correctSettlement,
  invalidateRecommendationsForGames,
  placeBet,
  settleBet,
  type LedgerState,
} from '../src/ledger';

/**
 * Integration tests covering the required workflow chains:
 *   manual price -> recommendation -> paper bet -> settlement -> bankroll
 *   data-quality event -> recommendation invalidation
 *   new prediction vintage without overwriting history
 */

const rc = DEFAULT_RISK_CONTROLS;

function freshLedger(): LedgerState {
  return { bets: [], betEvents: [], auditLog: [], bankrollBalance: 10_000 };
}

describe('manual price -> recommendation -> paper bet -> settlement -> bankroll', () => {
  it('runs the full pipeline deterministically', () => {
    // 1) Manual price entered and confirmed: home -3 at -105, consensus -110/-110.
    const manualAmerican = -105;
    const modelCoverProb = 0.565;
    const pushProb = 0.035;

    // 2) Deterministic evaluation.
    const be = breakEvenProbability(manualAmerican, pushProb);
    const edge = modelCoverProb - be;
    const ev = expectedValuePerDollar(modelCoverProb, manualAmerican, pushProb);
    expect(ev).toBeGreaterThan(0);
    expect(edge).toBeGreaterThan(rc.minEdgeForBet);

    const decision = decideRecommendation(
      {
        startingQuarterbackResolved: true,
        injuryFeedAvailable: true,
        marketPriceFresh: true,
        manualPriceConfirmed: true,
        kickoffTimeConsistent: true,
        venueConsistent: true,
        weatherAvailableIfOutdoor: true,
        modelApproved: true,
        predictionTimestampPresent: true,
        featureSnapshotPresent: true,
        criticalProviderHealthy: true,
        dataCompletenessScore: 0.93,
      },
      {
        evPerDollar: ev,
        edge,
        uncertaintyStd: 13.1,
        priceWorseThanMaxAcceptable: false,
        exposureWouldExceedLimit: false,
        modelMarketAligned: false,
        componentsMateriallyDisagree: false,
        invalidatedByNewerInformation: false,
      },
      {
        wouldQualifyAtBetterPrice: false,
        materialInjuryUncertainty: false,
        materialWeatherUncertainty: false,
        lineNearTargetThreshold: false,
        marketMovingTowardAcceptablePrice: false,
        awaitingOfficialUpdate: false,
      },
      rc,
    );
    expect(decision.status).toBe('BET');

    // 3) Stake sizing under caps.
    const stake = computeStake({
      winProbability: modelCoverProb,
      american: manualAmerican,
      pushProbability: pushProb,
      bankroll: 10_000,
      riskControls: rc,
      uncertaintyMultiplier: 0.85,
      dataQualityMultiplier: 0.93,
      calibrationMultiplier: 0.9,
      existingGameExposurePct: 0,
      existingTeamExposurePct: 0,
      existingClusterExposurePct: 0,
      existingWeeklyOpenStakePct: 0,
    });
    expect(stake.finalStakeAmount).toBeGreaterThan(0);
    expect(stake.finalStakePct).toBeLessThanOrEqual(rc.maxPerBetPctOfBankroll + 1e-12);

    // 4) Paper bet placement (append-only ledger).
    let ledger = freshLedger();
    ledger = placeBet(ledger, {
      userId: 'u1',
      bankrollAccountId: 'bk1',
      gameId: 'g1',
      market: 'SPREAD',
      selection: 'HOME',
      line: -3,
      american: manualAmerican,
      stake: stake.finalStakeAmount,
      mode: 'PAPER',
      placedAt: '2026-09-13T15:00:00Z',
      recommendationId: 'rec1',
      predictionId: 'pred1',
      modelVersionId: 'mv1',
      featureSnapshotId: 'fs1',
    });
    expect(ledger.bets).toHaveLength(1);
    expect(ledger.betEvents.map((e) => e.eventType)).toEqual(['PLACED']);
    expect(ledger.auditLog).toHaveLength(1);

    // 5) Settlement (WIN) updates bankroll by stake * (decimal − 1).
    const betId = ledger.bets[0]!.id;
    const before = ledger.bankrollBalance;
    ledger = settleBet(ledger, {
      betId,
      result: 'WIN',
      settledAt: '2026-09-13T23:30:00Z',
      closingLine: -3.5,
      closingAmerican: -110,
      closingLineValuePct: 1.8,
      actor: 'u1',
    });
    const stakeAmt = stake.finalStakeAmount;
    expect(ledger.bankrollBalance).toBeCloseTo(before + stakeAmt * (100 / 105), 2);
    expect(ledger.bets[0]!.result).toBe('WIN');
    expect(ledger.bets[0]!.closingLineValuePct).toBe(1.8);

    // 6) Settled history cannot be silently re-settled.
    expect(() =>
      settleBet(ledger, { betId, result: 'LOSS', settledAt: '2026-09-14T00:00:00Z', actor: 'u1' }),
    ).toThrow(/already settled/);

    // 7) Correction path appends events and adjusts bankroll transparently.
    const corrected = correctSettlement(ledger, betId, 'PUSH', '2026-09-14T01:00:00Z', 'u1', 'stat correction');
    expect(corrected.bets[0]!.result).toBe('PUSH');
    expect(corrected.betEvents.map((e) => e.eventType)).toEqual(['PLACED', 'SETTLED', 'CORRECTED']);
    expect(corrected.bankrollBalance).toBeCloseTo(before, 2); // push returns stake
  });

  it('push settlement leaves bankroll unchanged; loss deducts the stake', () => {
    let ledger = freshLedger();
    ledger = placeBet(ledger, {
      userId: 'u1', bankrollAccountId: 'bk1', gameId: 'g2', market: 'TOTAL',
      selection: 'OVER', line: 45, american: -110, stake: 50, mode: 'PAPER',
      placedAt: '2026-09-13T15:00:00Z',
    });
    const pushed = settleBet(ledger, {
      betId: ledger.bets[0]!.id, result: 'PUSH', settledAt: '2026-09-13T23:00:00Z', actor: 'u1',
    });
    expect(pushed.bankrollBalance).toBe(10_000);

    let ledger2 = freshLedger();
    ledger2 = placeBet(ledger2, {
      userId: 'u1', bankrollAccountId: 'bk1', gameId: 'g3', market: 'MONEYLINE',
      selection: 'AWAY', american: 150, stake: 40, mode: 'PAPER',
      placedAt: '2026-09-13T15:00:00Z',
    });
    const lost = settleBet(ledger2, {
      betId: ledger2.bets[0]!.id, result: 'LOSS', settledAt: '2026-09-13T23:00:00Z', actor: 'u1',
    });
    expect(lost.bankrollBalance).toBe(9_960);
  });

  it('rejects stakes exceeding bankroll', () => {
    const ledger = freshLedger();
    expect(() =>
      placeBet(ledger, {
        userId: 'u1', bankrollAccountId: 'bk1', gameId: 'g1', market: 'MONEYLINE',
        selection: 'HOME', american: -110, stake: 20_000, mode: 'PAPER',
        placedAt: '2026-09-13T15:00:00Z',
      }),
    ).toThrow(/exceeds available bankroll/);
  });
});

describe('data-quality event -> recommendation invalidation', () => {
  const baseRec: Recommendation = {
    id: 'rec1', gameId: 'g1', predictionId: 'p1', market: 'SPREAD', selection: 'HOME',
    line: -3, american: -105, status: 'BET', modelProbability: 0.56,
    conservativeProbability: 0.55, marketNoVigProbability: 0.52, breakEvenProbability: 0.512,
    edge: 0.038, evPerDollar: 0.04, pushProbability: 0.03, fairAmerican: -127,
    confidence: 'MEDIUM', stake: null, supportingFactors: [], opposingFactors: [],
    reasonsToPass: [], invalidationConditions: [], statusReasons: ['ok'],
    createdAt: '2026-09-13T12:00:00Z',
  };

  it('downgrades BET/WATCH to DATA INCOMPLETE for impacted games only', () => {
    const recs = [
      baseRec,
      { ...baseRec, id: 'rec2', gameId: 'g2', status: 'WATCH' as const },
      { ...baseRec, id: 'rec3', gameId: 'g3' },
      { ...baseRec, id: 'rec4', gameId: 'g1', status: 'PASS' as const },
    ];
    const out = invalidateRecommendationsForGames(
      recs, ['g1', 'g2'], 'Injury feed failure', '2026-09-13T13:00:00Z',
    );
    expect(out.find((r) => r.id === 'rec1')!.status).toBe('DATA INCOMPLETE');
    expect(out.find((r) => r.id === 'rec2')!.status).toBe('DATA INCOMPLETE');
    expect(out.find((r) => r.id === 'rec3')!.status).toBe('BET');
    expect(out.find((r) => r.id === 'rec4')!.status).toBe('PASS'); // pass stays pass
    expect(out.find((r) => r.id === 'rec1')!.invalidationReason).toBe('Injury feed failure');
  });
});

describe('prediction vintages are immutable', () => {
  const pred = (id: string, vintage: Prediction['vintage']): Prediction => ({
    id, gameId: 'g1', modelVersionId: 'mv1', vintage,
    asOfAt: '2026-09-10T12:00:00Z', createdAt: '2026-09-10T12:00:01Z',
    featureSnapshotId: 'fs1', homeWinProbability: 0.58, awayWinProbability: 0.42,
    expectedHomeScore: 24.1, expectedAwayScore: 20.9, expectedMargin: 3.2,
    expectedTotal: 45.0, marginInterval80: [-14, 20], totalInterval80: [32, 58],
    marginStd: 13.4, totalStd: 9.8, dataCompletenessScore: 0.92, isOfficial: true,
  });

  it('appends new vintages and preserves earlier ones', () => {
    let history: Prediction[] = [];
    history = addPredictionVintage(history, pred('p1', 'OPENING'));
    history = addPredictionVintage(history, pred('p2', 'EARLY_WEEK'));
    history = addPredictionVintage(history, pred('p3', 'FINAL_INJURY_REPORT'));
    expect(history).toHaveLength(3);
    expect(history[0]!.vintage).toBe('OPENING');
  });

  it('rejects overwriting an existing vintage for the same game and model', () => {
    const history = addPredictionVintage([], pred('p1', 'OPENING'));
    expect(() => addPredictionVintage(history, pred('p9', 'OPENING'))).toThrow(VintageOverwriteError);
    expect(() => addPredictionVintage(history, pred('p1', 'PREGAME'))).toThrow(VintageOverwriteError);
  });
});

describe('no-vig consistency for the full market', () => {
  it('spread two-way market at -105/-115 normalizes and sums to 1', () => {
    const [home, away] = noVigProbabilities([-105, -115]);
    expect(home! + away!).toBeCloseTo(1, 12);
    expect(away!).toBeGreaterThan(home!);
  });
});
