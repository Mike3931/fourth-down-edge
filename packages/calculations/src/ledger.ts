import type {
  AppMode,
  AuditLogEntry,
  Bet,
  BetEvent,
  BetResult,
  MarketType,
  Prediction,
  Recommendation,
  SelectionSide,
} from '@fde/shared-types';
import { americanToDecimal } from './odds';
import { roundCents } from './staking';

/**
 * Pure, append-only ledger operations. Every function returns NEW arrays and
 * objects — nothing is mutated — and settled history is never rewritten.
 * Corrections are represented as new events, never as silent overwrites.
 */

export interface LedgerState {
  bets: Bet[];
  betEvents: BetEvent[];
  auditLog: AuditLogEntry[];
  bankrollBalance: number;
}

let idCounter = 0;
export function nextId(prefix: string): string {
  idCounter += 1;
  return `${prefix}_${Date.now().toString(36)}_${idCounter}`;
}

export interface PlaceBetInput {
  userId: string;
  bankrollAccountId: string;
  gameId: string;
  market: MarketType;
  selection: SelectionSide;
  line?: number;
  american: number;
  stake: number;
  mode: AppMode;
  placedAt: string;
  recommendationId?: string;
  predictionId?: string;
  modelVersionId?: string;
  featureSnapshotId?: string;
}

export function placeBet(state: LedgerState, input: PlaceBetInput): LedgerState {
  // Paper mode is mandatory, and until now that rested entirely on both UI
  // call sites happening to pass the literal 'PAPER'. A third call site, or
  // one edit to an existing one, would have silently recorded a bet marked
  // REAL_TRACKING. The invariant belongs here, where it cannot be bypassed.
  //
  // This does NOT resolve the open product question of whether the mode
  // should be wired through or removed (see docs/model-governance.md); it
  // only makes the current fail-safe behaviour enforced rather than
  // accidental. Whichever way that question is decided, it should be
  // decided by changing this guard deliberately.
  if (input.mode !== 'PAPER') {
    throw new RangeError(
      `Refusing to record a bet in ${input.mode} mode: this build records paper bets only.`,
    );
  }
  if (input.stake <= 0) throw new RangeError('Stake must be positive.');
  if (input.stake > state.bankrollBalance) {
    throw new RangeError('Stake exceeds available bankroll.');
  }
  const bet: Bet = {
    id: nextId('bet'),
    result: 'PENDING',
    ...input,
  };
  const event: BetEvent = {
    id: nextId('bev'),
    betId: bet.id,
    eventType: 'PLACED',
    detail: `${input.mode} bet placed: ${input.selection} ${input.market} @ ${input.american}, stake ${input.stake}`,
    createdAt: input.placedAt,
    actor: input.userId,
  };
  const audit: AuditLogEntry = {
    id: nextId('aud'),
    userId: input.userId,
    action: 'BET_PLACED',
    entity: 'bets',
    entityId: bet.id,
    detail: event.detail,
    createdAt: input.placedAt,
  };
  return {
    bets: [...state.bets, bet],
    betEvents: [...state.betEvents, event],
    auditLog: [...state.auditLog, audit],
    bankrollBalance: state.bankrollBalance, // stake is committed, not deducted until settlement accounting below
  };
}

export interface SettleBetInput {
  betId: string;
  result: Exclude<BetResult, 'PENDING'>;
  settledAt: string;
  closingLine?: number;
  closingAmerican?: number;
  closingLineValuePct?: number;
  actor: string;
}

/**
 * Settlement accounting:
 *  WIN  -> bankroll += stake * (decimal − 1)
 *  LOSS -> bankroll −= stake
 *  PUSH/VOID -> bankroll unchanged
 * A settled bet may not be settled again; corrections append a CORRECTED
 * event and a reversing adjustment rather than editing history.
 */
export function settleBet(state: LedgerState, input: SettleBetInput): LedgerState {
  const bet = state.bets.find((b) => b.id === input.betId);
  if (!bet) throw new Error(`Unknown bet ${input.betId}`);
  if (bet.result !== 'PENDING') {
    throw new Error(`Bet ${bet.id} is already settled (${bet.result}). Use correctSettlement.`);
  }
  const delta = settlementDelta(bet.stake, bet.american, input.result);
  const settled: Bet = {
    ...bet,
    result: input.result,
    payout: roundCents(bet.stake + delta > 0 && input.result === 'WIN' ? bet.stake + delta : input.result === 'PUSH' || input.result === 'VOID' ? bet.stake : 0),
    closingLine: input.closingLine,
    closingAmerican: input.closingAmerican,
    closingLineValuePct: input.closingLineValuePct,
    settledAt: input.settledAt,
  };
  const event: BetEvent = {
    id: nextId('bev'),
    betId: bet.id,
    eventType: input.result === 'VOID' ? 'VOIDED' : 'SETTLED',
    detail: `Settled ${input.result}; bankroll delta ${delta.toFixed(2)}`,
    createdAt: input.settledAt,
    actor: input.actor,
  };
  const audit: AuditLogEntry = {
    id: nextId('aud'),
    action: 'BET_SETTLED',
    entity: 'bets',
    entityId: bet.id,
    detail: event.detail,
    createdAt: input.settledAt,
    userId: input.actor,
  };
  return {
    bets: state.bets.map((b) => (b.id === bet.id ? settled : b)),
    betEvents: [...state.betEvents, event],
    auditLog: [...state.auditLog, audit],
    bankrollBalance: roundCents(state.bankrollBalance + delta),
  };
}

export function settlementDelta(
  stake: number,
  american: number,
  result: Exclude<BetResult, 'PENDING'>,
): number {
  switch (result) {
    case 'WIN':
      return roundCents(stake * (americanToDecimal(american) - 1));
    case 'LOSS':
      return -stake;
    case 'PUSH':
    case 'VOID':
      return 0;
  }
}

/**
 * Correction: appends a CORRECTED event and applies a reversing delta plus
 * the new-result delta. The original settlement record remains in history.
 */
export function correctSettlement(
  state: LedgerState,
  betId: string,
  newResult: Exclude<BetResult, 'PENDING' | 'VOID'>,
  correctedAt: string,
  actor: string,
  reason: string,
): LedgerState {
  const bet = state.bets.find((b) => b.id === betId);
  if (!bet) throw new Error(`Unknown bet ${betId}`);
  if (bet.result === 'PENDING') throw new Error('Cannot correct an unsettled bet.');
  if (bet.result === newResult) throw new Error('Correction must change the result.');
  const reversal = -settlementDelta(bet.stake, bet.american, bet.result as Exclude<BetResult, 'PENDING'>);
  const applied = settlementDelta(bet.stake, bet.american, newResult);
  const corrected: Bet = { ...bet, result: newResult, settledAt: correctedAt };
  const event: BetEvent = {
    id: nextId('bev'),
    betId,
    eventType: 'CORRECTED',
    detail: `Correction ${bet.result} -> ${newResult}: ${reason}. Reversal ${reversal.toFixed(2)}, applied ${applied.toFixed(2)}`,
    createdAt: correctedAt,
    actor,
  };
  const audit: AuditLogEntry = {
    id: nextId('aud'),
    action: 'BET_CORRECTED',
    entity: 'bets',
    entityId: betId,
    detail: event.detail,
    createdAt: correctedAt,
    userId: actor,
  };
  return {
    bets: state.bets.map((b) => (b.id === betId ? corrected : b)),
    betEvents: [...state.betEvents, event],
    auditLog: [...state.auditLog, audit],
    bankrollBalance: roundCents(state.bankrollBalance + reversal + applied),
  };
}

// ---------------------------------------------------------------------------
// Prediction vintages — immutable history
// ---------------------------------------------------------------------------

export class VintageOverwriteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VintageOverwriteError';
  }
}

/**
 * Appends a new prediction vintage. Throws if a prediction with the same id
 * exists or if an existing (game, vintage, model) prediction would be
 * replaced. History is append-only.
 */
export function addPredictionVintage(
  history: Prediction[],
  next: Prediction,
): Prediction[] {
  if (history.some((p) => p.id === next.id)) {
    throw new VintageOverwriteError(`Prediction ${next.id} already exists.`);
  }
  if (
    history.some(
      (p) =>
        p.gameId === next.gameId &&
        p.vintage === next.vintage &&
        p.modelVersionId === next.modelVersionId,
    )
  ) {
    throw new VintageOverwriteError(
      `A ${next.vintage} prediction for game ${next.gameId} from model ${next.modelVersionId} already exists. ` +
        'A later prediction must never overwrite an earlier prediction.',
    );
  }
  return [...history, next];
}

// ---------------------------------------------------------------------------
// Recommendation invalidation from data-quality events
// ---------------------------------------------------------------------------

export function invalidateRecommendationsForGames(
  recommendations: Recommendation[],
  impactedGameIds: string[],
  reason: string,
  at: string,
): Recommendation[] {
  const impacted = new Set(impactedGameIds);
  return recommendations.map((r) => {
    if (!impacted.has(r.gameId) || r.invalidatedAt) return r;
    if (r.status === 'PASS' || r.status === 'DATA INCOMPLETE') return r;
    return {
      ...r,
      status: 'DATA INCOMPLETE' as const,
      statusReasons: [reason, ...r.statusReasons],
      invalidatedAt: at,
      invalidationReason: reason,
    };
  });
}
