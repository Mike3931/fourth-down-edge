import {
  DEMO_DATA_LABEL,
  type AuditLogEntry,
  type BankrollAccount,
  type Bet,
  type BetEvent,
  type DataQualityEvent,
  type FeedStatus,
  type Game,
  type InjuryReport,
  type ManualBookPrice,
  type ModelVersion,
  type OfficialCrew,
  type OddsSnapshot,
  type Player,
  type PlayerAvailabilitySnapshot,
  type Prediction,
  type PredictionComponent,
  type PredictionVintage,
  type Stadium,
  type Team,
  type WeatherSnapshot,
} from '@fde/shared-types';
import { centralInterval, marginDistribution, normalCdf, probabilityToFairAmerican, totalDistribution } from '@fde/calculations';
import { Rng } from './rng';
import { buildTeams, TEAM_SEEDS } from './teams';

/**
 * Deterministic demonstration dataset.
 *
 * Everything produced here is DEMONSTRATION DATA — NOT FOR REAL-MONEY
 * DECISIONS. Games use plain-text team names on a clearly labeled demo slate;
 * player names are fictional; market prices are mock consensus values.
 */

export const DEMO_NOW = '2026-09-10T16:00:00Z';
export const DEMO_SEASON = 2026;
export const DEMO_WEEK = 1;

export interface DemoDataset {
  label: string;
  demoNow: string;
  season: number;
  week: number;
  teams: Team[];
  stadiums: Stadium[];
  officials: OfficialCrew[];
  players: Player[];
  games: Game[];
  priorGames: Game[];
  weatherSnapshots: WeatherSnapshot[];
  injuryReports: InjuryReport[];
  availabilitySnapshots: PlayerAvailabilitySnapshot[];
  oddsSnapshots: OddsSnapshot[];
  manualPrices: ManualBookPrice[];
  modelVersions: ModelVersion[];
  predictions: Prediction[];
  predictionComponents: PredictionComponent[];
  bankrollAccount: BankrollAccount;
  bets: Bet[];
  betEvents: BetEvent[];
  auditLog: AuditLogEntry[];
  feedStatuses: FeedStatus[];
  dataQualityEvents: DataQualityEvent[];
  backtest: BacktestRecord[];
}

export interface BacktestRecord {
  id: string;
  week: number;
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL';
  p: number;
  outcome: 0 | 1;
  edge: number;
  clvPct: number;
  recStatus: 'BET' | 'WATCH' | 'PASS' | 'DATA INCOMPLETE';
  stake: number;
  profit: number;
  indoor: boolean;
  favorite: boolean;
  hoursBeforeKickoff: number;
  dataCompleteness: number;
  /** Settled as a push (spread/total landing exactly on the number). */
  push: boolean;
  /** Whether the realized margin fell inside the predicted 80% interval. */
  withinInterval80: boolean;
}

const FIRST = ['A.', 'B.', 'C.', 'D.', 'E.', 'J.', 'K.', 'L.', 'M.', 'N.', 'R.', 'S.', 'T.', 'W.'];
const LAST = ['Abrams', 'Barlowe', 'Caldwell', 'Danvers', 'Ellison', 'Fenwick', 'Garrity', 'Hollis', 'Ingram', 'Jarrell', 'Kessler', 'Loften', 'Marbury', 'Norwood', 'Okafor', 'Pruitt', 'Quimby', 'Rendell', 'Sable', 'Tillman', 'Underhill', 'Vickers', 'Wexford', 'Yarrow'];

function iso(y: number, mo: number, d: number, h: number, mi = 0): string {
  return new Date(Date.UTC(y, mo - 1, d, h, mi)).toISOString().replace('.000Z', 'Z');
}

function minutesBefore(isoTs: string, minutes: number): string {
  return new Date(new Date(isoTs).getTime() - minutes * 60_000).toISOString().replace('.000Z', 'Z');
}

/** Margin -> home win probability using a normal margin model (sd ~13.2). */
export function marginToWinProb(margin: number): number {
  return 1 - normalCdf(0, margin, 13.2);
}

function withVig(fairProb: number, vigPer: number): number {
  // Push each side's implied probability up by vigPer (in probability points).
  const p = Math.min(0.985, fairProb + vigPer);
  return probabilityToFairAmerican(Math.min(0.985, Math.max(0.015, p)));
}

export function generateDemoDataset(seed = 20260910): DemoDataset {
  const rng = new Rng(seed);
  const { teams, stadiums } = buildTeams();

  // --- Officials (fictional crews) -----------------------------------------
  const officials: OfficialCrew[] = Array.from({ length: 8 }, (_, i) => ({
    id: `crew_${i + 1}`,
    refereeName: `${rng.pick(FIRST)} ${rng.pick(LAST)} (Crew ${40 + i} — DEMO)`,
    crewPenaltyRatePerGame: Math.round(rng.float(10.5, 15.5) * 10) / 10,
    crewOverRate: Math.round(rng.float(0.46, 0.56) * 1000) / 1000,
    sampleGames: rng.int(28, 60),
  }));

  // --- Pairings: shuffle 32 teams into 16 games ----------------------------
  const idx = teams.map((_, i) => i);
  for (let i = idx.length - 1; i > 0; i--) {
    const j = rng.int(0, i);
    [idx[i], idx[j]] = [idx[j]!, idx[i]!];
  }
  const kickoffs: string[] = [
    iso(2026, 9, 11, 0, 15), // Thursday night (Sep 10, 8:15pm ET)
    ...Array(8).fill(iso(2026, 9, 13, 17, 0)), // Sunday early
    ...Array(3).fill(iso(2026, 9, 13, 20, 5)), // Sunday late
    iso(2026, 9, 13, 20, 25),
    iso(2026, 9, 14, 0, 20), // Sunday night
    iso(2026, 9, 15, 0, 15), // Monday night
    iso(2026, 9, 13, 17, 0),
  ];

  const games: Game[] = [];
  for (let g = 0; g < 16; g++) {
    const away = teams[idx[2 * g]!]!;
    const home = teams[idx[2 * g + 1]!]!;
    const homeSeed = TEAM_SEEDS.find((t) => t.abbr === home.abbreviation)!;
    const roofStatus =
      homeSeed.roof === 'DOME'
        ? 'DOME'
        : homeSeed.roof === 'RETRACTABLE'
          ? (rng.bool(0.5) ? 'RETRACTABLE_CLOSED' : 'RETRACTABLE_OPEN')
          : 'OUTDOOR';
    games.push({
      id: `game_${DEMO_SEASON}_w${DEMO_WEEK}_${away.abbreviation}_${home.abbreviation}`,
      season: DEMO_SEASON,
      week: DEMO_WEEK,
      kickoffUtc: kickoffs[g]!,
      awayTeamId: away.id,
      homeTeamId: home.id,
      stadiumId: `stad_${home.abbreviation}`,
      roofStatus,
      officialCrewId: officials[g % officials.length]!.id,
      status: 'SCHEDULED',
    });
  }

  // --- Market + model parameters per game ----------------------------------
  // marketMargin: consensus expectation of home margin. modelOffset: how far
  // our demo model deviates. Index-specific scripting guarantees scenario
  // coverage for BET / WATCH / PASS / DATA INCOMPLETE.
  const marketMargin: number[] = [];
  const marketTotal: number[] = [];
  const modelMarginOffset: number[] = [];
  const modelTotalOffset: number[] = [];
  for (let g = 0; g < 16; g++) {
    marketMargin.push(rng.round(rng.normal(1.5, 4.5), 0.5));
    marketTotal.push(rng.round(rng.float(38.5, 52.5), 0.5));
    modelMarginOffset.push(rng.round(rng.normal(0, 0.7), 0.1));
    modelTotalOffset.push(rng.round(rng.normal(0, 1.0), 0.1));
  }
  // Scripted scenarios:
  modelMarginOffset[0] = 2.9; //  g0 -> BET candidate on home spread
  modelMarginOffset[1] = 0.4;
  modelTotalOffset[1] = 3.1; //   g1 -> edge on OVER but injury uncertainty -> WATCH
  modelMarginOffset[2] = 1.8; //  g2 -> QB unresolved -> DATA INCOMPLETE
  modelMarginOffset[3] = 2.2; //  g3 -> stale market price -> DATA INCOMPLETE
  modelMarginOffset[4] = 1.6; //  g4 -> close to threshold -> WATCH at better price
  modelTotalOffset[5] = -2.6; // g5 -> outdoor, weather missing -> DATA INCOMPLETE
  modelMarginOffset[6] = 0.1; //  g6 -> aligned -> PASS
  modelTotalOffset[6] = 0.2;

  // --- Weather -------------------------------------------------------------
  const weatherSnapshots: WeatherSnapshot[] = [];
  games.forEach((game, g) => {
    const outdoor = game.roofStatus === 'OUTDOOR' || game.roofStatus === 'RETRACTABLE_OPEN';
    if (!outdoor) return;
    if (g === 5) return; // scripted missing weather feed for this outdoor game
    const wind = Math.round(rng.float(2, g === 1 ? 24 : 16));
    const precip = Math.round(rng.float(0, 0.6) * 100) / 100;
    const sev = wind >= 18 ? 'HIGH' : wind >= 12 ? 'MODERATE' : precip > 0.4 ? 'MODERATE' : 'LOW';
    const generatedAt = minutesBefore(DEMO_NOW, rng.int(60, 240));
    weatherSnapshots.push({
      id: `wx_${game.id}`,
      gameId: game.id,
      forecastGeneratedAt: generatedAt,
      temperatureF: Math.round(rng.float(48, 84)),
      windMph: wind,
      gustMph: wind + rng.int(2, 9),
      precipitationChance: precip,
      precipitationType: precip > 0.45 ? 'RAIN' : 'NONE',
      severity: sev,
      sourceUpdatedAt: generatedAt,
      observedAt: minutesBefore(DEMO_NOW, rng.int(30, 90)),
      ingestedAt: minutesBefore(DEMO_NOW, rng.int(10, 29)),
      source: 'demo-weather-feed',
      sourceRecordId: `wxr_${g}`,
    });
  });

  // --- Players + injuries --------------------------------------------------
  const players: Player[] = [];
  const injuryReports: InjuryReport[] = [];
  const availabilitySnapshots: PlayerAvailabilitySnapshot[] = [];

  const POSITIONS = ['QB', 'RB', 'WR', 'TE', 'LT', 'LG', 'C', 'EDGE', 'CB', 'S', 'LB'];
  const INJURIES = ['hamstring', 'ankle', 'knee', 'shoulder', 'concussion protocol', 'calf', 'back', 'foot', 'oblique', 'quadriceps'];

  function addPlayer(teamId: string, position: string, role: string): Player {
    const p: Player = {
      id: `pl_${teamId}_${position}_${players.length}`,
      teamId,
      name: `${rng.pick(FIRST)} ${rng.pick(LAST)}`,
      position,
      depthRole: role,
    };
    players.push(p);
    return p;
  }

  games.forEach((game, g) => {
    const nInjuries = g === 2 ? 3 : g === 1 ? 4 : rng.int(0, 3);
    for (let k = 0; k < nInjuries; k++) {
      const teamId = rng.bool() ? game.homeTeamId : game.awayTeamId;
      // Scripted: g2 -> unresolved QB; g1 -> WR1 + OL cluster.
      const position = g === 2 && k === 0 ? 'QB' : g === 1 && k === 0 ? 'WR' : g === 1 && k >= 2 ? rng.pick(['LT', 'LG', 'C'] as const) : rng.pick(POSITIONS);
      const player = addPlayer(g === 2 && k === 0 ? game.homeTeamId : teamId, position, `${position}1`);
      const replacement = addPlayer(player.teamId, position, `${position}2`);
      const isQbCase = g === 2 && k === 0;
      const designation = isQbCase ? 'QUESTIONABLE' : rng.pick(['NONE', 'QUESTIONABLE', 'QUESTIONABLE', 'DOUBTFUL', 'OUT'] as const);
      const conflict = g === 7 && k === 0;
      const srcUpd = minutesBefore(DEMO_NOW, rng.int(120, 20 * 60));
      injuryReports.push({
        id: `inj_${game.id}_${k}`,
        gameId: game.id,
        playerId: player.id,
        injury: rng.pick(INJURIES),
        practiceWed: isQbCase ? 'DNP' : rng.pick(['FULL', 'LIMITED', 'DNP'] as const),
        practiceThu: isQbCase ? 'LIMITED' : rng.pick(['FULL', 'LIMITED', 'DNP'] as const),
        practiceFri: 'NO_DATA',
        designation,
        conflictWarning: conflict ? 'Beat reporter and official report disagree on practice status' : undefined,
        sourceUpdatedAt: srcUpd,
        observedAt: minutesBefore(srcUpd, -rng.int(5, 30)),
        ingestedAt: minutesBefore(srcUpd, -rng.int(31, 45)),
        source: conflict ? 'demo-injury-feed (conflict: demo-news-feed)' : 'demo-injury-feed',
        sourceRecordId: `injr_${g}_${k}`,
      });
      const activeProb = isQbCase ? 0.55 : designation === 'OUT' ? 0 : designation === 'DOUBTFUL' ? 0.22 : designation === 'QUESTIONABLE' ? rng.float(0.5, 0.85) : 0.97;
      availabilitySnapshots.push({
        id: `avail_${game.id}_${k}`,
        gameId: game.id,
        playerId: player.id,
        activeProbability: Math.round(activeProb * 100) / 100,
        expectedSnapShareIfActive: Math.round(rng.float(0.55, 0.95) * 100) / 100,
        restrictionProbability: Math.round(rng.float(0.1, isQbCase ? 0.5 : 0.35) * 100) / 100,
        replacementPlayerId: replacement.id,
        replacementQuality: Math.round(rng.float(0.45, 0.85) * 100) / 100,
        estimatedTeamImpactPts: Math.round((position === 'QB' ? rng.float(3.5, 6.5) : rng.float(0.2, 2.2)) * 10) / 10,
        confidence: Math.round(rng.float(0.5, 0.9) * 100) / 100,
        sourceUpdatedAt: srcUpd,
        observedAt: minutesBefore(srcUpd, -10),
        ingestedAt: minutesBefore(srcUpd, -12),
        source: 'demo-availability-model',
        sourceRecordId: `availr_${g}_${k}`,
      });
    }
  });

  // --- Odds snapshots ------------------------------------------------------
  const oddsSnapshots: OddsSnapshot[] = [];
  const openAt = iso(2026, 9, 7, 14, 0);
  const midAt = iso(2026, 9, 9, 18, 0);

  games.forEach((game, g) => {
    const currentAt = g === 3 ? iso(2026, 9, 9, 10, 0) : minutesBefore(DEMO_NOW, rng.int(12, 40));
    const openMove = rng.round(rng.normal(0, 0.8), 0.5);
    const openSpread = -(marketMargin[g]! + openMove);
    const curSpread = -marketMargin[g]!;
    const openTotal = marketTotal[g]! + rng.round(rng.normal(0, 1.0), 0.5);
    const homeProb = marginToWinProb(marketMargin[g]!);

    const mk = (
      market: OddsSnapshot['market'],
      at: string,
      opts: Partial<OddsSnapshot>,
    ): OddsSnapshot => ({
      id: `odds_${game.id}_${market}_${at}`,
      gameId: game.id,
      market,
      book: 'DEMO CONSENSUS (mock)',
      awayAmerican: 0,
      homeAmerican: 0,
      isOpening: at === openAt,
      isClosing: false,
      sourceUpdatedAt: at,
      observedAt: at,
      ingestedAt: at,
      source: 'demo-odds-feed',
      ...opts,
    });

    for (const [at, spread, total] of [
      [openAt, openSpread, openTotal],
      [midAt, (openSpread + curSpread) / 2, (openTotal + marketTotal[g]!) / 2],
      [currentAt, curSpread, marketTotal[g]!],
    ] as const) {
      const juiceA = rng.pick([-108, -110, -112] as const);
      const juiceB = -220 - juiceA; // pairs to keep total juice ~constant
      oddsSnapshots.push(
        mk('SPREAD', at, { line: rng.round(spread, 0.5), awayAmerican: juiceB, homeAmerican: juiceA }),
        mk('TOTAL', at, {
          line: rng.round(total, 0.5),
          awayAmerican: 0, homeAmerican: 0,
          overAmerican: juiceA, underAmerican: juiceB,
        }),
        mk('MONEYLINE', at, {
          homeAmerican: withVig(homeProb, 0.02),
          awayAmerican: withVig(1 - homeProb, 0.02),
        }),
      );
    }
  });

  // --- Manual bet365 prices (entered by the demo user, immutable) ----------
  const g0 = games[0]!;
  const g4 = games[4]!;
  const curSpread0 = -marketMargin[0]!;
  const manualPrices: ManualBookPrice[] = [
    {
      id: 'mbp_1',
      userId: 'demo-user',
      gameId: g0.id,
      sportsbook: 'bet365 (manual entry)',
      market: 'SPREAD',
      selection: 'HOME',
      line: curSpread0,
      american: -105,
      priceObservedAt: minutesBefore(DEMO_NOW, 18),
      enteredAt: minutesBefore(DEMO_NOW, 16),
      confirmedVisible: true,
    },
    {
      id: 'mbp_0',
      userId: 'demo-user',
      gameId: g0.id,
      sportsbook: 'bet365 (manual entry)',
      market: 'SPREAD',
      selection: 'HOME',
      line: curSpread0,
      american: -108,
      priceObservedAt: minutesBefore(DEMO_NOW, 26 * 60),
      enteredAt: minutesBefore(DEMO_NOW, 26 * 60 - 2),
      confirmedVisible: true,
    },
    {
      id: 'mbp_2',
      userId: 'demo-user',
      gameId: g4.id,
      sportsbook: 'bet365 (manual entry)',
      market: 'SPREAD',
      selection: 'HOME',
      line: -marketMargin[4]!,
      american: -112,
      priceObservedAt: minutesBefore(DEMO_NOW, 55),
      enteredAt: minutesBefore(DEMO_NOW, 54),
      confirmedVisible: true,
    },
  ];

  // --- Model versions ------------------------------------------------------
  const mkModel = (
    id: string, name: string, version: string, status: ModelVersion['status'],
    algorithm: string, isPlaceholder: boolean, target: string,
  ): ModelVersion => ({
    id, name, version, status, target, algorithm,
    trainingPeriod: '2019–2024 demo backtest window',
    validationPeriod: '2025 demo holdout',
    featureSetVersion: 'fs-2026.09.1',
    calibrationVersion: 'cal-0.6.0',
    artifactHash: `sha256:${(id + version).padEnd(16, '0').slice(0, 16)}…(demo)`,
    gitCommit: 'demo-git-commit-placeholder',
    approvedAt: status === 'APPROVED_DEMO' ? iso(2026, 9, 1, 12, 0) : undefined,
    knownLimitations: isPlaceholder
      ? ['Placeholder — produces illustrative output only', 'Not trained on real data', 'Must be replaced by the external Python analytical service']
      : ['Demo-approved for demonstration workflows only', 'Thresholds are not historically validated', 'Not suitable for real-money decisions'],
    dataDependencies: ['schedule', 'odds', 'injury', 'weather', 'roster'],
    predictionHorizons: ['OPENING', 'EARLY_WEEK', 'PRACTICE_UPDATE', 'FINAL_INJURY_REPORT', 'PREGAME'],
    driftStatus: isPlaceholder ? 'UNKNOWN' : 'STABLE',
    lastRetrainedAt: iso(2026, 8, 25, 0, 0),
    nextReviewAt: iso(2026, 10, 1, 0, 0),
    performanceSummary: isPlaceholder
      ? 'No validated performance. Placeholder component.'
      : 'Demo backtest metrics only — see Performance Lab. Not validated for live use.',
    isPlaceholder,
  });

  const modelVersions: ModelVersion[] = [
    mkModel('mv_ensemble', 'fde-ensemble', '0.3.0-demo', 'APPROVED_DEMO', 'Weighted component ensemble (deterministic)', false, 'Win prob / margin / total distributions'),
    mkModel('mv_market', 'market-baseline', '1.2.0', 'APPROVED_DEMO', 'No-vig consensus prior', false, 'Market-implied probabilities'),
    mkModel('mv_rating', 'dynamic-team-rating', '0.9.1', 'APPROVED_DEMO', 'Elo-style recursive rating', false, 'Team strength'),
    mkModel('mv_stat', 'regularized-stat-baseline', '0.8.0', 'APPROVED_DEMO', 'Ridge regression on efficiency metrics', false, 'Margin / total'),
    mkModel('mv_bayes', 'bayesian-hierarchical', '0.1.0', 'PLACEHOLDER', 'Bayesian hierarchical (planned: PyMC)', true, 'Margin distribution'),
    mkModel('mv_gbm', 'gradient-boosting', '0.1.0', 'PLACEHOLDER', 'Gradient boosting (planned: LightGBM)', true, 'Win probability'),
    mkModel('mv_avail', 'player-availability-adjustment', '0.5.0', 'APPROVED_DEMO', 'Deterministic availability-impact adjustment', false, 'Injury adjustment (points)'),
    mkModel('mv_mc', 'monte-carlo-simulator', '0.1.0', 'PLACEHOLDER', 'Drive-level Monte Carlo (planned: Python service)', true, 'Score distributions'),
    mkModel('mv_cal', 'calibration-layer', '0.6.0', 'APPROVED_DEMO', 'Platt scaling placeholder coefficients', false, 'Probability calibration'),
  ];

  // --- Predictions with immutable vintages ---------------------------------
  const predictions: Prediction[] = [];
  const predictionComponents: PredictionComponent[] = [];
  const vintages: Array<{ v: PredictionVintage; asOf: string }> = [
    { v: 'OPENING', asOf: iso(2026, 9, 7, 16, 0) },
    { v: 'EARLY_WEEK', asOf: iso(2026, 9, 8, 18, 0) },
    { v: 'PRACTICE_UPDATE', asOf: iso(2026, 9, 10, 15, 0) },
  ];

  games.forEach((game, g) => {
    vintages.forEach(({ v, asOf }, vi) => {
      // Earlier vintages know less: shrink the model offset and widen sigma.
      const maturity = [0.45, 0.7, 1.0][vi]!;
      const margin = marketMargin[g]! + modelMarginOffset[g]! * maturity;
      const total = marketTotal[g]! + modelTotalOffset[g]! * maturity;
      const marginStd = 13.9 - 0.7 * vi;
      const totalStd = 10.6 - 0.4 * vi;
      const mDist = marginDistribution(margin, marginStd);
      const tDist = totalDistribution(total, totalStd);
      const homeWp = mDist.filter((p) => p.value > 0).reduce((a, p) => a + p.probability, 0);
      const completeness = g === 2 ? 0.78 : g === 5 ? 0.8 : Math.round(rng.float(0.88, 0.97) * 100) / 100;
      const pred: Prediction = {
        id: `pred_${game.id}_${v}`,
        gameId: game.id,
        modelVersionId: 'mv_ensemble',
        vintage: v,
        asOfAt: asOf,
        createdAt: minutesBefore(asOf, -2),
        featureSnapshotId: `fsnap_${game.id}_${v}`,
        homeWinProbability: Math.round(homeWp * 1000) / 1000,
        awayWinProbability: Math.round((1 - homeWp) * 1000) / 1000,
        expectedHomeScore: Math.round(((total + margin) / 2) * 10) / 10,
        expectedAwayScore: Math.round(((total - margin) / 2) * 10) / 10,
        expectedMargin: Math.round(margin * 10) / 10,
        expectedTotal: Math.round(total * 10) / 10,
        marginInterval80: centralInterval(mDist, 0.8),
        totalInterval80: centralInterval(tDist, 0.8),
        marginStd,
        totalStd,
        dataCompletenessScore: completeness,
        isOfficial: vi === vintages.length - 1,
      };
      predictions.push(pred);

      if (vi === vintages.length - 1) {
        const comps: Array<[string, number, boolean]> = [
          ['mv_market', 0, false],
          ['mv_rating', modelMarginOffset[g]! * 0.9, false],
          ['mv_stat', modelMarginOffset[g]! * 0.6, false],
          ['mv_bayes', modelMarginOffset[g]! * 1.15, true],
          ['mv_gbm', modelMarginOffset[g]! * 0.8, true],
          ['mv_avail', -availabilityImpact(game.id), false],
          ['mv_mc', modelMarginOffset[g]! * 1.05, true],
          ['mv_cal', modelMarginOffset[g]! * 1.0, false],
        ];
        comps.forEach(([mvId, off, ph], ci) => {
          const m = marketMargin[g]! + (off as number);
          predictionComponents.push({
            id: `pc_${pred.id}_${ci}`,
            predictionId: pred.id,
            componentName: modelVersions.find((x) => x.id === mvId)!.name,
            homeWinProbability: Math.round(marginToWinProb(m) * 1000) / 1000,
            expectedMargin: Math.round(m * 10) / 10,
            expectedTotal: Math.round((marketTotal[g]! + modelTotalOffset[g]! * 0.9) * 10) / 10,
            weight: ph ? 0 : ci === 0 ? 0.45 : 0.11,
            isPlaceholder: ph as boolean,
          });
        });
      }
    });
  });

  function availabilityImpact(gameId: string): number {
    return availabilitySnapshots
      .filter((a) => a.gameId === gameId && a.activeProbability < 0.9)
      .reduce((acc, a) => acc + a.estimatedTeamImpactPts * (1 - a.activeProbability) * 0.4, 0);
  }

  // --- Prior settled week (paper bets, closing prices, bankroll) -----------
  const priorGames: Game[] = [];
  const bets: Bet[] = [];
  const betEvents: BetEvent[] = [];
  const auditLog: AuditLogEntry[] = [];
  let bankroll = 10_000;

  const priorResults: Array<{ res: 'WIN' | 'LOSS' | 'PUSH'; market: 'SPREAD' | 'TOTAL' | 'MONEYLINE' }> = [
    { res: 'WIN', market: 'SPREAD' }, { res: 'LOSS', market: 'TOTAL' }, { res: 'WIN', market: 'MONEYLINE' },
    { res: 'PUSH', market: 'SPREAD' }, { res: 'WIN', market: 'SPREAD' }, { res: 'LOSS', market: 'MONEYLINE' },
  ];
  priorResults.forEach((pr, i) => {
    const away = teams[(2 * i) % 32]!;
    const home = teams[(2 * i + 1) % 32]!;
    const kick = iso(2026, 9, 6, 17, 0);
    const homeScore = rng.int(13, 34);
    const awayScore = rng.int(10, 31);
    const game: Game = {
      id: `game_${DEMO_SEASON}_w0_${away.abbreviation}_${home.abbreviation}_${i}`,
      season: DEMO_SEASON,
      week: 0,
      kickoffUtc: kick,
      awayTeamId: away.id,
      homeTeamId: home.id,
      stadiumId: `stad_${home.abbreviation}`,
      roofStatus: 'OUTDOOR',
      status: 'FINAL',
      finalAwayScore: awayScore,
      finalHomeScore: homeScore,
    };
    priorGames.push(game);

    const american = rng.pick([-110, -105, -115, 120, -108] as const);
    const stake = 50;
    const placedAt = iso(2026, 9, 5, 15, i * 7);
    const settledAt = iso(2026, 9, 6, 21, 30 + i);
    const clv = Math.round(rng.normal(0.6, 1.6) * 10) / 10;
    const decimal = american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american);
    const profit = pr.res === 'WIN' ? Math.round(stake * (decimal - 1) * 100) / 100 : pr.res === 'LOSS' ? -stake : 0;
    bankroll = Math.round((bankroll + profit) * 100) / 100;
    const bet: Bet = {
      id: `bet_prior_${i}`,
      userId: 'demo-user',
      bankrollAccountId: 'bank_demo',
      recommendationId: `rec_prior_${i}`,
      predictionId: `pred_prior_${i}`,
      modelVersionId: 'mv_ensemble',
      featureSnapshotId: `fsnap_prior_${i}`,
      gameId: game.id,
      market: pr.market,
      selection: pr.market === 'TOTAL' ? 'OVER' : rng.bool() ? 'HOME' : 'AWAY',
      line: pr.market === 'MONEYLINE' ? undefined : pr.market === 'TOTAL' ? 44.5 : -2.5,
      american,
      stake,
      mode: 'PAPER',
      placedAt,
      result: pr.res,
      payout: pr.res === 'WIN' ? stake + profit : pr.res === 'PUSH' ? stake : 0,
      closingLine: pr.market === 'MONEYLINE' ? undefined : pr.market === 'TOTAL' ? 45 : -3,
      closingAmerican: -110,
      closingLineValuePct: clv,
      settledAt,
    };
    bets.push(bet);
    betEvents.push(
      { id: `bev_prior_${i}_p`, betId: bet.id, eventType: 'PLACED', detail: `PAPER bet placed (${DEMO_DATA_LABEL})`, createdAt: placedAt, actor: 'demo-user' },
      { id: `bev_prior_${i}_s`, betId: bet.id, eventType: 'SETTLED', detail: `Settled ${pr.res}; profit ${profit.toFixed(2)}`, createdAt: settledAt, actor: 'demo-settlement-service' },
    );
    auditLog.push({
      id: `aud_prior_${i}`, userId: 'demo-user', action: 'BET_SETTLED', entity: 'bets', entityId: bet.id,
      detail: `Demo settlement ${pr.res}`, createdAt: settledAt,
    });
  });

  // Open paper bet on g0 from the confirmed manual price.
  const openBet: Bet = {
    id: 'bet_open_g0',
    userId: 'demo-user',
    bankrollAccountId: 'bank_demo',
    recommendationId: `rec_${g0.id}`,
    predictionId: `pred_${g0.id}_PRACTICE_UPDATE`,
    modelVersionId: 'mv_ensemble',
    featureSnapshotId: `fsnap_${g0.id}_PRACTICE_UPDATE`,
    gameId: g0.id,
    market: 'SPREAD',
    selection: 'HOME',
    line: curSpread0,
    american: -105,
    stake: 50,
    mode: 'PAPER',
    placedAt: minutesBefore(DEMO_NOW, 14),
    result: 'PENDING',
  };
  bets.push(openBet);
  betEvents.push({
    id: 'bev_open_g0', betId: openBet.id, eventType: 'PLACED',
    detail: 'PAPER bet placed from confirmed manual price mbp_1', createdAt: openBet.placedAt, actor: 'demo-user',
  });
  auditLog.push({
    id: 'aud_open_g0', userId: 'demo-user', action: 'BET_PLACED', entity: 'bets', entityId: openBet.id,
    detail: 'Open paper bet on demo slate', createdAt: openBet.placedAt,
  });

  const bankrollAccount: BankrollAccount = {
    id: 'bank_demo',
    userId: 'demo-user',
    mode: 'PAPER',
    currency: 'USD',
    startingBalance: 10_000,
    currentBalance: bankroll,
    createdAt: iso(2026, 9, 1, 0, 0),
  };

  // --- Feed statuses + data-quality events ---------------------------------
  const feedStatuses: FeedStatus[] = [
    feed('schedule', 'CURRENT', 45, 272, []),
    feed('roster', 'CURRENT', 130, 2944, []),
    feed('depthChart', 'AGING', 26 * 60, 1088, []),
    // Conflicting source reports on one game: the feed is live but two
    // providers disagree, so the record-level status is CONFLICTING.
    feed('injury', 'CONFLICTING', 95, injuryReports.length, [games[7]!.id]),
    feed('weather', 'MISSING', undefined, weatherSnapshots.length, [games[5]!.id]),
    feed('odds', 'STALE', 31 * 60, oddsSnapshots.length, [games[3]!.id]),
    feed('manualPrice', 'AGING', 54, manualPrices.length, [g4.id]),
    feed('officials', 'CURRENT', 20 * 60, officials.length, []),
    feed('modelService', 'CURRENT', 58, predictions.length, []),
    feed('predictionService', 'CURRENT', 58, predictions.length, []),
    feed('settlementService', 'CURRENT', 4 * 24 * 60, bets.length - 1, []),
    feed('entityMappings', 'CURRENT', 24 * 60, 3216, []),
    feed('database', 'CURRENT', 1, 0, []),
    feed('backgroundJobs', 'CURRENT', 12, 8, []),
  ];

  function feed(name: string, status: FeedStatus['status'], ageMin: number | undefined, count: number, impacted: string[]): FeedStatus {
    return {
      feed: name,
      status,
      lastSuccessAt: ageMin === undefined ? undefined : minutesBefore(DEMO_NOW, ageMin),
      lastAttemptAt: minutesBefore(DEMO_NOW, Math.min(ageMin ?? 10, 15)),
      recordCount: count,
      freshnessMinutes: ageMin,
      error:
        status === 'MISSING' ? 'Provider returned 502 for stadium coordinates'
          : status === 'STALE' ? 'Snapshot cadence exceeded threshold'
            : status === 'CONFLICTING' ? 'Two providers disagree on practice status for an impacted game'
              : undefined,
      impactedGameIds: impacted,
      impactedPredictionIds: impacted.map((gid) => `pred_${gid}_PRACTICE_UPDATE`),
      resolutionStatus:
        status === 'MISSING' ? 'FAILED'
          : status === 'STALE' ? 'DEGRADED'
            : status === 'AGING' || status === 'CONFLICTING' ? 'INVESTIGATING'
              : 'OK',
    };
  }

  const dataQualityEvents: DataQualityEvent[] = [
    {
      id: 'dq_1', feed: 'weather', severity: 'CRITICAL',
      message: `Weather feed failure for outdoor game — recommendations for the impacted game are downgraded to DATA INCOMPLETE`,
      impactedGameIds: [games[5]!.id], createdAt: minutesBefore(DEMO_NOW, 140),
    },
    {
      id: 'dq_2', feed: 'odds', severity: 'HIGH',
      message: 'Consensus odds snapshot stale beyond 2h threshold',
      impactedGameIds: [games[3]!.id], createdAt: minutesBefore(DEMO_NOW, 65),
    },
    {
      id: 'dq_3', feed: 'injury', severity: 'MODERATE',
      message: 'Conflicting practice-status reports from two sources',
      impactedGameIds: [games[7]!.id], createdAt: minutesBefore(DEMO_NOW, 200),
    },
  ];

  // --- Synthetic backtest history for Performance Lab ----------------------
  const backtest: BacktestRecord[] = [];
  const btRng = new Rng(seed + 1);
  for (let i = 0; i < 260; i++) {
    const p = Math.min(0.9, Math.max(0.1, btRng.float(0.3, 0.75)));
    // Slightly miscalibrated truth so calibration views show honest structure.
    const trueP = Math.min(0.95, Math.max(0.05, 0.88 * p + 0.055));
    const outcome: 0 | 1 = btRng.bool(trueP) ? 1 : 0;
    const statusRoll = btRng.float();
    const recStatus = statusRoll < 0.11 ? 'BET' : statusRoll < 0.3 ? 'WATCH' : statusRoll < 0.92 ? 'PASS' : 'DATA INCOMPLETE';
    const edge = btRng.normal(recStatus === 'BET' ? 0.032 : 0.004, 0.012);
    const market = btRng.pick(['MONEYLINE', 'SPREAD', 'TOTAL'] as const);
    // Moneylines cannot push; spreads/totals push at a realistic demo rate.
    const push = market !== 'MONEYLINE' && btRng.bool(0.04);
    const stake = recStatus === 'BET' ? 50 : 0;
    const win = outcome === 1;
    const profit = stake === 0 || push ? 0 : win ? Math.round(stake * (100 / 110) * 100) / 100 : -stake;
    backtest.push({
      id: `bt_${i}`,
      week: 1 + (i % 18),
      market,
      p, outcome,
      edge: Math.round(edge * 1000) / 1000,
      clvPct: Math.round(btRng.normal(0.35, 1.7) * 10) / 10,
      recStatus,
      stake,
      profit,
      indoor: btRng.bool(0.28),
      favorite: p > 0.5,
      hoursBeforeKickoff: btRng.int(1, 120),
      dataCompleteness: Math.round(btRng.float(0.75, 0.99) * 100) / 100,
      push,
      // Slightly under the nominal 80% — honest demo coverage, not a claim.
      withinInterval80: btRng.bool(0.78),
    });
  }

  return {
    label: DEMO_DATA_LABEL,
    demoNow: DEMO_NOW,
    season: DEMO_SEASON,
    week: DEMO_WEEK,
    teams, stadiums, officials, players,
    games, priorGames,
    weatherSnapshots, injuryReports, availabilitySnapshots,
    oddsSnapshots, manualPrices,
    modelVersions, predictions, predictionComponents,
    bankrollAccount, bets, betEvents, auditLog,
    feedStatuses, dataQualityEvents,
    backtest,
  };
}
