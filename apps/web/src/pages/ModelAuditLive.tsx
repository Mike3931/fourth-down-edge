import { useEngineModels, useModelComparison, type ComparisonRow } from '../lib/engine';
import EngineDown from '../components/EngineDown';

/**
 * The real model registry and the real out-of-sample scores.
 *
 * Every model is scored against the MARKET BENCHMARK rather than in
 * isolation, because a Brier score alone reads as "good" to almost anyone.
 * The number that decides whether a model is worth anything is the
 * difference from simply taking the market price, and that difference is
 * shown as the primary column with its own noise band alongside it.
 */

/** Rough 95% band for a Brier difference at this sample size. */
function noiseBand(n: number): number {
  // Per-game Brier differences on NFL game outcomes have sd of roughly
  // 0.15-0.25. The conservative end is used, so the band errs towards
  // calling a difference indistinguishable rather than real.
  return (1.96 * 0.25) / Math.sqrt(Math.max(n, 1));
}

function Verdict({ delta, band }: { delta: number; band: number }) {
  if (Math.abs(delta) < band) {
    return <span className="text-muted">indistinguishable from the market</span>;
  }
  return delta < 0 ? (
    <span className="text-success">better than the market</span>
  ) : (
    <span className="text-danger">worse than the market</span>
  );
}

export default function ModelAuditLive() {
  const models = useEngineModels();
  const comparison = useModelComparison();

  if (models.isLoading || comparison.isLoading) {
    return <div className="p-6 text-sm text-muted">Asking the engine…</div>;
  }
  const err = models.error ?? comparison.error;
  if (err) return <EngineDown title="Model Audit" message={(err as Error).message} />;

  const rows = comparison.data?.rows ?? [];
  const benchmarkId = comparison.data?.market_benchmark_id ?? 'market-benchmark-v1';

  // Several backtest runs can evaluate the same model on the same scope.
  // One row per (model, scope), but a DISAGREEMENT between runs is
  // surfaced rather than silently resolved: two runs of the same model
  // over the same games that return different numbers is a reproducibility
  // problem, and quietly taking the last one hides it.
  const byScope = new Map<string, Map<string, { row: ComparisonRow; briers: Set<number> }>>();
  for (const r of rows) {
    if (!byScope.has(r.scope)) byScope.set(r.scope, new Map());
    const scope = byScope.get(r.scope)!;
    const existing = scope.get(r.model_version_id);
    if (existing) {
      if (r.metrics?.brier !== undefined) existing.briers.add(r.metrics.brier);
    } else {
      scope.set(r.model_version_id, {
        row: r,
        briers: new Set(r.metrics?.brier !== undefined ? [r.metrics.brier] : []),
      });
    }
  }

  return (
    <div className="space-y-6 p-6">
      <header className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-fg">Model Audit</h1>
        <span className="rounded bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
          LIVE FROM ENGINE
        </span>
      </header>

      <section className="rounded border border-border">
        <div className="border-b border-border p-3">
          <h2 className="text-base font-medium text-fg">Registered models</h2>
          <p className="mt-0.5 text-xs text-muted">
            Approval status is set by the registry, not by this screen.
          </p>
        </div>
        <div className="overflow-x-auto p-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="py-1 pr-4">Model</th>
                <th className="py-1 pr-4">Approval</th>
                <th className="py-1 pr-4">Algorithm</th>
                <th className="py-1">Feature set</th>
              </tr>
            </thead>
            <tbody>
              {(models.data ?? []).map((m) => (
                <tr key={m.id} className="border-t border-border">
                  <td className="py-1 pr-4 font-medium text-fg">{m.id}</td>
                  <td className="py-1 pr-4">
                    <span
                      className={
                        m.approval_status === 'research_only'
                          ? 'rounded bg-warning/15 px-1.5 py-0.5 text-xs text-warning'
                          : 'rounded bg-success/15 px-1.5 py-0.5 text-xs text-success'
                      }
                    >
                      {m.approval_status}
                    </span>
                  </td>
                  <td className="py-1 pr-4 text-muted">{m.algorithm ?? '—'}</td>
                  <td className="py-1 text-muted">{m.feature_set ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {[...byScope.entries()].map(([scope, models]) => {
        const entries = [...models.values()];
        const bench = entries.find((e) => e.row.model_version_id === benchmarkId)?.row;
        const benchBrier = bench?.metrics?.brier;
        const n = bench?.sample_size ?? entries[0]?.row.sample_size ?? 0;
        const band = noiseBand(n);
        const sorted = [...entries].sort(
          (a, b) => (a.row.metrics?.brier ?? 9) - (b.row.metrics?.brier ?? 9),
        );
        const inconsistent = entries.filter((e) => e.briers.size > 1);
        return (
          <section key={scope} className="rounded border border-border">
            <div className="border-b border-border p-3">
              <h2 className="text-base font-medium text-fg">{scope}</h2>
              <p className="mt-0.5 text-xs text-muted">
                {n} games · lower Brier is better · differences smaller than ±{band.toFixed(5)}{' '}
                cannot be told from noise at this sample size
              </p>
              {inconsistent.length > 0 && (
                <p className="mt-1 text-xs text-warning">
                  {inconsistent.length} model
                  {inconsistent.length === 1 ? '' : 's'} scored differently by separate backtest
                  runs over the same games. Hover the badge for the values.
                </p>
              )}
            </div>
            <div className="overflow-x-auto p-3">
              <table className="w-full text-sm tabular-nums">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th className="py-1 pr-4">Model</th>
                    <th className="py-1 pr-4 text-right">Brier</th>
                    <th className="py-1 pr-4 text-right">vs market</th>
                    <th className="py-1">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(({ row: r, briers }) => {
                    const b = r.metrics?.brier;
                    const isBench = r.model_version_id === benchmarkId;
                    const delta =
                      b !== undefined && benchBrier !== undefined ? b - benchBrier : undefined;
                    return (
                      <tr key={r.model_version_id} className="border-t border-border">
                        <td className="py-1 pr-4 text-fg">
                          {r.model_version_id}
                          {isBench && (
                            <span className="ml-2 rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-muted">
                              benchmark
                            </span>
                          )}
                          {briers.size > 1 && (
                            <span
                              className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-[11px] text-warning"
                              title={`Backtest runs disagree: ${[...briers].map((x) => x.toFixed(7)).join(' vs ')}`}
                            >
                              runs disagree
                            </span>
                          )}
                        </td>
                        <td className="py-1 pr-4 text-right">{b?.toFixed(5) ?? '—'}</td>
                        <td className="py-1 pr-4 text-right">
                          {delta === undefined ? '—' : delta > 0 ? `+${delta.toFixed(5)}` : delta.toFixed(5)}
                        </td>
                        <td className="py-1 text-xs">
                          {isBench || delta === undefined ? (
                            <span className="text-muted">—</span>
                          ) : (
                            <Verdict delta={delta} band={band} />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}

      <footer className="rounded border border-border p-4 text-xs text-muted">
        Out-of-sample backtest scores. Not a forward-test result and not a claim about
        profitability. A model that cannot be distinguished from the market does not clear the
        vig, which at -110 requires winning 52.4% to break even.
      </footer>
    </div>
  );
}
