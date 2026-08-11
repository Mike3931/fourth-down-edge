import { useForwardPerformance, type ForwardCohort } from '../lib/engine';
import EngineDown from '../components/EngineDown';
import { fmtAgoLive } from '../lib/format';

/**
 * The forward-test record, per frozen policy.
 *
 * The other screen Phase 3 named and never built. It answers "is this
 * working", and the honest answer for a long time will be "not enough
 * evidence yet" — which is the answer it is built to give clearly rather
 * than to dress up.
 *
 * Deliberately separate from Model Audit, and never merged with it. Model
 * Audit reports the BACKTEST over seasons 2024 and 2025, both of which
 * docs/model-governance.md records as burned: they were read, compared
 * across six models, and reported, so they can no longer support an
 * out-of-sample claim. This screen reports a cohort collected
 * PROSPECTIVELY under a policy frozen before it started. Showing them
 * together would let a burned number stand in for an unburned one, which
 * is the single most flattering mistake available here.
 *
 * The numbers are paper. Nothing was staked.
 */

function units(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}u`;
}

function pct(p: number | null | undefined): string {
  if (p === null || p === undefined) return '—';
  return `${p >= 0 ? '+' : ''}${(p * 100).toFixed(2)}%`;
}

function Stat({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="rounded border border-border p-3">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${muted ? 'text-muted' : 'text-fg'}`}>
        {value}
      </div>
    </div>
  );
}

function Cohort({ c }: { c: ForwardCohort }) {
  const started = new Date(c.window.start) <= new Date();

  return (
    <section className="rounded border border-border">
      <div className="border-b border-border p-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-base font-medium text-fg">{c.policy_version}</h2>
          <span className="text-xs text-muted">
            {c.window.start} to {c.window.end}
          </span>
          {!started && (
            <span className="rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-muted">
              window not open yet
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted">
          {c.model_version}
          {c.calibration_version ? ` · calibration ${c.calibration_version}` : ' · no calibration pinned'}
          {' · policy hash '}
          <code>{c.policy_hash.slice(0, 12)}</code>
          {c.frozen_at ? ` · frozen ${c.frozen_at.slice(0, 10)}` : ''}
        </p>
      </div>

      {/* The sample warning leads. A reader who sees ROI first and the
          caveat last has already formed a view by the time they reach it. */}
      {c.sample_warning && (
        <div className="border-b border-border bg-warning/10 px-3 py-2 text-sm text-warning">
          {c.sample_warning} Every figure below is descriptive only — none of it
          supports a claim about edge in either direction.
        </div>
      )}

      <div className="grid gap-2 p-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Recorded" value={String(c.total_rows)} />
        <Stat label="Settled" value={String(c.settled)} />
        <Stat label="W / L / P" value={`${c.wins} / ${c.losses} / ${c.pushes}`} />
        <Stat label="P&L (paper)" value={units(c.total_pnl_units)} muted={c.settled === 0} />
        <Stat label="ROI per bet" value={pct(c.roi_per_bet)} muted={c.settled === 0} />
        <Stat label="Max drawdown" value={units(c.max_drawdown_units)} muted={c.settled === 0} />
        <Stat label="Mean CLV (line)" value={c.mean_clv_line?.toFixed(2) ?? '—'} muted />
        <Stat label="Mean CLV (prob)" value={pct(c.mean_clv_probability)} muted />
      </div>

      <div className="border-t border-border px-3 py-2">
        <div className="text-xs uppercase tracking-wide text-muted">Status breakdown</div>
        <div className="mt-1 flex flex-wrap gap-3 text-sm">
          {Object.keys(c.statuses).length === 0 && (
            <span className="text-muted">nothing recorded yet</span>
          )}
          {Object.entries(c.statuses)
            .sort()
            .map(([status, n]) => (
              <span key={status} className="text-muted">
                <span className="font-medium text-fg">{n}</span> {status}
              </span>
            ))}
        </div>
      </div>
    </section>
  );
}

export default function ForwardTestLive() {
  const { data, isLoading, error } = useForwardPerformance();

  if (isLoading) return <div className="p-6 text-sm text-muted">Asking the engine…</div>;
  if (error) return <EngineDown title="Forward Test" message={(error as Error).message} />;
  if (!data) return null;

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-fg">Forward Test</h1>
          <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
            REAL CAPTURED DATA
          </span>
        </div>
        <p className="text-sm text-muted">
          Prospectively collected under a policy frozen before collection began ·
          generated{' '}
          <span title={data.generated_at_utc}>{fmtAgoLive(data.generated_at_utc)}</span>
        </p>
        <p className="text-xs text-muted">
          Kept separate from Model Audit on purpose. That screen reports the backtest
          over 2024 and 2025, which were read and compared and are therefore burned as
          out-of-sample evidence. This is the only cohort that can support a forward
          claim, and it will need a season before it can support anything.
        </p>
      </header>

      {data.cohorts.length === 0 && (
        <div className="rounded border border-border p-4 text-sm text-muted">
          No policy has been frozen, so there is no cohort to report. This is the real
          answer, not a loading state.
        </div>
      )}

      {data.cohorts.map((c) => (
        <Cohort key={c.policy_version} c={c} />
      ))}

      <p className="text-xs text-muted">{data.not_a_claim}</p>
    </div>
  );
}
