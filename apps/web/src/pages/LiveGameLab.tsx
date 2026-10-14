import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { describeBookCoverage, describeConsensus, describeQuote } from '@fde/calculations';
import EngineDown from '../components/EngineDown';
import ResearchEvidence from '../components/ResearchEvidence';
import { engineGet, useForwardCandidates, useForwardSlate } from '../lib/engine';
import { fmtAgoLive, fmtInstant } from '../lib/format';
import { useBriefing } from '../lib/briefing';

/**
 * A real-game, read-only Game Lab. Everything rendered here comes from the
 * Python analytical engine. The demo Game Lab remains on /game/:gameId,
 * deliberately separate. No synthetic projections, fabricated odds,
 * live bet recommendations, or claimed sportsbook availability.
 *
 * /v1/forward/live has a limited kickoff horizon; /v1/forward/slate is
 * authoritative for game identity. Absence of captured quotes is NOT an
 * absence of an NFL fixture or evidence of an opportunity.
 */
interface CapturedQuote {
  sportsbook: string;
  market: string;
  selection: string;
  line: number | null;
  american: number;
  provider: string;
  provider_mode: string;
  observed_at: string;
}
interface Consensus {
  median_line: number | null;
  eligible_books: number;
  min_books_applied?: number | null;
  cohort?: string;
  observed_at: string;
}
interface Market {
  consensus: Consensus | null;
  reasons: string[];
  eligible_books_now: number;
}
interface CapturedGame {
  canonical_game_id: string;
  observed_at: string;
  schedule_provider: string;
  books: string[];
  quotes: CapturedQuote[];
  markets: Record<string, Market>;
  final: { home_score: number | null; away_score: number | null } | null;
}
interface CapturedResponse {
  data_mode: string;
  generated_at_utc: string;
  games: CapturedGame[];
  not_a_claim: string;
}

const odds = (value: number | null) => value == null ? '—' : value > 0 ? `+${value}` : `${value}`;
const percent = (value: number | null) =>
  value == null || !Number.isFinite(value) ? 'Unavailable' : `${(value * 100).toFixed(1)}%`;

export default function LiveGameLab() {
  const { gameId } = useParams<{ gameId: string }>();
  const slate = useForwardSlate(gameId);
  const preparation = useBriefing();
  const inputs = !preparation.error
    ? preparation.data?.games.find(g => g.canonical_game_id === gameId) : undefined;
  const markets = useQuery({
    queryKey: ['engine', 'forward-live', 'game-lab'],
    queryFn: () => engineGet<CapturedResponse>('/v1/forward/live'),
    enabled: Boolean(gameId),
    retry: false,
    refetchInterval: 60_000,
  });
  const candidates = useForwardCandidates(gameId);
  const game = slate.data?.games.find((g) => g.canonical_game_id === gameId);
  const captured = markets.data?.games.find((g) => g.canonical_game_id === gameId);
  const research = useMemo(
    () => (candidates.data?.candidates ?? []).filter((c) => c.canonical_game_id === gameId),
    [candidates.data, gameId],
  );
  const latestQuotes = useMemo(() => {
    // Every observation is historical, and the provider can send many
    // revisions. Show the newest observation per (book, market, side).
    const bySide = new Map<string, CapturedQuote[]>();
    for (const q of captured?.quotes ?? []) {
      const key = JSON.stringify([q.sportsbook, q.market, q.selection]);
      const time = Date.parse(q.observed_at);
      if (!Number.isFinite(time)) {
        bySide.set(JSON.stringify(q), [q]);
        continue;
      }
      const previous = bySide.get(key);
      const previousTime = previous?.[0] ? Date.parse(previous[0].observed_at) : -Infinity;
      if (!previous || time > previousTime) bySide.set(key, [q]);
      else if (time === previousTime && !previous.some((v) => JSON.stringify(v) === JSON.stringify(q))) {
        previous.push(q); // preserve contradictory newest observations
      }
    }
    return [...bySide.values()].flat().sort((a, b) =>
      (Date.parse(b.observed_at) || 0) - (Date.parse(a.observed_at) || 0));
  }, [captured]);

  if (!gameId) return <p role="alert" className="p-6 text-danger">Missing game identifier.</p>;
  if (slate.isLoading) return <p className="p-6 text-sm text-muted">Loading the real NFL schedule…</p>;
  if (slate.error) return <EngineDown title="Game Lab" message={(slate.error as Error).message} onRetry={() => { void slate.refetch(); void markets.refetch(); void candidates.refetch(); }} />;
  if (!game) {
    return (
      <div className="space-y-3 p-6">
        <h1 className="text-xl font-semibold text-fg">Game not found in the live schedule</h1>
        <p className="text-sm text-muted">The engine did not return this canonical game ID. No demo game was substituted.</p>
        <Link className="text-accent underline" to="/slate">Back to real NFL slate</Link>
      </div>
    );
  }

  const capturedAt = markets.data?.generated_at_utc;
  // Fail closed on freshness: an invalid or future observation timestamp
  // cannot be presented as a recently available bookmaker quote.
  const now = Date.now();
  const hasQuestionableTimestamps = latestQuotes.some((q) => {
    const timestamp = Date.parse(q.observed_at);
    return !Number.isFinite(timestamp) || timestamp > now + 30_000;
  });
  const hasStaleQuotes = latestQuotes.some((q) => {
    const timestamp = Date.parse(q.observed_at);
    return Number.isFinite(timestamp) && now - timestamp > 60 * 60 * 1000;
  });
  const sourceModes = [...new Set(latestQuotes.map((q) => q.provider_mode || 'UNKNOWN'))].sort();
  const hasNonLiveProvenance = latestQuotes.some((q) => q.provider_mode !== 'LIVE');
  // Two source records asserting different prices at the same instant
  // must not be silently collapsed into one authoritative observation.
  const quoteVersions = new Map<string, string>();
  const hasConflictingQuotes = (captured?.quotes ?? []).some((q) => {
    const key = JSON.stringify([q.sportsbook, q.market, q.selection, Date.parse(q.observed_at)]);
    const value = [q.line, q.american, q.provider, q.provider_mode].join('|');
    const previous = quoteVersions.get(key);
    if (previous !== undefined) return previous !== value;
    quoteVersions.set(key, value);
    return false;
  });
  return (
    <div className="space-y-5 p-2 sm:p-6">
      <header className="space-y-2">
        <button type="button" className="float-right rounded border border-border px-3 py-2 text-sm text-accent" disabled={slate.isFetching || markets.isFetching || candidates.isFetching || preparation.isFetching} onClick={() => { void slate.refetch(); void markets.refetch(); void candidates.refetch(); void preparation.refetch(); }}>Refresh analysis</button>
        <Link to="/slate" className="text-xs text-accent underline">← All games</Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-fg">Game Lab · {game.away_team_id} @ {game.home_team_id}</h1>
          <span className="rounded bg-success/15 px-2 py-1 text-xs text-success">ENGINE-BACKED RESEARCH</span>
        </div>
        <p className="text-sm text-muted">
          {fmtInstant(game.kickoff_utc)} · {game.season} {game.season_type}{' '}
          {game.week != null && game.week > 0 ? `Week ${game.week}` : ''} · {game.game_status}
        </p>
        <p className="text-xs text-muted">
          {game.venue ?? 'Venue not confirmed'} · Schedule: {game.schedule_provider} ·
          Game ID <code className="break-all">{game.canonical_game_id}</code>
        </p>
        <div className="rounded border border-warning/40 bg-warning/10 p-3 text-sm text-muted">
          Research only. Models have not been approved for real-money use. Market observations may be stale or unavailable;
          this screen is not a bet recommendation and does not communicate with bet365.
        </div>
      </header>

      <section aria-label="Prepared research inputs" className="rounded border border-border p-4">
        <div className="flex flex-wrap justify-between gap-2"><h2 className="text-lg font-semibold text-fg">Research inputs</h2><Link to="/war-room" className="text-sm text-accent underline">Back to War Room</Link></div>
        {preparation.isPending && <p role="status" className="mt-2 text-sm text-muted">Retrieving prepared evidence…</p>}
        {!preparation.isPending && !inputs && <p className="mt-2 text-sm text-muted">Input preparation is unavailable for this game. The briefing covers the current seven-day window; no demo evidence was substituted.</p>}
        {inputs && <>
          <p className="mt-3 text-sm text-muted">{inputs.weather ? `${inputs.weather.source.toUpperCase()} forecast: ${inputs.weather.temp_f ?? 'unknown'}°F; wind ${inputs.weather.wind_mph ?? 'unknown'} mph. Captured ${fmtAgoLive(inputs.weather.captured_at)}; ${inputs.weather.source_updated_at ? `issued ${fmtAgoLive(inputs.weather.source_updated_at)}` : 'issue time unknown'}.` : 'No weather forecast prepared for this game.'} Forecasts are observations; no probability adjustment was made.</p>
          <details className="mt-3 text-sm text-muted"><summary className="cursor-pointer text-fg">Missing evidence and qualification blockers</summary><ul className="mt-2 space-y-1">{inputs.missing.map(reason => <li key={reason}>{reason}</li>)}</ul><p className="mt-2">Missing reports do not mean players are healthy or confirmed active.</p></details>
        </>}
      </section>

      {inputs?.intelligence && <ResearchEvidence evidence={inputs.intelligence} />}

      <section aria-labelledby="market-title" className="rounded border border-border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="market-title" className="text-lg font-semibold text-fg">Market observations</h2>
          {capturedAt && <span className="text-xs text-muted" title={capturedAt}>Engine response {fmtAgoLive(capturedAt)}</span>}
        </div>
        {markets.isLoading && <p className="mt-2 text-sm text-muted">Retrieving captured market history…</p>}
        {markets.error && (
          <p role="alert" className="mt-2 text-sm text-danger">
            Market service unavailable: {(markets.error as Error).message}. No demo odds substituted.
          </p>
        )}
        {!markets.isLoading && !markets.error && !captured && (
          <p className="mt-2 text-sm text-muted">No captured market record for this game within the live endpoint's kickoff window.
            This is not a finding that sportsbooks have no line.</p>
        )}
        {captured && (
          <>
            <p className="mt-2 text-xs text-muted">
              {latestQuotes.length} latest book/side observations from {captured.books.length} book(s)
              · fixture observed {fmtAgoLive(captured.observed_at)}.
              {hasStaleQuotes && <span className="ml-1 text-warning">Some observations are over 60 minutes old.</span>}
              {hasQuestionableTimestamps && <span className="ml-1 text-danger">Unverified or future-dated observation time.</span>}
              {hasConflictingQuotes && <span className="ml-1 text-danger">Conflicting source observations at the same timestamp; do not rely on the displayed latest quote.</span>}
            </p>
            <p className={hasNonLiveProvenance ? 'mt-2 text-xs font-medium text-warning' : 'mt-2 text-xs text-muted'}>
              Provider evidence: {sourceModes.join(', ')}.
              {hasNonLiveProvenance
                ? ' At least one quote is not labelled LIVE by its source; do not interpret it as a current sportsbook offer.'
                : ' LIVE provenance describes capture origin, not a currently executable sportsbook price.'}
            </p>
            <p className="mt-2 text-xs text-warning">Times show when this app captured published quotes.
              Sportsbook price establishment time and current availability are unverified.
              ESPN may publish closing or summary prices; a recent capture does not make a price a current offer.</p>
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
              {(['SPREAD', 'TOTAL', 'MONEYLINE'] as const).map((market) => {
                const block = captured.markets?.[market];
                const snapshot = block?.consensus;
                const coverage = snapshot
                  ? describeBookCoverage(snapshot.eligible_books, snapshot.min_books_applied)
                  : null;
                return (
                  <div key={market} className="rounded border border-border p-3">
                    <h3 className="text-xs uppercase tracking-wide text-muted">{market}</h3>
                    {snapshot ? (
                      <>
                        <p className="mt-1 text-base font-medium text-fg">
                          {describeConsensus(market, snapshot.median_line, {
                            homeTeamId: game.home_team_id, awayTeamId: game.away_team_id,
                          })}
                        </p>
                        <p className="mt-1 text-xs text-muted">{coverage?.label} · captured {fmtAgoLive(snapshot.observed_at)}</p>
                        {coverage?.caveat && <p className="mt-1 text-xs text-warning">{coverage.caveat}</p>}
                        {snapshot.cohort && <p className="mt-1 text-xs text-muted">Cohort: {snapshot.cohort}</p>}
                      </>
                    ) : (
                      <>
                        <p className="mt-1 text-sm font-medium text-warning">DATA INCOMPLETE</p>
                        {(block?.reasons ?? ['No eligible consensus was captured']).map((reason) => (
                          <p key={reason} className="mt-1 text-xs text-muted">{reason}</p>
                        ))}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
            {latestQuotes.length > 0 && <ul className="mt-4 space-y-3 sm:hidden" aria-label="Observed market prices">
              {latestQuotes.map((q) => <li key={JSON.stringify(q)} className="rounded border border-border p-3">
                <div className="flex items-start justify-between gap-2">
                  <strong>{describeQuote(q.market, q.selection, q.line, { homeTeamId: game.home_team_id, awayTeamId: game.away_team_id })}</strong>
                  <span className="whitespace-nowrap font-medium">{odds(q.american)}</span>
                </div>
                <p className="mt-1 text-xs text-muted">{q.market} · {q.sportsbook}</p>
                <p className="mt-1 break-words text-xs text-muted">{q.provider} / {q.provider_mode || 'UNKNOWN'} · {Number.isFinite(Date.parse(q.observed_at)) ? fmtAgoLive(q.observed_at) : 'Time unavailable'}</p>
              </li>)}
            </ul>}
            {latestQuotes.length > 0 && (
              <div className="mt-4 hidden overflow-x-auto sm:block">
                <table className="w-full text-sm" aria-label="Latest observed market prices">
                  <thead><tr className="text-left text-xs uppercase text-muted">
                    <th scope="col" className="py-2 pr-3">Book</th><th scope="col" className="py-2 pr-3">Market</th>
                    <th scope="col" className="py-2 pr-3">Selection</th><th scope="col" className="py-2 pr-3">Odds</th>
                    <th scope="col" className="py-2 pr-3">Source / mode</th><th scope="col" className="py-2">Captured by app</th>
                  </tr></thead>
                  <tbody>{latestQuotes.map((q) => (
                    <tr key={JSON.stringify(q)} className="border-t border-border">
                      <td className="py-2 pr-3">{q.sportsbook}</td>
                      <td className="py-2 pr-3">{q.market}</td>
                      <td className="py-2 pr-3 font-medium text-fg">
                        {describeQuote(q.market, q.selection, q.line, {
                          homeTeamId: game.home_team_id, awayTeamId: game.away_team_id,
                        })}
                      </td>
                      <td className="py-2 pr-3">{odds(q.american)}</td>
                      <td className="py-2 pr-3 text-xs">{q.provider} / {q.provider_mode || 'UNKNOWN'}</td>
                      <td className="py-2" title={q.observed_at}>
                        {Number.isFinite(Date.parse(q.observed_at)) ? fmtAgoLive(q.observed_at) : 'Time unavailable'}
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="research-title" className="rounded border border-border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="research-title" className="text-lg font-semibold text-fg">Recorded research assessments</h2>
          <Link to="/candidates" className="text-xs text-accent underline">All candidates</Link>
        </div>
        {candidates.isLoading && <p className="mt-2 text-sm text-muted">Loading frozen-policy evaluations…</p>}
        {candidates.error && <p role="alert" className="mt-2 text-sm text-danger">
          Research assessments unavailable: {(candidates.error as Error).message}. No model values invented.
        </p>}
        {candidates.data && (candidates.data.has_more !== false || candidates.data.canonical_game_id !== gameId) && <p role="alert" className="mt-2 text-sm text-warning">
          Assessment results are incomplete or their game scope is unverified. No conclusion of absence can be drawn.</p>}
        {candidates.data && !candidates.data.gate.open && (
          <p className="mt-2 text-sm text-warning">Evaluation gate closed. See Data Health before interpreting any prior research record.</p>
        )}
        {candidates.data && candidates.data.has_more === false && candidates.data.canonical_game_id === gameId && research.length === 0 && (
          <p className="mt-2 text-sm text-muted">No active WATCH or RESEARCH_CANDIDATE entry for this game within this endpoint's pregame window.
            This is not a model prediction of zero edge, and PASS records are not part of this endpoint.</p>
        )}
        {research.map((candidate) => (
          <article key={JSON.stringify([candidate.market, candidate.selection, candidate.line, candidate.horizon, candidate.cohort, candidate.model_version, candidate.policy_version, candidate.as_of_at])} className="mt-3 rounded border border-border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="text-sm text-fg">{candidate.market} · {candidate.selection ?? 'Unspecified side'}</strong>
              <span className="rounded bg-bg-subtle px-2 py-0.5 text-xs text-muted">{candidate.status}</span>
              <span className="text-xs text-muted">{candidate.cohort} · {candidate.horizon}</span>
              {candidate.cohort !== 'official_forward_test' && (
                <span className="rounded border border-warning/40 px-2 py-0.5 text-xs text-warning">
                  Experimental/non-official cohort
                </span>
              )}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div><span className="block text-xs text-muted">Recorded price</span>{odds(candidate.american)}</div>
              <div><span className="block text-xs text-muted">Research probability</span>{percent(candidate.model_probability)}</div>
              <div><span className="block text-xs text-muted">Break-even</span>{percent(candidate.break_even_probability)}</div>
              <div><span className="block text-xs text-muted">Model edge (percentage points)</span>{percent(candidate.edge)}</div>
            </div>
            <p className="mt-2 text-xs text-muted">
              Model {candidate.model_version} · policy {candidate.policy_version} ·
              estimated price age {candidate.price_age_seconds == null ? 'unknown' : `${Math.round(candidate.price_age_seconds / 60)}m`} ·
              evaluated {candidate.as_of_at ? fmtAgoLive(candidate.as_of_at) : 'at an unknown instant'}.
              This is a historical evaluation, not an available quote.
            </p>
          </article>
        ))}
      </section>

      <footer className="rounded border border-border p-3 text-xs text-muted">
        There is no validated production betting recommendation, bookmaker connection, or real-money wager placement here.
        View <Link className="text-accent underline" to="/health">Data Health</Link> for missing inputs.
      </footer>
    </div>
  );
}
