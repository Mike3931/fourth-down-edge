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
