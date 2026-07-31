import { describe, expect, it } from 'vitest';
import { DEFAULT_RISK_CONTROLS } from '@fde/shared-types';
import {
  decideRecommendation,
  type CriticalDataChecks,
  type EdgeAssessment,
  type WatchSignals,
} from '../src/recommendation';

const okChecks: CriticalDataChecks = {
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
  dataCompletenessScore: 0.95,
};

const goodEdge: EdgeAssessment = {
  evPerDollar: 0.04,
  edge: 0.035,
  uncertaintyStd: 13.2,
  priceWorseThanMaxAcceptable: false,
  exposureWouldExceedLimit: false,
  modelMarketAligned: false,
  componentsMateriallyDisagree: false,
  invalidatedByNewerInformation: false,
};

const quiet: WatchSignals = {
  wouldQualifyAtBetterPrice: false,
  materialInjuryUncertainty: false,
  materialWeatherUncertainty: false,
  lineNearTargetThreshold: false,
  marketMovingTowardAcceptablePrice: false,
  awaitingOfficialUpdate: false,
};

const rc = DEFAULT_RISK_CONTROLS;

describe('decideRecommendation — DATA INCOMPLETE', () => {
  it('fires on unresolved quarterback', () => {
    const d = decideRecommendation({ ...okChecks, startingQuarterbackResolved: false }, goodEdge, quiet, rc);
    expect(d.status).toBe('DATA INCOMPLETE');
    expect(d.reasons).toContain('Starting quarterback identity unresolved');
  });
  it('fires on stale market price', () => {
    const d = decideRecommendation({ ...okChecks, marketPriceFresh: false }, goodEdge, quiet, rc);
    expect(d.status).toBe('DATA INCOMPLETE');
  });
  it('fires on unconfirmed manual price', () => {
    const d = decideRecommendation({ ...okChecks, manualPriceConfirmed: false }, goodEdge, quiet, rc);
    expect(d.status).toBe('DATA INCOMPLETE');
  });
  it('fires on unapproved model', () => {
    const d = decideRecommendation({ ...okChecks, modelApproved: false }, goodEdge, quiet, rc);
    expect(d.status).toBe('DATA INCOMPLETE');
  });
  it('fires on low completeness score', () => {
    const d = decideRecommendation({ ...okChecks, dataCompletenessScore: 0.5 }, goodEdge, quiet, rc);
    expect(d.status).toBe('DATA INCOMPLETE');
  });
  it('takes precedence over everything else', () => {
    const d = decideRecommendation(
      { ...okChecks, injuryFeedAvailable: false },
      { ...goodEdge, evPerDollar: -1 },
      { ...quiet, materialInjuryUncertainty: true },
      rc,
    );
    expect(d.status).toBe('DATA INCOMPLETE');
  });
});

describe('decideRecommendation — PASS', () => {
  it('fires on non-positive EV', () => {
    const d = decideRecommendation(okChecks, { ...goodEdge, evPerDollar: 0 }, quiet, rc);
    expect(d.status).toBe('PASS');
    expect(d.reasons).toContain('Expected value is not positive');
  });
  it('fires on sub-threshold edge', () => {
    const d = decideRecommendation(okChecks, { ...goodEdge, edge: 0.01 }, quiet, rc);
    expect(d.status).toBe('PASS');
  });
  it('fires on excessive uncertainty', () => {
    const d = decideRecommendation(okChecks, { ...goodEdge, uncertaintyStd: 20 }, quiet, rc);
    expect(d.status).toBe('PASS');
  });
  it('fires on exposure breach', () => {
    const d = decideRecommendation(okChecks, { ...goodEdge, exposureWouldExceedLimit: true }, quiet, rc);
    expect(d.status).toBe('PASS');
  });
  it('fires on model/market alignment', () => {
    const d = decideRecommendation(okChecks, { ...goodEdge, modelMarketAligned: true }, quiet, rc);
    expect(d.status).toBe('PASS');
  });
  it('fires on invalidation and structural pass beats watch signals', () => {
    const d = decideRecommendation(
      okChecks,
      { ...goodEdge, invalidatedByNewerInformation: true },
      { ...quiet, marketMovingTowardAcceptablePrice: true },
      rc,
    );
    expect(d.status).toBe('PASS');
  });
});

describe('decideRecommendation — WATCH', () => {
  it('soft price-only pass with watch signal becomes WATCH', () => {
    const d = decideRecommendation(
      okChecks,
      { ...goodEdge, edge: 0.015 },
      { ...quiet, wouldQualifyAtBetterPrice: true },
      rc,
    );
    expect(d.status).toBe('WATCH');
  });
  it('material injury uncertainty produces WATCH even with a good edge', () => {
    const d = decideRecommendation(okChecks, goodEdge, { ...quiet, materialInjuryUncertainty: true }, rc);
    expect(d.status).toBe('WATCH');
  });
  it('ineligible app mode produces WATCH, never BET', () => {
    const d = decideRecommendation(okChecks, goodEdge, quiet, rc, false);
    expect(d.status).toBe('WATCH');
  });
});

describe('decideRecommendation — BET', () => {
  it('requires everything to be clean', () => {
    const d = decideRecommendation(okChecks, goodEdge, quiet, rc);
    expect(d.status).toBe('BET');
    expect(d.reasons.length).toBeGreaterThan(3);
  });
  it('is deterministic', () => {
    const a = decideRecommendation(okChecks, goodEdge, quiet, rc);
    const b = decideRecommendation(okChecks, goodEdge, quiet, rc);
    expect(a).toEqual(b);
  });
});
