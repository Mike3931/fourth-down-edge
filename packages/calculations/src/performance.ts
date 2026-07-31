import type { CalibrationBin } from '@fde/shared-types';

/** Binary log loss (natural log), clamped for numerical stability. */
export function logLoss(pairs: Array<{ p: number; outcome: 0 | 1 }>): number {
  if (pairs.length === 0) return NaN;
  const eps = 1e-12;
  const s = pairs.reduce((acc, { p, outcome }) => {
    const pc = Math.min(1 - eps, Math.max(eps, p));
    return acc - (outcome === 1 ? Math.log(pc) : Math.log(1 - pc));
  }, 0);
  return s / pairs.length;
}

/** Brier score. */
export function brierScore(pairs: Array<{ p: number; outcome: 0 | 1 }>): number {
  if (pairs.length === 0) return NaN;
  return pairs.reduce((acc, { p, outcome }) => acc + (p - outcome) ** 2, 0) / pairs.length;
}

/** Expected calibration error over equal-width bins. */
export function calibrationBins(
  pairs: Array<{ p: number; outcome: 0 | 1 }>,
  binCount = 10,
): CalibrationBin[] {
  const bins: CalibrationBin[] = [];
  for (let i = 0; i < binCount; i++) {
    const lo = i / binCount;
    const hi = (i + 1) / binCount;
    const inBin = pairs.filter((x) => x.p >= lo && (i === binCount - 1 ? x.p <= hi : x.p < hi));
    const count = inBin.length;
    bins.push({
      predictedLow: lo,
      predictedHigh: hi,
      meanPredicted: count ? inBin.reduce((a, x) => a + x.p, 0) / count : (lo + hi) / 2,
      observedRate: count ? inBin.reduce((a, x) => a + x.outcome, 0) / count : NaN,
      count,
    });
  }
  return bins;
}

export function expectedCalibrationError(bins: CalibrationBin[], totalCount: number): number {
  if (totalCount === 0) return NaN;
  return bins.reduce((acc, b) => {
    if (b.count === 0 || Number.isNaN(b.observedRate)) return acc;
    return acc + (b.count / totalCount) * Math.abs(b.observedRate - b.meanPredicted);
  }, 0);
}

/** OLS slope/intercept of outcome on predicted probability. */
export function calibrationLine(
  pairs: Array<{ p: number; outcome: 0 | 1 }>,
): { slope: number; intercept: number } {
  const n = pairs.length;
  if (n < 2) return { slope: NaN, intercept: NaN };
  const meanP = pairs.reduce((a, x) => a + x.p, 0) / n;
  const meanY = pairs.reduce((a, x) => a + x.outcome, 0) / n;
  let sxx = 0;
  let sxy = 0;
  for (const { p, outcome } of pairs) {
    sxx += (p - meanP) ** 2;
    sxy += (p - meanP) * (outcome - meanY);
  }
  if (sxx === 0) return { slope: NaN, intercept: NaN };
  const slope = sxy / sxx;
  return { slope, intercept: meanY - slope * meanP };
}

/** Max drawdown of a bankroll curve (fraction of peak). */
export function maxDrawdown(curve: number[]): number {
  let peak = -Infinity;
  let mdd = 0;
  for (const v of curve) {
    peak = Math.max(peak, v);
    if (peak > 0) mdd = Math.max(mdd, (peak - v) / peak);
  }
  return mdd;
}

/**
 * Closing-line value in probability points for a selection: the closing
 * no-vig probability minus the no-vig probability implied by the price taken.
 * Beating the close means paying a lower implied probability than the market
 * finally settled on, so positive CLV is favorable.
 */
export function closingLineValuePct(
  takenNoVigProb: number,
  closingNoVigProb: number,
): number {
  return (closingNoVigProb - takenNoVigProb) * 100;
}
