import { valuesDisagree } from '@fde/calculations';
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
  //
  // "Different" needs a tolerance. Comparing raw floats made every model
  // trip the warning the moment a rerun happened, because reruns differ in
  // the last bits from summation order and from normalising `home_win_prob`
  // — around 1e-13. The screen then announced "6 models scored differently"
  // and offered "0.2028300 vs 0.2028300" as the evidence.
  //
  // That is worse than saying nothing. This badge is what surfaced a real
  // defect earlier — the screen citing a superseded metric — and a warning
  // that fires on every row is one nobody reads again. The threshold sits
  // well above float noise and well below anything that could matter: the
  // genuine `team-ratings-v1` determinism delta is 9.65e-06, six orders of
  // magnitude above it.
  // `valuesDisagree` lives in @fde/calculations, where the tolerance and the
  // two cases that bracket it are pinned by tests: 2e-16 of float noise must
  // not fire, and the real 9.65e-06 determinism delta must.
  const runsDisagree = (briers: Set<number>): boolean => valuesDisagree(briers);
  const byScope = new Map<string, Map<string, { row: ComparisonRow; briers: Set<number> }>>();
  for (const r of rows) {
    if (!byScope.has(r.scope)) byScope.set(r.scope, new Map());
    const scope = byScope.get(r.scope)!;
    const existing = scope.get(r.model_version_id);
    if (existing) {
      if (r.metrics?.brier !== undefined) existing.briers.add(r.metrics.brier);
      // Keep the LATEST evaluation, not the first one to arrive. The
      // endpoint now returns these oldest-first, so a later row supersedes
      // the one held. Previously the first row won, and since the endpoint
      // imposed no order on the pair, WHICH number appeared was arbitrary.
      //
      // It matters here specifically: `team-ratings-v1` carries two
      // evaluations per test scope, the before and after of the
      // deterministic-ordering correction, and reports/integrity/
      // CERTIFICATION.md records the earlier ones as superseded and not to
      // be cited. The screen was showing 0.21589 — the superseded value.
      existing.row = r;
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
          <table className="w-full text-sm" aria-label="Registered models">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th scope="col" className="py-1 pr-4">Model</th>
                <th scope="col" className="py-1 pr-4">Approval</th>
                <th scope="col" className="py-1 pr-4">Algorithm</th>
                <th scope="col" className="py-1">Feature set</th>
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
        const inconsistent = entries.filter((e) => runsDisagree(e.briers));
        return (
          <section key={scope} className="rounded border border-border">
            <div className="border-b border-border p-3">
              <h2 className="text-base font-medium text-fg">{scope}</h2>
              {/* Says what this number IS. It reads as a measured noise
                  threshold and it is not one: it is 1.96·0.25/√n, a
                  standing assumption about per-game Brier spread, not
                  anything computed from these games. It is also wide on
                  purpose — 0.25 is the spread of Brier LEVELS, while this
                  band judges a DIFFERENCE between two models scored on
                  the same games, which is far less variable. So it errs
                  toward calling a real difference indistinguishable, and
                  a reader deciding what to trust should know that is the
                  direction it errs in. */}
              <p className="mt-0.5 text-xs text-muted">
                {n} games · lower Brier is better · differences smaller than ±{band.toFixed(5)}{' '}
                are treated as indistinguishable
              </p>
              <p className="mt-0.5 text-xs text-muted">
                That threshold is a deliberately wide rule of thumb
                (1.96&nbsp;×&nbsp;0.25&nbsp;÷&nbsp;√{n}), not a confidence interval measured
                from these games. It errs toward calling a difference noise.
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
              <table
                className="w-full text-sm tabular-nums"
                aria-label={`Backtest scores for ${scope}`}
              >
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th scope="col" className="py-1 pr-4">Model</th>
                    <th scope="col" className="py-1 pr-4 text-right">Brier</th>
                    <th scope="col" className="py-1 pr-4 text-right">vs market</th>
                    <th scope="col" className="py-1">Verdict</th>
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
                          {runsDisagree(briers) && (
                            <span
                              className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-[11px] text-warning"
                              title={
                                `${briers.size} backtest runs scored this model over the same ` +
                                `games: ${[...briers].map((x) => x.toFixed(7)).join(' vs ')}. ` +
                                `The latest is shown. Which run supersedes which is settled in ` +
                                `reports/integrity/CERTIFICATION.md, not by this screen.`
                              }
                            >
                              {briers.size} runs · latest shown
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
