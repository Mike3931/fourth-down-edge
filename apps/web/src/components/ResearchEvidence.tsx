import type { Intelligence } from '../lib/briefing';
import { fmtInstant } from '../lib/format';

const value = (n: number | null | undefined) => n == null ? 'Unavailable' : Number.isInteger(n) ? String(n) : n.toFixed(2);

export default function ResearchEvidence({ evidence }: { evidence: Intelligence }) {
  return <section aria-label="NFL team and player research" className="space-y-4">
    <div><h2 className="text-xl font-semibold text-fg">Teams and players</h2>
      <p className="mt-1 text-sm text-muted">Evidence available at {fmtInstant(evidence.cutoff)}. Recent production describes completed games; it is not a projection.</p></div>
    {evidence.sources.some(s => s.status === 'REFRESH_FAILED') && <p role="alert" className="text-sm text-warning">A source refresh failed. Retained observations below are historical; current coverage is unverified.</p>}
    <div className="grid gap-4 lg:grid-cols-2">{evidence.teams.map(team => <article key={team.team_id} className="rounded border border-border p-4">
      <h3 className="text-lg font-semibold text-fg">{team.team_id}</h3>
      <p className="mt-1 text-xs text-muted">{team.roster_count} mapped roster players{team.roster_week ? ` · Week ${team.roster_week}` : ''}. Roster status does not confirm game-day participation.</p>
      <h4 className="mt-4 font-medium text-fg">Recent performance</h4>
      {team.recent_games.length ? <ul className="mt-2 space-y-2 text-sm text-muted">{team.recent_games.map(game => <li key={game.canonical_game_id} className="rounded bg-bg-subtle p-2">
        <strong className="text-fg">Week {game.week}</strong>: {value(game.metrics.passing_yards)} passing yards; {value(game.metrics.rushing_yards)} rushing yards.
        <span className="block text-xs">Passing EPA {value(game.metrics.passing_epa)} · rushing EPA {value(game.metrics.rushing_epa)}. Totals from the published team statistics.</span>
      </li>)}</ul> : <p className="mt-2 text-sm text-muted">No linked completed-game statistics available at this cutoff.</p>}
      <h4 className="mt-4 font-medium text-fg">Player usage and production</h4>
      <p className="mt-1 text-xs text-muted">Showing {team.players.length} of {team.player_count} mapped players with recent attempts, carries or targets. Routes and complete participation coverage are unavailable.</p>
      <ul aria-label={`${team.team_id} player production`} className="mt-2 space-y-3">{team.players.map(player => <li key={player.player_id} className="rounded bg-bg-subtle p-3">
        <div className="flex flex-wrap justify-between gap-1"><strong className="text-sm text-fg">{player.name} · {player.position ?? 'Position unknown'}</strong><span className="text-xs text-muted">Week {player.week}</span></div>
        <p className="mt-1 text-sm text-muted">{player.position === 'QB'
          ? `${value(player.metrics.attempts)} pass attempts · ${value(player.metrics.passing_yards)} passing yards`
          : `${value(player.metrics.carries)} carries · ${value(player.metrics.rushing_yards)} rushing yards`}</p>
        <p className="text-sm text-muted">{value(player.metrics.targets)} targets · {value(player.metrics.receptions)} receptions · {value(player.metrics.receiving_yards)} receiving yards</p>
        <p className="text-xs text-muted">{player.snap_share == null ? 'Matched offensive snap share unavailable' : `${(player.snap_share * 100).toFixed(0)}% of offensive snaps in that completed game`}</p>
      </li>)}</ul>
      <h4 className="mt-4 font-medium text-fg">Availability</h4>
      <p className="mt-1 text-sm text-warning">Complete official report and inactive-list coverage is unverified. Missing reports do not mean healthy or active.</p>
      {team.verified_availability.map(item => <p key={item.player_id} className="mt-2 text-sm text-muted">Verified recorded report: {item.player_id} · {item.practice ?? 'Practice unknown'} · {item.designation ?? 'Designation unknown'}. Issued {item.published_at ? fmtInstant(item.published_at) : 'unknown'}; captured {fmtInstant(item.captured_at)}.</p>)}
      {team.availability.length > 0 && <details className="mt-3 text-sm text-muted"><summary className="cursor-pointer text-fg">Secondary injury observations ({team.availability.length})</summary><ul className="mt-2 space-y-2">{team.availability.map(item => <li key={item.player_id}>{item.name}: {item.practice ?? 'Practice unknown'}; {item.designation ?? 'Designation unknown'}. Report issue time unverified; not a confirmed inactive.</li>)}</ul></details>}
    </article>)}</div>
    <details className="text-sm text-muted"><summary className="cursor-pointer text-fg">Sources and timing</summary><ul className="mt-3 space-y-3">{evidence.sources.map(source => <li key={source.dataset} className="break-words">
      <a href={source.source_url} target="_blank" rel="noreferrer" className="text-accent underline">{source.dataset.replaceAll('_', ' ')}</a>: {source.status.replaceAll('_', ' ').toLowerCase()}.
      <span className="block">Artifact published {source.published_at ? fmtInstant(source.published_at) : 'time unknown'} · first captured {fmtInstant(source.captured_at)}.</span>
      <span className="block">{source.coverage.source_rows} source rows; {source.coverage.accepted_rows} accepted research rows; {source.coverage.refused_rows} withheld{source.coverage.max_week ? ` · through Week ${source.coverage.max_week}` : ''}. Artifact publication is not an individual injury report's issue time.</span>
    </li>)}</ul></details>
    <details className="text-sm text-muted"><summary className="cursor-pointer text-fg">What is still missing?</summary><ul className="mt-2 space-y-1">{evidence.missing.map(reason => <li key={reason}>{reason}</li>)}</ul><p className="mt-2">Research inputs remain incomplete. No approved betting probability or price qualification follows from these observations.</p></details>
  </section>;
}
