import { useQuery } from '@tanstack/react-query';

/**
 * Access to the Python analytical engine.
 *
 * Separate from `lib/api.ts` on purpose. That module serves the
 * demonstration generator; this one only ever returns what the engine
 * actually computed. Nothing here falls back to generated data when the
 * engine is unreachable — a screen that quietly swaps real numbers for
 * invented ones is worse than a screen that is plainly down.
 */

// Same-origin via the dev proxy in vite.config.ts. A direct cross-port
// fetch is blocked in sandboxed preview panes and surfaces as a bare
// "Failed to fetch" that looks like the engine is down when it is fine.
export const ENGINE_BASE =
  (import.meta.env.VITE_FDE_API_URL as string | undefined) ?? '/research-api';
// No credential is read here, deliberately.
//
// Vite inlines every VITE_-prefixed variable into the shipped bundle as a
// string literal. A `VITE_FDE_API_TOKEN` therefore published the engine's
// bearer token to anyone who loaded the page - verified by building with
// one set and grepping the output. The dev proxy attaches the header
// server-side instead, from a variable Vite never sees.
export async function engineGet<T>(path: string): Promise<T> {
  const res = await fetch(`${ENGINE_BASE}${path}`);
  if (!res.ok) throw new Error(`Engine returned ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface HealthCheck {
  id: string;
  status: string;
  severity: string;
  explanation: string;
  remediation?: string | null;
  affected_game_count?: number;
  suppresses_candidates?: boolean;
}

export interface ForwardHealth {
  generated_at_utc: string;
  data_mode: string;
  total: number;
  ok: number;
  worst_severity: string;
  by_scope: Record<string, HealthCheck[]>;
}

export interface SlateGame {
  canonical_game_id: string;
  away_team_id: string;
  home_team_id: string;
  kickoff_utc: string;
  season: number;
  season_type: string;
  week: number;
  venue: string | null;
  neutral_site: boolean;
  game_status: string;
  schedule_provider: string;
}

export interface ForwardSlate {
  generated_at_utc: string;
  count: number;
  games: SlateGame[];
}

export interface ModelSummary {
  id: string;
  name?: string;
  approval_status: string;
  target?: string;
  algorithm?: string;
  feature_set?: string;
  created_at?: string;
}

export interface ComparisonRow {
  model_version_id: string;
  scope: string;
  sample_size: number;
  metrics: Record<string, number>;
}

export interface ModelComparison {
  rows: ComparisonRow[];
  market_benchmark_id: string;
}

export function useForwardHealth() {
  return useQuery({
    queryKey: ['engine', 'forward-health'],
    queryFn: () => engineGet<ForwardHealth>('/v1/forward/health'),
    refetchInterval: 120_000,
    retry: false,
  });
}

export function useForwardSlate() {
  return useQuery({
    queryKey: ['engine', 'forward-slate'],
    queryFn: () => engineGet<ForwardSlate>('/v1/forward/slate'),
    retry: false,
  });
}

export function useEngineModels() {
  return useQuery({
    queryKey: ['engine', 'models'],
    queryFn: () => engineGet<ModelSummary[]>('/v1/models'),
    retry: false,
  });
}

export function useModelComparison() {
  return useQuery({
    queryKey: ['engine', 'model-comparison'],
    queryFn: () => engineGet<ModelComparison>('/v1/performance/model-comparison'),
    retry: false,
  });
}

// --------------------------------------------------------------------------
// Research candidates and the forward-test record
// --------------------------------------------------------------------------

export interface CandidateRow {
  canonical_game_id: string;
  away_team_id: string;
  home_team_id: string;
  kickoff_utc: string;
  status: string;
  market: string;
  selection: string | null;
  horizon: string;
  line: number | null;
  american: number | null;
  price_source: string | null;
  price_age_seconds: number | null;
  model_probability: number | null;
  conservative_probability: number | null;
  break_even_probability: number | null;
  edge: number | null;
  expected_value: number | null;
  policy_version: string;
  model_version: string;
  as_of_at: string | null;
}

export interface GateCheck {
  check: string;
  explanation: string;
  remediation: string;
}

export interface CandidateGate {
  open: boolean;
  /** Failures in a suppressing scope. These stop the engine evaluating. */
  blocked_by: GateCheck[];
  /**
   * Operational failures. These do NOT stop the engine, and the engine
   * does not treat them as reasons to refuse — but they are usually why a
   * slate is empty, so a screen that drops them explains nothing.
   */
  degraded_by: GateCheck[];
}

export interface ForwardCandidates {
  generated_at_utc: string;
  data_mode: string;
  horizon_hours: number;
  gate: CandidateGate;
  count: number;
  candidates: CandidateRow[];
  not_a_claim: string;
}

export interface ForwardCohort {
  policy_version: string;
  data_mode: string;
  total_rows: number;
  statuses: Record<string, number>;
  settled: number;
  wins: number;
  losses: number;
  pushes: number;
  total_pnl_units: number;
  roi_per_bet: number | null;
  max_drawdown_units: number;
  mean_clv_line: number | null;
  mean_clv_probability: number | null;
  sample_warning: string | null;
  window: { start: string; end: string };
  model_version: string;
  calibration_version: string | null;
  policy_hash: string;
  frozen_at: string | null;
}

export interface ForwardPerformance {
  generated_at_utc: string;
  data_mode: string;
  cohorts: ForwardCohort[];
  not_a_claim: string;
}

export function useForwardCandidates() {
  return useQuery({
    queryKey: ['engine', 'forward-candidates'],
    queryFn: () => engineGet<ForwardCandidates>('/v1/forward/candidates'),
    refetchInterval: 60_000,
    retry: false,
  });
}

export function useForwardPerformance() {
  return useQuery({
    queryKey: ['engine', 'forward-performance'],
    queryFn: () => engineGet<ForwardPerformance>('/v1/forward/performance'),
    retry: false,
  });
}
