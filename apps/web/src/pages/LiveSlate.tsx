import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { describeConsensus, describeQuote } from '@fde/calculations';
import EngineDown from '../components/EngineDown';
import { fmtAgoLive, fmtInstant } from '../lib/format';

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

interface FinalScore {
  home_score: number | null;
  away_score: number | null;
  observed_at: string;
  provider: string;
}

interface LiveGame {
  canonical_game_id: string;
  final: FinalScore | null;
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
  // No credential here: a VITE_-prefixed variable would be inlined into
  // the shipped bundle. The dev proxy adds the header server-side.
  const res = await fetch(`${API_BASE}/v1/forward/live`);
  if (!res.ok) {
    throw new Error(`Engine returned ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as LiveResponse;
}

function fmtAmerican(n: number): string {
  return n > 0 ? `+${n}` : `${n}`;
}

// Lines are STORED home-relative. `describeQuote` / `describeConsensus`
// flip the away spread back to the away team's own number and name the
// side that owns it, so a row cannot be read as the opposite of what the
// book is offering. See packages/calculations/src/marketDisplay.ts.

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
    // This used to be a hand-rolled copy of EngineDown, which meant the
    // other three live screens and this one drifted independently.
    return (
      <EngineDown
        title="Live Slate"
        message={(error as Error).message}
        expectedAt={API_BASE}
      />
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
        {/* Readable on the surface, exact on hover: every `title` below
            carries the stored UTC instant verbatim. */}
        <p className="text-sm text-muted">
          Data mode <strong>{data?.data_mode}</strong> · captured{' '}
          <span title={data?.generated_at_utc}>
            {data ? fmtAgoLive(data.generated_at_utc) : '—'}
          </span>{' '}
          · last fetched{' '}
          <span title={new Date(dataUpdatedAt).toISOString()}>
            {fmtAgoLive(new Date(dataUpdatedAt).toISOString())}
          </span>
        </p>
        {/* What this screen is a window ONTO. Without it, a page holding
            one preseason game in August reads as "the NFL has one game",
            when it means "one game has captured prices". The distinction
            is the whole difference between a broken app and an empty
            pipeline, and the reader cannot make it unaided. */}
        <p className="text-xs text-muted">
          Games with prices captured in the last 72 hours — not the full schedule.{' '}
          <Link to="/slate" className="text-accent underline">
            All upcoming fixtures
          </Link>
        </p>
      </header>

      {games.length === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          <p>
            No fixtures captured in the current horizon. This is the real answer, not a
            loading state.
          </p>
          <p className="mt-2">
            The schedule is separate from the prices: fixtures may well be loaded with
            nothing captured against them yet.{' '}
            <Link to="/health" className="text-accent underline">
              Data Health
            </Link>{' '}
            says whether capture is running.
          </p>
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
              {g.final && (
                <span className="rounded bg-fg/10 px-2 py-0.5 text-sm font-semibold text-fg">
                  FINAL {g.away_team_id} {g.final.away_score} — {g.home_team_id}{' '}
                  {g.final.home_score}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-muted">
              Kickoff <span title={g.kickoff_utc}>{fmtInstant(g.kickoff_utc)}</span> ·{' '}
              {g.venue ?? 'venue unknown'} · {g.game_status}
            </p>
            <p className="mt-1 text-xs text-muted">
              Fixture attested by <strong>{g.schedule_provider}</strong>, observed{' '}
              <span title={g.observed_at}>{fmtAgoLive(g.observed_at)}</span> ·{' '}
              <code>{g.canonical_game_id}</code>
            </p>
            {g.final && (
              <p className="mt-1 text-xs text-muted">
                Result recorded from <strong>{g.final.provider}</strong>{' '}
                <span title={g.final.observed_at}>{fmtAgoLive(g.final.observed_at)}</span>. The
                score is a captured observation, not a settlement: no wager was
                placed and none is implied.
              </p>
            )}
          </div>

          <div className="border-b border-border p-4">
            <h3 className="text-sm font-medium text-fg">Consensus</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {Object.entries(g.markets).map(([market, block]) => (
                <div key={market} className="rounded border border-border p-3">
                  <div className="text-xs uppercase tracking-wide text-muted">{market}</div>
                  {block.consensus ? (
                    <div className="mt-1 text-lg font-semibold text-fg">
                      {describeConsensus(market, block.consensus.median_line, {
                        homeTeamId: g.home_team_id,
                        awayTeamId: g.away_team_id,
                      })}
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
            {/* Once per game, not once per market. The reason shown in
                each card above is true and local — not enough books in
                the window. The reason BEHIND it (no key, no scheduler, no
                policy) lives on Data Health, and without a way through
                the reader cannot tell a quiet market from a stopped
                pipeline. Three copies of the same link is just noise. */}
            {Object.values(g.markets).some((b) => !b.consensus) && (
              <Link to="/health" className="mt-2 inline-block text-xs text-accent underline">
                Why is nothing being captured?
              </Link>
            )}
          </div>

          <div className="p-4">
            <h3 className="text-sm font-medium text-fg">
              Captured quotes{' '}
              <span className="font-normal text-muted">
                ({g.quotes.length} from {g.books.join(', ') || 'no books'})
              </span>
            </h3>
            <div className="mt-2 overflow-x-auto">
              {/* Named per game, not "quotes": with several fixtures on
                  screen the tables are otherwise indistinguishable to
                  anyone navigating by landmark. */}
              <table
                className="w-full text-sm"
                aria-label={`Captured quotes for ${g.away_team_id} at ${g.home_team_id}`}
              >
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th scope="col" className="py-1 pr-4">Book</th>
                    <th scope="col" className="py-1 pr-4">Market</th>
                    <th scope="col" className="py-1 pr-4">Quote</th>
                    <th scope="col" className="py-1 pr-4 text-right">Price</th>
                    <th scope="col" className="py-1 pr-4">Source</th>
                    <th scope="col" className="py-1">Observed</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {g.quotes.map((q, i) => (
                    <tr key={`${q.sportsbook}-${q.market}-${q.selection}-${i}`} className="border-t border-border">
                      <td className="py-1 pr-4">{q.sportsbook}</td>
                      <td className="py-1 pr-4">{q.market}</td>
                      <td className="py-1 pr-4 font-medium text-fg">
                        {describeQuote(q.market, q.selection, q.line, {
                          homeTeamId: g.home_team_id,
                          awayTeamId: g.away_team_id,
                        })}
                      </td>
                      <td className="py-1 pr-4 text-right">{fmtAmerican(q.american)}</td>
                      <td className="py-1 pr-4 text-xs text-muted">
                        {q.provider} / {q.provider_mode}
                      </td>
                      <td className="py-1 text-xs text-muted" title={q.observed_at}>
                        {fmtAgoLive(q.observed_at)}
                      </td>
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
