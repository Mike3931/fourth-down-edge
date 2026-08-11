import { useEffect, useMemo, useState } from 'react';
import { soonestScheduledWeek, weekLabel } from '@fde/calculations';
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
  // '' means every week. The default is chosen below once the data
  // arrives: a full season is 272 rows, and a screen that opens on all of
  // them answers "what is in the database" when the question is "what is
  // on this week".
  const [week, setWeek] = useState<string>('');
  const [weekTouched, setWeekTouched] = useState(false);

  // `!= null`, not truthiness. Preseason games carry week 0, and `if
  // (g.week)` dropped every one of them from the filter — see weekLabel
  // in @fde/calculations, where both this and the default selection are
  // under test.
  const weeks = useMemo(() => {
    const seen = new Set<number>();
    for (const g of data?.games ?? []) if (g.week != null) seen.add(g.week);
    return [...seen].sort((a, b) => a - b);
  }, [data]);

  // The soonest week that still has a game ahead of it — the one a person
  // opening this screen is almost always asking about.
  const nextWeek = useMemo(
    () => soonestScheduledWeek(data?.games ?? []),
    [data],
  );

  // Applied once, and never again after the reader picks for themselves —
  // a default that keeps reasserting itself is a screen fighting its user.
  useEffect(() => {
    if (!weekTouched && week === '' && nextWeek !== null) setWeek(String(nextWeek));
  }, [nextWeek, week, weekTouched]);

  const games = useMemo(() => {
    const all = data?.games ?? [];
    const now = Date.now();
    return all.filter((g) => {
      if (week !== '' && String(g.week ?? '') !== week) return false;
      if (onlyUpcoming && new Date(g.kickoff_utc).getTime() < now) return false;
      if (!query.trim()) return true;
      const q = query.trim().toUpperCase();
      return (
        g.home_team_id.includes(q) ||
        g.away_team_id.includes(q) ||
        g.canonical_game_id.toUpperCase().includes(q)
      );
    });
  }, [data, query, onlyUpcoming, week]);

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
        <label className="flex items-center gap-2 text-sm text-muted">
          Week
          <select
            value={week}
            onChange={(e) => {
              setWeek(e.target.value);
              setWeekTouched(true);
            }}
            className="rounded border border-border bg-bg px-2 py-1.5 text-sm text-fg"
          >
            <option value="">All weeks</option>
            {weeks.map((w) => (
              <option key={w} value={String(w)}>
                {weekLabel(w)}
                {w === nextWeek ? ' (next)' : ''}
              </option>
            ))}
          </select>
        </label>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by team or id…"
          aria-label="Filter by team or id"
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
        <span className="text-sm text-muted">
          {games.length} shown
          {week !== '' && (
            <>
              {' '}
              of {data?.count ?? 0} ·{' '}
              <button
                type="button"
                onClick={() => {
                  setWeek('');
                  setWeekTouched(true);
                }}
                className="underline hover:text-fg"
              >
                show all weeks
              </button>
            </>
          )}
        </span>
      </div>

      {games.length === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          Nothing matches. This is the real answer, not a loading state.
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label="Ingested fixtures">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th scope="col" className="py-2 pr-4">Kickoff</th>
              <th scope="col" className="py-2 pr-4">Matchup</th>
              <th scope="col" className="py-2 pr-4">Season</th>
              <th scope="col" className="py-2 pr-4">Venue</th>
              <th scope="col" className="py-2 pr-4">Status</th>
              <th scope="col" className="py-2">Fixture source</th>
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
                  {/* Not `g.week ? …` — zero is the preseason, not absent. */}
                  {g.week != null && g.week > 0 ? ` W${g.week}` : ''}
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
