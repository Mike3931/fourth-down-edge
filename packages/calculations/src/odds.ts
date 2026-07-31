/**
 * Deterministic odds conversion utilities.
 * American odds must be an integer with absolute value >= 100 (except that
 * we tolerate any nonzero magnitude >= 100). Zero is invalid.
 */

export function assertValidAmerican(american: number): void {
  if (!Number.isFinite(american) || Math.abs(american) < 100) {
    throw new RangeError(`Invalid American odds: ${american}. |odds| must be >= 100.`);
  }
}

/** American odds -> decimal odds. */
export function americanToDecimal(american: number): number {
  assertValidAmerican(american);
  if (american > 0) return 1 + american / 100;
  return 1 + 100 / Math.abs(american);
}

/** American odds -> raw implied probability (includes vig). */
export function americanToImpliedProbability(american: number): number {
  assertValidAmerican(american);
  if (american > 0) return 100 / (american + 100);
  return Math.abs(american) / (Math.abs(american) + 100);
}

/**
 * Probability -> fair American odds.
 * p = 0.5 maps to +100 (equivalently -100); we return +100.
 */
export function probabilityToFairAmerican(probability: number): number {
  if (!(probability > 0 && probability < 1)) {
    throw new RangeError(`Probability must be in (0, 1), got ${probability}`);
  }
  if (probability <= 0.5) {
    return Math.round((100 * (1 - probability)) / probability);
  }
  return Math.round((-100 * probability) / (1 - probability));
}

/** Decimal odds -> American odds. */
export function decimalToAmerican(decimal: number): number {
  if (!(decimal > 1)) {
    throw new RangeError(`Decimal odds must be > 1, got ${decimal}`);
  }
  if (decimal >= 2) return Math.round((decimal - 1) * 100);
  return Math.round(-100 / (decimal - 1));
}

/**
 * Proportional no-vig (fair) probabilities for a set of mutually exclusive
 * outcomes quoted in American odds.
 */
export function noVigProbabilities(americans: number[]): number[] {
  if (americans.length < 2) {
    throw new RangeError('No-vig normalization requires at least two outcomes.');
  }
  const raw = americans.map(americanToImpliedProbability);
  const overround = raw.reduce((a, b) => a + b, 0);
  if (overround <= 0) throw new RangeError('Overround must be positive.');
  return raw.map((p) => p / overround);
}

/** Total book margin (overround − 1) for a set of quotes. */
export function overround(americans: number[]): number {
  return americans.map(americanToImpliedProbability).reduce((a, b) => a + b, 0) - 1;
}

/**
 * Break-even probability for a price, accounting for pushes:
 * a bettor needs win probability p such that EV = 0 given push probability q:
 *   p * b − (1 − p − q) = 0  =>  p = (1 − q) / (b + 1)
 * where b = decimal − 1 (net profit per unit staked).
 */
export function breakEvenProbability(american: number, pushProbability = 0): number {
  if (pushProbability < 0 || pushProbability >= 1) {
    throw new RangeError(`Push probability must be in [0, 1), got ${pushProbability}`);
  }
  const b = americanToDecimal(american) - 1;
  return (1 - pushProbability) / (b + 1);
}
