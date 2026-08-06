import { useQuery } from '@tanstack/react-query';

/**
 * The only screen in this app backed by real captured data.
 *
 * Every other page reads the deterministic demonstration generator. This
 * one calls the Python engine's /v1/forward/live and renders exactly what
 * was captured: real fixtures, real book prices, and the consensus
 * builder's own verdict.
 *
 * It renders a missing consensus as the REASON it is missing rather than
 * as a blank or a zero. A market the engine declined to price is a result,
 * and showing it as an empty cell is how a screen starts implying it knows
 * something it does not.
 */

// Same-origin by default via the dev proxy declared in vite.config.ts. A
// direct cross-port fetch is blocked in sandboxed preview panes, and the
// failure surfaces as a bare "Failed to fetch" that looks like the engine
// is down when it is answering perfectly well on curl.
const API_BASE = (import.meta.env.VITE_FDE_API_URL as string | undefined) ?? '/research-api';
const API_TOKEN = import.meta.env.VITE_FDE_API_TOKEN as string | undefined;

interface Quote {
  sportsbook: string;
  market: string;
  selection: string;
  line: number | null;
  american: number;
  decimal_odds: number;
  provider: string;
  provider_mode: string;
  observed_at: string;
}

interface MarketBlock {
  consensus: { median_line: number | null; eligible_books: number; observed_at: string } | null;
  reasons: string[];
  eligible: number;
  considered: number;
}

interface LiveGame {
  canonical_game_id: string;
  away_team_id: string;
  home_team_id: string;
  kickoff_utc: string;
  season: number;
  season_type: string;
  venue: string | null;
  neutral_site: boolean;
  game_status: string;
  schedule_provider: string;
  observed_at: string;
  books: string[];
  quotes: Quote[];
  markets: Record<string, MarketBlock>;
}

interface LiveResponse {
  generated_at_utc: string;
  data_mode: string;
  games: LiveGame[];
  not_a_claim: string;
}

async function fetchLive(): Promise<LiveResponse> {
  const res = await fetch(`${API_BASE}/v1/forward/live`, {
    headers: API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {},
  });
  if (!res.ok) {
    throw new Error(`Engine returned ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as LiveResponse;
}

function fmtAmerican(n: number): string {
  return n > 0 ? `+${n}` : `${n}`;
}

function fmtLine(n: number | null): string {
  if (n === null) return '—';
  return n > 0 ? `+${n}` : `${n}`;
}

export default function LiveSlate() {
  const { data, isLoading, error, dataUpdatedAt, refetch, isFetching } = useQuery({
    queryKey: ['forward-live'],
    queryFn: fetchLive,
    refetchInterval: 60_000,
    retry: false,
  });

  if (isLoading) {
    return <div className="p-6 text-sm text-muted">Asking the engine for captured data…</div>;
  }

  if (error) {
    // Never silently fall back to demo content: a page that quietly swaps
    // real data for generated data is worse than a page that is down.
    return (
      <div className="p-6">
        <h1 className="text-xl font-semibold text-fg">Live Slate</h1>
        <div className="mt-4 rounded border border-danger/40 bg-danger/10 p-4 text-sm">
          <p className="font-medium text-danger">The analytical engine is not reachable.</p>
          <p className="mt-2 text-muted">{(error as Error).message}</p>
          <p className="mt-3 text-muted">
            Expected at <code>{API_BASE}</code>. Start it with:
          </p>
          <pre className="mt-2 overflow-x-auto rounded bg-bg p-2 text-xs">
            cd apps/api &amp;&amp; FDE_ALLOW_UNAUTHENTICATED=1 uvicorn fde_api.api.main:app --port 8000
          </pre>
          <p className="mt-3 text-muted">
            No demonstration data is shown here. This screen is empty rather than misleading.
          </p>
        </div>
      </div>
    );
  }

  const games = data?.games ?? [];

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-fg">Live Slate</h1>
          <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
            REAL CAPTURED DATA
          </span>
          <button
            onClick={() => void refetch()}
            className="ml-auto rounded border border-border px-2 py-1 text-xs text-muted hover:text-fg"
          >
            {isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        <p className="text-sm text-muted">
          Data mode <strong>{data?.data_mode}</strong> · generated {data?.generated_at_utc} · last
          fetched {new Date(dataUpdatedAt).toISOString()}
        </p>
      </header>

      {games.length === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          No fixtures captured in the current horizon. This is the real answer, not a loading state.
        </div>
      )}

      {games.map((g) => (
        <article key={g.canonical_game_id} className="rounded border border-border">
          <div className="border-b border-border p-4">
            <div className="flex flex-wrap items-baseline gap-2">
              <h2 className="text-lg font-semibold text-fg">
                {g.away_team_id} @ {g.home_team_id}
              </h2>
              <span className="rounded bg-bg-subtle px-2 py-0.5 text-xs text-muted">
                {g.season} {g.season_type}
              </span>
              {g.neutral_site && (
                <span className="rounded bg-bg-subtle px-2 py-0.5 text-xs text-muted">neutral site</span>
              )}
            </div>
            <p className="mt-1 text-sm text-muted">
              Kickoff {g.kickoff_utc} · {g.venue ?? 'venue unknown'} · {g.game_status}
            </p>
            <p className="mt-1 text-xs text-muted">
              Fixture attested by <strong>{g.schedule_provider}</strong>, observed {g.observed_at} ·{' '}
              <code>{g.canonical_game_id}</code>
            </p>
          </div>

          <div className="border-b border-border p-4">
            <h3 className="text-sm font-medium text-fg">Consensus</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {Object.entries(g.markets).map(([market, block]) => (
                <div key={market} className="rounded border border-border p-3">
                  <div className="text-xs uppercase tracking-wide text-muted">{market}</div>
                  {block.consensus ? (
                    <div className="mt-1 text-lg font-semibold text-fg">
                      {fmtLine(block.consensus.median_line)}
                      <span className="ml-2 text-xs font-normal text-muted">
                        {block.consensus.eligible_books} books
                      </span>
                    </div>
                  ) : (
                    <div className="mt-1">
                      <div className="text-sm font-medium text-warning">DATA INCOMPLETE</div>
                      {block.reasons.map((r) => (
                        <p key={r} className="mt-1 text-xs text-muted">
                          {r}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="p-4">
            <h3 className="text-sm font-medium text-fg">
              Captured quotes{' '}
              <span className="font-normal text-muted">
                ({g.quotes.length} from {g.books.join(', ') || 'no books'})
              </span>
            </h3>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th className="py-1 pr-4">Book</th>
                    <th className="py-1 pr-4">Market</th>
                    <th className="py-1 pr-4">Selection</th>
                    <th className="py-1 pr-4 text-right">Line</th>
                    <th className="py-1 pr-4 text-right">Price</th>
                    <th className="py-1 pr-4">Source</th>
                    <th className="py-1">Observed</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {g.quotes.map((q, i) => (
                    <tr key={`${q.sportsbook}-${q.market}-${q.selection}-${i}`} className="border-t border-border">
                      <td className="py-1 pr-4">{q.sportsbook}</td>
                      <td className="py-1 pr-4">{q.market}</td>
                      <td className="py-1 pr-4">{q.selection}</td>
                      <td className="py-1 pr-4 text-right">{fmtLine(q.line)}</td>
                      <td className="py-1 pr-4 text-right">{fmtAmerican(q.american)}</td>
                      <td className="py-1 pr-4 text-xs text-muted">
                        {q.provider} / {q.provider_mode}
                      </td>
                      <td className="py-1 text-xs text-muted">{q.observed_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </article>
      ))}

      <footer className="rounded border border-border p-4 text-xs text-muted">
        {data?.not_a_claim}
      </footer>
    </div>
  );
}
