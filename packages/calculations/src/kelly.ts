import { americanToDecimal } from './odds';

/**
 * Full Kelly fraction for a binary bet at decimal odds d with win probability p:
 *
 *   f* = ((d − 1) * p − (1 − p)) / (d − 1)
 *
 * Push probability shrinks both win and loss mass; conditioning on non-push
 * outcomes: p' = p / (1 − q). We compute Kelly on the conditional
 * probabilities and scale the stake by (1 − q) is NOT applied — the stake is
 * still at risk only on non-push outcomes, so the conditional Kelly fraction
 * is the standard conservative choice. A negative result is clamped to zero:
 * a negative Kelly must never generate a stake.
 */
export function fullKelly(
  winProbability: number,
  american: number,
  pushProbability = 0,
): number {
  if (!(winProbability >= 0 && winProbability <= 1)) {
    throw new RangeError(`Win probability must be in [0, 1], got ${winProbability}`);
  }
  if (!(pushProbability >= 0 && pushProbability < 1)) {
    throw new RangeError(`Push probability must be in [0, 1), got ${pushProbability}`);
  }
  const d = americanToDecimal(american);
  const b = d - 1;
  const pConditional = winProbability / (1 - pushProbability);
  if (pConditional >= 1) return 1; // degenerate; caller caps far below this anyway
  const f = (b * pConditional - (1 - pConditional)) / b;
  return Math.max(0, f);
}

/** Fractional Kelly (e.g., 0.25 for quarter Kelly). Never negative. */
export function fractionalKelly(
  winProbability: number,
  american: number,
  fraction: number,
  pushProbability = 0,
): number {
  if (!(fraction > 0 && fraction <= 1)) {
    throw new RangeError(`Kelly fraction must be in (0, 1], got ${fraction}`);
  }
  return fullKelly(winProbability, american, pushProbability) * fraction;
}
