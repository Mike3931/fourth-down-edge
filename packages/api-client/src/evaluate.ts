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
  assertNoLookahead,
  breakEvenProbability,
  computeStake,
  decideRecommendation,
  describeStrengthEdge,
  expectedValuePerDollar,
  filterToCutoff,
  isModelUsableAtCutoff,
  isUsableAtCutoff,
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

function pickLatest(
  odds: OddsSnapshot[],
  gameId: string,
  market: MarketType,
  cutoffAt: string | undefined,
): OddsSnapshot | undefined {
  const inScope = odds.filter((o) => o.gameId === gameId && o.market === market);
  const usable = cutoffAt === undefined ? inScope : filterToCutoff(inScope, cutoffAt);
  return usable.sort((a, b) => a.observedAt.localeCompare(b.observedAt)).at(-1);
}

/**
 * Latest snapshot that was actually observable at `cutoffAt`.
 *
 * Use this anywhere the result feeds a prediction, an edge, or a
 * recommendation. The cutoff is required: this used to be one function with
 * an optional `cutoffAt`, and an evaluation call site that omitted it
 * silently priced on future data. That failure is invisible in testing
 * because the demo dataset is generated in correct chronological order —
 * which is precisely why the original unwired point-in-time guard went
 * unnoticed until an audit found it (see docs/limitations.md #17).
 *
 * Splitting the two uses into two names means the unsafe one can only be
 * reached by asking for it.
 */
export function latestSnapshotAsOf(
  odds: OddsSnapshot[],
  gameId: string,
  market: MarketType,
  cutoffAt: string,
): OddsSnapshot | undefined {
  return pickLatest(odds, gameId, market, cutoffAt);
}

/**
 * Latest snapshot regardless of observation time — the current state of the
 * market.
 *
 * Display only: Weekly Slate, Market Monitor, and the Game Lab headers show
 * where the market is now, which is a legitimately different question from
 * what was knowable at a cutoff. The result of this function must never
 * reach a prediction or a recommendation; use `latestSnapshotAsOf` there.
 */
export function latestSnapshotForDisplay(
  odds: OddsSnapshot[],
  gameId: string,
  market: MarketType,
): OddsSnapshot | undefined {
  return pickLatest(odds, gameId, market, undefined);
}

function openingSnapshot(
  odds: OddsSnapshot[],
  gameId: string,
  market: MarketType,
  cutoffAt?: string,
): OddsSnapshot | undefined {
  return odds.find(
    (o) => o.gameId === gameId && o.market === market && o.isOpening &&
      (cutoffAt === undefined || isUsableAtCutoff(o, cutoffAt)),
  );
}

/** No-vig probability required by a specific selection at a specific snapshot. */
function noVigProbabilityForSelection(snapshot: OddsSnapshot, selection: SelectionSide): number | undefined {
  if (selection === 'HOME' || selection === 'AWAY') {
    if (snapshot.homeAmerican === 0 && snapshot.awayAmerican === 0) return undefined;
    const [homeNV, awayNV] = noVigProbabilities([snapshot.homeAmerican, snapshot.awayAmerican]);
    return selection === 'HOME' ? homeNV : awayNV;
  }
  if (snapshot.overAmerican === undefined || snapshot.underAmerican === undefined) return undefined;
  const [overNV, underNV] = noVigProbabilities([snapshot.overAmerican, snapshot.underAmerican]);
  return selection === 'OVER' ? overNV : underNV;
}

/**
 * True when the market has drifted in this selection's favor since opening
 * — i.e. the price has been improving, even if it has not yet crossed the
 * qualifying threshold. Uses the same opening/current snapshots Market
 * Monitor already displays as line movement.
 *
 * For SPREAD/TOTAL this compares the point line, not the quoted American
 * odds: this demo dataset's per-snapshot juice is drawn independently at
 * random (it does not track the line), so a probability computed from juice
 * alone would mostly be reading noise. The line is where this generator's
 * genuine movement lives. MONEYLINE has no line to compare, so it falls
 * back to no-vig probability — though note this dataset generator computes
 * a single moneyline price once per game and reuses it for every snapshot,
 * so moneyline movement will never be detected until that's also modeled.
 */
export function movingTowardAcceptablePrice(
  ds: DemoDataset,
  gameId: string,
  market: MarketType,
  selection: SelectionSide,
  cutoffAt?: string,
): boolean {
  const opening = openingSnapshot(ds.oddsSnapshots, gameId, market, cutoffAt);
  // pickLatest, not latestSnapshotAsOf: this function's own cutoff is
  // optional and mirrors openingSnapshot above, so the undefined case is
  // handled here rather than smuggled past a required parameter.
  const current = pickLatest(ds.oddsSnapshots, gameId, market, cutoffAt);
  if (!opening || !current || opening.id === current.id) return false;

  if (market === 'MONEYLINE') {
    const openProb = noVigProbabilityForSelection(opening, selection);
    const currentProb = noVigProbabilityForSelection(current, selection);
    if (openProb === undefined || currentProb === undefined) return false;
    return currentProb < openProb - 0.003; // ignore noise below ~0.3 probability points
  }

  if (opening.line === undefined || current.line === undefined) return false;
  const delta = current.line - opening.line; // ignore sub-half-point noise
  if (Math.abs(delta) < 0.5) return false;
  if (market === 'SPREAD') {
    return selection === 'HOME' ? delta > 0 : delta < 0;
  }
  // TOTAL: OVER wants the total to fall, UNDER wants it to rise.
  return selection === 'OVER' ? delta < 0 : delta > 0;
}

/**
 * cutoffAt is REQUIRED. It was optional, and the one caller that omitted it
 * (the web app's useCandidates path) therefore computed displayed edges and
 * EV from records unrestricted by any observation cutoff - numbers that look
 * exactly like a recommendation's.
 *
 * Requiring it changes no behaviour today: the demo dataset contains zero
 * records dated after ds.demoNow, so filtering to that cutoff is a no-op.
 * That is the point. The same "correct data hides the defect" property is
 * why the original unwired point-in-time guard survived until an audit
 * (docs/limitations.md #17), so the guard belongs in the signature rather
 * than in the dataset's good behaviour.
 */
export function evaluateCandidates(ds: DemoDataset, gameId: string, cutoffAt: string): CandidateEvaluation[] {
  const pred = ds.predictions.find((p) => p.gameId === gameId && p.isOfficial);
  if (!pred) return [];
  const mDist = marginDistribution(pred.expectedMargin, pred.marginStd);
  const tDist = totalDistribution(pred.expectedTotal, pred.totalStd);
  const out: CandidateEvaluation[] = [];

  const freshManual = (market: MarketType, selection: SelectionSide): ManualBookPrice | undefined => {
    const candidates = ds.manualPrices.filter(
      (m) => m.gameId === gameId && m.market === market && m.selection === selection && m.confirmedVisible,
    );
    const inScope = cutoffAt === undefined
      ? candidates
      : candidates.filter((m) => isUsableAtCutoff({ observedAt: m.enteredAt }, cutoffAt));
    return inScope.sort((a, b) => a.enteredAt.localeCompare(b.enteredAt)).at(-1);
  };

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
  const spread = latestSnapshotAsOf(ds.oddsSnapshots, gameId, 'SPREAD', cutoffAt);
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
  const total = latestSnapshotAsOf(ds.oddsSnapshots, gameId, 'TOTAL', cutoffAt);
  if (total?.line !== undefined && total.overAmerican !== undefined && total.underAmerican !== undefined) {
    const { over, push: pushP, under } = totalOutcomeProbabilities(tDist, total.line);
    const [overNV, underNV] = noVigProbabilities([total.overAmerican, total.underAmerican]);
    const mOver = freshManual('TOTAL', 'OVER');
    const mUnder = freshManual('TOTAL', 'UNDER');
    push('TOTAL', 'OVER', total.line, mOver?.american ?? total.overAmerican, over, overNV!, pushP, mOver);
    push('TOTAL', 'UNDER', total.line, mUnder?.american ?? total.underAmerican, under, underNV!, pushP, mUnder);
  }

  // MONEYLINE
  const ml = latestSnapshotAsOf(ds.oddsSnapshots, gameId, 'MONEYLINE', cutoffAt);
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
  /**
   * Portfolio-wide open stake as a fraction of bankroll. Legitimately a
   * single scalar shared across every game in a batch call — it's a
   * whole-portfolio total, not per-game.
   */
  existingWeeklyOpenStakePct?: number;
  /**
   * Already-open stake in dollars, keyed by id. Per-game and per-team caps
   * cannot share one flat percentage across a batch evaluation of many
   * games — each game needs its own existing-exposure baseline, and each
   * selection's cap depends on which specific team it backs. Dollar amounts
   * (not pre-computed percentages) let evaluateGame divide by the bankroll
   * it's already resolving, so callers don't need to duplicate that math.
   */
  existingGameExposureByGame?: Record<string, number>;
  existingTeamExposureByTeam?: Record<string, number>;
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

  // Prediction cutoff for this evaluation: "right now" in the demo's frozen
  // clock. Every lookup below is restricted to records observed at or before
  // this instant — point-in-time integrity enforced live in the pipeline
  // that actually produces a recommendation, not just unit-tested in
  // isolation (see @fde/calculations/pointInTime and docs/limitations.md,
  // "The frozen demo clock").
  const now = ds.demoNow;
  try {
    assertNoLookahead(ds.oddsSnapshots.filter((o) => o.gameId === gameId), now, `odds snapshots for ${gameId}`);
    assertNoLookahead(ds.weatherSnapshots.filter((w) => w.gameId === gameId), now, `weather snapshots for ${gameId}`);
    assertNoLookahead(ds.injuryReports.filter((r) => r.gameId === gameId), now, `injury reports for ${gameId}`);
    assertNoLookahead(ds.availabilitySnapshots.filter((a) => a.gameId === gameId), now, `availability snapshots for ${gameId}`);
  } catch (err) {
    // A lookahead violation is a missing/bad critical-data condition, not a
    // fatal error: degrade this one game to DATA INCOMPLETE (spec principle
    // "missing critical information must produce Data Incomplete, not a
    // fabricated recommendation") rather than throwing out of a batch
    // evaluation and taking every other game down with it.
    const reason = err instanceof Error ? err.message : String(err);
    return {
      id: `rec_${gameId}`,
      gameId,
      predictionId: pred?.id ?? 'missing',
      market: 'SPREAD',
      selection: 'HOME',
      american: -110,
      status: 'DATA INCOMPLETE',
      modelProbability: 0,
      conservativeProbability: 0,
      marketNoVigProbability: 0,
      breakEvenProbability: 0,
      edge: 0,
      evPerDollar: 0,
      pushProbability: 0,
      fairAmerican: 0,
      confidence: 'LOW',
      stake: null,
      supportingFactors: [],
      opposingFactors: [],
      reasonsToPass: [],
      invalidationConditions: [],
      statusReasons: [`Point-in-time integrity violation: ${reason}`],
      createdAt: now,
    };
  }

  const candidates = evaluateCandidates(ds, gameId, now);
  const best = [...candidates].sort((a, b) => b.edge - a.edge)[0];

  const spreadSnap = latestSnapshotAsOf(ds.oddsSnapshots, gameId, 'SPREAD', now);
  const priceAge = spreadSnap ? ageMinutes(spreadSnap.observedAt, now) : Infinity;

  const availability = filterToCutoff(ds.availabilitySnapshots.filter((a) => a.gameId === gameId), now);
  const qbUnresolved = availability.some((a) => {
    const player = ds.players.find((p) => p.id === a.playerId);
    return player?.position === 'QB' && a.activeProbability > 0.15 && a.activeProbability < 0.85;
  });
  const outdoor = game.roofStatus === 'OUTDOOR' || game.roofStatus === 'RETRACTABLE_OPEN';
  const weatherInScope = filterToCutoff(ds.weatherSnapshots.filter((w) => w.gameId === gameId), now);
  const weather = weatherInScope.sort((a, b) => a.observedAt.localeCompare(b.observedAt)).at(-1);
  const injuryFeedDown = ds.feedStatuses.find((f) => f.feed === 'injury')?.status === 'MISSING';

  const manual = best?.manualPriceId
    ? ds.manualPrices.find((m) => m.id === best.manualPriceId)
    : undefined;
  const manualFresh = manual
    ? manual.confirmedVisible && ageMinutes(manual.enteredAt, now) <= rc.maxPriceAgeMinutes
    : false;

  const official = ds.modelVersions.find((m) => m.id === pred?.modelVersionId);

  const checks: CriticalDataChecks = {
    startingQuarterbackResolved: !qbUnresolved,
    injuryFeedAvailable: !injuryFeedDown,
    marketPriceFresh: priceAge <= 8 * 60,
    // Manual price confirmation blocks only when a manual record exists but
    // is unconfirmed; absence of a manual price caps at WATCH further below.
    manualPriceConfirmed: manual ? manual.confirmedVisible : true,
    // Structurally always true in v1, not a forgotten TODO: this dataset
    // (and the Supabase schema behind it) models exactly one canonical
    // schedule source per game, so there is no second feed for kickoff time
    // or venue to disagree with. A genuine conflict check requires multiple
    // real schedule providers reconciled through entity_mappings — see
    // docs/limitations.md ("Schedule conflict detection").
    kickoffTimeConsistent: true,
    venueConsistent: true,
    weatherAvailableIfOutdoor: !outdoor || !!weather,
    // Both the model's approval status AND its approval date relative to
    // this cutoff matter — a model approved after the fact could not
    // legitimately have produced a prediction at this cutoff.
    modelApproved: official?.status === 'APPROVED_DEMO' && isModelUsableAtCutoff(official?.approvedAt, now),
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

  // Existing exposure (dollars) converted to a fraction of the current
  // bankroll, resolved per-game and for whichever specific team this
  // selection backs. A single flat scalar can't work across a batch
  // evaluation of many games — each game has its own already-committed
  // stake, and team exposure depends on which side is actually selected.
  const existingGameExposureDollars = ctx.existingGameExposureByGame?.[gameId] ?? 0;
  const selectedTeamId =
    best?.selection === 'HOME' ? game.homeTeamId : best?.selection === 'AWAY' ? game.awayTeamId : undefined;
  const existingTeamExposureDollars = selectedTeamId
    ? (ctx.existingTeamExposureByTeam?.[selectedTeamId] ?? 0)
    : 0;
  const existingGameExposurePct = bankroll > 0 ? existingGameExposureDollars / bankroll : 0;
  const existingTeamExposurePct = bankroll > 0 ? existingTeamExposureDollars / bankroll : 0;
  // No cross-game correlation model exists — that would require modeling
  // (shared officiating crews, shared weather systems, division/conference
  // linkage) the source specification doesn't define, and fabricating one
  // would be new scope, not a fix. Same-game, cross-market exposure is the
  // one correlation signal this app already reasons about (see the Bet
  // Card's correlation warning), so it doubles as the cluster proxy rather
  // than leaving the cap permanently inert at zero.
  const existingClusterExposurePct = existingGameExposurePct;
  const existingWeeklyOpenStakePct = ctx.existingWeeklyOpenStakePct ?? 0;

  // A cap already fully consumed must PASS the recommendation outright, not
  // surface as a green BET card with a computed stake of $0 — the spec
  // requires "portfolio exposure is within limits" as a BET precondition.
  const exposureWouldExceedLimit =
    existingWeeklyOpenStakePct >= rc.maxWeeklyOpenStakePct ||
    existingGameExposurePct >= rc.maxPerGamePct ||
    existingTeamExposurePct >= rc.maxPerTeamWeeklyPct ||
    existingClusterExposurePct >= rc.maxCorrelatedClusterPct;

  const edgeA: EdgeAssessment = {
    evPerDollar: best?.evPerDollar ?? -1,
    edge: best?.edge ?? -1,
    uncertaintyStd: pred?.marginStd ?? 99,
    // Structurally always false in v1: both of these require comparing the
    // CURRENT price/conditions against a PERSISTED prior recommendation
    // snapshot (the price/data as of when a recommendation was first
    // issued). evaluateGame recomputes fresh on every call — recommendations
    // aren't persisted the way predictions are (predictions have immutable
    // vintages; recommendations don't) — so there is nothing earlier to
    // compare against yet. Genuine support needs recommendation history
    // tracking, which is real backend work, not a wiring fix. See
    // docs/limitations.md ("Recommendation invalidation over time").
    priceWorseThanMaxAcceptable: false,
    exposureWouldExceedLimit,
    modelMarketAligned: best ? Math.abs(best.edge) < 0.005 : true,
    componentsMateriallyDisagree: componentsDisagree(ds, pred?.id),
    invalidatedByNewerInformation: false,
  };

  const watch: WatchSignals = {
    wouldQualifyAtBetterPrice: !!best && best.edge >= rc.minEdgeForBet * 0.6 && best.edge < rc.minEdgeForBet,
    materialInjuryUncertainty: injuryUncertain,
    materialWeatherUncertainty: weatherUncertain,
    lineNearTargetThreshold: !!best && Math.abs(best.edge - rc.minEdgeForBet) < 0.005,
    marketMovingTowardAcceptablePrice:
      !!best && movingTowardAcceptablePrice(ds, gameId, best.market, best.selection, now),
    awaitingOfficialUpdate: ds.injuryReports.some(
      (r) => r.gameId === gameId && r.practiceFri === 'NO_DATA' && r.designation === 'QUESTIONABLE',
    ),
  };

  // Always eligible: the product currently defines no ineligible mode.
  // PAPER is the default and always eligible; REAL_TRACKING requires
  // completing the loss-budget/acknowledgment flow in Settings before the
  // user can even enter it, so there is no further "ineligible mode" state
  // to gate on here (unlike the exposure caps, this isn't a hardcoded stub
  // masking a real signal — there is genuinely nothing to check yet).
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

  let stake =
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
          existingGameExposurePct,
          existingTeamExposurePct,
          existingClusterExposurePct,
          existingWeeklyOpenStakePct,
        })
      : null;

  // A cap can leave a sliver of technically-legal room (a few cents) without
  // being fully exhausted. That room is real and correctly computed, but a
  // sub-minimum stake isn't a wager any real sportsbook accepts, so
  // presenting it as an actionable BET card would overstate what's on
  // offer. Downgrade to PASS rather than show a stake nobody could place.
  const MIN_VIABLE_STAKE = 1;
  if (decision.status === 'BET' && stake && stake.finalStakeAmount < MIN_VIABLE_STAKE) {
    decision = {
      status: 'PASS',
      reasons: [
        `Remaining risk-limit room ($${stake.finalStakeAmount.toFixed(2)}) is below the $${MIN_VIABLE_STAKE.toFixed(2)} minimum viable stake — ${stake.bindingConstraint} is nearly exhausted`,
        ...decision.reasons,
      ],
    };
    stake = null;
  }

  const home = ds.teams.find((t) => t.id === game.homeTeamId)!;
  const away = ds.teams.find((t) => t.id === game.awayTeamId)!;
  // NOT the team the model favours - the team being BET. It was called
  // `favoredTeam`, and the rationale line below took that name at its word.
  const selectedTeam = best?.selection === 'HOME' ? home.name : best?.selection === 'AWAY' ? away.name : undefined;

  const supporting: string[] = [];
  const opposing: string[] = [];
  if (best && pred) {
    if (best.market === 'SPREAD') {
      // `describeStrengthEdge` names the team the MODEL favours. This line
      // used to name the SELECTED team — the one being bet
      // — and pass `Math.abs(expectedMargin)`, discarding the sign that
      // identifies the favourite. Recommending an underdog is the ordinary
      // shape of a value bet, so it read backwards exactly when the card
      // was working. See marketDisplay.ts, where it is under test.
      const strength = describeStrengthEdge(pred.expectedMargin, spreadSnap?.line ?? 0, {
        homeTeamId: home.abbreviation,
        awayTeamId: away.abbreviation,
      });
      if (strength) supporting.push(strength);
    }
    if (best.edge > 0.015) supporting.push(`Market disagreement: conservative probability ${(best.conservativeProbability * 100).toFixed(1)}% vs break-even ${(best.breakEvenProbability * 100).toFixed(1)}%`);
    const avail = availability.find((a) => a.activeProbability < 0.9);
    if (avail) {
      const pl = ds.players.find((p) => p.id === avail.playerId);
      opposing.push(`Replacement-player concern: ${pl?.name ?? 'starter'} (${pl?.position}) active probability ${(avail.activeProbability * 100).toFixed(0)}%`);
    }
    if (weather && weather.severity !== 'LOW' && weather.severity !== 'NONE') opposing.push(`Weather risk: wind ${weather.windMph} mph, severity ${weather.severity}`);
    if (best.priceSource !== 'bet365 (manual entry)') opposing.push('Price is mock consensus, not a manually confirmed book price');
    if (existingGameExposureDollars > 0) {
      opposing.push(`Existing exposure on this game: ${(existingGameExposurePct * 100).toFixed(2)}% of bankroll (cap ${(rc.maxPerGamePct * 100).toFixed(2)}%)`);
    }
    if (existingTeamExposureDollars > 0 && selectedTeam) {
      opposing.push(`Existing weekly exposure to ${selectedTeam}: ${(existingTeamExposurePct * 100).toFixed(2)}% of bankroll (cap ${(rc.maxPerTeamWeeklyPct * 100).toFixed(2)}%)`);
    }
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
