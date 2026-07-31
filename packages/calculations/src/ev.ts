import { americanToDecimal } from './odds';

/**
 * Expected value per dollar staked.
 *
 *   EV = p_win * netProfit − p_loss * 1
 *
 * Push probability is handled explicitly: on a push the stake is returned,
 * contributing 0 to EV. p_loss = 1 − p_win − p_push.
 */
export function expectedValuePerDollar(
  winProbability: number,
  american: number,
  pushProbability = 0,
): number {
  validateProbs(winProbability, pushProbability);
  const netProfit = americanToDecimal(american) - 1;
  const lossProbability = 1 - winProbability - pushProbability;
  return winProbability * netProfit - lossProbability;
}

/** Edge = model win probability − break-even probability at the price. */
export function edgeVsBreakEven(
  winProbability: number,
  breakEvenProb: number,
): number {
  return winProbability - breakEvenProb;
}

function validateProbs(winProbability: number, pushProbability: number): void {
  if (!(winProbability >= 0 && winProbability <= 1)) {
    throw new RangeError(`Win probability must be in [0, 1], got ${winProbability}`);
  }
  if (!(pushProbability >= 0 && pushProbability < 1)) {
    throw new RangeError(`Push probability must be in [0, 1), got ${pushProbability}`);
  }
  if (winProbability + pushProbability > 1 + 1e-12) {
    throw new RangeError('winProbability + pushProbability must not exceed 1.');
  }
}
