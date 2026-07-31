import {
  DEFAULT_RISK_CONTROLS,
  type ManualBookPrice,
  type MarketType,
  type OddsSnapshot,
  type Recommendation,
  type RiskControls,
  type SelectionSide,
} from '@fde/shared-types';
import {
  ageMinutes,
  americanToImpliedProbability,
  breakEvenProbability,
  computeStake,
  decideRecommendation,
  expectedValuePerDollar,
  marginDistribution,
  noVigProbabilities,
  probabilityToFairAmerican,
  spreadOutcomeProbabilities,
  totalDistribution,
  totalOutcomeProbabilities,
  type CriticalDataChecks,
  type EdgeAssessment,
  type WatchSignals,
} from '@fde/calculations';
import type { DemoDataset } from './dataset';

/**
 * Deterministic game evaluation: joins the latest official prediction with
 * the freshest odds snapshot and any confirmed manual price, then runs the
 * deterministic recommendation engine and stake pipeline from
 * @fde/calculations. No language-model output enters any number here.
 */

export interface CandidateEvaluation {
  market: MarketType;
  selection: SelectionSide;
  line?: number;
  american: number;
  priceSource: 'CONSENSUS (mock)' | 'bet365 (manual entry)';
  manualPriceId?: string;
  modelProbability: number;
  conservativeProbability: number;
  marketNoVigProbability: number;
  breakEvenProbability: number;
  pushProbability: number;
  edge: number;
  evPerDollar: number;
  fairAmerican: number;
}

/** Shrink model probability toward market no-vig prob (conservatism). */
const MARKET_SHRINK = 0.35;

export function latestSnapshot(
  odds: OddsSnapshot[],
  gameId: string,
  market: MarketType,
): OddsSnapshot | undefined {
  return odds
    .filter((o) => o.gameId === gameId && o.market === market)
    .sort((a, b) => a.observedAt.localeCompare(b.observedAt))
    .at(-1);
}

export function evaluateCandidates(ds: DemoDataset, gameId: string): CandidateEvaluation[] {
  const pred = ds.predictions.find((p) => p.gameId === gameId && p.isOfficial);
  if (!pred) return [];
  const mDist = marginDistribution(pred.expectedMargin, pred.marginStd);
  const tDist = totalDistribution(pred.expectedTotal, pred.totalStd);
  const out: CandidateEvaluation[] = [];

  const freshManual = (market: MarketType, selection: SelectionSide): ManualBookPrice | undefined =>
    ds.manualPrices
      .filter((m) => m.gameId === gameId && m.market === market && m.selection === selection && m.confirmedVisible)
      .sort((a, b) => a.enteredAt.localeCompare(b.enteredAt))
      .at(-1);

  const push = (market: MarketType, selection: SelectionSide, line: number | undefined,
    american: number, modelP: number, marketP: number, pushP: number,
    manual?: ManualBookPrice): void => {
    const conservative = modelP * (1 - MARKET_SHRINK) + marketP * MARKET_SHRINK;
    const be = breakEvenProbability(american, pushP);
    out.push({
      market, selection, line, american,
      priceSource: manual ? 'bet365 (manual entry)' : 'CONSENSUS (mock)',
      manualPriceId: manual?.id,
      modelProbability: modelP,
      conservativeProbability: conservative,
      marketNoVigProbability: marketP,
      breakEvenProbability: be,
      pushProbability: pushP,
      edge: conservative - be,
      evPerDollar: expectedValuePerDollar(conservative, american, pushP),
      fairAmerican: probabilityToFairAmerican(Math.min(0.985, Math.max(0.015, conservative))),
    });
  };

  // SPREAD
  const spread = latestSnapshot(ds.oddsSnapshots, gameId, 'SPREAD');
  if (spread?.line !== undefined) {
    const { cover, push: pushP } = spreadOutcomeProbabilities(mDist, spread.line);
    const awayRes = spreadOutcomeProbabilities(mDist, spread.line); // away covers when home doesn't
    const [homeNV, awayNV] = noVigProbabilities([spread.homeAmerican, spread.awayAmerican]);
    const manualHome = freshManual('SPREAD', 'HOME');
    push('SPREAD', 'HOME', spread.line, manualHome?.american ?? spread.homeAmerican, cover, homeNV!, pushP, manualHome);
    const manualAway = freshManual('SPREAD', 'AWAY');
    push('SPREAD', 'AWAY', spread.line === 0 ? 0 : -spread.line, manualAway?.american ?? spread.awayAmerican,
      awayRes.lose, awayNV!, pushP, manualAway);
  }

  // TOTAL
  const total = latestSnapshot(ds.oddsSnapshots, gameId, 'TOTAL');
  if (total?.line !== undefined && total.overAmerican !== undefined && total.underAmerican !== undefined) {
    const { over, push: pushP, under } = totalOutcomeProbabilities(tDist, total.line);
    const [overNV, underNV] = noVigProbabilities([total.overAmerican, total.underAmerican]);
    const mOver = freshManual('TOTAL', 'OVER');
    const mUnder = freshManual('TOTAL', 'UNDER');
    push('TOTAL', 'OVER', total.line, mOver?.american ?? total.overAmerican, over, overNV!, pushP, mOver);
    push('TOTAL', 'UNDER', total.line, mUnder?.american ?? total.underAmerican, under, underNV!, pushP, mUnder);
  }

  // MONEYLINE
  const ml = latestSnapshot(ds.oddsSnapshots, gameId, 'MONEYLINE');
  if (ml) {
    const [homeNV, awayNV] = noVigProbabilities([ml.homeAmerican, ml.awayAmerican]);
    const mHome = freshManual('MONEYLINE', 'HOME');
    const mAway = freshManual('MONEYLINE', 'AWAY');
    push('MONEYLINE', 'HOME', undefined, mHome?.american ?? ml.homeAmerican, pred.homeWinProbability, homeNV!, 0, mHome);
    push('MONEYLINE', 'AWAY', undefined, mAway?.american ?? ml.awayAmerican, pred.awayWinProbability, awayNV!, 0, mAway);
  }

  return out;
}

export interface EvaluationContext {
  riskControls?: RiskControls;
  bankroll?: number;
  existingWeeklyOpenStakePct?: number;
  existingGameExposurePct?: number;
  existingTeamExposurePct?: number;
}

export function evaluateGame(
  ds: DemoDataset,
  gameId: string,
  ctx: EvaluationContext = {},
): Recommendation {
  const rc = ctx.riskControls ?? DEFAULT_RISK_CONTROLS;
  const bankroll = ctx.bankroll ?? ds.bankrollAccount.currentBalance;
  const game = ds.games.find((g) => g.id === gameId);
  if (!game) throw new Error(`Unknown game ${gameId}`);
  const pred = ds.predictions.find((p) => p.gameId === gameId && p.isOfficial);
  const candidates = evaluateCandidates(ds, gameId);
  const best = [...candidates].sort((a, b) => b.edge - a.edge)[0];

  const now = ds.demoNow;
  const spreadSnap = latestSnapshot(ds.oddsSnapshots, gameId, 'SPREAD');
  const priceAge = spreadSnap ? ageMinutes(spreadSnap.observedAt, now) : Infinity;

  const availability = ds.availabilitySnapshots.filter((a) => a.gameId === gameId);
  const qbUnresolved = availability.some((a) => {
    const player = ds.players.find((p) => p.id === a.playerId);
    return player?.position === 'QB' && a.activeProbability > 0.15 && a.activeProbability < 0.85;
  });
  const outdoor = game.roofStatus === 'OUTDOOR' || game.roofStatus === 'RETRACTABLE_OPEN';
  const weather = ds.weatherSnapshots.find((w) => w.gameId === gameId);
  const injuryFeedDown = ds.feedStatuses.find((f) => f.feed === 'injury')?.status === 'MISSING';

  const manual = best?.manualPriceId
    ? ds.manualPrices.find((m) => m.id === best.manualPriceId)
    : undefined;
  const manualFresh = manual
    ? manual.confirmedVisible && ageMinutes(manual.enteredAt, now) <= rc.maxPriceAgeMinutes
    : false;

  const checks: CriticalDataChecks = {
    startingQuarterbackResolved: !qbUnresolved,
    injuryFeedAvailable: !injuryFeedDown,
    marketPriceFresh: priceAge <= 8 * 60,
    // Manual price confirmation blocks only when a manual record exists but
    // is unconfirmed; absence of a manual price caps at WATCH further below.
    manualPriceConfirmed: manual ? manual.confirmedVisible : true,
    kickoffTimeConsistent: true,
    venueConsistent: true,
    weatherAvailableIfOutdoor: !outdoor || !!weather,
    modelApproved:
      ds.modelVersions.find((m) => m.id === pred?.modelVersionId)?.status === 'APPROVED_DEMO',
    predictionTimestampPresent: !!pred?.asOfAt,
    featureSnapshotPresent: !!pred?.featureSnapshotId,
    criticalProviderHealthy: !ds.feedStatuses.some(
      (f) => f.resolutionStatus === 'FAILED' && f.impactedGameIds.includes(gameId),
    ),
    dataCompletenessScore: pred?.dataCompletenessScore ?? 0,
  };

  const injuryUncertain = availability.some(
    (a) => a.activeProbability > 0.2 && a.activeProbability < 0.9 && a.estimatedTeamImpactPts >= 1.5,
  );
  const weatherUncertain = !!weather && (weather.severity === 'HIGH' || weather.severity === 'CRITICAL');

  const edgeA: EdgeAssessment = {
    evPerDollar: best?.evPerDollar ?? -1,
    edge: best?.edge ?? -1,
    uncertaintyStd: pred?.marginStd ?? 99,
    priceWorseThanMaxAcceptable: false,
    exposureWouldExceedLimit: (ctx.existingWeeklyOpenStakePct ?? 0) >= rc.maxWeeklyOpenStakePct,
    modelMarketAligned: best ? Math.abs(best.edge) < 0.005 : true,
    componentsMateriallyDisagree: componentsDisagree(ds, pred?.id),
    invalidatedByNewerInformation: false,
  };

  const watch: WatchSignals = {
    wouldQualifyAtBetterPrice: !!best && best.edge >= rc.minEdgeForBet * 0.6 && best.edge < rc.minEdgeForBet,
    materialInjuryUncertainty: injuryUncertain,
    materialWeatherUncertainty: weatherUncertain,
    lineNearTargetThreshold: !!best && Math.abs(best.edge - rc.minEdgeForBet) < 0.005,
    marketMovingTowardAcceptablePrice: false,
    awaitingOfficialUpdate: ds.injuryReports.some(
      (r) => r.gameId === gameId && r.practiceFri === 'NO_DATA' && r.designation === 'QUESTIONABLE',
    ),
  };

  let decision = decideRecommendation(checks, edgeA, watch, rc, true);

  // BET requires a recently confirmed manual price at the exact line/price.
  if (decision.status === 'BET' && !manualFresh) {
    decision = {
      status: 'WATCH',
      reasons: [
        'Qualifies on consensus pricing — awaiting recent manually confirmed bet365 price',
        ...decision.reasons,
      ],
    };
  }

  const stake =
    decision.status === 'BET' && best
      ? computeStake({
          winProbability: best.conservativeProbability,
          american: best.american,
          pushProbability: best.pushProbability,
          bankroll,
          riskControls: rc,
          uncertaintyMultiplier: 0.85,
          dataQualityMultiplier: checks.dataCompletenessScore,
          calibrationMultiplier: 0.9,
          existingGameExposurePct: ctx.existingGameExposurePct ?? 0,
          existingTeamExposurePct: ctx.existingTeamExposurePct ?? 0,
          existingClusterExposurePct: 0,
          existingWeeklyOpenStakePct: ctx.existingWeeklyOpenStakePct ?? 0,
        })
      : null;

  const home = ds.teams.find((t) => t.id === game.homeTeamId)!;
  const away = ds.teams.find((t) => t.id === game.awayTeamId)!;
  const favoredTeam = best?.selection === 'HOME' ? home.name : best?.selection === 'AWAY' ? away.name : undefined;

  const supporting: string[] = [];
  const opposing: string[] = [];
  if (best && pred) {
    if (Math.abs(pred.expectedMargin) > 0.1 && best.market === 'SPREAD') {
      supporting.push(
        `Opponent-adjusted team strength favors ${favoredTeam} by ${Math.abs(pred.expectedMargin).toFixed(1)} points vs market ${spreadSnap?.line ?? 0}`,
      );
    }
    if (best.edge > 0.015) supporting.push(`Market disagreement: conservative probability ${(best.conservativeProbability * 100).toFixed(1)}% vs break-even ${(best.breakEvenProbability * 100).toFixed(1)}%`);
    const avail = availability.find((a) => a.activeProbability < 0.9);
    if (avail) {
      const pl = ds.players.find((p) => p.id === avail.playerId);
      opposing.push(`Replacement-player concern: ${pl?.name ?? 'starter'} (${pl?.position}) active probability ${(avail.activeProbability * 100).toFixed(0)}%`);
    }
    if (weather && weather.severity !== 'LOW' && weather.severity !== 'NONE') opposing.push(`Weather risk: wind ${weather.windMph} mph, severity ${weather.severity}`);
    if (best.priceSource !== 'bet365 (manual entry)') opposing.push('Price is mock consensus, not a manually confirmed book price');
    opposing.push('Demo thresholds are not historically validated');
  }

  return {
    id: `rec_${gameId}`,
    gameId,
    predictionId: pred?.id ?? 'missing',
    manualPriceId: best?.manualPriceId,
    market: best?.market ?? 'SPREAD',
    selection: best?.selection ?? 'HOME',
    line: best?.line,
    american: best?.american ?? -110,
    status: decision.status,
    modelProbability: best?.modelProbability ?? 0,
    conservativeProbability: best?.conservativeProbability ?? 0,
    marketNoVigProbability: best?.marketNoVigProbability ?? 0,
    breakEvenProbability: best?.breakEvenProbability ?? 0,
    edge: best?.edge ?? 0,
    evPerDollar: best?.evPerDollar ?? 0,
    pushProbability: best?.pushProbability ?? 0,
    fairAmerican: best?.fairAmerican ?? 0,
    confidence: (pred?.dataCompletenessScore ?? 0) > 0.92 && (best?.edge ?? 0) > 0.03 ? 'HIGH' : (best?.edge ?? 0) > 0.015 ? 'MEDIUM' : 'LOW',
    stake,
    supportingFactors: supporting,
    opposingFactors: opposing,
    reasonsToPass: decision.status === 'PASS' ? decision.reasons : [],
    invalidationConditions: [
      'Starting quarterback status changes',
      `Line moves past ${best?.market === 'TOTAL' ? 'total' : 'spread'} target`,
      'Manual price confirmation older than 60 minutes at decision time',
      'Any critical feed failure affecting this game',
    ],
    targetPrice: best ? Math.max(best.fairAmerican, -125) : undefined,
    invalidationPrice: best ? best.american - 10 : undefined,
    statusReasons: decision.reasons,
    createdAt: ds.demoNow,
  };
}

function componentsDisagree(ds: DemoDataset, predictionId?: string): boolean {
  if (!predictionId) return false;
  const comps = ds.predictionComponents.filter(
    (c) => c.predictionId === predictionId && !c.isPlaceholder && c.weight > 0,
  );
  if (comps.length < 2) return false;
  const margins = comps.map((c) => c.expectedMargin);
  return Math.max(...margins) - Math.min(...margins) > 6.5;
}
