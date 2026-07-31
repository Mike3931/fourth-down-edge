import type { RecommendationStatus, RiskControls } from '@fde/shared-types';

/**
 * Deterministic recommendation engine.
 *
 * Returns exactly one of: BET, WATCH, PASS, DATA INCOMPLETE — with the
 * ordered list of reasons that produced the status. No probabilistic or
 * language-model logic is permitted here.
 *
 * Thresholds supplied via RiskControls are DEMO THRESHOLDS in version 1 and
 * are labeled as such in the UI; they are not historically validated.
 */

export interface CriticalDataChecks {
  startingQuarterbackResolved: boolean;
  injuryFeedAvailable: boolean;
  marketPriceFresh: boolean;
  manualPriceConfirmed: boolean;
  kickoffTimeConsistent: boolean;
  venueConsistent: boolean;
  weatherAvailableIfOutdoor: boolean;
  modelApproved: boolean;
  predictionTimestampPresent: boolean;
  featureSnapshotPresent: boolean;
  criticalProviderHealthy: boolean;
  dataCompletenessScore: number;
}

export interface EdgeAssessment {
  evPerDollar: number;
  edge: number;
  /** Std of the margin distribution as an uncertainty proxy. */
  uncertaintyStd: number;
  priceWorseThanMaxAcceptable: boolean;
  exposureWouldExceedLimit: boolean;
  modelMarketAligned: boolean;
  componentsMateriallyDisagree: boolean;
  invalidatedByNewerInformation: boolean;
}

export interface WatchSignals {
  wouldQualifyAtBetterPrice: boolean;
  materialInjuryUncertainty: boolean;
  materialWeatherUncertainty: boolean;
  lineNearTargetThreshold: boolean;
  marketMovingTowardAcceptablePrice: boolean;
  awaitingOfficialUpdate: boolean;
}

export interface RecommendationDecision {
  status: RecommendationStatus;
  reasons: string[];
}

export function decideRecommendation(
  checks: CriticalDataChecks,
  edge: EdgeAssessment,
  watch: WatchSignals,
  rc: RiskControls,
  userModeEligible = true,
): RecommendationDecision {
  // 1) DATA INCOMPLETE — any critical condition fails.
  const incomplete: string[] = [];
  if (!checks.startingQuarterbackResolved) incomplete.push('Starting quarterback identity unresolved');
  if (!checks.injuryFeedAvailable) incomplete.push('Critical injury feed missing');
  if (!checks.marketPriceFresh) incomplete.push('Market price stale');
  if (!checks.manualPriceConfirmed) incomplete.push('Manual price not confirmed');
  if (!checks.kickoffTimeConsistent) incomplete.push('Kickoff time conflict');
  if (!checks.venueConsistent) incomplete.push('Venue conflict');
  if (!checks.weatherAvailableIfOutdoor) incomplete.push('Weather unavailable for outdoor game');
  if (!checks.modelApproved) incomplete.push('Model version not approved');
  if (!checks.predictionTimestampPresent) incomplete.push('Prediction timestamp missing');
  if (!checks.featureSnapshotPresent) incomplete.push('Feature snapshot missing');
  if (!checks.criticalProviderHealthy) incomplete.push('Critical provider failure');
  if (checks.dataCompletenessScore < rc.minDataCompletenessScore) {
    incomplete.push(
      `Data-completeness score ${checks.dataCompletenessScore.toFixed(2)} below minimum ${rc.minDataCompletenessScore.toFixed(2)}`,
    );
  }
  if (incomplete.length > 0) return { status: 'DATA INCOMPLETE', reasons: incomplete };

  // 2) PASS — hard disqualifiers.
  const pass: string[] = [];
  if (edge.invalidatedByNewerInformation) pass.push('Recommendation invalidated by newer information');
  if (edge.evPerDollar <= 0) pass.push('Expected value is not positive');
  if (edge.edge < rc.minEdgeForBet) {
    pass.push(`Edge ${(edge.edge * 100).toFixed(1)}% below required threshold ${(rc.minEdgeForBet * 100).toFixed(1)}% (demo threshold)`);
  }
  if (edge.uncertaintyStd > rc.maxUncertaintyMarginStd) pass.push('Uncertainty too high');
  if (edge.priceWorseThanMaxAcceptable) pass.push('Price worse than maximum acceptable price');
  if (edge.exposureWouldExceedLimit) pass.push('Portfolio exposure would exceed a limit');
  if (edge.modelMarketAligned) pass.push('Model and market are effectively aligned');
  if (edge.componentsMateriallyDisagree) pass.push('Model components materially disagree');

  // 3) WATCH signals.
  const watchReasons: string[] = [];
  if (watch.wouldQualifyAtBetterPrice) watchReasons.push('Selection would qualify at a better price');
  if (watch.materialInjuryUncertainty) watchReasons.push('Injury uncertainty remains material');
  if (watch.materialWeatherUncertainty) watchReasons.push('Weather uncertainty remains material');
  if (watch.lineNearTargetThreshold) watchReasons.push('Line is close to the target threshold');
  if (watch.marketMovingTowardAcceptablePrice) watchReasons.push('Market moving toward an acceptable price');
  if (watch.awaitingOfficialUpdate) watchReasons.push('Required official update expected');

  if (pass.length > 0) {
    // A price/movement/injury watch signal can soften a marginal pass into
    // WATCH only when the ONLY pass reasons are price- or threshold-related
    // and nothing structural disqualifies the selection.
    const softPassOnly = pass.every((r) =>
      r.startsWith('Edge') || r.startsWith('Price worse') || r.startsWith('Expected value'),
    );
    if (softPassOnly && watchReasons.length > 0) {
      return { status: 'WATCH', reasons: [...watchReasons, ...pass] };
    }
    return { status: 'PASS', reasons: pass };
  }

  if (!userModeEligible) {
    return { status: 'WATCH', reasons: ['Application mode not eligible for bet recommendations', ...watchReasons] };
  }

  if (watchReasons.length > 0) return { status: 'WATCH', reasons: watchReasons };

  return {
    status: 'BET',
    reasons: [
      'All critical data complete',
      'Manual price recently confirmed',
      'Model approved',
      `Edge exceeds demo threshold ${(rc.minEdgeForBet * 100).toFixed(1)}%`,
      'Expected value positive after conservative adjustments',
      'Uncertainty acceptable',
      'Portfolio exposure within limits',
    ],
  };
}
