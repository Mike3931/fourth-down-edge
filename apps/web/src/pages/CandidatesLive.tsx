import { Link } from 'react-router-dom';
import { useForwardCandidates, type CandidateRow } from '../lib/engine';
import EngineDown from '../components/EngineDown';
import { fmtAgoLive, fmtInstant } from '../lib/format';

/**
 * Research candidates recorded by the forward test.
 *
 * The screen Phase 3 named and never built. It answers the question the
 * Live Slate deliberately does not: given what was captured, which
 * selections cleared the frozen policy's threshold.
 *
 * Three things this screen must never do, all of them easy to do by
 * accident:
 *
 *   It must not imply a wager. The engine has no BET state; the strongest
 *   thing it can say is RESEARCH_CANDIDATE, and the copy says what that
 *   is rather than leaving the reader to assume.
 *
 *   It must not imply the price is still there. A candidate is a record of
 *   a price observed at an instant, and the age of that observation is
 *   shown next to it for exactly that reason.
 *
 *   It must not look empty when it is BLOCKED. An empty table reads as
 *   "no opportunities today". A gate that is closed means the engine
 *   declined to look, which is a completely different statement, so the
 *   closed gate is rendered instead of the table and says what is stopping
 *   it.
 *
 * And it must not claim the engine refused when it did not. The gate used
 * to close on any CRITICAL health check, including operational ones the
 * evaluation path itself treats as degradation and proceeds past — so with
 * no odds key this screen announced a refusal while the engine was busy
 * writing DATA INCOMPLETE rows. Blocking and degradation are now two lists
 * because they are two states, and only one of them is a refusal.
 */

function pct(p: number | null, digits = 1): string {
  return p === null || p === undefined ? '—' : `${(p * 100).toFixed(digits)}%`;
}

function signedPct(p: number | null): string {
  if (p === null || p === undefined) return '—';
  return `${p >= 0 ? '+' : ''}${(p * 100).toFixed(1)}%`;
}

function american(n: number | null): string {
  if (n === null || n === undefined) return '—';
  return n > 0 ? `+${n}` : `${n}`;
}

/** The selection as a person would say it aloud. */
function describe(c: CandidateRow): string {
  const team = c.selection === 'AWAY' ? c.away_team_id : c.home_team_id;
  if (c.market === 'SPREAD') {
    // Ledger lines are stored home-relative, like every other line in this
    // system; the away side takes the negation. See marketDisplay.ts.
    const line = c.line === null ? null : c.selection === 'AWAY' ? -c.line : c.line;
    if (line === null) return `${team}`;
    if (line === 0) return `${team} PK`;
    return `${team} ${line > 0 ? '+' : ''}${line}`;
  }
  if (c.market === 'TOTAL') {
    const side = c.selection === 'UNDER' ? 'Under' : 'Over';
    return c.line === null ? side : `${side} ${c.line}`;
  }
  return team;
}

export default function CandidatesLive() {
  const { data, isLoading, error } = useForwardCandidates();

  if (isLoading) return <div className="p-6 text-sm text-muted">Asking the engine…</div>;
  if (error) return <EngineDown title="Research Candidates" message={(error as Error).message} />;
  if (!data) return null;

  const gate = data.gate;

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-fg">Research Candidates</h1>
          <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
            REAL CAPTURED DATA
          </span>
        </div>
        <p className="text-sm text-muted">
          Selections that cleared the frozen policy&apos;s threshold, for games kicking
          off in the next {Math.round(data.horizon_hours / 24)} days · recorded{' '}
          <span title={data.generated_at_utc}>{fmtAgoLive(data.generated_at_utc)}</span>
        </p>
      </header>

      {/* A closed gate is not an empty result. Rendered INSTEAD of the
          table, because a reader who sees an empty table concludes there
          were no opportunities — when the truth is the engine refused to
          look and said why. */}
      {!gate.open && (
        <div className="rounded border border-danger/40 bg-danger/10 p-4">
          <p className="text-sm font-semibold text-danger">
            No candidates are being produced. This is not an empty slate — the engine
            is declining to evaluate.
          </p>
          <ul className="mt-2 space-y-1.5">
            {gate.blocked_by.map((b) => (
              <li key={b.check} className="text-sm">
                <code className="text-fg">{b.check}</code>
                <span className="text-muted"> — {b.explanation}</span>
                <div className="text-xs text-muted">
                  <span className="font-medium">Fix:</span> {b.remediation}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">
            <Link to="/health" className="text-accent underline">
              Data Health
            </Link>{' '}
            carries the full check list.
          </p>
        </div>
      )}

      {/* An open gate with an empty table has TWO readings, and the
          difference is the whole point of this screen.

          "Nothing cleared the threshold" is the ordinary one and is worth
          saying plainly. But it asserts that prices were there and the
          model was unimpressed — and with one book capturing, the engine
          reaches DATA INCOMPLETE without ever comparing anything. Saying
          the first while the second is true is the more damaging error of
          the two, because it implies a working pipeline.

          So the degraded checks are folded in here rather than left on
          Data Health. They do not close the gate and are not presented as
          if they did; they are presented as the likelier explanation. */}
      {gate.open && data.count === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          <p>
            The engine evaluated and recorded no candidate. Most games most weeks
            produce none, so this is a result rather than a gap.
          </p>
          {gate.degraded_by.length > 0 && (
            <>
              <p className="mt-2 text-warning">
                But the platform is degraded, and that is the more likely
                explanation — an evaluation missing its inputs reaches DATA
                INCOMPLETE, which is also an empty table.
              </p>
              <ul className="mt-2 space-y-1.5">
                {gate.degraded_by.map((d) => (
                  <li key={d.check} className="text-sm">
                    <code className="text-fg">{d.check}</code>
                    <span className="text-muted"> — {d.explanation}</span>
                    <div className="text-xs text-muted">
                      <span className="font-medium">Fix:</span> {d.remediation}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-3 text-xs">
            <Link to="/health" className="text-accent underline">
              Data Health
            </Link>{' '}
            carries the full check list.
          </p>
        </div>
      )}

      {data.count > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Research candidates">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th scope="col" className="py-2 pr-4">Kickoff</th>
                <th scope="col" className="py-2 pr-4">Game</th>
                <th scope="col" className="py-2 pr-4">Selection</th>
                <th scope="col" className="py-2 pr-4 text-right">Price</th>
                <th scope="col" className="py-2 pr-4 text-right">Model</th>
                <th scope="col" className="py-2 pr-4 text-right">Break-even</th>
                <th scope="col" className="py-2 pr-4 text-right">Edge</th>
                <th scope="col" className="py-2 pr-4">Price seen</th>
                <th scope="col" className="py-2">Status</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.candidates.map((c) => (
                <tr
                  key={`${c.canonical_game_id}-${c.market}-${c.selection}-${c.horizon}`}
                  className="border-t border-border"
                >
                  <td className="py-1.5 pr-4 whitespace-nowrap text-muted" title={c.kickoff_utc}>
                    {fmtInstant(c.kickoff_utc)}
                  </td>
                  <td className="py-1.5 pr-4 text-muted">
                    {c.away_team_id} @ {c.home_team_id}
                  </td>
                  <td className="py-1.5 pr-4 font-medium text-fg">{describe(c)}</td>
                  <td className="py-1.5 pr-4 text-right">{american(c.american)}</td>
                  <td className="py-1.5 pr-4 text-right">{pct(c.model_probability)}</td>
                  <td className="py-1.5 pr-4 text-right text-muted">
                    {pct(c.break_even_probability)}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-medium text-fg">
                    {signedPct(c.edge)}
                  </td>
                  {/* The age of the observation, not of the row. A price
                      seen two hours ago may well be gone. */}
                  <td className="py-1.5 pr-4 text-xs text-muted">
                    {c.price_age_seconds === null
                      ? '—'
                      : `${Math.round(c.price_age_seconds / 60)}m old`}
                    {c.price_source ? ` · ${c.price_source}` : ''}
                  </td>
                  <td className="py-1.5">
                    <span
                      className={
                        c.status === 'RESEARCH_CANDIDATE'
                          ? 'rounded bg-success/15 px-1.5 py-0.5 text-xs text-success'
                          : 'rounded bg-bg-subtle px-1.5 py-0.5 text-xs text-muted'
                      }
                    >
                      {c.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-muted">{data.not_a_claim}</p>
      <p className="text-xs text-muted">
        A candidate records that a price was observed and cleared a threshold at a
        point in time. It is not advice, it is not a wager, and it does not mean the
        price is still available — check the book yourself before acting on anything
        here. This software places no wagers and has no BET state.
      </p>
    </div>
  );
}
