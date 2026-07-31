/**
 * @fde/shared-types — canonical domain types for Fourth Down Edge.
 *
 * These types mirror the Supabase/PostgreSQL schema (see supabase/migrations)
 * and the OpenAPI contract for the future Python analytical service.
 * All timestamps are ISO-8601 UTC strings in persistent storage.
 */

// ---------------------------------------------------------------------------
// Status vocabularies (exact strings required by the product specification)
// ---------------------------------------------------------------------------

export type RecommendationStatus = 'BET' | 'WATCH' | 'PASS' | 'DATA INCOMPLETE';

export type DataFreshness = 'CURRENT' | 'AGING' | 'STALE' | 'MISSING' | 'CONFLICTING';

export type AppMode = 'PAPER' | 'REAL_TRACKING';

export type MarketType = 'MONEYLINE' | 'SPREAD' | 'TOTAL';

export type SelectionSide = 'HOME' | 'AWAY' | 'OVER' | 'UNDER';

export type PredictionVintage =
  | 'OPENING'
  | 'EARLY_WEEK'
  | 'PRACTICE_UPDATE'
  | 'FINAL_INJURY_REPORT'
  | 'PREGAME'
  | 'CLOSING_CAPTURE';

export type PlayerAvailabilityState = 'INACTIVE' | 'ACTIVE_RESTRICTED' | 'ACTIVE_ORDINARY';

export type BetResult = 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | 'PENDING';

export type ModelApprovalStatus = 'APPROVED_DEMO' | 'PLACEHOLDER' | 'IN_DEVELOPMENT' | 'RETIRED';

export type RoofStatus = 'OUTDOOR' | 'DOME' | 'RETRACTABLE_OPEN' | 'RETRACTABLE_CLOSED';

export type Severity = 'NONE' | 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL';

// ---------------------------------------------------------------------------
// Point-in-time envelope
// ---------------------------------------------------------------------------

/**
 * Every source-derived record distinguishes when the real-world event
 * occurred, when the source updated it, when we first observed it, and when
 * we ingested it. Predictions may only consume records whose observedAt is at
 * or before the prediction cutoff (asOfAt).
 */
export interface PointInTimeMeta {
  /** When the real-world event occurred (if applicable). */
  eventAt?: string;
  /** When the upstream source last updated the information. */
  sourceUpdatedAt: string;
  /** When this application first observed the information. */
  observedAt: string;
  /** When this application ingested/persisted the information. */
  ingestedAt: string;
  /** Provider or feed identifier. */
  source: string;
  /** Source-native record identifier when available. */
  sourceRecordId?: string;
}

// ---------------------------------------------------------------------------
// Reference entities
// ---------------------------------------------------------------------------

export interface Team {
  id: string;
  /** Plain-text name only. No NFL or team logos anywhere in the product. */
  name: string;
  abbreviation: string;
  conference: 'AFC' | 'NFC';
  division: 'EAST' | 'NORTH' | 'SOUTH' | 'WEST';
}

export interface Stadium {
  id: string;
  name: string;
  city: string;
  surface: 'GRASS' | 'TURF';
  roof: 'OUTDOOR' | 'DOME' | 'RETRACTABLE';
  altitudeFt: number;
  timezone: string;
}

export interface OfficialCrew {
  id: string;
  refereeName: string;
  crewPenaltyRatePerGame: number;
  crewOverRate: number;
  sampleGames: number;
}

export interface Player {
  id: string;
  teamId: string;
  name: string;
  position: string;
  depthRole: string;
}

export interface Game {
  id: string;
  season: number;
  week: number;
  kickoffUtc: string;
  awayTeamId: string;
  homeTeamId: string;
  stadiumId: string;
  roofStatus: RoofStatus;
  officialCrewId?: string;
  status: 'SCHEDULED' | 'FINAL' | 'IN_PROGRESS' | 'POSTPONED';
  finalAwayScore?: number;
  finalHomeScore?: number;
}

export interface WeatherSnapshot extends PointInTimeMeta {
  id: string;
  gameId: string;
  forecastGeneratedAt: string;
  temperatureF: number;
  windMph: number;
  gustMph: number;
  precipitationChance: number;
  precipitationType: 'NONE' | 'RAIN' | 'SNOW' | 'MIXED';
  severity: Severity;
}

// ---------------------------------------------------------------------------
// Injuries and availability
// ---------------------------------------------------------------------------

export type PracticeStatus = 'FULL' | 'LIMITED' | 'DNP' | 'NOT_LISTED' | 'NO_DATA';

export type GameDesignation = 'NONE' | 'QUESTIONABLE' | 'DOUBTFUL' | 'OUT' | 'IR';

export interface InjuryReport extends PointInTimeMeta {
  id: string;
  gameId: string;
  playerId: string;
  injury: string;
  practiceWed: PracticeStatus;
  practiceThu: PracticeStatus;
  practiceFri: PracticeStatus;
  designation: GameDesignation;
  conflictWarning?: string;
}

export interface PlayerAvailabilitySnapshot extends PointInTimeMeta {
  id: string;
  gameId: string;
  playerId: string;
  /** Probability the player is active at kickoff, 0..1. */
  activeProbability: number;
  /** Expected snap share conditional on being active, 0..1. */
  expectedSnapShareIfActive: number;
  /** Probability of a restricted workload conditional on active, 0..1. */
  restrictionProbability: number;
  replacementPlayerId?: string;
  /** Replacement quality relative to starter, 0..1 (1 = equal quality). */
  replacementQuality: number;
  /** Estimated team impact in points if the player is unavailable. */
  estimatedTeamImpactPts: number;
  confidence: number;
}

// ---------------------------------------------------------------------------
// Markets and prices
// ---------------------------------------------------------------------------

export interface OddsSnapshot extends PointInTimeMeta {
  id: string;
  gameId: string;
  market: MarketType;
  /** Consensus book label. Version 1 uses clearly-labeled mock consensus. */
  book: string;
  /** Spread line (home-relative) or total line; undefined for moneyline. */
  line?: number;
  awayAmerican: number;
  homeAmerican: number;
  /** OVER price maps to awayAmerican slot for totals? No — explicit fields: */
  overAmerican?: number;
  underAmerican?: number;
  isOpening: boolean;
  isClosing: boolean;
}

/**
 * A manually entered sportsbook price. Immutable and append-only: a new
 * observation always creates a new record; prior records are never mutated.
 */
export interface ManualBookPrice {
  id: string;
  userId: string;
  gameId: string;
  sportsbook: string;
  market: MarketType;
  selection: SelectionSide;
  line?: number;
  american: number;
  /** When the user says the price was visible on the book. */
  priceObservedAt: string;
  /** When the user entered it into this application. */
  enteredAt: string;
  /** Required attestation checkbox state at entry time. */
  confirmedVisible: boolean;
}

// ---------------------------------------------------------------------------
// Models, predictions, recommendations
// ---------------------------------------------------------------------------

export interface ModelVersion {
  id: string;
  name: string;
  version: string;
  status: ModelApprovalStatus;
  target: string;
  algorithm: string;
  trainingPeriod: string;
  validationPeriod: string;
  featureSetVersion: string;
  calibrationVersion: string;
  artifactHash: string;
  gitCommit: string;
  approvedAt?: string;
  knownLimitations: string[];
  dataDependencies: string[];
  predictionHorizons: string[];
  driftStatus: 'STABLE' | 'WATCH' | 'DRIFTING' | 'UNKNOWN';
  lastRetrainedAt?: string;
  nextReviewAt?: string;
  performanceSummary: string;
  isPlaceholder: boolean;
}

export interface PredictionComponent {
  id: string;
  predictionId: string;
  componentName: string;
  /** Home-relative margin contribution or probability contribution. */
  homeWinProbability: number;
  expectedMargin: number;
  expectedTotal: number;
  weight: number;
  isPlaceholder: boolean;
}

export interface Prediction {
  id: string;
  gameId: string;
  modelVersionId: string;
  vintage: PredictionVintage;
  /** Prediction cutoff: only data observed at/before this instant was used. */
  asOfAt: string;
  createdAt: string;
  featureSnapshotId: string;
  homeWinProbability: number;
  awayWinProbability: number;
  expectedHomeScore: number;
  expectedAwayScore: number;
  expectedMargin: number;
  expectedTotal: number;
  marginInterval80: [number, number];
  totalInterval80: [number, number];
  marginStd: number;
  totalStd: number;
  dataCompletenessScore: number;
  isOfficial: boolean;
}

export interface FactorAssessment {
  factor: string;
  rawData: string;
  modelMetric: string;
  estimatedEffect: string;
  confidence: 'LOW' | 'MEDIUM' | 'HIGH';
  sourceTimestamp: string;
  likelyPricedIntoMarket: boolean;
  favors: 'HOME' | 'AWAY' | 'NEUTRAL';
}

export interface StakeBreakdown {
  fullKellyPct: number;
  quarterKellyPct: number;
  afterUncertaintyHaircutPct: number;
  afterDataQualityHaircutPct: number;
  afterCalibrationHaircutPct: number;
  perBetCapPct: number;
  finalStakePct: number;
  finalStakeAmount: number;
  bindingConstraint: string;
}

export interface Recommendation {
  id: string;
  gameId: string;
  predictionId: string;
  manualPriceId?: string;
  market: MarketType;
  selection: SelectionSide;
  line?: number;
  american: number;
  status: RecommendationStatus;
  modelProbability: number;
  conservativeProbability: number;
  marketNoVigProbability: number;
  breakEvenProbability: number;
  edge: number;
  evPerDollar: number;
  pushProbability: number;
  fairAmerican: number;
  confidence: 'LOW' | 'MEDIUM' | 'HIGH';
  stake: StakeBreakdown | null;
  supportingFactors: string[];
  opposingFactors: string[];
  reasonsToPass: string[];
  invalidationConditions: string[];
  targetPrice?: number;
  invalidationPrice?: number;
  statusReasons: string[];
  createdAt: string;
  invalidatedAt?: string;
  invalidationReason?: string;
}

// ---------------------------------------------------------------------------
// Bankroll, bets, settlements
// ---------------------------------------------------------------------------

export interface BankrollAccount {
  id: string;
  userId: string;
  mode: AppMode;
  currency: 'USD';
  startingBalance: number;
  currentBalance: number;
  createdAt: string;
}

export interface Bet {
  id: string;
  userId: string;
  bankrollAccountId: string;
  recommendationId?: string;
  predictionId?: string;
  modelVersionId?: string;
  featureSnapshotId?: string;
  gameId: string;
  market: MarketType;
  selection: SelectionSide;
  line?: number;
  american: number;
  stake: number;
  mode: AppMode;
  placedAt: string;
  result: BetResult;
  payout?: number;
  closingLine?: number;
  closingAmerican?: number;
  closingLineValuePct?: number;
  settledAt?: string;
}

export interface BetEvent {
  id: string;
  betId: string;
  eventType: 'PLACED' | 'SETTLED' | 'VOIDED' | 'CORRECTED';
  detail: string;
  createdAt: string;
  actor: string;
}

export interface ExposureSummary {
  openStake: number;
  weeklyExposurePct: number;
  byGame: Record<string, number>;
  byTeam: Record<string, number>;
  potentialMaxWeeklyLoss: number;
}

// ---------------------------------------------------------------------------
// Risk configuration
// ---------------------------------------------------------------------------

export interface RiskControls {
  kellyFraction: number;
  maxPerBetPctOfBankroll: number;
  maxPerGamePct: number;
  maxPerTeamWeeklyPct: number;
  maxCorrelatedClusterPct: number;
  maxWeeklyOpenStakePct: number;
  monthlyLossBudgetRequiredForReal: number;
  minEdgeForBet: number;
  minDataCompletenessScore: number;
  maxPriceAgeMinutes: number;
  maxUncertaintyMarginStd: number;
}

export const DEFAULT_RISK_CONTROLS: RiskControls = {
  kellyFraction: 0.25,
  maxPerBetPctOfBankroll: 0.005,
  maxPerGamePct: 0.01,
  maxPerTeamWeeklyPct: 0.015,
  maxCorrelatedClusterPct: 0.0125,
  maxWeeklyOpenStakePct: 0.04,
  monthlyLossBudgetRequiredForReal: 0,
  /** DEMO THRESHOLD — not historically validated. */
  minEdgeForBet: 0.02,
  minDataCompletenessScore: 0.85,
  maxPriceAgeMinutes: 60,
  maxUncertaintyMarginStd: 15.5,
};

// ---------------------------------------------------------------------------
// Data health
// ---------------------------------------------------------------------------

export interface FeedStatus {
  feed: string;
  status: DataFreshness;
  lastSuccessAt?: string;
  lastAttemptAt?: string;
  recordCount: number;
  freshnessMinutes?: number;
  error?: string;
  impactedGameIds: string[];
  impactedPredictionIds: string[];
  resolutionStatus: 'OK' | 'INVESTIGATING' | 'DEGRADED' | 'FAILED';
}

export interface DataQualityEvent {
  id: string;
  feed: string;
  severity: Severity;
  message: string;
  impactedGameIds: string[];
  createdAt: string;
  resolvedAt?: string;
}

// ---------------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------------

export interface PerformanceMetrics {
  logLoss: number;
  brierScore: number;
  calibrationError: number;
  calibrationSlope: number;
  calibrationIntercept: number;
  meanClosingLineValuePct: number;
  roiAfterVig: number;
  maxDrawdown: number;
  winRate: number;
  pushRate: number;
  averageEdge: number;
  recommendationCount: number;
  wagerCount: number;
  averageStake: number;
  intervalCoverage80: number;
}

export interface CalibrationBin {
  predictedLow: number;
  predictedHigh: number;
  meanPredicted: number;
  observedRate: number;
  count: number;
}

export interface AuditLogEntry {
  id: string;
  userId?: string;
  action: string;
  entity: string;
  entityId: string;
  detail: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// User settings
// ---------------------------------------------------------------------------

export type OddsFormat = 'AMERICAN' | 'DECIMAL';

export interface UserSettings {
  userId: string;
  oddsFormat: OddsFormat;
  timezone: string;
  mode: AppMode;
  riskControls: RiskControls;
  realTrackingAcknowledgedAt?: string;
  monthlyLossBudget?: number;
}

/** Banner text required on every screen that shows demonstration data. */
export const DEMO_DATA_LABEL = 'DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS';
