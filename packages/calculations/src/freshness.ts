import type { DataFreshness } from '@fde/shared-types';

export interface FreshnessThresholds {
  /** Minutes after which data is AGING. */
  agingMinutes: number;
  /** Minutes after which data is STALE. */
  staleMinutes: number;
}

export const DEFAULT_FEED_THRESHOLDS: Record<string, FreshnessThresholds> = {
  schedule: { agingMinutes: 24 * 60, staleMinutes: 72 * 60 },
  roster: { agingMinutes: 12 * 60, staleMinutes: 48 * 60 },
  depthChart: { agingMinutes: 24 * 60, staleMinutes: 72 * 60 },
  injury: { agingMinutes: 6 * 60, staleMinutes: 24 * 60 },
  weather: { agingMinutes: 3 * 60, staleMinutes: 12 * 60 },
  odds: { agingMinutes: 30, staleMinutes: 120 },
  manualPrice: { agingMinutes: 30, staleMinutes: 60 },
};

/**
 * Classify freshness of a record given its observedAt and now.
 * MISSING when observedAt is undefined; CONFLICTING is decided upstream by
 * source reconciliation and passed through via `hasConflict`.
 */
export function classifyFreshness(
  observedAt: string | undefined,
  now: string,
  thresholds: FreshnessThresholds,
  hasConflict = false,
): DataFreshness {
  if (hasConflict) return 'CONFLICTING';
  if (!observedAt) return 'MISSING';
  const ageMs = new Date(now).getTime() - new Date(observedAt).getTime();
  if (Number.isNaN(ageMs) || ageMs < 0) return 'CONFLICTING';
  const ageMinutes = ageMs / 60_000;
  if (ageMinutes <= thresholds.agingMinutes) return 'CURRENT';
  if (ageMinutes <= thresholds.staleMinutes) return 'AGING';
  return 'STALE';
}

export function ageMinutes(observedAt: string, now: string): number {
  return (new Date(now).getTime() - new Date(observedAt).getTime()) / 60_000;
}

/** Worst-of aggregation for an overall health status. */
const SEVERITY_ORDER: DataFreshness[] = ['CURRENT', 'AGING', 'STALE', 'CONFLICTING', 'MISSING'];

export function worstFreshness(statuses: DataFreshness[]): DataFreshness {
  let worst: DataFreshness = 'CURRENT';
  for (const s of statuses) {
    if (SEVERITY_ORDER.indexOf(s) > SEVERITY_ORDER.indexOf(worst)) worst = s;
  }
  return worst;
}
