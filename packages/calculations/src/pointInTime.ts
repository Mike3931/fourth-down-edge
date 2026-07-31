/**
 * Point-in-time integrity utilities.
 *
 * A prediction with cutoff (asOfAt) may use only records whose observedAt is
 * at or before the cutoff. These helpers reject look-ahead leakage
 * deterministically and are exercised heavily in tests.
 */

export interface ObservedRecord {
  observedAt: string;
}

export class LookaheadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LookaheadError';
  }
}

function ts(iso: string, label: string): number {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) throw new LookaheadError(`Invalid timestamp for ${label}: "${iso}"`);
  return t;
}

/** True when the record is usable at the given prediction cutoff. */
export function isUsableAtCutoff(record: ObservedRecord, cutoffAt: string): boolean {
  return ts(record.observedAt, 'record.observedAt') <= ts(cutoffAt, 'cutoffAt');
}

/** Filter to only records observed at or before the cutoff. */
export function filterToCutoff<T extends ObservedRecord>(records: T[], cutoffAt: string): T[] {
  return records.filter((r) => isUsableAtCutoff(r, cutoffAt));
}

/**
 * Latest usable record at the cutoff (by observedAt), or undefined.
 * Deterministic tie-break on id when present.
 */
export function latestAtCutoff<T extends ObservedRecord & { id?: string }>(
  records: T[],
  cutoffAt: string,
): T | undefined {
  const usable = filterToCutoff(records, cutoffAt);
  if (usable.length === 0) return undefined;
  return usable.reduce((best, r) => {
    const bt = ts(best.observedAt, 'observedAt');
    const rt = ts(r.observedAt, 'observedAt');
    if (rt > bt) return r;
    if (rt === bt && (r.id ?? '') > (best.id ?? '')) return r;
    return best;
  });
}

/** Throws LookaheadError if any record was observed after the cutoff. */
export function assertNoLookahead(
  records: ObservedRecord[],
  cutoffAt: string,
  context: string,
): void {
  for (const r of records) {
    if (!isUsableAtCutoff(r, cutoffAt)) {
      throw new LookaheadError(
        `${context}: record observed at ${r.observedAt} is after prediction cutoff ${cutoffAt}. ` +
          'Future information must never enter a historical prediction.',
      );
    }
  }
}

/**
 * Guards a forecast generated timestamp (e.g., weather model run) against the
 * cutoff — a forecast generated after the cutoff is look-ahead even if it was
 * "observed" earlier by clock error.
 */
export function assertForecastUsable(
  forecastGeneratedAt: string,
  observedAt: string,
  cutoffAt: string,
): void {
  if (ts(forecastGeneratedAt, 'forecastGeneratedAt') > ts(cutoffAt, 'cutoffAt')) {
    throw new LookaheadError(
      `Weather forecast generated at ${forecastGeneratedAt} is after cutoff ${cutoffAt}.`,
    );
  }
  if (ts(observedAt, 'observedAt') > ts(cutoffAt, 'cutoffAt')) {
    throw new LookaheadError(
      `Weather forecast observed at ${observedAt} is after cutoff ${cutoffAt}.`,
    );
  }
}

/**
 * A model version is usable only if it was approved at or before the cutoff.
 */
export function isModelUsableAtCutoff(
  approvedAt: string | undefined,
  cutoffAt: string,
): boolean {
  if (!approvedAt) return false;
  return ts(approvedAt, 'approvedAt') <= ts(cutoffAt, 'cutoffAt');
}

/**
 * Game results are usable only for predictions whose cutoff is after the
 * game finished — i.e., never for the game being predicted, and never for
 * games that had not finished by the cutoff.
 */
export function isResultUsableAtCutoff(
  gameEndedAt: string | undefined,
  cutoffAt: string,
): boolean {
  if (!gameEndedAt) return false;
  return ts(gameEndedAt, 'gameEndedAt') <= ts(cutoffAt, 'cutoffAt');
}
