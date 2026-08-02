import { describe, expect, it } from 'vitest';
import { placeBet, type LedgerState, type PlaceBetInput } from '../src/ledger';

/**
 * Paper mode is mandatory. Before this guard existed, that rested entirely
 * on both UI call sites happening to pass the literal 'PAPER' — a fact
 * verifiable only by reading the code. A third call site, or one edit to
 * an existing one, would have silently recorded a bet marked REAL_TRACKING.
 *
 * These tests pin the invariant at the only place a bet can be created.
 */

function freshLedger(): LedgerState {
  return { bets: [], betEvents: [], auditLog: [], bankrollBalance: 10_000 };
}

function input(overrides: Partial<PlaceBetInput> = {}): PlaceBetInput {
  return {
    userId: 'u1',
    bankrollAccountId: 'bk1',
    gameId: 'g1',
    market: 'SPREAD',
    selection: 'HOME',
    line: -3,
    american: -110,
    stake: 100,
    mode: 'PAPER',
    placedAt: '2026-09-13T15:00:00Z',
    recommendationId: 'rec1',
    predictionId: 'pred1',
    modelVersionId: 'mv1',
    featureSnapshotId: 'fs1',
    ...overrides,
  } as PlaceBetInput;
}

describe('paper mode is mandatory at the ledger boundary', () => {
  it('records a paper bet', () => {
    const ledger = placeBet(freshLedger(), input());
    expect(ledger.bets).toHaveLength(1);
    expect(ledger.bets[0]?.mode).toBe('PAPER');
  });

  it('refuses REAL_TRACKING outright', () => {
    expect(() => placeBet(freshLedger(), input({ mode: 'REAL_TRACKING' as PlaceBetInput['mode'] })))
      .toThrow(/records paper bets only/);
  });

  it('refuses any mode that is not exactly PAPER', () => {
    for (const mode of ['real_tracking', 'LIVE', 'paper', '', undefined, null]) {
      expect(() => placeBet(freshLedger(), input({ mode: mode as PlaceBetInput['mode'] })))
        .toThrow(/records paper bets only/);
    }
  });

  it('rejects before touching the ledger, so nothing is half-written', () => {
    const ledger = freshLedger();
    try {
      placeBet(ledger, input({ mode: 'REAL_TRACKING' as PlaceBetInput['mode'] }));
    } catch {
      /* expected */
    }
    expect(ledger.bets).toHaveLength(0);
    expect(ledger.betEvents).toHaveLength(0);
    expect(ledger.auditLog).toHaveLength(0);
  });

  it('checks the mode before the stake, so an invalid mode is never masked by a stake error', () => {
    // Both are invalid here. The mode message must win, or a real-money
    // attempt could be reported as a mere staking problem.
    expect(() =>
      placeBet(freshLedger(), {
        ...input({ mode: 'REAL_TRACKING' as PlaceBetInput['mode'] }),
        stake: -5,
      }),
    ).toThrow(/records paper bets only/);
  });
});
