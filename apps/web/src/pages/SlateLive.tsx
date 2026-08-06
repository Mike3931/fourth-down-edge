import { useMemo, useState } from 'react';
import { useForwardSlate, type SlateGame } from '../lib/engine';
import EngineDown from '../components/EngineDown';

/**
 * The real ingested slate.
 *
 * Provenance is a visible column, not a footnote. A fixture attested only
 * by an odds provider is a weaker record than one from the schedule feed,
 * and the difference is invisible unless the screen shows it.
 */

function fmtKick(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  });
}

export default function SlateLive() {
  const { data, isLoading, error } = useForwardSlate();
  const [query, setQuery] = useState('');
  const [onlyUpcoming, setOnlyUpcoming] = useState(true);

  const games = useMemo(() => {
    const all = data?.games ?? [];
    const now = Date.now();
    return all.filter((g) => {
      if (onlyUpcoming && new Date(g.kickoff_utc).getTime() < now) return false;
      if (!query.trim()) return true;
      const q = query.trim().toUpperCase();
      return (
        g.home_team_id.includes(q) ||
        g.away_team_id.includes(q) ||
        g.canonical_game_id.toUpperCase().includes(q)
      );
    });
  }, [data, query, onlyUpcoming]);

  if (isLoading) return <div className="p-6 text-sm text-muted">Asking the engine…</div>;
  if (error) return <EngineDown title="Slate" message={(error as Error).message} />;

  const byProvider = new Map<string, number>();
  for (const g of data?.games ?? []) {
    byProvider.set(g.schedule_provider, (byProvider.get(g.schedule_provider) ?? 0) + 1);
  }

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-fg">Slate</h1>
          <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
            LIVE FROM ENGINE
          </span>
        </div>
        <p className="text-sm text-muted">
          {data?.count ?? 0} games ingested ·{' '}
          {[...byProvider.entries()].map(([p, n]) => `${n} from ${p}`).join(', ')}
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by team or id…"
          className="rounded border border-border bg-bg px-3 py-1.5 text-sm text-fg placeholder:text-muted"
        />
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={onlyUpcoming}
            onChange={(e) => setOnlyUpcoming(e.target.checked)}
          />
          Upcoming only
        </label>
        <span className="text-sm text-muted">{games.length} shown</span>
      </div>

      {games.length === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          Nothing matches. This is the real answer, not a loading state.
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="py-2 pr-4">Kickoff</th>
              <th className="py-2 pr-4">Matchup</th>
              <th className="py-2 pr-4">Season</th>
              <th className="py-2 pr-4">Venue</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2">Fixture source</th>
            </tr>
          </thead>
          <tbody>
            {games.map((g: SlateGame) => (
              <tr key={g.canonical_game_id} className="border-t border-border">
                <td className="py-1.5 pr-4 whitespace-nowrap text-muted">{fmtKick(g.kickoff_utc)}</td>
                <td className="py-1.5 pr-4 font-medium text-fg">
                  {g.away_team_id} @ {g.home_team_id}
                  {g.neutral_site && (
                    <span className="ml-2 rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-muted">
                      neutral
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-4 text-muted">
                  {g.season} {g.season_type}
                  {g.week ? ` W${g.week}` : ''}
                </td>
                <td className="py-1.5 pr-4 text-muted">{g.venue ?? '—'}</td>
                <td className="py-1.5 pr-4 text-muted">{g.game_status}</td>
                <td className="py-1.5">
                  <span
                    className={
                      g.schedule_provider === 'nflverse'
                        ? 'rounded bg-bg-subtle px-1.5 py-0.5 text-xs text-muted'
                        : 'rounded bg-warning/15 px-1.5 py-0.5 text-xs text-warning'
                    }
                    title={
                      g.schedule_provider === 'nflverse'
                        ? 'From the schedule feed.'
                        : 'Attested only by an odds provider — a weaker record than the schedule feed.'
                    }
                  >
                    {g.schedule_provider}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
