import { Link } from 'react-router-dom';
import EngineDown from '../components/EngineDown';
import ModelResearch from '../components/ModelResearch';
import { useBriefing } from '../lib/briefing';
import { fmtInstant, fmtAgoLive } from '../lib/format';

const panel = 'rounded-xl border border-edge bg-panel p-5';
const changeLabels: Record<string, string> = {
  kickoff_utc: 'Kickoff changed', game_status: 'Game status changed',
  markets_observed: 'Market coverage changed', books_observed: 'Book coverage changed',
  injury_observations: 'Availability evidence changed', weather_captured_at: 'Weather forecast captured',
  quote_evidence: 'Published market observations changed', new_game: 'Game added to captured slate',
  intelligence_evidence_hash: 'Team or player research changed',
};

export default function WarRoom() {
  const query = useBriefing();
  if (query.isPending) return <p role="status" className="p-6 text-ink-muted">Preparing your NFL briefing…</p>;
  // Never render cached findings as current after a failed refresh.
  if (query.error || !query.data) return <EngineDown title="War Room" message="Research is unavailable. No cached findings are presented as current." onRetry={() => void query.refetch()} />;
  const data = query.data;
  const run = data.latest_run;
  const runAge = run?.finished_at ? Date.now() - Date.parse(run.finished_at) : Infinity;
  const currentRun = run?.status === 'finished' && runAge >= 0 && runAge <= 3_600_000;
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div><p className="text-xs uppercase tracking-[0.2em] text-accent">Fourth Down Edge · Tennessee</p>
          <h1 className="mt-2 text-3xl font-semibold text-ink sm:text-4xl">The War Room</h1>
          <p className="mt-2 text-sm text-ink-muted">NFL briefing · Central Time · research only.</p></div>
        <button className="rounded-lg border border-edge px-4 py-2 text-sm text-accent focus-visible:outline focus-visible:outline-2" disabled={query.isFetching} onClick={() => void query.refetch()}>{query.isFetching ? 'Refreshing…' : 'Refresh briefing'}</button>
      </header>
      <nav aria-label="Research shortcuts" className="flex flex-wrap gap-4 text-sm text-accent lg:hidden"><Link to="/slate" className="underline">NFL slate</Link><Link to="/health" className="underline">Data health</Link></nav>
      <section aria-label="Game day briefing" className={`${panel} border-accent/30`}>
        <div className="flex flex-wrap justify-between gap-2 text-xs text-ink-muted"><span>GAME DAY BRIEFING</span><span>Engine connected · read {fmtAgoLive(data.as_of_at)}</span></div>
        <p className="mt-4 max-w-3xl text-lg leading-relaxed text-ink">{data.briefing}</p>
        <p className="mt-3 text-xs text-ink-muted">{currentRun && run?.finished_at ? `Last capture run ${fmtAgoLive(run.finished_at)}.` : 'Research capture is not current or has not completed.'} Viewing captured games in Central Time. Capture coverage is not a complete-season guarantee.</p>
        {run?.status === 'failed' && <p role="alert" className="mt-3 text-sm text-danger">Latest research run failed. Published observations may be old. Check Data Health before interpreting them.</p>}
      </section>
      <section aria-label="Top opportunity" className={panel}>
        <p className="text-xs uppercase tracking-widest text-ink-muted">Top opportunity</p>
        <h2 className="mt-3 text-3xl font-semibold text-ink">NO BET</h2>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-muted">No opportunity satisfies the verified bet365 Tennessee price and model requirements. Another book’s price cannot qualify a bet365 selection.</p>
        <details className="mt-4"><summary className="cursor-pointer text-sm text-accent">Singles and SGP status</summary><div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg bg-bg p-4"><h3 className="font-medium text-ink">Best single</h3><p className="mt-2 text-sm text-ink-muted">Unavailable until exact prices and approved research qualify.</p></div>
          <div className="rounded-lg bg-bg p-4"><h3 className="font-medium text-ink">Best SGP</h3><p className="mt-2 text-sm text-ink-muted">Unavailable. Exact combined pricing, permitted legs and a validated joint model are required.</p></div>
        </div></details>
      </section>
      <ModelResearch />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[['Games today', data.today_count], ['Games with published observations', data.observed_market_games], ['Verified bet365 opportunities', data.verified_bet365_count]].map(([label, count]) => <div key={label} className={panel}><p className="text-xs text-ink-muted">{label}</p><p className="mt-2 text-3xl font-semibold text-ink">{count}</p></div>)}
      </div>
      <section aria-label="Research slate" className="space-y-3">
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Games to investigate</h2><Link className="text-sm text-accent underline" to="/slate">Full captured slate</Link></div>
        <p className="text-sm text-ink-muted">Today and the next seven days. Showing {Math.min(6, data.games.length)} of {data.games.length} captured games; open the full slate for all games. These are research destinations, not picks.</p>
        {data.games.length === 0 && <div className={panel}><p className="text-ink">No captured games in this window</p><p className="mt-2 text-sm text-ink-muted">This does not establish that no NFL games are scheduled. A successful source capture is needed.</p></div>}
        <div className="grid gap-3 md:grid-cols-2">{data.games.slice(0, 6).map(game => <article key={game.canonical_game_id} className={panel}>
          <div className="flex flex-wrap justify-between gap-2"><h3 className="text-xl font-semibold text-ink">{game.away_team_id} at {game.home_team_id}</h3><span className="text-xs text-warning">DATA INCOMPLETE</span></div>
          <p className="mt-2 text-sm text-ink-muted">{fmtInstant(game.kickoff_utc)} · {game.game_status}</p>
          <p className="mt-4 text-sm text-ink">{game.markets_observed.length ? `Published ${game.markets_observed.join(', ').toLowerCase()} observations` : 'No published market observations'}</p>
          <p className="mt-1 text-xs text-ink-muted">Source: {game.schedule.source} · first schedule capture {fmtAgoLive(game.schedule.captured_at)}</p>
          {game.intelligence && <p className="mt-2 text-sm text-ink-muted">{game.intelligence.teams.map(team => `${team.team_id}: ${team.roster_count} mapped roster players, ${team.recent_games.length} recent games`).join(' · ')}. Open Game Lab for production and availability evidence.</p>}
          {game.weather && <p className="mt-2 text-xs text-ink-muted">{game.weather.source.toUpperCase()} forecast: {game.weather.temp_f == null ? 'temperature unavailable' : `${game.weather.temp_f}°F`}, {game.weather.wind_mph == null ? 'wind unavailable' : `wind ${game.weather.wind_mph} mph`} · {game.weather.source_updated_at ? `issued ${fmtAgoLive(game.weather.source_updated_at)}` : 'issue time unknown'} · captured {fmtAgoLive(game.weather.captured_at)}. No model adjustment.</p>}
          <details className="mt-3 text-sm text-ink-muted"><summary className="cursor-pointer text-ink">What is missing?</summary><ul className="mt-2 space-y-1">{game.missing.map(reason => <li key={reason}>{reason}</li>)}</ul><p className="mt-2">{game.research_records.length} recorded assessment(s), kept separate by study cohort. These do not qualify a current bet365 offer.</p></details>
          <Link className="mt-5 inline-block rounded-lg border border-accent/40 px-4 py-2 text-sm text-accent" to={`/live/game/${encodeURIComponent(game.canonical_game_id)}`}>Open Game Lab →</Link>
        </article>)}</div>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <section className={panel}><h2 className="font-semibold text-ink">What changed?</h2>{data.changes.length ? data.changes.map(change => {
          const game = data.games.find(g => g.canonical_game_id === change.canonical_game_id);
          return <p key={change.canonical_game_id} className="mt-3 break-words text-sm text-ink-muted">{game ? `${game.away_team_id} at ${game.home_team_id}` : change.canonical_game_id}: {change.fields.map(field => changeLabels[field] ?? 'Research evidence changed').join('; ')}. Review the evidence in Game Lab.</p>;
        }) : <p className="mt-3 text-sm text-ink-muted">No differences available between comparable completed runs. This is not proof that nothing changed in the NFL.</p>}</section>
        <section className={panel}><h2 className="font-semibold text-ink">Value watchlist</h2><p className="mt-3 text-sm text-ink-muted">No defensible bet365 price targets are available. Missing probability and price evidence prevents calculation.</p><Link className="mt-4 inline-block text-sm text-accent underline" to="/health">See data coverage and blockers →</Link></section>
      </div>
      <p className="text-xs leading-relaxed text-ink-muted">Research only. Published quotes may be closing or summary prices. A recent capture does not establish current availability. No wagers are placed, and no model is promoted by this briefing.</p>
    </div>
  );
}
