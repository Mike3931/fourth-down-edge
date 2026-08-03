/**
 * Typed client for the Python analytical engine (apps/api, FastAPI).
 *
 * Contract rules enforced here, not by convention:
 *  - Every response is validated at runtime with zod against the OpenAPI
 *    shapes; a payload that does not match fails loudly.
 *  - There is NO fallback from research data to demo data: failures
 *    return explicit error states ('unavailable' | 'model-unavailable')
 *    that the UI must render as DATA INCOMPLETE, never as numbers.
 *  - Research responses always carry the research banner; the UI shows it.
 */

import { z } from 'zod';

export const RESEARCH_MODE_LABEL = 'RESEARCH MODE — MODEL NOT APPROVED FOR REAL-MONEY DECISIONS';

// --------------------------------------------------------------------------
// Schemas (mirror apps/api/src/fde_api/api/schemas.py)
// --------------------------------------------------------------------------

export const healthSchema = z.object({
  status: z.enum(['ok', 'degraded']),
  research_banner: z.string(),
  database: z.enum(['ok', 'unavailable']),
  games: z.number().int(),
  predictions: z.number().int(),
  auth: z.string(),
  version: z.string(),
});

export const modelSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  target: z.string(),
  algorithm: z.string(),
  approval_status: z.enum(['research_only', 'approved', 'retired']),
  feature_set: z.string().nullable(),
  calibration_version: z.string().nullable(),
  train_window: z.string().nullable(),
  validation_window: z.string().nullable(),
  test_window: z.string().nullable(),
  created_at: z.string(),
});

export const predictionOutputsSchema = z.object({
  expected_home_points: z.number(),
  expected_away_points: z.number(),
  expected_margin: z.number(),
  expected_total: z.number(),
  sigma_margin: z.number(),
  sigma_total: z.number(),
  home_win_prob: z.number().min(0).max(1),
  spread_cover_prob: z.number().min(0).max(1).nullable().optional(),
  spread_push_prob: z.number().min(0).max(1).nullable().optional(),
  total_over_prob: z.number().min(0).max(1).nullable().optional(),
  total_push_prob: z.number().min(0).max(1).nullable().optional(),
  margin_p10: z.number(),
  margin_p90: z.number(),
  total_p10: z.number(),
  total_p90: z.number(),
  components: z.record(z.unknown()).nullable().optional(),
});

export const predictionSchema = z.object({
  id: z.string(),
  game_id: z.string(),
  model_version_id: z.string(),
  model_approval_status: z.enum(['research_only', 'approved', 'retired']),
  feature_snapshot_id: z.string().nullable(),
  horizon: z.string(),
  as_of_at: z.string(),
  created_at: z.string(),
  outputs: predictionOutputsSchema,
  research_banner: z.string(),
});

export const modelComparisonSchema = z.object({
  rows: z.array(
    z.object({
      model_version_id: z.string(),
      scope: z.string(),
      sample_size: z.number().int(),
      metrics: z.record(z.unknown()),
    }),
  ),
  market_benchmark_id: z.string(),
  research_banner: z.string(),
});

export type ResearchHealth = z.infer<typeof healthSchema>;
export type ResearchModel = z.infer<typeof modelSummarySchema>;
export type ResearchPrediction = z.infer<typeof predictionSchema>;
export type ModelComparison = z.infer<typeof modelComparisonSchema>;

// --------------------------------------------------------------------------
// Result type — errors are values, so the UI cannot forget to handle them
// --------------------------------------------------------------------------

export type ResearchResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: 'unavailable' | 'model-unavailable' | 'invalid-response'; detail: string };

async function fetchValidated<T>(
  url: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<ResearchResult<T>> {
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (e) {
    return { ok: false, error: 'unavailable', detail: `Network error: ${String(e)}` };
  }
  if (resp.status === 404) {
    return { ok: false, error: 'model-unavailable', detail: `Not found: ${url}` };
  }
  if (!resp.ok) {
    // FastAPI puts an explanation in `detail`. A 503 for a missing API
    // token says exactly which variable to set; discarding that turns an
    // actionable misconfiguration into a bare "unavailable".
    let detail = `HTTP ${resp.status} from analytical engine`;
    try {
      const body: unknown = await resp.json();
      const serverDetail =
        typeof body === 'object' && body !== null && 'detail' in body
          ? (body as { detail: unknown }).detail
          : undefined;
      if (typeof serverDetail === 'string' && serverDetail) {
        detail = `${detail}: ${serverDetail}`;
      }
    } catch {
      // Non-JSON error body (a gateway page, say) — the status alone stands.
    }
    return { ok: false, error: 'unavailable', detail };
  }
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    return { ok: false, error: 'invalid-response', detail: 'Response was not JSON' };
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    return { ok: false, error: 'invalid-response', detail: parsed.error.message };
  }
  return { ok: true, data: parsed.data };
}

// --------------------------------------------------------------------------
// Client
// --------------------------------------------------------------------------

export class ResearchApiClient {
  constructor(private readonly baseUrl: string) {}

  private url(path: string): string {
    return `${this.baseUrl.replace(/\/$/, '')}${path}`;
  }

  health(): Promise<ResearchResult<ResearchHealth>> {
    return fetchValidated(this.url('/health'), healthSchema);
  }

  models(): Promise<ResearchResult<ResearchModel[]>> {
    return fetchValidated(this.url('/v1/models'), z.array(modelSummarySchema));
  }

  gamePredictions(gameId: string): Promise<ResearchResult<ResearchPrediction[]>> {
    return fetchValidated(
      this.url(`/v1/games/${encodeURIComponent(gameId)}/predictions`),
      z.array(predictionSchema),
    );
  }

  modelComparison(): Promise<ResearchResult<ModelComparison>> {
    return fetchValidated(this.url('/v1/performance/model-comparison'), modelComparisonSchema);
  }
}
