import { useForwardHealth, type HealthCheck } from '../lib/engine';
import EngineDown from '../components/EngineDown';

/**
 * The engine's real health checks, grouped by the scope each speaks for.
 *
 * Scope is the point. An unset provider key and a leaked future feature
 * are both "unhealthy", and collapsing them into one number is how a
 * platform problem gets mistaken for a reason to distrust a decision — or,
 * worse, how a decision-integrity failure hides behind a green count.
 */

const SCOPE_COPY: Record<string, { title: string; blurb: string }> = {
  DECISION_INPUT: {
    title: 'Decision input',
    blurb: 'Affects whether a decision can honestly be made at all.',
  },
  OPERATIONAL_PLATFORM: {
    title: 'Operational platform',
    blurb: 'The machinery. A problem here does not by itself invalidate an analysis.',
  },
  POSTGAME_EVALUATION: {
    title: 'Post-game evaluation',
    blurb: 'Affects what can be measured after the fact.',
  },
  GOVERNANCE_INTEGRITY: {
    title: 'Governance integrity',
    blurb: 'Affects whether the record itself can be trusted.',
  },
};

function sevClass(severity: string, ok: boolean): string {
  if (ok) return 'text-success';
  if (severity === 'CRITICAL') return 'text-danger';
  if (severity === 'WARNING') return 'text-warning';
  return 'text-muted';
}

function CheckRow({ c }: { c: HealthCheck }) {
  const ok = c.status === 'OK';
  return (
    <li className="border-t border-border py-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className={`text-xs font-semibold ${sevClass(c.severity, ok)}`}>
          {ok ? 'OK' : c.severity}
        </span>
        <code className="text-sm text-fg">{c.id}</code>
        {c.suppresses_candidates && !ok && (
          <span className="rounded bg-danger/15 px-1.5 py-0.5 text-[11px] text-danger">
            suppresses candidates
          </span>
        )}
      </div>
      <p className="mt-0.5 text-sm text-muted">{c.explanation}</p>
      {!ok && c.remediation && (
        <p className="mt-0.5 text-xs text-muted">
          <span className="font-medium">Fix:</span> {c.remediation}
        </p>
      )}
    </li>
  );
}

export default function DataHealthLive() {
  const { data, isLoading, error } = useForwardHealth();

  if (isLoading) return <div className="p-6 text-sm text-muted">Asking the engine…</div>;
  if (error) {
    return <EngineDown title="Data Health" message={(error as Error).message} />;
  }
  if (!data) return null;

  const failing = data.total - data.ok;
  const banner =
    data.worst_severity === 'CRITICAL'
      ? 'bg-danger/10 border-danger/40 text-danger'
      : data.worst_severity === 'WARNING'
        ? 'bg-warning/10 border-warning/40 text-warning'
        : 'bg-success/10 border-success/40 text-success';

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-fg">Data Health</h1>
          <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
            LIVE FROM ENGINE
          </span>
        </div>
        <div className={`rounded border p-3 text-sm ${banner}`}>
          <strong>{data.ok} of {data.total} checks passing.</strong>{' '}
          {failing === 0
            ? 'Nothing outstanding.'
            : `${failing} outstanding — worst severity ${data.worst_severity}.`}
        </div>
        <p className="text-xs text-muted">
          Data mode {data.data_mode} · generated {data.generated_at_utc}
        </p>
      </header>

      {Object.entries(data.by_scope).map(([scope, checks]) => {
        const copy = SCOPE_COPY[scope] ?? { title: scope, blurb: '' };
        const bad = checks.filter((c) => c.status !== 'OK');
        return (
          <section key={scope} className="rounded border border-border">
            <div className="border-b border-border p-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <h2 className="text-base font-medium text-fg">{copy.title}</h2>
                <span className="text-xs text-muted">
                  {checks.length - bad.length}/{checks.length} passing
                </span>
              </div>
              <p className="mt-0.5 text-xs text-muted">{copy.blurb}</p>
            </div>
            <ul className="px-3 pb-2">
              {/* Failing first: the reason anyone opens this page. */}
              {[...bad, ...checks.filter((c) => c.status === 'OK')].map((c) => (
                <CheckRow key={c.id} c={c} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
