import { useQuery } from '@tanstack/react-query';
import { Card, LoadingState, Mono, Pill } from '@fde/ui';
import { RESEARCH_MODE_LABEL, ResearchApiClient } from '@fde/api-client';
import { useStore } from '../lib/store';
import { fmtPct } from '../lib/format';

/**
 * Research predictions from the Python analytical engine.
 *
 * Hard rules, matching the engine contract:
 *  - Research mode is opt-in (Settings → analytical engine URL). Off = this
 *    panel renders a single "not connected" line, nothing else.
 *  - No silent fallback: if the engine is unreachable or returns an invalid
 *    payload, this panel shows DATA INCOMPLETE — it never substitutes demo
 *    numbers for research numbers.
 *  - Everything shown here is research_only and carries the banner; the
 *    styling (violet) is deliberately distinct from demo amber.
 */
export default function ResearchPanel({ gameId }: { gameId: string }) {
  const store = useStore();
  const baseUrl = store.settings.researchApiUrl?.trim();
  const client = baseUrl ? new ResearchApiClient(baseUrl) : null;

  const query = useQuery({
    queryKey: ['research-predictions', baseUrl, gameId],
    queryFn: () => client!.gamePredictions(gameId),
    enabled: Boolean(client),
    staleTime: 60_000,
    retry: false,
  });

  if (!baseUrl) {
    return (
      <Card className="p-4">
        <h2 className="text-sm font-semibold text-ink">Research predictions</h2>
        <p className="mt-1.5 text-xs text-ink-faint">
          Analytical engine not connected. Set its URL in Settings to see research-grade model output
          here. Demo recommendations elsewhere on this page are unaffected.
        </p>
      </Card>
    );
  }

  return (
    <Card className="border-violet-500/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Research predictions</h2>
        <Pill tone="warn">RESEARCH — NOT APPROVED</Pill>
      </div>
      <p className="mt-1 rounded bg-violet-500/10 px-2 py-1 text-[11px] font-medium tracking-wide text-violet-300">
        {RESEARCH_MODE_LABEL}
      </p>

      {query.isLoading ? (
        <div className="mt-3">
          <LoadingState label="Querying analytical engine…" />
        </div>
      ) : null}

      {query.data && !query.data.ok ? (
        <div className="mt-3 rounded border border-edge bg-panel-raised p-3">
          <p className="text-xs font-semibold text-warn">DATA INCOMPLETE</p>
          <p className="mt-1 text-xs text-ink-muted">
            {query.data.error === 'unavailable'
              ? 'The analytical engine is unreachable. Research output is withheld rather than substituted — demo data is never shown in its place.'
              : query.data.error === 'model-unavailable'
                ? 'No research prediction exists for this game (model or game unknown to the engine).'
                : 'The engine returned a response that failed validation; refusing to display unverified numbers.'}
          </p>
          <Mono className="mt-1 block text-[10px] text-ink-faint">{query.data.detail.slice(0, 200)}</Mono>
        </div>
      ) : null}

      {query.data?.ok && query.data.data.length === 0 ? (
        <p className="mt-3 text-xs text-ink-muted">
          The engine is connected but has no stored predictions for this game.
        </p>
      ) : null}

      {query.data?.ok && query.data.data.length > 0 ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead>
              <tr className="border-b border-edge text-[10px] uppercase tracking-wider text-ink-faint">
                <th className="py-1.5 pr-3">Model</th>
                <th className="py-1.5 pr-3">Status</th>
                <th className="py-1.5 pr-3">Horizon</th>
                <th className="py-1.5 pr-3">Home win</th>
                <th className="py-1.5 pr-3">Margin (80% PI)</th>
                <th className="py-1.5 pr-3">Total (80% PI)</th>
                <th className="py-1.5">As of (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {query.data.data.map((p) => (
                <tr key={p.id} className="border-b border-edge/50">
                  <td className="py-1.5 pr-3 text-ink">{p.model_version_id}</td>
                  <td className="py-1.5 pr-3">
                    <Pill tone="warn">{p.model_approval_status}</Pill>
                  </td>
                  <td className="py-1.5 pr-3 text-ink-muted">{p.horizon}</td>
                  <td className="py-1.5 pr-3">
                    <Mono className="text-ink">{fmtPct(p.outputs.home_win_prob)}</Mono>
                  </td>
                  <td className="py-1.5 pr-3">
                    <Mono className="text-ink">
                      {p.outputs.expected_margin.toFixed(1)}{' '}
                      <span className="text-ink-faint">
                        [{p.outputs.margin_p10.toFixed(0)}, {p.outputs.margin_p90.toFixed(0)}]
                      </span>
                    </Mono>
                  </td>
                  <td className="py-1.5 pr-3">
                    <Mono className="text-ink">
                      {p.outputs.expected_total.toFixed(1)}{' '}
                      <span className="text-ink-faint">
                        [{p.outputs.total_p10.toFixed(0)}, {p.outputs.total_p90.toFixed(0)}]
                      </span>
                    </Mono>
                  </td>
                  <td className="py-1.5">
                    <Mono className="text-ink-faint">{p.as_of_at.slice(0, 16).replace('T', ' ')}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}
