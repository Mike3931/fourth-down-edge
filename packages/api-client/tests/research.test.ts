import { afterEach, describe, expect, it, vi } from 'vitest';
import { RESEARCH_MODE_LABEL, ResearchApiClient, predictionSchema } from '../src/research';

const VALID_PREDICTION = {
  id: 'pred_x',
  game_id: '2025_01_KC_LAC',
  model_version_id: 'market-residual-v1',
  model_approval_status: 'research_only',
  feature_snapshot_id: null,
  horizon: 'PREGAME',
  as_of_at: '2025-09-07T15:30:00+00:00',
  created_at: '2026-08-01T00:00:00+00:00',
  outputs: {
    expected_home_points: 24.1,
    expected_away_points: 21.3,
    expected_margin: 2.8,
    expected_total: 45.4,
    sigma_margin: 13.1,
    sigma_total: 9.9,
    home_win_prob: 0.58,
    spread_cover_prob: 0.52,
    spread_push_prob: 0.02,
    total_over_prob: 0.49,
    total_push_prob: 0.01,
    margin_p10: -14.0,
    margin_p90: 19.6,
    total_p10: 32.7,
    total_p90: 58.1,
    components: null,
  },
  research_banner: RESEARCH_MODE_LABEL,
};

function mockFetch(impl: () => Promise<Response> | Response) {
  vi.stubGlobal('fetch', vi.fn(impl));
}

afterEach(() => vi.unstubAllGlobals());

describe('ResearchApiClient', () => {
  it('returns validated predictions on success', async () => {
    mockFetch(() => new Response(JSON.stringify([VALID_PREDICTION]), { status: 200 }));
    const res = await new ResearchApiClient('http://engine.local').gamePredictions('2025_01_KC_LAC');
    expect(res.ok).toBe(true);
    if (res.ok) {
      const [first] = res.data;
      expect(first).toBeDefined();
      expect(first!.outputs.home_win_prob).toBeCloseTo(0.58);
      expect(first!.research_banner).toBe(RESEARCH_MODE_LABEL);
    }
  });

  it('reports unavailable on network failure — never a fallback value', async () => {
    mockFetch(() => Promise.reject(new TypeError('fetch failed')));
    const res = await new ResearchApiClient('http://engine.local').health();
    expect(res).toMatchObject({ ok: false, error: 'unavailable' });
  });

  it('reports model-unavailable on 404', async () => {
    mockFetch(() => new Response('{"detail":"nope"}', { status: 404 }));
    const res = await new ResearchApiClient('http://engine.local').gamePredictions('nope');
    expect(res).toMatchObject({ ok: false, error: 'model-unavailable' });
  });

  it('surfaces the engine\'s explanation for a misconfiguration 503', async () => {
    // The engine returns 503 with the variable to set when no API token is
    // configured. Reporting a bare "unavailable" would send someone hunting
    // for an outage that is really a missing environment variable.
    mockFetch(() => new Response(
      JSON.stringify({
        detail: 'API token not configured. Set FDE_API_TOKEN, or set FDE_ALLOW_UNAUTHENTICATED=1 to serve without authentication on purpose.',
      }),
      { status: 503 },
    ));
    const res = await new ResearchApiClient('http://engine.local').health();
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error).toBe('unavailable');
      expect(res.detail).toContain('503');
      expect(res.detail).toContain('FDE_API_TOKEN');
    }
  });

  it('still reports the status when an error body is not JSON', async () => {
    mockFetch(() => new Response('<html>502 Bad Gateway</html>', { status: 502 }));
    const res = await new ResearchApiClient('http://engine.local').health();
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error).toBe('unavailable');
      expect(res.detail).toContain('502');
    }
  });

  it('rejects payloads that fail runtime validation', async () => {
    const bad = { ...VALID_PREDICTION, outputs: { ...VALID_PREDICTION.outputs, home_win_prob: 1.7 } };
    mockFetch(() => new Response(JSON.stringify([bad]), { status: 200 }));
    const res = await new ResearchApiClient('http://engine.local').gamePredictions('x');
    expect(res).toMatchObject({ ok: false, error: 'invalid-response' });
  });

  it('rejects non-JSON bodies', async () => {
    mockFetch(() => new Response('<html>gateway error</html>', { status: 200 }));
    const res = await new ResearchApiClient('http://engine.local').health();
    expect(res).toMatchObject({ ok: false, error: 'invalid-response' });
  });

  it('schema enforces research approval statuses only', () => {
    const parsed = predictionSchema.safeParse({
      ...VALID_PREDICTION,
      model_approval_status: 'totally_fine_trust_me',
    });
    expect(parsed.success).toBe(false);
  });
});
