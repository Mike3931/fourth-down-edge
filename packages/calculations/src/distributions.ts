/**
 * Scoring-distribution helpers used for demo predictions and Game Lab charts.
 *
 * V1 approximates margin and total distributions as discretized normals with
 * extra mass on key football numbers for the margin. This is an explicitly
 * labeled placeholder for the future simulation-based analytical service.
 */

export function normalPdf(x: number, mean: number, std: number): number {
  const z = (x - mean) / std;
  return Math.exp(-0.5 * z * z) / (std * Math.sqrt(2 * Math.PI));
}

/** Abramowitz–Stegun approximation of the standard normal CDF. */
export function normalCdf(x: number, mean = 0, std = 1): number {
  const z = (x - mean) / (std * Math.SQRT2);
  return 0.5 * (1 + erf(z));
}

export function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y =
    1 -
    (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-ax * ax);
  return sign * y;
}

/** NFL key-number extra mass for margins (home-relative absolute margin). */
const KEY_NUMBER_BOOST: Record<number, number> = {
  3: 2.1, 7: 1.75, 6: 1.35, 10: 1.3, 4: 1.2, 14: 1.2, 17: 1.1,
};

export interface DiscretePoint {
  value: number;
  probability: number;
}

/**
 * Discretized margin distribution over integer margins in [-45, 45], with
 * key-number boosting and zero-margin removed (NFL games rarely tie; we fold
 * tie mass into +/-1). Probabilities sum to 1.
 */
export function marginDistribution(mean: number, std: number): DiscretePoint[] {
  const pts: DiscretePoint[] = [];
  let mass = 0;
  for (let m = -45; m <= 45; m++) {
    if (m === 0) continue;
    const boost = KEY_NUMBER_BOOST[Math.abs(m)] ?? 1;
    const p = normalPdf(m, mean, std) * boost;
    pts.push({ value: m, probability: p });
    mass += p;
  }
  return pts.map((p) => ({ value: p.value, probability: p.probability / mass }));
}

/** Discretized total distribution over integer totals in [10, 90]. */
export function totalDistribution(mean: number, std: number): DiscretePoint[] {
  const pts: DiscretePoint[] = [];
  let mass = 0;
  for (let t = 10; t <= 90; t++) {
    const p = normalPdf(t, mean, std);
    pts.push({ value: t, probability: p });
    mass += p;
  }
  return pts.map((p) => ({ value: p.value, probability: p.probability / mass }));
}

/** P(home wins) from a margin distribution. */
export function homeWinProbability(dist: DiscretePoint[]): number {
  return dist.filter((p) => p.value > 0).reduce((a, p) => a + p.probability, 0);
}

/**
 * Cover/push/loss probabilities for a HOME spread bet at `line` (home-relative:
 * home -3.5 means line = -3.5; home covers when margin + line > 0).
 */
export function spreadOutcomeProbabilities(
  dist: DiscretePoint[],
  line: number,
): { cover: number; push: number; lose: number } {
  let cover = 0;
  let push = 0;
  for (const p of dist) {
    const adj = p.value + line;
    if (Math.abs(adj) < 1e-9) push += p.probability;
    else if (adj > 0) cover += p.probability;
  }
  return { cover, push, lose: Math.max(0, 1 - cover - push) };
}

/** Over/push/under probabilities at a total line. */
export function totalOutcomeProbabilities(
  dist: DiscretePoint[],
  line: number,
): { over: number; push: number; under: number } {
  let over = 0;
  let push = 0;
  for (const p of dist) {
    if (Math.abs(p.value - line) < 1e-9) push += p.probability;
    else if (p.value > line) over += p.probability;
  }
  return { over, push, under: Math.max(0, 1 - over - push) };
}

/** Central interval [lo, hi] containing at least `coverage` mass. */
export function centralInterval(
  dist: DiscretePoint[],
  coverage: number,
): [number, number] {
  const sorted = [...dist].sort((a, b) => a.value - b.value);
  const tail = (1 - coverage) / 2;
  let cum = 0;
  let lo = sorted[0]!.value;
  let hi = sorted[sorted.length - 1]!.value;
  for (const p of sorted) {
    cum += p.probability;
    if (cum >= tail) { lo = p.value; break; }
  }
  cum = 0;
  for (let i = sorted.length - 1; i >= 0; i--) {
    cum += sorted[i]!.probability;
    if (cum >= tail) { hi = sorted[i]!.value; break; }
  }
  return [lo, hi];
}
